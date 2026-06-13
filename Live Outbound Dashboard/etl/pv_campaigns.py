"""Pull /campaign/stats from PV and upsert into pv_campaign_snapshot.

Entry point: python -m etl.pv_campaigns

Sanitization note: the real PlusVibe workspace id has been replaced with a
placeholder. The snapshot-diff logic, delta reporting, and Slack summary are the
real production code, unchanged.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time

from .classifier import classify_archetype, mpc_candidate
from .db import bulk_upsert, connect
from .pv_client import PVClient
from .slack import post as slack_post

WORKSPACE_ID = "PV_WORKSPACE_ID_XXX"  # Summit Executive Partners (SEP)
WINDOW_START = "2023-01-01"  # historical backfill — pull every campaign SEP has ever run
TABLE = "pv_campaign_snapshot"
CONFLICT_COLS = ("snapshot_date", "campaign_id")


def _to_iso(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        try:
            return dt.datetime.fromtimestamp(v, tz=dt.timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    return v


def shape_row(snapshot_date: str, pulled_at: str, raw: dict) -> dict:
    name = raw.get("camp_name") or raw.get("campaign_name") or ""
    return {
        "snapshot_date": snapshot_date,
        "campaign_id": raw.get("_id") or raw.get("id"),
        "name": name,
        "archetype": classify_archetype(name),
        "mpc_candidate": mpc_candidate(name),
        "status": raw.get("status"),
        "created_at": _to_iso(raw.get("created_at")),
        "created_by": raw.get("created_by"),
        "sent_count": raw.get("sent_count") or 0,
        "replied_count": raw.get("replied_count") or 0,
        "bounced_count": raw.get("bounced_count") or 0,
        "lead_count": raw.get("lead_count") or 0,
        "lead_contacted_count": raw.get("lead_contacted_count") or 0,
        "positive_reply_count": raw.get("positive_reply_count") or 0,
        "pulled_at": pulled_at,
    }


def prior_summary(conn, snapshot_date: str) -> dict | None:
    """Pull the most recent prior snapshot's totals for delta comparison."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            select snapshot_date::text,
                   count(*),
                   coalesce(sum(sent_count),0),
                   coalesce(sum(replied_count),0),
                   coalesce(sum(positive_reply_count),0)
            from pv_campaign_snapshot
            where snapshot_date < %s
            group by snapshot_date
            order by snapshot_date desc
            limit 1
            """,
            [snapshot_date],
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"date": row[0], "campaigns": row[1], "sent": row[2], "replied": row[3], "positives": row[4]}
    finally:
        cur.close()


def today_summary(conn, snapshot_date: str) -> dict:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            select count(*),
                   coalesce(sum(sent_count),0),
                   coalesce(sum(replied_count),0),
                   coalesce(sum(positive_reply_count),0),
                   count(*) filter (where archetype='MPC')
            from pv_campaign_snapshot
            where snapshot_date=%s
            """,
            [snapshot_date],
        )
        n, sent, replied, pos, mpc = cur.fetchone()
        # New campaigns: in today's snapshot but not in any prior snapshot.
        cur.execute(
            """
            select count(*) from pv_campaign_snapshot t
            where t.snapshot_date=%s
              and not exists (
                select 1 from pv_campaign_snapshot p
                where p.campaign_id=t.campaign_id and p.snapshot_date<%s
              )
            """,
            [snapshot_date, snapshot_date],
        )
        (new_camps,) = cur.fetchone()
        return {
            "campaigns": n, "sent": sent, "replied": replied, "positives": pos,
            "mpc": mpc, "new": new_camps,
        }
    finally:
        cur.close()


def fmt_delta(curr: int, prev: int | None) -> str:
    if prev is None:
        return ""
    d = curr - prev
    if d == 0: return " (±0)"
    return f" ({'+' if d > 0 else ''}{d:,})"


def main() -> int:
    api_key = os.environ.get("PV_API_KEY")
    if not api_key:
        print("PV_API_KEY env var is required", file=sys.stderr)
        return 2
    start = time.monotonic()
    snapshot_date = dt.date.today().isoformat()
    pulled_at = dt.datetime.now(dt.timezone.utc).isoformat()
    client = PVClient(api_key, WORKSPACE_ID)
    print(f"Pulling /campaign/stats ({WINDOW_START} -> {snapshot_date})...")
    stats = client.get("/campaign/stats", start_date=WINDOW_START, end_date=snapshot_date)
    if not isinstance(stats, list):
        print(f"Unexpected /campaign/stats shape: {str(stats)[:300]}", file=sys.stderr)
        return 1
    rows = [shape_row(snapshot_date, pulled_at, c) for c in stats]
    rows = [r for r in rows if r["campaign_id"]]
    print(f"  {len(rows)} campaigns shaped for upsert")
    conn = connect()
    try:
        prior = prior_summary(conn, snapshot_date)
        affected = bulk_upsert(conn, TABLE, rows, CONFLICT_COLS)
        today = today_summary(conn, snapshot_date)
    finally:
        conn.close()
    runtime = int(time.monotonic() - start)
    print(f"Upserted {affected} rows into {TABLE} for snapshot_date={snapshot_date}")
    _ = prior  # appease linter; we use it below

    run_url = ""
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if repo and run_id:
        run_url = f"\n<https://github.com/{repo}/actions/runs/{run_id}|view run>"
    msg_lines = [
        ":white_check_mark: *SEP dashboard — campaigns daily snapshot*",
        f"• Pulled: *{today['campaigns']:,}* campaigns{fmt_delta(today['campaigns'], prior['campaigns'] if prior else None)}",
        f"• Total sent: *{today['sent']:,}*{fmt_delta(today['sent'], prior['sent'] if prior else None)}",
        f"• Total replied: *{today['replied']:,}*{fmt_delta(today['replied'], prior['replied'] if prior else None)}",
        f"• PV positive replies: *{today['positives']:,}*{fmt_delta(today['positives'], prior['positives'] if prior else None)}",
        f"• MPC campaigns: *{today['mpc']:,}*",
    ]
    if prior:
        msg_lines.append(f"• New campaigns since `{prior['date']}`: *{today['new']:,}*")
    else:
        msg_lines.append("• _(first snapshot — no prior to compare)_")
    msg_lines.append(f"• Runtime: {runtime}s · snapshot_date `{snapshot_date}`" + run_url)
    slack_post("\n".join(msg_lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
