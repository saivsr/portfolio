"""Postgres helper for the Summit Executive Partners (SEP) dashboard ETL.

pg8000 (pure Python) so GitHub Actions runners don't need native deps.
Connection params come from env; password has no default.

Sanitization note: the real Supabase pooler host/region and project ref have
been replaced with obvious placeholders below. The connection logic, SSL config,
and the bulk-upsert batching are the real production code, unchanged.
"""
from __future__ import annotations

import os
from typing import Iterable, Mapping, Sequence

import pg8000.dbapi

_DEFAULTS = {
    # Real values redacted — supplied via env in production.
    "SUPABASE_HOST": "aws-1-REGION.pooler.supabase.com",
    "SUPABASE_USER": "postgres.PROJECT_REF_XXX",
    "SUPABASE_DB": "postgres",
    "SUPABASE_PORT": "5432",
}


def _env(name: str) -> str:
    val = os.environ.get(name) or _DEFAULTS.get(name)
    if val is None:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def connect():
    """Open a pg8000 connection to Supabase Postgres (SSL on, autocommit off)."""
    password = os.environ.get("SUPABASE_PASSWORD")
    if not password:
        raise RuntimeError("SUPABASE_PASSWORD env var is required")
    conn = pg8000.dbapi.connect(
        host=_env("SUPABASE_HOST"),
        user=_env("SUPABASE_USER"),
        password=password,
        database=_env("SUPABASE_DB"),
        port=int(_env("SUPABASE_PORT")),
        ssl_context=True,
    )
    conn.autocommit = False
    return conn


def bulk_upsert(
    conn,
    table: str,
    rows: Sequence[Mapping[str, object]],
    conflict_cols: Iterable[str],
) -> int:
    """Bulk INSERT ... ON CONFLICT (cols) DO UPDATE SET ... — chunked.

    The Postgres wire protocol caps params per statement at 65,535, so we
    auto-split into batches sized to stay safely under that. All rows must
    share the same keys. Returns total rows processed (falls back to len(rows)
    when the driver returns -1 for ON CONFLICT statements).
    """
    if not rows:
        return 0
    conflict_cols = list(conflict_cols)
    # Dedupe by the conflict key (last occurrence wins). Two rows that share a
    # conflict key inside ONE INSERT trigger Postgres error 21000 ("ON CONFLICT
    # DO UPDATE command cannot affect row a second time") and fail the whole
    # batch. Some upstream feeds (e.g. the HubSpot contacts delta) occasionally
    # return duplicate ids, so collapse them here instead of crashing the pull.
    if conflict_cols and len(rows) > 1:
        deduped: dict = {}
        for r in rows:
            deduped[tuple(r.get(c) for c in conflict_cols)] = r
        if len(deduped) != len(rows):
            rows = list(deduped.values())
    cols = list(rows[0].keys())
    update_cols = [c for c in cols if c not in conflict_cols]
    placeholders = "(" + ",".join(["%s"] * len(cols)) + ")"
    set_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols) or \
        ", ".join(f"{c}={table}.{c}" for c in conflict_cols)
    # 60k param ceiling (safety margin under Postgres' 65535). Each row consumes
    # len(cols) params, so max rows per batch = 60000 // len(cols).
    batch_size = max(1, 60000 // max(1, len(cols)))
    total = 0
    cur = conn.cursor()
    try:
        for i in range(0, len(rows), batch_size):
            chunk = rows[i:i + batch_size]
            values_sql = ",".join([placeholders] * len(chunk))
            sql = (
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES {values_sql} "
                f"ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET {set_clause}"
            )
            flat: list[object] = []
            for r in chunk:
                for c in cols:
                    flat.append(r.get(c))
            cur.execute(sql, flat)
            n = cur.rowcount
            total += len(chunk) if n is None or n < 0 else n
        conn.commit()
        return total
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
