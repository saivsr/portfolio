// lib/supabase/queries.ts
// Server-only data-access layer for the dashboard. One helper per materialized
// view, each returning { data, error, viewMissing } so pages degrade gracefully.
//
// Sanitization note: the client is the fictional "Summit Executive Partners
// (SEP)"; the SEP-prefixed Missive label strings are the real label scheme with
// the fictional acronym; dollar figures in comments are rounded / illustrative.
// All query logic, the pagination guard, and the byRange reconciliation are the
// real production code, unchanged.
import "server-only";
import { cache } from "react";
import { createClient } from "./server";
import type {
  HeadlineRow,
  PerRepMpcRow,
  PerRepNonMpcRow,
  IndustryRow,
  JobFunctionRow,
  PositiveReplyRow,
  DealRow,
  MpcCandidateRow,
  MpcSummaryRow,
} from "@/types/dashboard";

/**
 * All query helpers wrap Supabase calls in try/catch and return:
 *   { data: T[], error: string | null, viewMissing: boolean }
 *
 * `viewMissing` is true when the underlying materialized view doesn't exist
 * yet (Postgres error code 42P01). Pages key off that to show a "setting up"
 * state rather than crashing — the parallel ETL agent is still building the
 * views.
 */
export type QueryResult<T> = {
  data: T[];
  error: string | null;
  viewMissing: boolean;
};

// ── Trusted Missive labels (the SINGLE source of truth for interest) ──
// These mirror the matview SQL byte-for-byte. Per the governing principle,
// we trust the SEP-applied Missive labels directly (no LLM body-verify):
// a thread is "positive" iff it carries Interested OR Call Booked; a "call"
// iff it carries Call Booked. Keep these here so the byRange JS paths can
// never silently drift from the materialized-view definitions. (Redirect
// is resolved upstream into Interested before it ever reaches a snapshot.)
const LABEL_INTERESTED = "SEP - Interested";
const LABEL_CALL_BOOKED = "SEP - Call Booked";

function labelsArePositive(labels: string[] | null | undefined): boolean {
  const l = labels ?? [];
  return l.includes(LABEL_INTERESTED) || l.includes(LABEL_CALL_BOOKED);
}
function labelsAreCall(labels: string[] | null | undefined): boolean {
  return (labels ?? []).includes(LABEL_CALL_BOOKED);
}

const VIEW_MISSING_CODES = new Set(["42P01", "PGRST205", "PGRST204"]);

function isViewMissingError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as { code?: string; message?: string };
  if (e.code && VIEW_MISSING_CODES.has(e.code)) return true;
  const msg = (e.message ?? "").toLowerCase();
  return (
    msg.includes("does not exist") ||
    msg.includes("not found") ||
    msg.includes("could not find the table")
  );
}

async function runQuery<T>(
  fn: (
    sb: Awaited<ReturnType<typeof createClient>>,
  ) => PromiseLike<{ data: T[] | null; error: unknown }>,
  opts: { admin?: boolean } = {},
): Promise<QueryResult<T>> {
  try {
    // Default to admin (service-role) for server-component reads — RLS isn't
    // configured for the snapshot/dim tables and there's no per-user auth in
    // v1 (Vercel password protection gates the whole site).
    const sb = await createClient({ admin: opts.admin !== false });
    const { data, error } = await fn(sb);
    if (error) {
      if (isViewMissingError(error)) {
        return { data: [], error: null, viewMissing: true };
      }
      const msg =
        typeof error === "object" && error && "message" in error
          ? String((error as { message: unknown }).message)
          : String(error);
      return { data: [], error: msg, viewMissing: false };
    }
    return { data: data ?? [], error: null, viewMissing: false };
  } catch (e) {
    if (isViewMissingError(e)) {
      return { data: [], error: null, viewMissing: true };
    }
    return {
      data: [],
      error: e instanceof Error ? e.message : String(e),
      viewMissing: false,
    };
  }
}

// PostgREST caps a single response (Supabase default ≈1000 rows). Windowed
// aggregations read EVERY matching row and count in JS, so a capped response
// silently undercounts (e.g. a 3,627-thread window returning only 1,000 rows
// halves every per-rep/industry total). Page through the full result set with
// .range() so counts always reconcile to the matviews.
type Rangeable = {
  range: (
    from: number,
    to: number,
  ) => PromiseLike<{ data: unknown[] | null; error: unknown }>;
};
async function fetchAllRows<T>(
  makeQuery: () => Rangeable,
): Promise<{ data: T[]; error: unknown }> {
  const PAGE = 1000;
  const all: T[] = [];
  let offset = 0;
  // Guard caps at 100 pages (100k rows) so a logic error can't loop forever.
  for (let guard = 0; guard < 100; guard++) {
    const { data, error } = await makeQuery().range(offset, offset + PAGE - 1);
    if (error) return { data: all, error };
    const rows = (data ?? []) as T[];
    all.push(...rows);
    if (rows.length < PAGE) break;
    offset += PAGE;
  }
  return { data: all, error: null };
}

// Paginated sibling of runQuery: pages through the full result set with
// .range() (PostgREST caps a single response at ~1000 rows) while keeping the
// same QueryResult shape + viewMissing handling. Use for list/table reads that
// can exceed 1000 rows so they never silently truncate. `fn` must return a
// builder WITHOUT .limit()/.range() — this applies .range() per page.
async function runQueryAll<T>(
  fn: (sb: Awaited<ReturnType<typeof createClient>>) => Rangeable,
  opts: { admin?: boolean } = {},
): Promise<QueryResult<T>> {
  try {
    const sb = await createClient({ admin: opts.admin !== false });
    const PAGE = 1000;
    const all: T[] = [];
    let offset = 0;
    for (let guard = 0; guard < 100; guard++) {
      const { data, error } = await fn(sb).range(offset, offset + PAGE - 1);
      if (error) {
        if (isViewMissingError(error)) {
          return { data: [], error: null, viewMissing: true };
        }
        const msg =
          typeof error === "object" && error && "message" in error
            ? String((error as { message: unknown }).message)
            : String(error);
        return { data: [], error: msg, viewMissing: false };
      }
      const rows = (data ?? []) as T[];
      all.push(...rows);
      if (rows.length < PAGE) break;
      offset += PAGE;
    }
    return { data: all, error: null, viewMissing: false };
  } catch (e) {
    if (isViewMissingError(e)) {
      return { data: [], error: null, viewMissing: true };
    }
    return {
      data: [],
      error: e instanceof Error ? e.message : String(e),
      viewMissing: false,
    };
  }
}

