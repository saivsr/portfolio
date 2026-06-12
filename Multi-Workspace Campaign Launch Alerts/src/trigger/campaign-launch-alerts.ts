/**
 * Multi-Workspace Campaign Launch Alerts
 * ---------------------------------------
 * Scheduled task: poll every client workspace on the cold-email platform,
 * diff the ACTIVE campaign set against what we've already announced, and post a
 * single Slack alert per genuinely-new launch. First run silently seeds state.
 *
 * Faithful standalone reconstruction of a production n8n workflow.
 *
 * Sanitization note: client/workspace names and IDs arrive from the live API at
 * runtime; nothing confidential is hard-coded here. Secrets are read from env.
 */

import { schedules, logger } from "@trigger.dev/sdk/v3";
import { Pool } from "pg";
import { WebClient } from "@slack/web-api";

const PLUSVIBE_BASE_URL = "https://api.plusvibe.ai/api/v1";
const PAGE_SIZE = 100;
const MAX_PAGES_PER_WORKSPACE = 50; // safety ceiling: 5,000 active campaigns/workspace

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required env var: ${name}`);
  return value;
}

// Module-level singletons (reused across runs).
const pool = new Pool({ connectionString: requireEnv("DATABASE_URL") });
const slack = new WebClient(requireEnv("SLACK_BOT_TOKEN"));

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Workspace {
  _id: string;
  name: string;
}

/** Raw campaign shape from the platform (only the fields we use). */
interface PvCampaign {
  id: string;
  camp_name?: string;
  workspace_id?: string;
  status?: string;
  created_at?: string;
  lead_count?: number;
  sequence_steps?: number;
  daily_limit?: number;
}

interface Campaign {
  campaignId: string;
  campName: string;
  workspaceId: string;
  workspaceName: string;
  status: string;
  leadCount: number;
  sequenceSteps: number;
  dailyLimit: number;
}

// ---------------------------------------------------------------------------
// PlusVibe client
// ---------------------------------------------------------------------------

async function pvGet(path: string, params: Record<string, string>): Promise<unknown> {
  const url = new URL(`${PLUSVIBE_BASE_URL}${path}`);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);

  const res = await fetch(url, {
    headers: { "x-api-key": requireEnv("PLUSVIBE_API_KEY") },
  });
  if (!res.ok) {
    throw new Error(`PlusVibe ${path} -> ${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** One account-wide key returns every workspace the agency runs. */
async function listWorkspaces(): Promise<Workspace[]> {
  const body = (await pvGet("/authenticate", {})) as { workspaces?: Workspace[] };
  return Array.isArray(body?.workspaces) ? body.workspaces : [];
}

/**
 * Every ACTIVE parent campaign for a workspace, following skip-based pagination.
 * `parent` excludes sub-sequences; the list is sorted newest-created-first, so a
 * fresh launch surfaces on the first page.
 */
async function listActiveCampaigns(workspaceId: string): Promise<PvCampaign[]> {
  const out: PvCampaign[] = [];
  for (let page = 0; page < MAX_PAGES_PER_WORKSPACE; page++) {
    const body = await pvGet("/campaign/list-all", {
      workspace_id: workspaceId,
      status: "ACTIVE",
      campaign_type: "parent",
      limit: String(PAGE_SIZE),
      skip: String(page * PAGE_SIZE),
    });
    // Response is a top-level array; tolerate a `{ data: [...] }` wrapper too.
    const items: PvCampaign[] = Array.isArray(body)
      ? (body as PvCampaign[])
      : ((body as { data?: PvCampaign[] })?.data ?? []);
    out.push(...items);
    if (items.length < PAGE_SIZE) break;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Normalize
// ---------------------------------------------------------------------------

function normalize(raw: PvCampaign[], nameById: Map<string, string>): Campaign[] {
  const seen = new Set<string>();
  const out: Campaign[] = [];
  for (const c of raw) {
    if (!c?.id || seen.has(c.id)) continue;
    seen.add(c.id);
    const workspaceId = c.workspace_id ?? "";
    out.push({
      campaignId: String(c.id),
      campName: c.camp_name || "(unnamed)",
      workspaceId,
      workspaceName: nameById.get(workspaceId) || workspaceId,
      status: c.status || "",
      leadCount: c.lead_count ?? 0,
      sequenceSteps: c.sequence_steps ?? 0,
      dailyLimit: c.daily_limit ?? 0,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// State (Postgres)
// ---------------------------------------------------------------------------

async function getSeenCampaignIds(): Promise<Set<string>> {
  const { rows } = await pool.query<{ campaign_id: string }>(
    "SELECT campaign_id FROM automation.pv_launch_state",
  );
  return new Set(rows.map((r) => r.campaign_id));
}

/**
 * Record candidates and return the ids we *actually* inserted.
 * `ON CONFLICT DO NOTHING RETURNING` makes "record-then-alert" race-proof:
 * two overlapping runs can never both win the same campaign id.
 */
async function recordNewLaunches(candidates: Campaign[]): Promise<Set<string>> {
  const inserted = new Set<string>();
  for (const c of candidates) {
    const { rows } = await pool.query<{ campaign_id: string }>(
      `INSERT INTO automation.pv_launch_state
         (campaign_id, workspace_id, workspace_name, camp_name, status)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (campaign_id) DO NOTHING
       RETURNING campaign_id`,
      [c.campaignId, c.workspaceId, c.workspaceName, c.campName, c.status],
    );
    if (rows[0]) inserted.add(rows[0].campaign_id);
  }
  return inserted;
}

// ---------------------------------------------------------------------------
// Slack
// ---------------------------------------------------------------------------

function formatAlert(c: Campaign, detectedAt: string): string {
  return [
    "🚀 *New campaign launched*",
    `*Client / Workspace:* ${c.workspaceName}`,
    `*Campaign:* ${c.campName}`,
    `*Status:* ${c.status}  ·  *Leads:* ${c.leadCount}  ·  *Sequence steps:* ${c.sequenceSteps}  ·  *Daily limit:* ${c.dailyLimit}`,
    `*Launched:* ${detectedAt}`,
  ].join("\n");
}

function detectionStamp(): string {
  return new Date().toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

// ---------------------------------------------------------------------------
// Scheduled task
// ---------------------------------------------------------------------------

export const campaignLaunchAlerts = schedules.task({
  id: "campaign-launch-alerts",
  cron: "*/30 * * * *",
  maxDuration: 120,
  run: async () => {
    const workspaces = await listWorkspaces();
    const nameById = new Map(workspaces.map((w) => [w._id, w.name]));

    // Fan out across workspaces — one failing workspace must not sink the run.
    const raw: PvCampaign[] = [];
    for (const ws of workspaces) {
      try {
        raw.push(...(await listActiveCampaigns(ws._id)));
      } catch (err) {
        logger.error("workspace fetch failed", { workspaceId: ws._id, err: String(err) });
      }
    }

    const active = normalize(raw, nameById);
    const seen = await getSeenCampaignIds();
    const firstRun = seen.size === 0; // empty table => silent seed

    const fresh = active.filter((c) => !seen.has(c.campaignId));
    if (fresh.length === 0) {
      logger.info("no new launches", { active: active.length });
      return { active: active.length, alerted: 0, seeded: false };
    }

    const inserted = await recordNewLaunches(fresh);

    // First run ever: record the whole backlog silently, never replay it as launches.
    if (firstRun) {
      logger.info("seeded state silently", { seeded: inserted.size });
      return { active: active.length, alerted: 0, seeded: true };
    }

    const detectedAt = detectionStamp();
    const channel = requireEnv("SLACK_CHANNEL_ID");
    let alerted = 0;
    for (const c of fresh) {
      if (!inserted.has(c.campaignId)) continue; // only alert rows we actually inserted
      await slack.chat.postMessage({ channel, text: formatAlert(c, detectedAt) });
      alerted++;
    }

    logger.info("posted launch alerts", { alerted });
    return { active: active.length, alerted, seeded: false };
  },
});
