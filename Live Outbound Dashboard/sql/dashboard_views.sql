-- SEP Dashboard — materialized views (Phase 3).
-- One view per report section. Apply with refresh_dim.py (or psql).
--
-- All views read from dim tables + the latest snapshot_date of the raw
-- tables. Each carries a UNIQUE INDEX so REFRESH MATERIALIZED VIEW
-- CONCURRENTLY can run without locking out readers.
--
-- Conventions:
--   * "positive replies" = Missive threads with the Interested or Call Booked
--     label, in today's snapshot.
--   * "calls booked"     = Missive threads with the Call Booked label only.
--   * "closed-won"       = deal_dim.is_closed_won = true.
--   * Per-rep aggregates use campaign_dim.assigned_rep when the campaign is
--     attributable; nulls roll up to a synthetic "(unassigned)" bucket so
--     numbers always reconcile.
--
-- ─────────────────────────────────────────────────────────────────────
-- Sanitization note: the client is the fictional "Summit Executive Partners
-- (SEP)"; rep names (Ryan / Andre / Dylan / Marcus / Carol / Owen / Rafael)
-- are fictional stand-ins applied consistently; dollar figures in comments are
-- rounded / illustrative. All SQL logic, CTEs, and the attribution waterfall
-- are the real production code, unchanged.
-- ─────────────────────────────────────────────────────────────────────

-- Drop in dependency order if re-applying. The Step-E outlier/ranking views
-- (mv_campaign_rankings, mv_campaign_duds, mv_meeting_deal_ratio) read from
-- mv_thread_rep_attribution + deal_dim and nothing depends on them, so they
-- drop FIRST (before the base view they read).
drop materialized view if exists mv_mpc_summary cascade;
drop materialized view if exists mv_per_mpc_candidate cascade;
drop materialized view if exists mv_meeting_deal_ratio cascade;
drop materialized view if exists mv_campaign_duds cascade;
drop materialized view if exists mv_campaign_rankings cascade;
drop materialized view if exists mv_reconciliation cascade;
drop materialized view if exists mv_positive_replies cascade;
drop materialized view if exists mv_job_function_breakdown cascade;
drop materialized view if exists mv_industry_breakdown cascade;
drop materialized view if exists mv_per_rep_nonmpc cascade;
drop materialized view if exists mv_per_rep_mpc cascade;
drop materialized view if exists mv_meetings_by_rep cascade;
drop materialized view if exists mv_thread_rep_attribution cascade;
drop materialized view if exists mv_headline cascade;


-- ── mv_meetings_by_rep: real Calendly meetings from Slack #sep-appts ─
-- Sourced from sep_slack_meetings.json (report baseline). Each row is one
-- Calendly meeting attributed to a SEP rep. The dashboard previously
-- used the "SEP - Call Booked" Missive label as a meetings proxy — this
-- view is the actual meeting count and matches the baseline per-rep numbers
-- (Ryan 16, Dylan 17, etc.). When the Slack ETL is wired later, this
-- view will refresh from live data.
-- ── mv_thread_rep_attribution: one row per thread, fully attributed ──
-- The "source of truth" for per-rep filtering by date. Every per-rep
-- range query (UI date filter) reads from this view rather than redoing
-- the attribution waterfall in JS — keeps logic in one place.
--
-- Step C: Missive is the source of truth. Per-rep credit comes ONLY from
-- Missive sep_authors + the #sep-appts Slack feed — never the HubSpot deal
-- owner and never the deal-side attributed_rep. The deal-side / owner
-- fallbacks have been removed (they leaked HubSpot owner credit into the
-- per-rep tables, violating the governing invariant).
--
-- Rep waterfall (first non-null wins):
--   1. #sep-appts Slack rep        (slack_meetings.actual_rep by lead email)
--   2. candidate → MPC rep         (campaign_dim by mpc_candidate)
--   3. PV-MPC campaign assigned_rep (contact_dim.first_seen_campaign)
--   4. Missive sep_authors → rep_dim (email + display-name + nickname)
--
-- MPC waterfall (first non-null wins): Missive-enrichment (missive_thread_
-- campaign) → baseline → live contact_dim/PV path.
create materialized view mv_thread_rep_attribution as
with
-- Most recent NON-NULL lead_email ever captured for each thread. The daily
-- Missive pull occasionally lands a thread before the enrichment step has
-- re-attached the lead's email, so the latest snapshot can show a blank email
-- even though an earlier snapshot already knew it. Because positives/calls are
-- count(distinct lead_email), those blanks silently drop out — making the
-- headline wobble ±80/day purely on enrichment timing (e.g. 86 of 97 blank
-- positive threads on one snapshot were known on a prior day). This backfill
-- restores the most recent known email so the count reflects real interest,
-- not pull timing.
thread_email_backfill as (
  select distinct on (conv_id) conv_id, lead_email
  from missive_thread_snapshot
  where lead_email is not null and btrim(lead_email) <> ''
  order by conv_id, snapshot_date desc
),
threads_today as (
  select
    m.snapshot_date, m.conv_id, m.subject, m.last_activity, m.messages_count,
    m.label_ids, m.label_names,
    -- own email if the latest pull carried it, else the last known one
    coalesce(nullif(btrim(m.lead_email), ''), teb.lead_email) as lead_email,
    m.raw_json, m.pulled_at, m.sep_authors, m.sep_author_names
  from missive_thread_snapshot m
  left join thread_email_backfill teb on teb.conv_id = m.conv_id
  where m.snapshot_date = (select max(snapshot_date) from missive_thread_snapshot)
),
sep_rep_by_thread as (
  select distinct on (m.conv_id)
         m.conv_id,
         rd.rep_key
  from threads_today m,
       lateral unnest(
         coalesce(m.sep_authors, '{}'::text[]),
         coalesce(m.sep_author_names, '{}'::text[])
       ) as sa(email, name)
  join rep_dim rd
    on lower(rd.hubspot_email) = lower(sa.email)
    or lower(rd.rep_key) = lower(split_part(coalesce(sa.name, ''), ' ', 1))
    or lower(rd.rep_key) = lower(split_part(sa.email, '@', 1))
    or (rd.rep_key = 'Rafael' and lower(split_part(coalesce(sa.name, ''), ' ', 1)) = 'rafa')
),
-- #sep-appts Slack rep, keyed on the invitee/lead email. Reads the
-- slack_meetings backfill table AND the live slack_appt_event webhook
-- feed, UNIONed so the view picks up live appts automatically. actual_rep
-- already equals rep_dim.rep_key (validated: Andre/Marcus/Carol/Owen/…).
--
-- IMPORTANT: row_number() must run AFTER the union, not inside each branch.
-- If an email appears in BOTH sources, per-branch numbering yields rn=1 in
-- each → two rows survive → the lead_email join below fans a thread into
-- duplicate rows (breaks the unique conv_id index). Number the combined set
-- so there is exactly one rep per email (most recent meeting wins).
slack_appt_rep as (
  select email, rep_key from (
    select email, rep_key,
           row_number() over (
             partition by email order by meeting_ts desc nulls last
           ) as rn
    from (
      select lower(email) as email, actual_rep as rep_key,
             meeting_date::timestamptz as meeting_ts
      from slack_meetings
      where email is not null and email <> '' and actual_rep is not null
      union all
      select lower(lead_email) as email, actual_rep as rep_key,
             meeting_at as meeting_ts
      from slack_appt_event
      where lead_email is not null and lead_email <> '' and actual_rep is not null
    ) u
  ) s
  where rn = 1
),
-- Missive thread→campaign enrichment, deduped to ONE row per lead_email
-- (missive_thread_campaign is keyed by conv_id, so a lead with N threads has
-- N rows — joining on lead_email without this dedupe fans out the thread
-- rows and breaks the unique conv_id index). MPC-positive wins (is_mpc desc),
-- then the most recently enriched thread — same precedence as the dim loaders.
mtc_by_email as (
  select distinct on (lower(lead_email))
         lower(lead_email) as lead_email, is_mpc, mpc_candidate, campaign_name
  from missive_thread_campaign
  where lead_email is not null
  order by lower(lead_email), is_mpc desc nulls last, enriched_at desc nulls last
),
-- deal_pick retained ONLY to supply the campaign_id fallback below. Its
-- attributed_rep / hubspot_owner_id are intentionally NOT selected — per-rep
-- credit never comes from a deal record or a HubSpot owner.
deal_pick as (
  select distinct on (lower(primary_contact_email))
         lower(primary_contact_email) as email,
         attributed_campaign
  from deal_dim
  where primary_contact_email is not null
  order by lower(primary_contact_email), is_closed_won desc, amount desc nulls last
)
select
  m.snapshot_date,
  m.conv_id,
  m.last_activity,
  lower(m.lead_email)                                                   as lead_email,
  m.label_names,
  case when 'SEP - Interested' = any(m.label_names)
        or 'SEP - Call Booked' = any(m.label_names)
       then true else false end                                          as is_positive,
  case when 'SEP - Call Booked' = any(m.label_names)
       then true else false end                                          as is_call_booked,
  -- Rep waterfall (Missive sep_authors + #sep-appts Slack ONLY — no deal /
  -- owner fallback):
  --   1. #sep-appts Slack rep (slack_meetings/slack_appt_event by email)
  --   2. candidate → MPC rep (campaign_dim by mpc_candidate)
  --   3. PV-MPC campaign assigned_rep (contact_dim.first_seen_campaign)
  --   4. Missive sep_authors → rep_dim
  coalesce(slack_appt.rep_key, pdf_rep.rep_key, c.assigned_rep, sep_rep.rep_key) as rep_key,
  -- MPC tag: Missive-enrichment (missive_thread_campaign) wins, then the
  -- authoritative baseline (covers historical threads PV has since
  -- deleted), then the live contact_dim path.
  coalesce(mtc.is_mpc, pdf.is_mpc, c.is_mpc, false)                      as campaign_is_mpc,
  coalesce(mtc.mpc_candidate, pdf.candidate, c.mpc_candidate)            as mpc_candidate,
  coalesce(mtc.campaign_name, pdf.campaign, c.name)                      as campaign_name,
  coalesce(cd.first_seen_campaign, dp.attributed_campaign)               as campaign_id,
  c.archetype                                                            as campaign_archetype