// ─────────────────────────────────────────────────────────────────
// Headline metrics
// ─────────────────────────────────────────────────────────────────
// `mv_headline` (Postgres) ships columns total_sent / total_replied /
// total_positives / total_calls_booked / closed_won_count /
// closed_won_revenue. The dashboard pages were written against an
// aspirational shape (sent/replied/positives/meetings/closed_won/
// closed_won_amount) — alias at query time so pages keep working without
// any frontend rewrites.
const HEADLINE_SELECT =
  "sent:total_sent, replied:total_replied, positives:total_positives, " +
  "meetings:total_calls_booked, closed_won:closed_won_count, " +
  "closed_won_amount:closed_won_revenue, as_of:refreshed_at";

export async function getHeadline(): Promise<QueryResult<HeadlineRow>> {
  return runQuery<HeadlineRow>((sb) =>
    sb.from("mv_headline").select(HEADLINE_SELECT).limit(1000),
  );
}

/**
 * Date-range-aware headline metrics.
 *
 * Returns ONE row matching the HeadlineRow shape. Sums sent/replied from PV
 * campaigns CREATED in [from, to], counts positives/calls from the
 * mv_thread_rep_attribution view (same source + DISTINCT-lead_email
 * aggregation as the unfiltered mv_headline) for threads with last_activity
 * in [from, to], and counts closed-won deals with closedate in [from, to].
 * We can't slice cumulative campaign counters more precisely because PV
 * reports lifetime totals per campaign — created_at is the best proxy we have.
 *
 * NOTE on reconciliation: the unfiltered mv_headline includes ~8 baseline
 * "phantom" rows that have a null last_activity; any date-window filter
 * necessarily excludes them, so a wide range can land a hair under the all-time
 * matview total. That gap is the phantom rows only — the label/dedup logic is
 * now identical.
 *
 * `from` and `to` are YYYY-MM-DD strings (the global DateRangePicker emits this
 * shape via the ?from=&to= URL params).
 */
export async function getHeadlineByRange(opts: {
  from: string;
  to: string;
}): Promise<QueryResult<HeadlineRow>> {
  // Raw snapshot tables (pv_campaign_snapshot, missive_thread_snapshot,
  // deal_dim) live behind RLS in Supabase and aren't readable to the anon
  // role. Use the service key here — this runs only in Server Components,
  // never reaches the browser.
  return runQuery<HeadlineRow>(async (sb) => {
    // Boundary timestamps — `to` is end-of-day inclusive.
    const fromTs = `${opts.from}T00:00:00.000Z`;
    const toTs = `${opts.to}T23:59:59.999Z`;

    // Fetch latest pv snapshot_date so we don't double-count campaign rows
    // across historical snapshots. (Missive positives now come from
    // mv_thread_rep_attribution, which pins its own latest snapshot
    // internally — no separate snapshot lookup needed there.)
    const pvSnap = await sb
      .from("pv_campaign_snapshot")
      .select("snapshot_date")
      .order("snapshot_date", { ascending: false })
      .limit(1);
    type SnapRow = { snapshot_date: string };
    const pvLatest = (pvSnap.data as unknown as SnapRow[] | null)?.[0]
      ?.snapshot_date;

    // 1) PV campaigns CREATED in window — sum sent/replied from latest snap.
    const pvQ = pvLatest
      ? sb
          .from("pv_campaign_snapshot")
          .select("sent_count, replied_count")
          .eq("snapshot_date", pvLatest)
          .gte("created_at", fromTs)
          .lte("created_at", toTs)
          .limit(5000)
      : Promise.resolve({ data: [], error: null });
    // 2) Positives / calls in window — read from mv_thread_rep_attribution,
    //    the SAME source mv_headline uses (its `mi_latest` CTE). The matview
    //    counts DISTINCT lead_email (not per-thread conv_id), so we must do
    //    the same here or the filtered headline diverges from the unfiltered
    //    matview total. Reading the raw missive_thread_snapshot and counting
    //    one-per-thread silently over-counts (multiple threads per lead) AND
    //    misses the baseline phantom rows the attribution view folds in.
    const miQ = fetchAllRows<MIRow>(() =>
      sb
        .from("mv_thread_rep_attribution")
        .select("lead_email, is_positive, is_call_booked")
        .gte("last_activity", fromTs)
        .lte("last_activity", toTs),
    );
    // 3) Deals closed in window — count + sum.
    const dealQ = sb
      .from("deal_dim")
      .select("deal_id, amount")
      .eq("is_closed_won", true)
      .eq("is_outbound_attributed", true)
      .gte("closedate", fromTs)
      .lte("closedate", toTs)
      .limit(5000);

    const [pvRes, miRes, dealRes] = await Promise.all([pvQ, miQ, dealQ]);
    const err = pvRes.error || miRes.error || dealRes.error;
    if (err) return { data: null, error: err };

    type PVRow = { sent_count: number | null; replied_count: number | null };
    const pvRows = (pvRes.data as unknown as PVRow[]) || [];
    let total_sent = 0;
    let total_replied = 0;
    for (const r of pvRows) {
      total_sent += Number(r.sent_count ?? 0);
      total_replied += Number(r.replied_count ?? 0);
    }

    // Mirror mv_headline.mi_latest: count(distinct lead_email) filter (...).
    // is_positive / is_call_booked are pre-computed in the attribution view
    // from the trusted Missive labels (Interested / Call Booked), so we don't
    // re-derive them from label_names here — keeps label logic in one place.
    type MIRow = {
      lead_email: string | null;
      is_positive: boolean | null;
      is_call_booked: boolean | null;
    };
    const miRows = (miRes.data as unknown as MIRow[]) || [];
    const positiveEmails = new Set<string>();
    const callEmails = new Set<string>();
    for (const r of miRows) {
      const email = r.lead_email;
      if (!email) continue;
      if (r.is_positive) positiveEmails.add(email);
      if (r.is_call_booked) callEmails.add(email);
    }
    const total_positives = positiveEmails.size;
    const total_calls_booked = callEmails.size;

    type DealQRow = { deal_id: string; amount: number | null };
    const deals = (dealRes.data as unknown as DealQRow[]) || [];
    const closed_won_count = deals.length;
    const closed_won_revenue = deals.reduce(
      (acc, d) => acc + Number(d.amount ?? 0),
      0,
    );

    const row: HeadlineRow = {
      sent: total_sent,
      replied: total_replied,
      positives: total_positives,
      meetings: total_calls_booked,
      closed_won: closed_won_count,
      closed_won_amount: closed_won_revenue,
      as_of: new Date().toISOString(),
      window_start: opts.from,
      window_end: opts.to,
    };
    return { data: [row], error: null };
  }, { admin: true });
}

