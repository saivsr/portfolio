"""Slack webhook helper. Optional — no-op if SLACK_WEBHOOK_URL is unset."""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def post(text: str) -> None:
    """Post a message to the Slack incoming webhook in SLACK_WEBHOOK_URL.

    Silent no-op if the env var is missing. Errors print to stderr but don't raise —
    we don't want a Slack outage to fail the ETL job.
    """
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        return
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as e:
        print(f"[slack] post failed (non-fatal): {e}", file=sys.stderr)