from threads_today m
left join contact_dim cd        on cd.email             = lower(m.lead_email)
left join campaign_dim c        on c.campaign_id        = cd.first_seen_campaign
left join sep_rep_by_thread sep_rep on sep_rep.conv_id  = m.conv_id
-- Missive enrichment: thread→campaign with the locked comment-resolver
-- cascade. Source of truth for is_mpc / candidate / campaign_name. Deduped
-- to one row per lead_email (see mtc_by_email CTE) so the join is 1:1.
left join mtc_by_email mtc on mtc.lead_email = lower(m.lead_email)
-- #sep-appts Slack rep (sanctioned per-rep credit signal alongside Missive).
left join slack_appt_rep slack_appt on slack_appt.email = lower(m.lead_email)
-- deal_pick retained only for campaign_id fallback (NOT for rep/owner credit).
left join deal_pick dp          on dp.email             = lower(m.lead_email)
-- baseline per-thread classification: 297 positives at the baseline cut.
-- Survives PV's lead deletion.
left join pdf_thread_classification pdf on pdf.email    = lower(m.lead_email)
-- baseline candidate → MPC rep via campaign_dim (assigned_rep already maps the
-- Notion-synced candidate→rep table). For candidates without a campaign_dim
-- row, fall back to a direct REP_BY_CANDIDATE lookup using rep_dim by
-- joining campaign_dim on mpc_candidate name.
left join (
  select distinct on (lower(mpc_candidate)) lower(mpc_candidate) as cand_lower, assigned_rep as rep_key
  from campaign_dim
  where mpc_candidate is not null and assigned_rep is not null
) pdf_rep on pdf_rep.cand_lower = lower(coalesce(mtc.mpc_candidate, pdf.candidate, ''))

union all

-- Phantom rows: baseline emails seen labelled at the baseline cut but the
-- live Missive snapshot no longer has (deleted/archived between then and
-- now). Conversations got 8 emails dropped from Missive — 2 MPC Call
-- Booked, 1 MPC Interested, 4 non-MPC Call Booked, 1 non-MPC Interested.
-- The baseline was itself a frozen Missive snapshot, so this preserves Missive
-- history when live Missive loses it. New labels going forward all land
-- in live Missive — this phantom path only fires for the closed gap.
select
  null::date                                                              as snapshot_date,
  'pdf-' || md5(pdf.email)                                                as conv_id,
  null::timestamptz                                                       as last_activity,
  lower(pdf.email)                                                        as lead_email,
  ARRAY[pdf.label]::text[]                                                as label_names,
  case when pdf.label in ('SEP - Interested', 'SEP - Call Booked')
       then true else false end                                            as is_positive,
  case when pdf.label = 'SEP - Call Booked'
       then true else false end                                            as is_call_booked,
  -- Rep waterfall for phantoms: #sep-appts Slack rep wins, then baseline candidate.
  coalesce(slack_appt2.rep_key, pdf_rep2.rep_key)                         as rep_key,
  -- MPC: Missive-enrichment wins over the baseline (same precedence as
  -- the live branch above), keyed on the phantom email.
  coalesce(mtc.is_mpc, pdf.is_mpc, false)                                  as campaign_is_mpc,
  coalesce(mtc.mpc_candidate, pdf.candidate)                               as mpc_candidate,
  coalesce(mtc.campaign_name, pdf.campaign)                                as campaign_name,
  null::text                                                               as campaign_id,
  null::text                                                               as campaign_archetype
from pdf_thread_classification pdf
-- Reuse the deduped CTEs from the WITH clause (visible to every UNION branch).
left join mtc_by_email mtc on mtc.lead_email = lower(pdf.email)
left join slack_appt_rep slack_appt2 on slack_appt2.email = lower(pdf.email)
left join (
  select distinct on (lower(mpc_candidate)) lower(mpc_candidate) as cand_lower, assigned_rep as rep_key
  from campaign_dim
  where mpc_candidate is not null and assigned_rep is not null
) pdf_rep2 on pdf_rep2.cand_lower = lower(coalesce(mtc.mpc_candidate, pdf.candidate, ''))
where not exists (
  select 1 from missive_thread_snapshot mts
  where mts.snapshot_date = (select max(snapshot_date) from missive_thread_snapshot)
    and lower(mts.lead_email) = lower(pdf.email)
);