// ─────────────────────────────────────────────────────────────────
// Per-rep tables
// ─────────────────────────────────────────────────────────────────
// `mv_per_rep_mpc` columns: rep, sent, replied, interested, calls, deals,
// revenue. The page type expects rep_key/display_name/positives/calls_booked/
// closed_won/won_amount/meetings_taken. Alias on the way out.
export async function getPerRepMpc(): Promise<QueryResult<PerRepMpcRow>> {
  return runQuery<PerRepMpcRow>((sb) =>
    sb
      .from("mv_per_rep_mpc")
      .select(
        "rep_key:rep, display_name:rep, sent, replied, positives:interested, " +
          "calls_booked:calls, mpc_meetings, total_meetings, " +
          "closed_won:deals, won_amount:revenue",
      )
      .neq("rep", "(unassigned)")
      .order("interested", { ascending: false }),
  );
}

/**
 * Per-rep MPC stats over a date range.
 *
 * Reads from mv_thread_rep_attribution (the pre-attributed per-thread view)
 * for positives + calls so the same full rep waterfall used everywhere else
 * applies on the date-filtered path too. Aggregation mirrors mv_per_rep_mpc
 * EXACTLY: count(distinct conv_id) for interested / mpc_meetings (scoped to
 * campaign_is_mpc) and total_meetings (all scope) — NOT distinct lead_email,
 * which would diverge from the matview. Plus MPC sent/replied from PV
 * campaign snapshots (filtered by created_at) and deals from deal_dim
 * (filtered by closedate).
 */
export async function getPerRepMpcByRange(opts: {
  from: string;
  to: string;
}): Promise<QueryResult<PerRepMpcRow>> {
  return runQuery<PerRepMpcRow>(async (sb) => {
    const fromTs = `${opts.from}T00:00:00.000Z`;
    const toTs = `${opts.to}T23:59:59.999Z`;

    const camps = await sb
      .from("campaign_dim")
      .select("campaign_id, assigned_rep, is_mpc")
      .limit(2000);
    type CampDim = {
      campaign_id: string;
      assigned_rep: string | null;
      is_mpc: boolean | null;
    };
    const mpcCampaignIds = new Set<string>();
    const repByCampaign = new Map<string, string>();
    for (const r of (camps.data as unknown as CampDim[]) || []) {
      if (r.is_mpc) mpcCampaignIds.add(r.campaign_id);
      if (r.assigned_rep) repByCampaign.set(r.campaign_id, r.assigned_rep);
    }

    // 1) MPC sent/replied per rep from PV campaign snapshots in window.
    const pvSnap = await sb
      .from("pv_campaign_snapshot")
      .select("snapshot_date")
      .order("snapshot_date", { ascending: false })
      .limit(1);
    const pvLatest = (pvSnap.data as unknown as { snapshot_date: string }[] | null)?.[0]?.snapshot_date;
    const sentByRep = new Map<string, number>();
    const repliedByRep = new Map<string, number>();
    if (pvLatest) {
      const pvCamps = await sb
        .from("pv_campaign_snapshot")
        .select("campaign_id, sent_count, replied_count")
        .eq("snapshot_date", pvLatest)
        .gte("created_at", fromTs)
        .lte("created_at", toTs)
        .limit(5000);
      type PVRow = { campaign_id: string; sent_count: number | null; replied_count: number | null };
      for (const r of (pvCamps.data as unknown as PVRow[]) || []) {
        if (!mpcCampaignIds.has(r.campaign_id)) continue;
        const rep = repByCampaign.get(r.campaign_id) ?? "(unassigned)";
        sentByRep.set(rep, (sentByRep.get(rep) ?? 0) + Number(r.sent_count ?? 0));
        repliedByRep.set(rep, (repliedByRep.get(rep) ?? 0) + Number(r.replied_count ?? 0));
      }
    }

    // 2) Positives + calls per rep from mv_thread_rep_attribution.
    //    MPC SCOPE only: must filter on campaign_is_mpc=true to mirror the
    //    matview path. Without this filter the byRange path silently
    //    includes non-MPC threads, ~4x over-counting MPC positives.
    //    Also fetch ALL Call Booked threads (not just MPC) so we can populate
    //    total_meetings alongside mpc_meetings.
    const threads = await fetchAllRows<ThreadAttr>(() =>
      sb
        .from("mv_thread_rep_attribution")
        .select("conv_id, rep_key, is_positive, is_call_booked, campaign_is_mpc, last_activity")
        .gte("last_activity", fromTs)
        .lte("last_activity", toTs),
    );
    type ThreadAttr = {
      conv_id: string | null;
      rep_key: string | null;
      is_positive: boolean | null;
      is_call_booked: boolean | null;
      campaign_is_mpc: boolean | null;
    };
    // Mirror mv_per_rep_mpc EXACTLY, which de-duplicates on conv_id:
    //   interested    = count(distinct conv_id) filter (is_positive)    where campaign_is_mpc
    //   mpc_meetings  = count(distinct conv_id) filter (is_call_booked) where campaign_is_mpc
    //   total_meetings= count(distinct conv_id) filter (is_call_booked) [all scope] (mv_meetings_by_rep)
    // Using distinct lead_email here would diverge — the matview keys on
    // conv_id, and a single lead can hold multiple threads.
    const mpcPosConvs = new Map<string, Set<string>>();        // rep → distinct conv_ids
    const mpcMeetingConvs = new Map<string, Set<string>>();    // rep → distinct conv_ids, call_booked AND mpc
    const totalMeetingConvs = new Map<string, Set<string>>();  // rep → distinct conv_ids, call_booked (all scope)
    for (const t of (threads.data as unknown as ThreadAttr[]) || []) {
      const rep = t.rep_key;
      const conv = t.conv_id;
      if (!rep || !conv) continue;
      const isMpc = t.campaign_is_mpc === true;
      if (t.is_positive && isMpc) {
        if (!mpcPosConvs.has(rep)) mpcPosConvs.set(rep, new Set());
        mpcPosConvs.get(rep)!.add(conv);
      }
      if (t.is_call_booked) {
        if (!totalMeetingConvs.has(rep)) totalMeetingConvs.set(rep, new Set());
        totalMeetingConvs.get(rep)!.add(conv);
        if (isMpc) {
          if (!mpcMeetingConvs.has(rep)) mpcMeetingConvs.set(rep, new Set());
          mpcMeetingConvs.get(rep)!.add(conv);
        }
      }
    }
    const posByRep = new Map<string, number>();
    const mpcMeetingsByRep = new Map<string, number>();
    const totalMeetingsByRep = new Map<string, number>();
    for (const [rep, set] of mpcPosConvs) posByRep.set(rep, set.size);
    for (const [rep, set] of mpcMeetingConvs) mpcMeetingsByRep.set(rep, set.size);
    for (const [rep, set] of totalMeetingConvs) totalMeetingsByRep.set(rep, set.size);

    // 3) Deals in window — outbound-attributed, MPC-scope only.
    //    The matview's deals/revenue columns are MPC-scoped, so we must
    //    filter to MPC deals here too or the byRange path inflates the MPC
    //    rep table with non-MPC closed-won. deal_dim now carries an
    //    authoritative `campaign_is_mpc` flag (Missive-first coalesce) — use
    //    it directly rather than re-deriving scope from a campaign_dim join.
    const deals = await sb
      .from("deal_dim")
      .select("attributed_rep, amount, campaign_is_mpc")
      .eq("is_outbound_attributed", true)
      .eq("is_closed_won", true)
      .eq("campaign_is_mpc", true)
      .gte("closedate", fromTs)
      .lte("closedate", toTs)
      .limit(5000);
    type DealQRow = {
      attributed_rep: string | null;
      amount: number | null;
      campaign_is_mpc: boolean | null;
    };
    const dealsByRep = new Map<string, number>();
    const revenueByRep = new Map<string, number>();
    for (const d of (deals.data as unknown as DealQRow[]) || []) {
      const rep = d.attributed_rep;
      if (!rep) continue;
      dealsByRep.set(rep, (dealsByRep.get(rep) ?? 0) + 1);
      revenueByRep.set(rep, (revenueByRep.get(rep) ?? 0) + Number(d.amount ?? 0));
    }

    const allReps = new Set<string>([
      ...sentByRep.keys(),
      ...posByRep.keys(),
      ...mpcMeetingsByRep.keys(),
      ...totalMeetingsByRep.keys(),
      ...dealsByRep.keys(),
    ]);
    const rows: PerRepMpcRow[] = [];
    for (const rep of allReps) {
      if (rep === "(unassigned)") continue;
      rows.push({
        rep_key: rep,
        display_name: rep,
        sent: sentByRep.get(rep) ?? 0,
        replied: repliedByRep.get(rep) ?? 0,
        positives: posByRep.get(rep) ?? 0,
        calls_booked: mpcMeetingsByRep.get(rep) ?? 0,
        mpc_meetings: mpcMeetingsByRep.get(rep) ?? 0,
        total_meetings: totalMeetingsByRep.get(rep) ?? 0,
        meetings_taken: mpcMeetingsByRep.get(rep) ?? 0,
        closed_won: dealsByRep.get(rep) ?? 0,
        won_amount: revenueByRep.get(rep) ?? 0,
      } as PerRepMpcRow);
    }
    rows.sort((a, b) => (b.positives ?? 0) - (a.positives ?? 0));
    return { data: rows, error: null };
  }, { admin: true });
}

