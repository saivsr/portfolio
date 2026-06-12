/**
 * Low Performing Campaign Automation
 * Built at Astris Partners — auto-pauses underperforming email campaigns
 * 
 * SANITIZATION NOTE: API keys, webhook URLs, workspace IDs, and client names
 * have been replaced with placeholders. Logic and scale are preserved.
 */

import { logger, schedules } from "@trigger.dev/sdk/v3";

const CONFIG = {
  plusVibeApiKey: process.env.PLUSVIBE_API_KEY!, // Sanitized: real key removed
  slackWebhookUrl: process.env.SLACK_WEBHOOK_URL!, // Sanitized: real webhook removed
  maxLeadsWithNoPositive: 2000,
  maxReplyRatePercent: 0.5,
  minLeadsForRateCheck: 500,
  maxRescheduledPercent: 20,
};

// Sanitized: Real client workspace IDs replaced with dummy UUIDs
// Client names replaced with fictional equivalents
const WORKSPACES = [
  { name: "Astris", id: "00000000-0000-4000-8000-000000000001" },
  { name: "Seraph", id: "00000000-0000-4000-8000-000000000002" },
  { name: "Gideon", id: "00000000-0000-4000-8000-000000000003" },
  { name: "Revolve Tech", id: "00000000-0000-4000-8000-000000000004" },
  { name: "CareLink", id: "00000000-0000-4000-8000-000000000005" },
  { name: "Inferex", id: "00000000-0000-4000-8000-000000000006" },
  { name: "Mindwell", id: "00000000-0000-4000-8000-000000000007" },
  { name: "GiveWell", id: "00000000-0000-4000-8000-000000000008" },
  { name: "Wisepath", id: "00000000-0000-4000-8000-000000000009" },
  { name: "Cavora", id: "00000000-0000-4000-8000-000000000010" },
  { name: "GuardianAI", id: "00000000-0000-4000-8000-000000000011" },
  { name: "Bright Horizon Capital", id: "00000000-0000-4000-8000-000000000012" },
];

const API_BASE = "https://api.plusvibe.ai/api/v1";

function formatDate(date: Date): string {
  return date.toISOString().split("T")[0];
}

async function getLeadStatusCounts(
  workspaceId: string,
  campaignId: string
): Promise<{ rescheduled: number; total: number } | null> {
  try {
    const res = await fetch(
      `${API_BASE}/lead/count/lead-status?workspace_id=${workspaceId}&campaign_id=${campaignId}`,
      { headers: { "x-api-key": CONFIG.plusVibeApiKey } }
    );
    if (!res.ok) return null;
    const data = await res.json();
    let rescheduled = 0,
      total = 0;
    for (const item of data) {
      const count = Number(item.count) || 0;
      total += count;
      if (String(item.status).toUpperCase() === "RESCHEDULED") rescheduled = count;
    }
    return { rescheduled, total };
  } catch {
    return null;
  }
}

async function findOldestCampaignDate(workspaceId: string, today: Date): Promise<string> {
  try {
    const todayStr = formatDate(today);
    const res = await fetch(
      `${API_BASE}/analytics/campaign/stats?workspace_id=${workspaceId}&start_date=${todayStr}&end_date=${todayStr}`,
      { headers: { "x-api-key": CONFIG.plusVibeApiKey } }
    );
    if (!res.ok) return formatDate(new Date(today.getTime() - 1095 * 24 * 60 * 60 * 1000));
    const data = await res.json();
    const campaigns = Array.isArray(data) ? data : Object.values(data);
    let oldest = today;
    for (const c of campaigns as any[]) {
      if (c.created_at) {
        const d = new Date(c.created_at);
        if (!isNaN(d.getTime()) && d < oldest) oldest = d;
      }
    }
    return formatDate(oldest);
  } catch {
    return formatDate(new Date(today.getTime() - 1095 * 24 * 60 * 60 * 1000));
  }
}

async function fetchCampaignStats(
  workspaceId: string,
  startDate: string,
  endDate: string
): Promise<any[] | null> {
  try {
    const res = await fetch(
      `${API_BASE}/analytics/campaign/stats?workspace_id=${workspaceId}&start_date=${startDate}&end_date=${endDate}`,
      { headers: { "x-api-key": CONFIG.plusVibeApiKey } }
    );
    if (!res.ok) return null;
    const data = await res.json();
    return Array.isArray(data) ? data : Object.values(data);
  } catch {
    return null;
  }
}