create unique index mv_thread_rep_attribution_pk
  on mv_thread_rep_attribution (conv_id);
create index mv_thread_rep_attribution_la
  on mv_thread_rep_attribution (last_activity);


-- ── mv_meetings_by_rep: ALL meetings booked per rep ────────────────
-- Source: Missive. A "meeting" is a Missive thread carrying the
-- `SEP - Call Booked` label (applied by reps when a Calendly meeting
-- is confirmed). The Slack #sep-appts scrape is NOT used — it's
-- partial (only meetings someone posted), has limited backfill
-- (current-year only), and Missive is the singular source of truth for
-- every dashboard metric.
create materialized view mv_meetings_by_rep as
select rep_key as rep,
       count(distinct conv_id) as meetings,
       count(distinct lead_email) as unique_contacts,
       min(last_activity) as first_meeting,
       max(last_activity) as last_meeting
from mv_thread_rep_attribution
where is_call_booked = true
  and rep_key is not null
group by rep_key;
create unique index mv_meetings_by_rep_pk on mv_meetings_by_rep (rep);


-- ── helper: latest snapshot dates ────────────────────────────────────
-- Inlined into each view via subselects to keep them self-contained.

-- ── mv_headline: 1-row summary ───────────────────────────────────────
create materialized view mv_headline as
with
camp_latest as (
  select coalesce(sum(sent_count), 0) as total_sent,
         coalesce(sum(replied_count), 0) as total_replied
  from pv_campaign_snapshot
  where snapshot_date = (select max(snapshot_date) from pv_campaign_snapshot)
),
mi_latest as (
  -- Sourced from mv_thread_rep_attribution which includes phantom rows
  -- for baseline emails Missive has since lost (deleted/archived
  -- between the baseline cut and now). Pure-Missive count would miss 8
  -- historically-real positives.
  select
    count(distinct lead_email) filter (where is_positive)       as total_positives,
    count(distinct lead_email) filter (where is_call_booked)    as total_calls_booked
  from mv_thread_rep_attribution
),
deal_summary as (
  -- Headline mirrors the baseline: only deals attributed to outbound
  -- (primary contact email present in Missive or PV lead snapshots).
  select count(*) filter (where is_closed_won and is_outbound_attributed)                as closed_won_count,
         coalesce(sum(amount) filter (where is_closed_won and is_outbound_attributed), 0) as closed_won_revenue
  from deal_dim
)
select
  1::int                              as id,           -- stable PK for refresh concurrently
  camp_latest.total_sent,
  camp_latest.total_replied,
  mi_latest.total_positives,
  mi_latest.total_calls_booked,
  deal_summary.closed_won_count,
  deal_summary.closed_won_revenue,
  now()                                as refreshed_at
from camp_latest, mi_latest, deal_summary;
create unique index mv_headline_pk on mv_headline (id);