// `mv_per_rep_nonmpc` is now PER REP (was archetype). Columns:
//   rep, threads, positives, calls, closed_won, won_amount,
//   active_pipeline, lost, total, close_rate
// Rep attribution: Missive sep_authors → rep_dim.hubspot_email.
// Scope: non-MPC OR unknown-campaign threads + non-MPC deals.
export async function getPerRepNonMpc(): Promise<
  QueryResult<PerRepNonMpcRow>
> {
  return runQuery<PerRepNonMpcRow>((sb) =>
    sb
      .from("mv_per_rep_nonmpc")
      .select(
        "rep_key:rep, display_name:rep, threads, positives, calls, " +
          "closed_won, won_amount, active_pipeline, close_rate",
      )
      .neq("rep", "(unassigned)")
      .order("positives", { ascending: false }),
  );
}

/**
 * Range-aware per-rep Non-MPC. Combines:
 *   - Threads from mv_thread_rep_attribution (non-MPC scope) for
 *     threads/positives/calls
 *   - Deals from deal_dim (outbound, non-MPC scope) for
 *     closed_won/won_amount/active_pipeline/close_rate
 * Same waterfall as the all-time view; matches the page's PerRepNonMpcRow.
 */
export async function getPerRepNonMpcByRange(opts: {
  from: string;
  to: string;
}): Promise<QueryResult<PerRepNonMpcRow>> {
  return runQuery<PerRepNonMpcRow>(async (sb) => {
    const fromTs = `${opts.from}T00:00:00.000Z`;
    const toTs = `${opts.to}T23:59:59.999Z`;

    // Threads: non-MPC scope (campaign_is_mpc IS NULL or false) within window
    const threads = await fetchAllRows<ThreadAttr>(() =>
      sb
        .from("mv_thread_rep_attribution")
        .select("rep_key, is_positive, is_call_booked, campaign_is_mpc, last_activity")
        .or("campaign_is_mpc.is.null,campaign_is_mpc.eq.false")
        .gte("last_activity", fromTs)
        .lte("last_activity", toTs),
    );
    type ThreadAttr = {
      rep_key: string | null;
      is_positive: boolean | null;
      is_call_booked: boolean | null;
    };
    const tagg = new Map<string, { threads: number; positives: number; calls: number }>();
    for (const t of (threads.data as unknown as ThreadAttr[]) || []) {
      const rep = t.rep_key;
      if (!rep) continue;
      const cur = tagg.get(rep) ?? { threads: 0, positives: 0, calls: 0 };
      cur.threads += 1;
      if (t.is_positive) cur.positives += 1;
      if (t.is_call_booked) cur.calls += 1;
      tagg.set(rep, cur);
    }

    // Deals: non-MPC scope within window. deal_dim now carries an
    // authoritative `campaign_is_mpc` flag (Missive-first coalesce), so we
    // scope on it directly — a deal is non-MPC when the flag is false OR null
    // (unknown campaign). This matches how the matview partitions MPC vs
    // non-MPC and avoids drift from the old campaign_dim join.
    const deals = await sb
      .from("deal_dim")
      .select(
        "deal_id, attributed_rep, campaign_is_mpc, amount, is_closed_won, stage_label, closedate, createdate",
      )
      .eq("is_outbound_attributed", true)
      .or("campaign_is_mpc.is.null,campaign_is_mpc.eq.false")
      .or(
        `and(closedate.gte.${fromTs},closedate.lte.${toTs}),` +
          `and(createdate.gte.${fromTs},createdate.lte.${toTs})`,
      )
      .limit(5000);
    type DealRow = {
      attributed_rep: string | null;
      campaign_is_mpc: boolean | null;
      amount: number | null;
      is_closed_won: boolean | null;
      stage_label: string | null;
    };
    const dagg = new Map<
      string,
      { closed_won: number; won_amount: number; active_pipeline: number; total: number }
    >();
    for (const d of (deals.data as unknown as DealRow[]) || []) {
      const rep = d.attributed_rep;
      if (!rep) continue;
      const cur =
        dagg.get(rep) ?? { closed_won: 0, won_amount: 0, active_pipeline: 0, total: 0 };
      cur.total += 1;
      if (d.is_closed_won) {
        cur.closed_won += 1;
        cur.won_amount += Number(d.amount ?? 0);
      } else if (!(d.stage_label ?? "").toLowerCase().includes("lost")) {
        cur.active_pipeline += 1;
      }
      dagg.set(rep, cur);
    }

    const allReps = new Set<string>([...tagg.keys(), ...dagg.keys()]);
    const rows: PerRepNonMpcRow[] = [];
    for (const rep of allReps) {
      const t = tagg.get(rep) ?? { threads: 0, positives: 0, calls: 0 };
      const d = dagg.get(rep) ?? { closed_won: 0, won_amount: 0, active_pipeline: 0, total: 0 };
      rows.push({
        rep_key: rep,
        display_name: rep,
        threads: t.threads,
        positives: t.positives,
        calls: t.calls,
        closed_won: d.closed_won,
        won_amount: d.won_amount,
        active_pipeline: d.active_pipeline,
        close_rate: d.total > 0 ? d.closed_won / d.total : 0,
      } as unknown as PerRepNonMpcRow);
    }
    rows.sort((a, b) => (b.positives ?? 0) - (a.positives ?? 0));
    return { data: rows, error: null };
  }, { admin: true });
}

