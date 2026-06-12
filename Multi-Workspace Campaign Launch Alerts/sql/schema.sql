-- Durable state for campaign-launch dedupe.
--
-- One row per campaign we've already announced. Because this survives across
-- runs, an already-live campaign is never re-announced -- which is what prevents
-- the "replay the whole backlog" failure (see README). Kept in its own schema so
-- it sits apart from product tables.

create schema if not exists automation;

create table if not exists automation.pv_launch_state (
  campaign_id        text primary key,
  workspace_id       text,
  workspace_name     text,
  camp_name          text,
  status             text,
  first_seen_active  timestamptz default now()
);