-- ── mv_per_rep_mpc: per-rep MPC stats ────────────────────────────────
-- Attribution waterfall for `positives`:
--   1. contact_dim.first_seen_campaign → campaign_dim.assigned_rep (MPC)
--   2. missive_thread_snapshot.sep_authors → rep_dim.hubspot_email
--      (the SEP rep who actually replied on the thread)
-- The contact_dim path catches threads with a PV lead snapshot we still
-- have. The sep_authors path catches everything else where a SEP rep
-- replied — works for threads PV has already purged.
create materialized view mv_per_rep_mpc as
with
camp_today as (
  select * from pv_campaign_snapshot
  where snapshot_date = (select max(snapshot_date) from pv_campaign_snapshot)
),
sent_replied as (
  select c.assigned_rep as rep,
         coalesce(sum(ct.sent_count), 0)    as sent,
         coalesce(sum(ct.replied_count), 0) as replied
  from camp_today ct
  join campaign_dim c on c.campaign_id = ct.campaign_id and c.is_mpc
  group by c.assigned_rep
),
-- Strictly MPC positives, sourced from mv_thread_rep_attribution which
-- already coalesces the authoritative MPC tag with the live contact_dim
-- path. Matches baseline semantics: a thread counts as MPC iff PV/baseline
-- tagged it as MPC, not just because a rep happened to reply on it.
positives as (
  select rep_key as rep,
         count(distinct conv_id) filter (where is_positive) as interested,
         count(distinct conv_id) filter (where is_call_booked) as calls
  from mv_thread_rep_attribution
  where campaign_is_mpc = true
    and rep_key is not null
  group by rep_key
),
deals as (
  -- MPC-scoped closed-won, attributed to the rep via deal_dim.attributed_rep.
  -- Scope uses deal_dim.campaign_is_mpc (the authoritative Missive-first MPC
  -- flag) — NOT a campaign_dim join on attributed_campaign, which is a campaign
  -- NAME (not an id) and so rarely matched. Without the scope this column
  -- leaked NON-MPC deals into the MPC table (e.g. a rep's ~$250K non-MPC book).
  -- The Non-MPC view scopes the exact complement, so each deal shows once.
  select attributed_rep as rep,
         count(*) filter (
           where is_closed_won and is_outbound_attributed
             and coalesce(campaign_is_mpc, false)
         )                                                                          as deals,
         coalesce(sum(amount) filter (
           where is_closed_won and is_outbound_attributed
             and coalesce(campaign_is_mpc, false)
         ), 0)                                                                      as revenue
  from deal_dim
  group by attributed_rep
),
-- MPC meetings: Missive threads with `SEP - Call Booked` label where
-- the contact is on an MPC campaign. Matches baseline "MPC meetings booked"
-- methodology and reconciles to ~32 all-time (vs. Slack scrape's 18).
mpc_meetings as (
  select rep_key as rep, count(distinct conv_id) as mpc_meetings_taken
  from mv_thread_rep_attribution
  where is_call_booked = true
    and campaign_is_mpc = true
    and rep_key is not null
  group by rep_key
),
total_meetings as (
  select rep, meetings as all_meetings from mv_meetings_by_rep
),
all_reps as (
  select rep from sent_replied
  union
  select rep from positives
  union
  select rep from deals
  union
  select rep from total_meetings
)
select
  coalesce(a.rep, '(unassigned)') as rep,
  coalesce(sr.sent, 0)              as sent,
  coalesce(sr.replied, 0)           as replied,
  coalesce(p.interested, 0)         as interested,
  coalesce(p.calls, 0)              as calls,
  coalesce(mm.mpc_meetings_taken,0) as mpc_meetings,
  coalesce(tm.all_meetings, 0)      as total_meetings,
  coalesce(d.deals, 0)              as deals,
  coalesce(d.revenue, 0)            as revenue
from all_reps a
left join sent_replied sr   on sr.rep is not distinct from a.rep
left join positives    p    on p.rep  is not distinct from a.rep
left join deals        d    on d.rep  is not distinct from a.rep
left join mpc_meetings mm   on mm.rep is not distinct from a.rep
left join total_meetings tm on tm.rep is not distinct from a.rep;
create unique index mv_per_rep_mpc_pk on mv_per_rep_mpc (rep);


-- ── mv_per_rep_nonmpc: PER-REP non-MPC outcomes ─────────────────────
-- A "non-MPC" thread is a Missive thread whose contact_dim.first_seen_campaign
-- is non-MPC OR null (unknown). Rep attribution uses missive_thread_snapshot
-- .sep_authors → rep_dim.hubspot_email — the rep who actually replied is the
-- rep. This is the only working path because non-MPC campaigns have no
-- assigned_rep on campaign_dim (pooled outbound lanes have no single owner).
--
-- Deals follow the same scope: is_outbound_attributed=true AND
-- (attributed_campaign IS NULL OR campaign is non-MPC).
create materialized view mv_per_rep_nonmpc as
with
sep_rep_by_thread as (
  select distinct on (m.conv_id)
         m.conv_id,
         lower(m.lead_email) as lead_email,
         rd.rep_key
  from missive_thread_snapshot m,
       lateral unnest(
         coalesce(m.sep_authors, '{}'::text[]),
         coalesce(m.sep_author_names, '{}'::text[])
       ) as sa(email, name)
  join rep_dim rd
    on lower(rd.hubspot_email) = lower(sa.email)
    or lower(rd.rep_key) = lower(split_part(coalesce(sa.name, ''), ' ', 1))
    or lower(rd.rep_key) = lower(split_part(sa.email, '@', 1))
    -- Nickname aliases (extend here as more surface)
    or (rd.rep_key = 'Rafael' and lower(split_part(coalesce(sa.name, ''), ' ', 1)) = 'rafa')
  where m.snapshot_date = (select max(snapshot_date) from missive_thread_snapshot)
    and m.lead_email is not null
),
-- #sep-appts Slack rep, keyed on lead email (slack_meetings backfill + live
-- slack_appt_event webhook). Sanctioned per-rep signal alongside sep_authors.
-- row_number() must run AFTER the union (see note on the first slack_appt_rep
-- CTE above): per-branch numbering lets an email present in BOTH sources
-- survive twice and fan a thread into duplicate rows.
slack_appt_rep as (
  select email, rep_key from (
    select email, rep_key,
           row_number() over (
             partition by email order by meeting_ts desc nulls last
           ) as rn
    from (
      select lower(email) as email, actual_rep as rep_key,
             meeting_date::timestamptz as meeting_ts
      from slack_meetings
      where email is not null and email <> '' and actual_rep is not null
      union all
      select lower(lead_email) as email, actual_rep as rep_key,
             meeting_at as meeting_ts
      from slack_appt_event
      where lead_email is not null and lead_email <> '' and actual_rep is not null
    ) u
  ) s
  where rn = 1
),
-- Missive enrichment deduped to one row per lead_email (see note in
-- mv_thread_rep_attribution) so the join doesn't fan out the thread rows.
mtc_by_email as (
  select distinct on (lower(lead_email))
         lower(lead_email) as lead_email, is_mpc
  from missive_thread_campaign
  where lead_email is not null
  order by lower(lead_email), is_mpc desc nulls last, enriched_at desc nulls last
),
nonmpc_threads as (
  -- Non-MPC scope: coalesce(mtc.is_mpc, c.is_mpc, false) = false — i.e. the
  -- Missive-enrichment MPC tag wins (matches mv_thread_rep_attribution); if
  -- Missive has no opinion, fall back to the contact_dim/PV campaign flag.
  -- Reactivation campaigns classify_archetype→Other → non-MPC, so they land
  -- here and credit the comment-named / sep_authors rep.
  --
  -- Rep waterfall (Missive sep_authors + #sep-appts Slack ONLY — no deal /
  -- owner fallback). Threads with no rep signal are dropped (under-count
  -- beats mis-attributing generic-bucket positives to a HubSpot owner).
  select m.conv_id,
         m.label_names,
         coalesce(slack_appt.rep_key, sep_rep.rep_key) as rep
  from missive_thread_snapshot m
  left join contact_dim cd on cd.email = lower(m.lead_email)
  left join campaign_dim c on c.campaign_id = cd.first_seen_campaign
  left join mtc_by_email mtc on mtc.lead_email = lower(m.lead_email)
  left join sep_rep_by_thread sep_rep on sep_rep.conv_id = m.conv_id
  left join slack_appt_rep slack_appt on slack_appt.email = lower(m.lead_email)
  where m.snapshot_date = (select max(snapshot_date) from missive_thread_snapshot)
    and coalesce(mtc.is_mpc, c.is_mpc, false) = false
    and coalesce(slack_appt.rep_key, sep_rep.rep_key) is not null
),
thread_agg as (
  select rep,
         count(*)                                                                as threads,
         count(*) filter (
           where 'SEP - Interested' = any(label_names)
              or 'SEP - Call Booked' = any(label_names)
         )                                                                         as positives,
         count(*) filter (where 'SEP - Call Booked' = any(label_names))            as calls
  from nonmpc_threads
  group by rep
),
nonmpc_deals as (
  -- Non-MPC scope via deal_dim.campaign_is_mpc (authoritative Missive-first
  -- flag). The old campaign_dim join (campaign_id = attributed_campaign) almost
  -- never matched — attributed_campaign holds a campaign NAME — so it silently
  -- dropped most non-MPC deals (Owen 9/~$250K and Carol 3/~$48K showed 0).
  select d.*
  from deal_dim d
  where d.is_outbound_attributed
    and not coalesce(d.campaign_is_mpc, false)
),
deal_agg as (
  select
    coalesce(attributed_rep, '(unassigned)') as rep,
    count(*) filter (where is_closed_won)                                       as closed_won,
    coalesce(sum(amount) filter (where is_closed_won), 0)                       as won_amount,
    count(*) filter (
      where not is_closed_won and lower(coalesce(stage_label,'')) not like '%lost%'
    )                                                                            as active_pipeline,
    count(*) filter (
      where not is_closed_won and lower(coalesce(stage_label,'')) like '%lost%'
    )                                                                            as lost,
    count(*)                                                                     as total
  from nonmpc_deals
  group by coalesce(attributed_rep, '(unassigned)')
),
all_reps as (
  select rep from thread_agg
  union
  select rep from deal_agg
)
select
  a.rep,
  coalesce(t.threads, 0)             as threads,
  coalesce(t.positives, 0)           as positives,
  coalesce(t.calls, 0)               as calls,
  coalesce(d.closed_won, 0)          as closed_won,
  coalesce(d.won_amount, 0)          as won_amount,
  coalesce(d.active_pipeline, 0)     as active_pipeline,
  coalesce(d.lost, 0)                as lost,
  coalesce(d.total, 0)               as total,
  case when coalesce(d.total, 0) > 0
       then d.closed_won::float / d.total else 0 end as close_rate
from all_reps a
left join thread_agg t on t.rep is not distinct from a.rep
left join deal_agg   d on d.rep is not distinct from a.rep;
create unique index mv_per_rep_nonmpc_pk on mv_per_rep_nonmpc (rep);


-- ── mv_industry_breakdown: per-industry counts ───────────────────────
create materialized view mv_industry_breakdown as
with
threads_today as (
  select * from missive_thread_snapshot
  where snapshot_date = (select max(snapshot_date) from missive_thread_snapshot)
),
joined as (
  select
    coalesce(cd.industry, 'Cross-functional Roles (industry-ambiguous)') as industry,
    m.conv_id,
    m.label_names
  from threads_today m
  left join contact_dim cd on cd.email = lower(m.lead_email)
)
select
  industry,
  count(*)                                                                as threads,
  count(*) filter (
    where 'SEP - Interested' = any(label_names) or 'SEP - Call Booked' = any(label_names)
  ) as positives,
  count(*) filter (where 'SEP - Call Booked' = any(label_names))                as calls
from joined
group by industry;
create unique index mv_industry_breakdown_pk on mv_industry_breakdown (industry);


-- ── mv_job_function_breakdown: per-function counts ───────────────────
create materialized view mv_job_function_breakdown as
with
threads_today as (
  select * from missive_thread_snapshot
  where snapshot_date = (select max(snapshot_date) from missive_thread_snapshot)
),
joined as (
  select
    coalesce(cd.job_function, 'Cross-functional') as job_function,
    m.conv_id,
    m.label_names
  from threads_today m
  left join contact_dim cd on cd.email = lower(m.lead_email)
)
select
  job_function,
  count(*)                                                                as threads,
  count(*) filter (
    where 'SEP - Interested' = any(label_names) or 'SEP - Call Booked' = any(label_names)
  ) as positives,
  count(*) filter (where 'SEP - Call Booked' = any(label_names))                as calls
from joined
group by job_function;
create unique index mv_job_function_breakdown_pk on mv_job_function_breakdown (job_function);


-- ── mv_positive_replies: one row per positive thread ─────────────────
-- Campaign / MPC attribution: Missive-enrichment (missive_thread_campaign)
-- wins, then the live contact_dim/PV campaign, then the deal-side campaign.
-- Rep attribution waterfall (Missive sep_authors + #sep-appts Slack ONLY —
-- the deal-side attributed_rep and the HubSpot-owner fallback were removed
-- so per-rep credit can never come from a HubSpot owner):
--   1. #sep-appts Slack rep (slack_meetings / slack_appt_event by email)
--   2. PV-MPC campaign assigned_rep (contact_dim.first_seen_campaign)
--   3. missive_thread_snapshot.sep_authors → rep_dim (the SEP rep who replied)
create materialized view mv_positive_replies as
with
threads_today as (
  select * from missive_thread_snapshot
  where snapshot_date = (select max(snapshot_date) from missive_thread_snapshot)
),
labelled as (
  select * from threads_today
  where 'SEP - Interested' = any(label_names) or 'SEP - Call Booked' = any(label_names)
),
-- Per-thread SEP-side rep from Missive external_authors.
sep_rep_by_thread as (
  select distinct on (m.conv_id)
         m.conv_id,
         rd.rep_key
  from threads_today m,
       lateral unnest(
         coalesce(m.sep_authors, '{}'::text[]),
         coalesce(m.sep_author_names, '{}'::text[])
       ) as sa(email, name)
  join rep_dim rd
    on lower(rd.hubspot_email) = lower(sa.email)
    or lower(rd.rep_key) = lower(split_part(coalesce(sa.name, ''), ' ', 1))
    or lower(rd.rep_key) = lower(split_part(sa.email, '@', 1))
    -- Nickname aliases (extend here as more surface)
    or (rd.rep_key = 'Rafael' and lower(split_part(coalesce(sa.name, ''), ' ', 1)) = 'rafa')
),
-- #sep-appts Slack rep, keyed on lead email.
-- row_number() must run AFTER the union (see note on the first slack_appt_rep
-- CTE above): per-branch numbering lets an email present in BOTH sources
-- survive twice and fan a thread into duplicate rows.
slack_appt_rep as (
  select email, rep_key from (
    select email, rep_key,
           row_number() over (
             partition by email order by meeting_ts desc nulls last
           ) as rn
    from (
      select lower(email) as email, actual_rep as rep_key,
             meeting_date::timestamptz as meeting_ts
      from slack_meetings
      where email is not null and email <> '' and actual_rep is not null
      union all
      select lower(lead_email) as email, actual_rep as rep_key,
             meeting_at as meeting_ts
      from slack_appt_event
      where lead_email is not null and lead_email <> '' and actual_rep is not null
    ) u
  ) s
  where rn = 1
),
-- Missive enrichment deduped to one row per lead_email (1:1 join keeps the
-- unique conv_id index intact). MPC-positive wins, then latest enrichment.
mtc_by_email as (
  select distinct on (lower(lead_email))
         lower(lead_email) as lead_email, is_mpc, mpc_candidate, campaign_name
  from missive_thread_campaign
  where lead_email is not null
  order by lower(lead_email), is_mpc desc nulls last, enriched_at desc nulls last
),
-- deal_pick retained only for the deal columns (deal_id / amount / stage),
-- NOT for rep or owner credit.
deal_pick as (
  select distinct on (lower(primary_contact_email))
         lower(primary_contact_email) as email,
         deal_id, is_closed_won, is_outbound_attributed,
         amount, attributed_campaign, stage_label
  from deal_dim
  where primary_contact_email is not null
  order by lower(primary_contact_email), is_closed_won desc, amount desc nulls last
)
select
  l.conv_id,
  l.subject,
  l.last_activity,
  lower(l.lead_email)                                                     as lead_email,
  l.label_names,
  cd.company,
  cd.job_title,
  cd.industry,
  cd.job_function,
  case when 'SEP - Call Booked' = any(l.label_names) then true else false end as is_call_booked,
  case when 'SEP - Interested'  = any(l.label_names) then true else false end as is_interested,
  coalesce(slack_appt.rep_key, camp_contact.assigned_rep, sep_rep.rep_key) as rep_key,
  coalesce(rep_k.display_name, rep_c.display_name, rep_s.display_name)     as rep,
  coalesce(cd.first_seen_campaign, dp.attributed_campaign)                as campaign_id,
  coalesce(mtc.campaign_name, camp_contact.name, camp_deal.name)          as campaign_name,
  coalesce(mtc.mpc_candidate, camp_contact.mpc_candidate, camp_deal.mpc_candidate) as mpc_candidate,
  coalesce(mtc.is_mpc, camp_contact.is_mpc, camp_deal.is_mpc, false)      as campaign_is_mpc,
  dp.deal_id                                                              as deal_id,
  dp.is_closed_won                                                        as deal_is_closed_won,
  dp.is_outbound_attributed                                               as deal_is_outbound_attributed,
  dp.stage_label                                                          as deal_stage,
  dp.amount                                                               as deal_amount
from labelled l
left join contact_dim cd          on cd.email                = lower(l.lead_email)
left join mtc_by_email mtc        on mtc.lead_email          = lower(l.lead_email)
left join deal_pick dp            on dp.email                = lower(l.lead_email)
left join sep_rep_by_thread sep_rep on sep_rep.conv_id       = l.conv_id
left join slack_appt_rep slack_appt on slack_appt.email      = lower(l.lead_email)
left join campaign_dim camp_contact on camp_contact.campaign_id = cd.first_seen_campaign
left join campaign_dim camp_deal    on camp_deal.campaign_id    = dp.attributed_campaign
left join rep_dim rep_k           on rep_k.rep_key           = slack_appt.rep_key
left join rep_dim rep_c           on rep_c.rep_key           = camp_contact.assigned_rep
left join rep_dim rep_s           on rep_s.rep_key           = sep_rep.rep_key;
create unique index mv_positive_replies_pk on mv_positive_replies (conv_id);


-- ── mv_reconciliation: sent → replied → positive → calls → deals ─────
create materialized view mv_reconciliation as
with
camp_today as (
  select coalesce(sum(sent_count), 0)    as sent,
         coalesce(sum(replied_count), 0) as replied
  from pv_campaign_snapshot
  where snapshot_date = (select max(snapshot_date) from pv_campaign_snapshot)
),
mi_today as (
  select count(*) filter (
           where 'SEP - Interested' = any(label_names) or 'SEP - Call Booked' = any(label_names)
         ) as positives,
         count(*) filter (where 'SEP - Call Booked' = any(label_names)) as calls
  from missive_thread_snapshot
  where snapshot_date = (select max(snapshot_date) from missive_thread_snapshot)
),
deal_summary as (
  -- Funnel-end deals: outbound-attributed only (mirrors the baseline).
  select count(*) filter (where is_closed_won and is_outbound_attributed)                as deals,
         coalesce(sum(amount) filter (where is_closed_won and is_outbound_attributed), 0) as revenue
  from deal_dim
)
select
  1::int                       as id,
  ct.sent,
  ct.replied,
  mi.positives,
  mi.calls,
  ds.deals,
  ds.revenue,
  (ct.sent - ct.replied)       as drop_sent_to_replied,
  (ct.replied - mi.positives)  as drop_replied_to_positive,
  (mi.positives - mi.calls)    as drop_positive_to_call,
  (mi.calls - ds.deals)        as drop_call_to_deal,
  now()                        as refreshed_at
from camp_today ct, mi_today mi, deal_summary ds;
create unique index mv_reconciliation_pk on mv_reconciliation (id);


-- ════════════════════════════════════════════════════════════════════
-- STEP E — campaign outlier / ranking views
-- ════════════════════════════════════════════════════════════════════
-- All three read TWO grains and union their campaign keys:
--   (a) the Missive FUNNEL grain off mv_thread_rep_attribution (one row per
--       thread carrying campaign_name + campaign_is_mpc + is_positive +
--       is_call_booked) → threads / positives / calls_booked, and
--   (b) the DEAL grain off deal_dim, grouped by deal_dim.attributed_campaign
--       → deals / won_amount.
--
-- DEAL side joins deal_dim.attributed_campaign (FIX 2), NOT the contact email's
-- own thread campaign. Rationale: E2 now makes every outbound deal inherit its
-- anchoring Missive thread's campaign into deal_dim.attributed_campaign
-- (email-then-domain). Re-deriving the campaign from the deal contact's OWN
-- positive thread (the previous approach) dropped every deal whose contact has
-- no own positive/call-booked thread — notably the domain-anchored closed-won
-- deals — so the campaign rollup saw only ~9 / ~$213K of the ~26 / ~$672K
-- headline (a ~$459K under-report). Grouping the closed-won outbound deals
-- straight off attributed_campaign (NULL → '(unattributed)') is exhaustive: the
-- deal side now totals exactly the headline, reconciling. Missive remains the
-- source of truth for the campaign tag (attributed_campaign is itself
-- anchor-inherited from Missive); HubSpot only supplies deal amount / closed-won.
--
-- NOTE on deploy ordering: attributed_campaign is fully populated only after E2's
-- dim_promote rebuilds deal_dim. Pre-E2 the rollup still reconciles in TOTAL
-- (NULLs collapse into '(unattributed)'); E2 simply moves deals out of
-- '(unattributed)' onto their real campaign rows (10→22 of 26 with a campaign).
-- The logic/DDL is correct either way.
--
-- Campaign key: campaign_name / attributed_campaign, with NULL collapsed to
-- '(unattributed)' so the unique index holds and counts reconcile (the
-- unattributed bucket carries the LinkedIn-only / unmappable threads + any
-- closed-won deal with no campaign). A campaign may appear on the deal side
-- with no Missive funnel threads (deal-only) or vice-versa, so the two grains
-- are UNIONed on the key (full coverage), not inner-joined. is_mpc travels with
-- the campaign: MPC iff any of its threads is tagged MPC by the Missive
-- enrichment OR any of its deals carries campaign_is_mpc.

-- ── mv_campaign_rankings: rank campaigns by calls booked ─────────────
-- Per campaign: is_mpc, threads, positives, calls_booked, deals, won_amount.
-- Ranks run WITHIN the MPC / non-MPC partition (per spec — never compare an
-- MPC campaign against a pooled non-MPC scrape) on calls_booked:
--   rank_calls_desc = 1 → most calls booked in its partition (top performer)
--   rank_calls_asc  = 1 → fewest calls booked in its partition (laggard)
-- Ties share a rank (standard rank(), gaps after ties). The dashboard reads
-- rank_calls_desc for "top campaigns" and rank_calls_asc for "lowest".
create materialized view mv_campaign_rankings as
with
camp_threads as (
  -- One row per (campaign) with the Missive funnel counts. Threads are
  -- counted by distinct conv_id; positives / calls by distinct lead_email to
  -- match the headline's distinct-email semantics.
  select
    coalesce(campaign_name, '(unattributed)')                  as campaign_name,
    bool_or(campaign_is_mpc)                                   as thread_is_mpc,
    count(distinct conv_id)                                    as threads,
    count(distinct lead_email) filter (where is_positive)      as positives,
    count(distinct lead_email) filter (where is_call_booked)   as calls_booked
  from mv_thread_rep_attribution
  group by coalesce(campaign_name, '(unattributed)')
),
-- DEAL side: group closed-won outbound deals straight off
-- deal_dim.attributed_campaign (anchor-inherited from Missive by E2). This is
-- exhaustive over the closed-won outbound headline set — every closed-won
-- outbound deal lands on its campaign row, NULL collapsing into '(unattributed)'.
camp_deals as (
  select coalesce(attributed_campaign, '(unattributed)')       as campaign_name,
         bool_or(coalesce(campaign_is_mpc, false))             as deal_is_mpc,
         count(*)                                              as deals,
         coalesce(sum(amount), 0)                              as won_amount
  from deal_dim
  where is_closed_won and is_outbound_attributed
  group by coalesce(attributed_campaign, '(unattributed)')
),
-- Union the two grains on the campaign key so deal-only campaigns (a deal whose
-- anchor campaign never produced an own positive thread) are not dropped, and
-- thread-only campaigns keep zero deals.
camp_keys as (
  select campaign_name from camp_threads
  union
  select campaign_name from camp_deals
),
combined as (
  select
    k.campaign_name,
    coalesce(ct.thread_is_mpc, false)
      or coalesce(cd.deal_is_mpc, false)                       as is_mpc,
    coalesce(ct.threads, 0)      as threads,
    coalesce(ct.positives, 0)    as positives,
    coalesce(ct.calls_booked, 0) as calls_booked,
    coalesce(cd.deals, 0)        as deals,
    coalesce(cd.won_amount, 0)   as won_amount
  from camp_keys k
  left join camp_threads ct on ct.campaign_name = k.campaign_name
  left join camp_deals   cd on cd.campaign_name = k.campaign_name
)
select
  campaign_name,
  is_mpc,
  threads,
  positives,
  calls_booked,
  deals,
  won_amount,
  rank() over (partition by is_mpc order by calls_booked desc) as rank_calls_desc,
  rank() over (partition by is_mpc order by calls_booked asc)  as rank_calls_asc
from combined;
create unique index mv_campaign_rankings_pk
  on mv_campaign_rankings (campaign_name);
create index mv_campaign_rankings_mpc_desc
  on mv_campaign_rankings (is_mpc, rank_calls_desc);


-- ── mv_campaign_duds: campaigns that produced nothing ────────────────
-- A "dud" is a campaign that has threads (someone replied / it ran) but never
-- converted: positives = 0 AND calls_booked = 0 AND threads > 0. These are the
-- campaigns to cut. The '(unattributed)' bucket is excluded — it is a catch-all,
-- not a real campaign, so flagging it as a dud would be meaningless.
--
-- This view carries NO deal/revenue column (a dud is defined purely on the
-- Missive funnel: zero positives AND zero calls on threads that ran), so the
-- FIX-2 deal_dim.attributed_campaign rewrite does not touch it. A campaign with
-- deals but no threads can never be a dud (threads = 0 fails threads > 0), which
-- is correct — it converted, by definition.
create materialized view mv_campaign_duds as
with
camp_threads as (
  select
    coalesce(campaign_name, '(unattributed)')                  as campaign_name,
    bool_or(campaign_is_mpc)                                   as is_mpc,
    count(distinct conv_id)                                    as threads,
    count(distinct lead_email) filter (where is_positive)      as positives,
    count(distinct lead_email) filter (where is_call_booked)   as calls_booked
  from mv_thread_rep_attribution
  group by coalesce(campaign_name, '(unattributed)')
)
select
  campaign_name,
  is_mpc,
  threads,
  positives,
  calls_booked
from camp_threads
where positives = 0
  and calls_booked = 0
  and threads > 0
  and campaign_name <> '(unattributed)';
create unique index mv_campaign_duds_pk on mv_campaign_duds (campaign_name);


-- ── mv_meeting_deal_ratio: meetings-to-deals efficiency per campaign ─
-- Per campaign: calls_booked, deals_won, ratio (deals_won / calls_booked).
-- top/bottom-decile flags use percentile_cont over the ratio across all
-- campaigns that actually booked a call (calls_booked > 0 — a 0/0 ratio is
-- undefined and would pollute the decile cutoffs):
--   is_top_decile    = ratio >= 90th-percentile cutoff (best converters)
--   is_bottom_decile = ratio <= 10th-percentile cutoff (meetings that stall)
-- Campaigns with calls_booked = 0 are still emitted (ratio NULL, flags false)
-- so the view is a complete per-campaign roster, but they are excluded from the
-- percentile computation.
create materialized view mv_meeting_deal_ratio as
with
camp_threads as (
  select
    coalesce(campaign_name, '(unattributed)')                  as campaign_name,
    bool_or(campaign_is_mpc)                                   as thread_is_mpc,
    count(distinct lead_email) filter (where is_call_booked)   as calls_booked
  from mv_thread_rep_attribution
  group by coalesce(campaign_name, '(unattributed)')
),
-- DEAL side off deal_dim.attributed_campaign (FIX 2) — exhaustive over the
-- closed-won outbound headline set, same as mv_campaign_rankings.
camp_deals as (
  select coalesce(attributed_campaign, '(unattributed)')       as campaign_name,
         bool_or(coalesce(campaign_is_mpc, false))             as deal_is_mpc,
         count(*)                                              as deals_won
  from deal_dim
  where is_closed_won and is_outbound_attributed
  group by coalesce(attributed_campaign, '(unattributed)')
),
camp_keys as (
  select campaign_name from camp_threads
  union
  select campaign_name from camp_deals
),
combined as (
  select
    k.campaign_name,
    coalesce(ct.thread_is_mpc, false)
      or coalesce(cd.deal_is_mpc, false)                       as is_mpc,
    coalesce(ct.calls_booked, 0)                               as calls_booked,
    coalesce(cd.deals_won, 0)                                  as deals_won,
    case when coalesce(ct.calls_booked, 0) > 0
         then coalesce(cd.deals_won, 0)::numeric / ct.calls_booked
         else null end                                         as ratio
  from camp_keys k
  left join camp_threads ct on ct.campaign_name = k.campaign_name
  left join camp_deals   cd on cd.campaign_name = k.campaign_name
),
cutoffs as (
  -- Decile cutoffs computed only over campaigns that booked >=1 call.
  select
    percentile_cont(0.9) within group (order by ratio) as top_cut,
    percentile_cont(0.1) within group (order by ratio) as bottom_cut
  from combined
  where calls_booked > 0
)
-- The `ratio > 0` guard on is_top_decile matters because the ratio
-- distribution is currently ~94% zeros (most campaigns book calls but win no
-- deals yet), which drags both percentile cutoffs to 0.0. Without the guard
-- `ratio >= 0` would flag every campaign as top decile. Guarding top on a
-- strictly-positive ratio keeps the flag meaningful (only real converters)
-- and disjoint from bottom; once deal→campaign attribution fills in under
-- Steps B/C the cutoffs lift off zero on their own.
select
  c.campaign_name,
  c.is_mpc,
  c.calls_booked,
  c.deals_won,
  c.ratio,
  case when c.calls_booked > 0 and c.ratio > 0 and c.ratio >= co.top_cut then true else false end as is_top_decile,
  case when c.calls_booked > 0 and c.ratio <= co.bottom_cut             then true else false end as is_bottom_decile
from combined c, cutoffs co;
create unique index mv_meeting_deal_ratio_pk
  on mv_meeting_deal_ratio (campaign_name);


-- ════════════════════════════════════════════════════════════════════
-- Report sections recreated as views
-- ════════════════════════════════════════════════════════════════════

-- ── mv_per_mpc_candidate: "Per MPC Candidate" table ──────────────────
-- One row per MPC candidate. Two grains, FULL OUTER JOINed on candidate:
--   (a) the PV-snapshot grain — campaign_dim (is_mpc, mpc_candidate not null)
--       joined to the latest pv_campaign_snapshot → campaigns / sent / replied,
--   (b) the Missive funnel grain off mv_thread_rep_attribution (campaign_is_mpc,
--       mpc_candidate not null) → positives (distinct lead_email where
--       is_positive) / calls (distinct conv_id where is_call_booked).
-- FULL OUTER JOIN so a candidate whose campaigns PV has since purged (positives
-- but no sent row) AND a candidate with sent-but-no-positives both appear.
-- partner_rep is campaign_dim.assigned_rep (one rep per candidate — validated
-- no candidate maps to >1 rep); NULL for PV-purged candidates with no surviving
-- campaign_dim row. pos_rate = positives / sent, 0 when sent = 0.
create materialized view mv_per_mpc_candidate as
with
camp_today as (
  select * from pv_campaign_snapshot
  where snapshot_date = (select max(snapshot_date) from pv_campaign_snapshot)
),
camp_side as (
  -- PV-snapshot grain: this candidate's MPC campaigns + their sent/replied.
  select
    c.mpc_candidate                    as candidate,
    max(c.assigned_rep)                as partner_rep,
    count(distinct c.campaign_id)      as campaigns,
    coalesce(sum(ct.sent_count), 0)    as sent,
    coalesce(sum(ct.replied_count), 0) as replied
  from campaign_dim c
  left join camp_today ct on ct.campaign_id = c.campaign_id
  where c.is_mpc and c.mpc_candidate is not null
  group by c.mpc_candidate
),
funnel_side as (
  -- Missive funnel grain: positives by distinct lead_email, calls by distinct
  -- conv_id — matches the headline's distinct-email / distinct-thread semantics.
  select
    mpc_candidate                                              as candidate,
    count(distinct lead_email) filter (where is_positive)      as positives,
    count(distinct conv_id) filter (where is_call_booked)      as calls
  from mv_thread_rep_attribution
  where campaign_is_mpc = true and mpc_candidate is not null
  group by mpc_candidate
),
ind_side as (
  -- Per-candidate industry = the dominant real vertical among the candidate's
  -- leads. The campaign's target vertical is already baked into
  -- contact_dim.industry (via campaign_industry_hint), so the mode is clean
  -- (e.g. an "Edtech" campaign -> EdTech / Education). Ambiguous /
  -- cross-functional leads are excluded so a real vertical surfaces; the
  -- column is NULL (shown as "—") when a candidate has no industry-tagged
  -- leads. ADDITIVE ONLY — a display label that touches no count.
  select
    t.mpc_candidate                                  as candidate,
    mode() within group (order by cd.industry)       as industry
  from mv_thread_rep_attribution t
  join contact_dim cd on cd.email = lower(t.lead_email)
  where t.campaign_is_mpc = true and t.mpc_candidate is not null
    and cd.industry is not null
    and cd.industry <> 'Cross-functional Roles (industry-ambiguous)'
  group by t.mpc_candidate
)
select
  coalesce(cs.candidate, fs.candidate)        as candidate,
  cs.partner_rep                              as partner_rep,
  coalesce(cs.campaigns, 0)                   as campaigns,
  coalesce(cs.sent, 0)                        as sent,
  coalesce(cs.replied, 0)                     as replied,
  coalesce(fs.positives, 0)                   as positives,
  coalesce(fs.calls, 0)                       as calls,
  case when coalesce(cs.sent, 0) > 0
       then coalesce(fs.positives, 0)::numeric / cs.sent
       else 0 end                             as pos_rate,
  isd.industry                                as industry
from camp_side cs
full outer join funnel_side fs on fs.candidate = cs.candidate
left join ind_side isd on isd.candidate = coalesce(cs.candidate, fs.candidate);
create unique index mv_per_mpc_candidate_pk
  on mv_per_mpc_candidate (candidate);


-- ── mv_mpc_summary: "MPC vs Non-MPC" comparison ──────────────────────
-- EXACTLY TWO rows: cohort = 'MPC' and 'Non-MPC'. Each cohort aggregates three
-- grains scoped to its MPC flag:
--   * campaigns / sent / replied — campaign_dim.is_mpc joined to the latest
--     pv_campaign_snapshot.
--   * positives / calls — mv_thread_rep_attribution grouped by campaign_is_mpc
--     (positives = distinct lead_email where is_positive; calls = distinct
--     conv_id where is_call_booked).
--   * closed_won / revenue — deal_dim (is_closed_won and is_outbound_attributed)
--     scoped by campaign_is_mpc.
-- MPC row = campaign_is_mpc true; Non-MPC row = NOT coalesce(campaign_is_mpc,
-- false) — so a NULL MPC flag rolls into Non-MPC and the two cohorts partition
-- the universe exactly (closed_won sums to 20, revenue to ~$485K across both).
-- reply_rate = replied / sent; meeting_to_positive = calls / positives; both 0
-- when their denominator is 0.
create materialized view mv_mpc_summary as
with
cohorts as (
  select true as is_mpc, 'MPC'::text as cohort
  union all
  select false as is_mpc, 'Non-MPC'::text as cohort
),
camp_today as (
  select * from pv_campaign_snapshot
  where snapshot_date = (select max(snapshot_date) from pv_campaign_snapshot)
),
camp_side as (
  -- campaigns/sent/replied from campaign_dim.is_mpc joined to latest snapshot.
  select c.is_mpc                            as is_mpc,
         count(distinct c.campaign_id)       as campaigns,
         coalesce(sum(ct.sent_count), 0)     as sent,
         coalesce(sum(ct.replied_count), 0)  as replied
  from campaign_dim c
  left join camp_today ct on ct.campaign_id = c.campaign_id
  group by c.is_mpc
),
funnel_side as (
  -- positives/calls from the Missive funnel, grouped by the MPC flag.
  select coalesce(campaign_is_mpc, false)                      as is_mpc,
         count(distinct lead_email) filter (where is_positive) as positives,
         count(distinct conv_id) filter (where is_call_booked) as calls
  from mv_thread_rep_attribution
  group by coalesce(campaign_is_mpc, false)
),
deal_side as (
  -- closed-won outbound deals + revenue, scoped by the MPC flag.
  select coalesce(campaign_is_mpc, false)                                       as is_mpc,
         count(*) filter (where is_closed_won and is_outbound_attributed)        as closed_won,
         coalesce(sum(amount) filter (where is_closed_won and is_outbound_attributed), 0) as revenue
  from deal_dim
  group by coalesce(campaign_is_mpc, false)
)
select
  co.cohort,
  coalesce(cs.campaigns, 0)   as campaigns,
  coalesce(cs.sent, 0)        as sent,
  coalesce(cs.replied, 0)     as replied,
  case when coalesce(cs.sent, 0) > 0
       then coalesce(cs.replied, 0)::numeric / cs.sent
       else 0 end             as reply_rate,
  coalesce(fs.positives, 0)   as positives,
  coalesce(fs.calls, 0)       as calls,
  case when coalesce(fs.positives, 0) > 0
       then coalesce(fs.calls, 0)::numeric / fs.positives
       else 0 end             as meeting_to_positive,
  coalesce(ds.closed_won, 0)  as closed_won,
  coalesce(ds.revenue, 0)     as revenue
from cohorts co
left join camp_side   cs on cs.is_mpc = co.is_mpc
left join funnel_side fs on fs.is_mpc = co.is_mpc
left join deal_side   ds on ds.is_mpc = co.is_mpc;
create unique index mv_mpc_summary_pk on mv_mpc_summary (cohort);