// ─────────────────────────────────────────────────────────────────
// Breakdowns
// ─────────────────────────────────────────────────────────────────
// `mv_industry_breakdown` ships: industry, threads, positives, calls.
// IndustryRow expects industry, sent, replied, positives, calls_booked, etc.
export async function getIndustryBreakdown(): Promise<QueryResult<IndustryRow>> {
  return runQuery<IndustryRow>((sb) =>
    sb
      .from("mv_industry_breakdown")
      .select(
        "industry, threads, positives, calls_booked:calls",
      )
      .order("positives", { ascending: false }),
  );
}

// `mv_job_function_breakdown` ships: job_function, threads, positives, calls.
export async function getJobFunctionBreakdown(): Promise<
  QueryResult<JobFunctionRow>
> {
  return runQuery<JobFunctionRow>((sb) =>
    sb
      .from("mv_job_function_breakdown")
      .select(
        "job_function, threads, positives, calls_booked:calls",
      )
      .order("positives", { ascending: false }),
  );
}

/**
 * Shared helper — aggregate Missive threads in a date window joined to
 * contact_dim, grouped by either industry or job_function. Used by both
 * getIndustryBreakdownByRange and getJobFunctionBreakdownByRange.
 */
async function breakdownByRange(
  sb: Awaited<ReturnType<typeof createClient>>,
  bucketField: "industry" | "job_function",
  fallback: string,
  from: string,
  to: string,
): Promise<{ bucket: string; threads: number; positives: number; calls: number }[]> {
  const fromTs = `${from}T00:00:00.000Z`;
  const toTs = `${to}T23:59:59.999Z`;
  const miSnap = await sb
    .from("missive_thread_snapshot")
    .select("snapshot_date")
    .order("snapshot_date", { ascending: false })
    .limit(1);
  const miLatest = (miSnap.data as unknown as { snapshot_date: string }[] | null)?.[0]?.snapshot_date;
  if (!miLatest) return [];
  type ThreadRow = { lead_email: string | null; label_names: string[] | null };
  type ContactRow = { email: string; industry: string | null; job_function: string | null };
  const [threadsRes, contactsRes] = await Promise.all([
    fetchAllRows<ThreadRow>(() =>
      sb
        .from("missive_thread_snapshot")
        .select("lead_email, label_names")
        .eq("snapshot_date", miLatest)
        .gte("last_activity", fromTs)
        .lte("last_activity", toTs),
    ),
    // contact_dim is ~7k rows — far over the response cap — so paging here is
    // essential: an un-paged fetch left most threads without an industry and
    // dumped them into the "Cross-functional" fallback bucket.
    fetchAllRows<ContactRow>(() =>
      sb.from("contact_dim").select("email, industry, job_function"),
    ),
  ]);
  const bucketByEmail = new Map<string, string>();
  for (const c of (contactsRes.data as unknown as ContactRow[]) || []) {
    bucketByEmail.set(c.email, (c[bucketField] || fallback) as string);
  }
  const agg = new Map<string, { threads: number; positives: number; calls: number }>();
  for (const t of (threadsRes.data as unknown as ThreadRow[]) || []) {
    const email = (t.lead_email ?? "").toLowerCase();
    if (!email) continue;
    const bucket = bucketByEmail.get(email) ?? fallback;
    const cur = agg.get(bucket) ?? { threads: 0, positives: 0, calls: 0 };
    cur.threads += 1;
    if (labelsArePositive(t.label_names)) cur.positives += 1;
    if (labelsAreCall(t.label_names)) cur.calls += 1;
    agg.set(bucket, cur);
  }
  return Array.from(agg.entries())
    .map(([bucket, v]) => ({ bucket, ...v }))
    .sort((a, b) => b.threads - a.threads);
}