async function pauseCampaign(workspaceId: string, campaignId: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/campaign/pause`, {
      method: "POST",
      headers: { "x-api-key": CONFIG.plusVibeApiKey, "Content-Type": "application/json" },
      body: JSON.stringify({ workspace_id: workspaceId, campaign_id: campaignId }),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function sendSlackNotification(
  campaignName: string,
  workspaceName: string,
  reason: string,
  stats: { leadsContacted: number; emailsSent: number; positiveReplies: number; replyRate: number }
): Promise<void> {
  const message = {
    blocks: [
      {
        type: "header",
        text: { type: "plain_text", text: "🛑 Campaign Auto-Paused", emoji: true },
      },
      {
        type: "section",
        fields: [
          { type: "mrkdwn", text: `*Campaign:*\n${campaignName}` },
          { type: "mrkdwn", text: `*Workspace:*\n${workspaceName}` },
        ],
      },
      {
        type: "section",
        text: { type: "mrkdwn", text: `*Reason:*\n${reason}` },
      },
      {
        type: "section",
        fields: [
          { type: "mrkdwn", text: `*Leads Contacted:*\n${stats.leadsContacted.toLocaleString()}` },
          { type: "mrkdwn", text: `*Emails Sent:*\n${stats.emailsSent.toLocaleString()}` },
        ],
      },
      {
        type: "section",
        fields: [
          { type: "mrkdwn", text: `*Positive Replies:*\n${stats.positiveReplies}` },
          { type: "mrkdwn", text: `*Reply Rate:*\n${stats.replyRate.toFixed(2)}%` },
        ],
      },
      { type: "divider" },
    ],
  };
  try {
    await fetch(CONFIG.slackWebhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message),
    });
  } catch (error) {
    logger.error("Slack notification failed", { error });
  }
}

async function processWorkspace(
  workspace: { name: string; id: string },
  today: Date
): Promise<{ checked: number; paused: number; pausedList: string[] }> {
  const { name: workspaceName, id: workspaceId } = workspace;
  logger.info(`📂 Processing: ${workspaceName}`);

  const endDate = formatDate(today);
  const startDate = await findOldestCampaignDate(workspaceId, today);

  const campaigns = await fetchCampaignStats(workspaceId, startDate, endDate);
  if (!campaigns) {
    logger.error(`Failed to fetch stats for ${workspaceName}`);
    return { checked: 0, paused: 0, pausedList: [] };
  }

  let checked = 0,
    paused = 0;
  const pausedList: string[] = [];

  for (const campaign of campaigns) {
    const campaignId = campaign._id;
    const campaignName = String(campaign.camp_name || "Unknown").trim();
    const status = String(campaign.status || "").toUpperCase();

    if (!campaignId || status !== "ACTIVE") continue;

    const leadsContacted = Number(campaign.lead_contacted_count) || 0;
    const emailsSent = Number(campaign.sent_count) || 0;
    const positiveReplies = Number(campaign.positive_reply_count) || 0;
    const replies = Number(campaign.replied_count) || 0;
    const leadCount = Number(campaign.lead_count) || 0;
    const completedCount = Number(campaign.completed_lead_count) || 0;

    // Skip completed campaigns
    if (leadCount > 0 && (completedCount >= leadCount || leadsContacted >= leadCount)) continue;

    // OOO check - skip campaigns with high out-of-office rate
    const leadStatus = await getLeadStatusCounts(workspaceId, campaignId);
    if (leadStatus && leadStatus.total > 0) {
      const oooPercent = (leadStatus.rescheduled / leadStatus.total) * 100;
      if (oooPercent > CONFIG.maxRescheduledPercent) {
        logger.info(`SKIP [${oooPercent.toFixed(1)}% OOO]: ${campaignName}`);
        continue;
      }
    }

    checked++;
    const replyRate = leadsContacted > 0 ? (replies / leadsContacted) * 100 : 0;

    // If campaign has 1+ positive replies, it's safe - don't pause
    if (positiveReplies >= 1) {
      logger.info(`✅ OK: ${campaignName} (${positiveReplies} positive replies, ${replyRate.toFixed(2)}%)`);
      continue;
    }

    // PAUSE 1: 2000+ leads contacted AND 0 positive replies
    if (leadsContacted >= CONFIG.maxLeadsWithNoPositive) {
      logger.warn(`🛑 PAUSE [No positive replies after 2000+]: ${campaignName}`);
      if (await pauseCampaign(workspaceId, campaignId)) {
        paused++;
        pausedList.push(campaignName);
        await sendSlackNotification(campaignName, workspaceName, `No positive replies after ${leadsContacted.toLocaleString()} leads contacted`, {
          leadsContacted,
          emailsSent,
          positiveReplies,
          replyRate,
        });
        continue;
      }
    }

    // PAUSE 2: 500+ leads contacted AND <0.5% reply rate AND 0 positive replies
    if (leadsContacted >= CONFIG.minLeadsForRateCheck && replyRate < CONFIG.maxReplyRatePercent) {
      logger.warn(`🛑 PAUSE [Low reply rate, no positives]: ${campaignName} (${replyRate.toFixed(2)}%)`);
      if (await pauseCampaign(workspaceId, campaignId)) {
        paused++;
        pausedList.push(campaignName);
        await sendSlackNotification(campaignName, workspaceName, `Reply rate ${replyRate.toFixed(2)}% with no positive replies (threshold: ${CONFIG.maxReplyRatePercent}%)`, {
          leadsContacted,
          emailsSent,
          positiveReplies,
          replyRate,
        });
        continue;
      }
    }

    logger.info(`✅ OK: ${campaignName} (${replyRate.toFixed(2)}%)`);
  }

  return { checked, paused, pausedList };
}

export const monitorLowPerformingCampaigns = schedules.task({
  id: "monitor-low-performing-campaigns",
  cron: "0 9 * * 1-5", // 9am UTC, Monday-Friday
  maxDuration: 900,
  run: async () => {
    logger.info("🔴 Starting campaign monitor...");
    const today = new Date();

    let totalChecked = 0,
      totalPaused = 0;
    const allPaused: { workspace: string; campaign: string }[] = [];

    for (const workspace of WORKSPACES) {
      const result = await processWorkspace(workspace, today);
      totalChecked += result.checked;
      totalPaused += result.paused;
      for (const c of result.pausedList) {
        allPaused.push({ workspace: workspace.name, campaign: c });
      }
    }

    logger.info(`✅ COMPLETE`, {
      workspaces: WORKSPACES.length,
      checked: totalChecked,
      paused: totalPaused,
      campaigns: allPaused,
    });
    return {
      workspaces: WORKSPACES.length,
      checked: totalChecked,
      paused: totalPaused,
      campaigns: allPaused,
    };
  },
});