export async function getIndustryBreakdownByRange(opts: {
  from: string;
  to: string;
}): Promise<QueryResult<IndustryRow>> {
  return runQuery<IndustryRow>(async (sb) => {
    const out = await breakdownByRange(
      sb,
      "industry",
      "Cross-functional Roles (industry-ambiguous)",
      opts.from,
      opts.to,
    );
    return {
      data: out.map(
        (r) =>
          ({
            industry: r.bucket,
            threads: r.threads,
            positives: r.positives,
            calls_booked: r.calls,
          } as unknown as IndustryRow),
      ),
      error: null,
    };
  }, { admin: true });
}

export async function getJobFunctionBreakdownByRange(opts: {
  from: string;
  to: string;
}): Promise<QueryResult<JobFunctionRow>> {
  return runQuery<JobFunctionRow>(async (sb) => {
    // Fallback bucket MUST match mv_job_function_breakdown's
    // coalesce(cd.job_function, 'Cross-functional') — using a different
    // label ("Other Specialist") both diverged from the matview AND leaked
    // internal taxonomy into client-facing copy.
    const out = await breakdownByRange(
      sb,
      "job_function",
      "Cross-functional",
      opts.from,
      opts.to,
    );
    return {
      data: out.map(
        (r) =>
          ({
            job_function: r.bucket,
            function: r.bucket,
            threads: r.threads,
            positives: r.positives,
            calls_booked: r.calls,
          } as unknown as JobFunctionRow),
      ),
      error: null,
    };
  }, { admin: true });
}

// ─────────────────────────────────────────────────────────────────
// Positive replies (full filterable list)
// ─────────────────────────────────────────────────────────────────
// Explicit client-safe column list for the Positive Replies table. Excludes the
// internal deal_* columns (deal_id / stage / amount / won flags) the view also
// carries — they are never rendered and must not ride along in the RSC payload.
const POSITIVE_REPLY_COLS =
  "conv_id, subject, last_activity, lead_email, label_names, company, " +
  "job_title, industry, job_function, is_call_booked, is_interested, " +
  "rep_key, rep, campaign_id, campaign_name, mpc_candidate, campaign_is_mpc";

export async function getPositiveRepliesByRange(opts: {
  from: string;
  to: string;
}): Promise<QueryResult<PositiveReplyRow>> {
  const fromTs = `${opts.from}T00:00:00.000Z`;
  const toTs = `${opts.to}T23:59:59.999Z`;
  return runQueryAll<PositiveReplyRow>((sb) =>
    sb
      .from("mv_positive_replies")
      .select(POSITIVE_REPLY_COLS)
      .gte("last_activity", fromTs)
      .lte("last_activity", toTs)
      .order("last_activity", { ascending: false })
      .order("conv_id", { ascending: true }),
  );
}

export async function getPositiveReplies(): Promise<
  QueryResult<PositiveReplyRow>
> {
  return runQueryAll<PositiveReplyRow>((sb) =>
    sb
      .from("mv_positive_replies")
      .select(POSITIVE_REPLY_COLS)
      .order("last_activity", { ascending: false })
      .order("conv_id", { ascending: true }),
  );
}

/**
 * Per-lead drill-down for one industry — backs the Industry page's "Focus"
 * filter so clicking a vertical lists the actual leads behind it (lead,
 * company, campaign, call-booked, partner). Optionally scoped to a window.
 */
export async function getPositiveRepliesByIndustry(opts: {
  industry: string;
  from?: string;
  to?: string;
}): Promise<QueryResult<PositiveReplyRow>> {
  return runQueryAll<PositiveReplyRow>((sb) => {
    let q = sb
      .from("mv_positive_replies")
      .select(POSITIVE_REPLY_COLS)
      .eq("industry", opts.industry);
    if (opts.from && opts.to) {
      q = q
        .gte("last_activity", `${opts.from}T00:00:00.000Z`)
        .lte("last_activity", `${opts.to}T23:59:59.999Z`);
    }
    return q
      .order("last_activity", { ascending: false })
      .order("conv_id", { ascending: true });
  });
}

// ─────────────────────────────────────────────────────────────────
// Deals (closed-won)
// ─────────────────────────────────────────────────────────────────
// EVERY deal helper filters is_outbound_attributed=true. This dashboard only
// surfaces deals that came from OUR outbound — never the client's referrals,
// retainer clients, or partner-sourced placements.
const DEAL_SELECT =
  "deal_id, dealname, amount, stage_label, is_closed_won, " +
  "is_outbound_attributed, deal_source, primary_contact_email, " +
  "attributed_campaign, attributed_rep, closedate, createdate";

export async function getClosedWonDeals(): Promise<QueryResult<DealRow>> {
  return runQueryAll<DealRow>((sb) =>
    sb
      .from("deal_dim")
      .select(DEAL_SELECT)
      .eq("is_outbound_attributed", true)
      .eq("is_closed_won", true)
      .order("closedate", { ascending: false }),
  );
}

/**
 * Closed-won outbound-attributed deals with closedate in [from, to].
 */
export async function getClosedWonDealsByRange(opts: {
  from: string;
  to: string;
}): Promise<QueryResult<DealRow>> {
  const fromTs = `${opts.from}T00:00:00.000Z`;
  const toTs = `${opts.to}T23:59:59.999Z`;
  return runQueryAll<DealRow>((sb) =>
    sb
      .from("deal_dim")
      .select(DEAL_SELECT)
      .eq("is_outbound_attributed", true)
      .eq("is_closed_won", true)
      .gte("closedate", fromTs)
      .lte("closedate", toTs)
      .order("closedate", { ascending: false }),
  );
}

/**
 * Every outbound-attributed deal (any stage) — drives the Pipeline / Lost
 * tabs on the Deals page. Filter is the same: only our deals.
 */
export async function getAllAttributedDeals(): Promise<QueryResult<DealRow>> {
  return runQueryAll<DealRow>((sb) =>
    sb
      .from("deal_dim")
      .select(DEAL_SELECT)
      .eq("is_outbound_attributed", true)
      .order("closedate", { ascending: false, nullsFirst: false }),
  );
}

/**
 * Range-aware variant: every outbound-attributed deal that "touched" the
 * window. A deal touches the window if either its closedate OR its createdate
 * falls inside [from, to] — closed deals are dated by close, open deals by
 * create. We can't AND both because open deals have null closedate.
 *
 * This is the query that backs the Pipeline / Lost tabs when the global date
 * filter is active. Without it, those tabs ignored the filter entirely.
 */
export async function getAllAttributedDealsByRange(opts: {
  from: string;
  to: string;
}): Promise<QueryResult<DealRow>> {
  const fromTs = `${opts.from}T00:00:00.000Z`;
  const toTs = `${opts.to}T23:59:59.999Z`;
  return runQueryAll<DealRow>((sb) =>
    sb
      .from("deal_dim")
      .select(DEAL_SELECT)
      .eq("is_outbound_attributed", true)
      .or(
        `and(closedate.gte.${fromTs},closedate.lte.${toTs}),` +
          `and(createdate.gte.${fromTs},createdate.lte.${toTs})`,
      )
      .order("closedate", { ascending: false, nullsFirst: false }),
  );
}

// ─────────────────────────────────────────────────────────────────
// Last refresh timestamp — max(pulled_at) across the four big snapshots
// ─────────────────────────────────────────────────────────────────
// Wrapped in React `cache()` so the top-nav, page headers, and footer that
// all need the freshness timestamp share a SINGLE per-request fetch instead
// of each firing the four snapshot queries independently.
export const getLastRefresh = cache(async function getLastRefresh(): Promise<{
  ts: string | null;
  bySource: Record<string, string | null>;
}> {
  const sources: Array<{ source: string; table: string }> = [
    { source: "pv_campaigns", table: "pv_campaign_snapshot" },
    { source: "missive_threads", table: "missive_thread_snapshot" },
    { source: "hubspot_deals", table: "hubspot_deal_snapshot" },
    { source: "hubspot_contacts", table: "hubspot_contact_snapshot" },
  ];

  // Snapshot tables live behind RLS and aren't readable to the anon role.
  // Use the service key (admin) — same as every other server-side read here.
  // Without this the query returns [] under RLS and the header falsely shows
  // "Last refresh —" with a red "No data" dot even when data is fresh.
  const sb = await createClient({ admin: true });
  const bySource: Record<string, string | null> = {};
  let maxTs: string | null = null;

  for (const { source, table } of sources) {
    try {
      const { data, error } = await sb
        .from(table)
        .select("pulled_at")
        .order("pulled_at", { ascending: false })
        .limit(1);
      if (error || !data || data.length === 0) {
        bySource[source] = null;
        continue;
      }
      const ts = (data[0] as { pulled_at: string | null }).pulled_at;
      bySource[source] = ts;
      if (ts && (!maxTs || ts > maxTs)) maxTs = ts;
    } catch {
      bySource[source] = null;
    }
  }

  return { ts: maxTs, bySource };
});

// ─────────────────────────────────────────────────────────────────
// MPC vs Non-MPC comparison + per-MPC-candidate
// All-time matview reads; both reconcile to the headline (MPC + Non-MPC
// closed_won = 14, revenue ≈ $311K — MPC 3/≈$59K, Non-MPC 11/≈$252K).
// ─────────────────────────────────────────────────────────────────
export async function getMpcSummary(): Promise<QueryResult<MpcSummaryRow>> {
  return runQuery<MpcSummaryRow>((sb) =>
    sb
      .from("mv_mpc_summary")
      .select(
        "cohort, campaigns, sent, replied, reply_rate, positives, calls, " +
          "meeting_to_positive, closed_won, revenue",
      )
      .limit(10),
  );
}

export async function getMpcCandidates(): Promise<
  QueryResult<MpcCandidateRow>
> {
  return runQuery<MpcCandidateRow>((sb) =>
    sb
      .from("mv_per_mpc_candidate")
      .select(
        "candidate, partner_rep, campaigns, sent, replied, positives, calls, pos_rate, industry",
      )
      .order("positives", { ascending: false })
      .limit(1000),
  );
}

// Date-range-aware MPC summary + per-candidate. Mirror the all-time matview
// logic but windowed: campaigns/sent/replied from PV campaigns CREATED in the
// window, positives/calls from mv_thread_rep_attribution (last_activity in
// window, PAGED so a >1000-row window doesn't truncate), deals from deal_dim
// (closedate in window). Keeps these pages consistent with the headline filter.
type CampMeta = { campaign_id: string; is_mpc: boolean | null; mpc_candidate: string | null; assigned_rep: string | null };
type AttrRow = { lead_email: string | null; conv_id: string | null; is_positive: boolean | null; is_call_booked: boolean | null; campaign_is_mpc: boolean | null; mpc_candidate: string | null };
type PVRowM = { campaign_id: string; sent_count: number | null; replied_count: number | null };

async function _latestPvSnapshot(sb: Awaited<ReturnType<typeof createClient>>): Promise<string | null> {
  const r = await sb.from("pv_campaign_snapshot").select("snapshot_date").order("snapshot_date", { ascending: false }).limit(1);
  return (r.data as unknown as { snapshot_date: string }[] | null)?.[0]?.snapshot_date ?? null;
}

export async function getMpcSummaryByRange(opts: { from: string; to: string }): Promise<QueryResult<MpcSummaryRow>> {
  return runQuery<MpcSummaryRow>(async (sb) => {
    const fromTs = `${opts.from}T00:00:00.000Z`;
    const toTs = `${opts.to}T23:59:59.999Z`;
    const camps = await sb.from("campaign_dim").select("campaign_id, is_mpc, mpc_candidate, assigned_rep").limit(5000);
    const isMpcCampaign = new Map<string, boolean>();
    for (const r of (camps.data as unknown as CampMeta[]) || []) isMpcCampaign.set(r.campaign_id, !!r.is_mpc);

    const agg = { MPC: { campaigns: 0, sent: 0, replied: 0 }, NON: { campaigns: 0, sent: 0, replied: 0 } };
    const pvLatest = await _latestPvSnapshot(sb);
    if (pvLatest) {
      const pv = await sb.from("pv_campaign_snapshot").select("campaign_id, sent_count, replied_count").eq("snapshot_date", pvLatest).gte("created_at", fromTs).lte("created_at", toTs).limit(5000);
      for (const r of (pv.data as unknown as PVRowM[]) || []) {
        const k = isMpcCampaign.get(r.campaign_id) ? "MPC" : "NON";
        agg[k].campaigns += 1; agg[k].sent += Number(r.sent_count ?? 0); agg[k].replied += Number(r.replied_count ?? 0);
      }
    }
    const threads = await fetchAllRows<AttrRow>(() =>
      sb.from("mv_thread_rep_attribution").select("lead_email, conv_id, is_positive, is_call_booked, campaign_is_mpc").gte("last_activity", fromTs).lte("last_activity", toTs));
    const posMpc = new Set<string>(), posNon = new Set<string>(), callMpc = new Set<string>(), callNon = new Set<string>();
    for (const t of threads.data) {
      const mpc = t.campaign_is_mpc === true;
      if (t.is_positive && t.lead_email) (mpc ? posMpc : posNon).add(t.lead_email);
      if (t.is_call_booked && t.conv_id) (mpc ? callMpc : callNon).add(t.conv_id);
    }
    const deals = await sb.from("deal_dim").select("amount, campaign_is_mpc").eq("is_closed_won", true).eq("is_outbound_attributed", true).gte("closedate", fromTs).lte("closedate", toTs).limit(5000);
    let wonMpc = 0, wonNon = 0, revMpc = 0, revNon = 0;
    for (const d of (deals.data as unknown as { amount: number | null; campaign_is_mpc: boolean | null }[]) || []) {
      if (d.campaign_is_mpc) { wonMpc += 1; revMpc += Number(d.amount ?? 0); } else { wonNon += 1; revNon += Number(d.amount ?? 0); }
    }
    const mk = (cohort: string, a: { campaigns: number; sent: number; replied: number }, pos: Set<string>, calls: Set<string>, won: number, rev: number): MpcSummaryRow => ({
      cohort, campaigns: a.campaigns, sent: a.sent, replied: a.replied,
      reply_rate: a.sent > 0 ? a.replied / a.sent : 0,
      positives: pos.size, calls: calls.size,
      meeting_to_positive: pos.size > 0 ? calls.size / pos.size : 0,
      closed_won: won, revenue: rev,
    });
    return { data: [mk("MPC", agg.MPC, posMpc, callMpc, wonMpc, revMpc), mk("Non-MPC", agg.NON, posNon, callNon, wonNon, revNon)], error: null };
  }, { admin: true });
}

export async function getMpcCandidatesByRange(opts: { from: string; to: string }): Promise<QueryResult<MpcCandidateRow>> {
  return runQuery<MpcCandidateRow>(async (sb) => {
    const fromTs = `${opts.from}T00:00:00.000Z`;
    const toTs = `${opts.to}T23:59:59.999Z`;
    const camps = await sb.from("campaign_dim").select("campaign_id, is_mpc, mpc_candidate, assigned_rep").limit(5000);
    const campMeta = new Map<string, CampMeta>();
    const repByCand = new Map<string, string>();
    for (const r of (camps.data as unknown as CampMeta[]) || []) {
      campMeta.set(r.campaign_id, r);
      if (r.is_mpc && r.mpc_candidate && r.assigned_rep) repByCand.set(r.mpc_candidate, r.assigned_rep);
    }
    const byCand = new Map<string, { campaigns: number; sent: number; replied: number }>();
    const pvLatest = await _latestPvSnapshot(sb);
    if (pvLatest) {
      const pv = await sb.from("pv_campaign_snapshot").select("campaign_id, sent_count, replied_count").eq("snapshot_date", pvLatest).gte("created_at", fromTs).lte("created_at", toTs).limit(5000);
      for (const r of (pv.data as unknown as PVRowM[]) || []) {
        const m = campMeta.get(r.campaign_id);
        if (!m || !m.is_mpc || !m.mpc_candidate) continue;
        const e = byCand.get(m.mpc_candidate) ?? { campaigns: 0, sent: 0, replied: 0 };
        e.campaigns += 1; e.sent += Number(r.sent_count ?? 0); e.replied += Number(r.replied_count ?? 0);
        byCand.set(m.mpc_candidate, e);
      }
    }
    const threads = await fetchAllRows<AttrRow>(() =>
      sb.from("mv_thread_rep_attribution").select("lead_email, conv_id, is_positive, is_call_booked, campaign_is_mpc, mpc_candidate").gte("last_activity", fromTs).lte("last_activity", toTs));
    const posByCand = new Map<string, Set<string>>(), callByCand = new Map<string, Set<string>>();
    for (const t of threads.data) {
      if (t.campaign_is_mpc !== true || !t.mpc_candidate) continue;
      if (t.is_positive && t.lead_email) { (posByCand.get(t.mpc_candidate) ?? posByCand.set(t.mpc_candidate, new Set()).get(t.mpc_candidate)!).add(t.lead_email); }
      if (t.is_call_booked && t.conv_id) { (callByCand.get(t.mpc_candidate) ?? callByCand.set(t.mpc_candidate, new Set()).get(t.mpc_candidate)!).add(t.conv_id); }
    }
    // Industry is a candidate property (range-independent) — pull the resolved
    // label straight from the matview so the windowed view shows it too.
    const indRes = await sb
      .from("mv_per_mpc_candidate")
      .select("candidate, industry")
      .limit(1000);
    const indByCand = new Map<string, string | null>();
    for (const r of (indRes.data as unknown as { candidate: string; industry: string | null }[]) || []) {
      indByCand.set(r.candidate, r.industry ?? null);
    }
    const cands = new Set<string>([...byCand.keys(), ...posByCand.keys(), ...callByCand.keys()]);
    const rows: MpcCandidateRow[] = [...cands].map((cand) => {
      const cc = byCand.get(cand);
      const sent = cc?.sent ?? 0;
      const positives = posByCand.get(cand)?.size ?? 0;
      return {
        candidate: cand, partner_rep: repByCand.get(cand) ?? null,
        campaigns: cc?.campaigns ?? 0, sent, replied: cc?.replied ?? 0,
        positives, calls: callByCand.get(cand)?.size ?? 0,
        pos_rate: sent > 0 ? positives / sent : 0,
        industry: indByCand.get(cand) ?? null,
      };
    });
    rows.sort((a, b) => (b.positives ?? 0) - (a.positives ?? 0));
    return { data: rows, error: null };
  }, { admin: true });
}
