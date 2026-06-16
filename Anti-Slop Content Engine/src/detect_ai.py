#!/usr/bin/env python3
"""
detect_ai.py — Pre-delivery AI-detection quality gate for the content engine.

  ┌─────────────────────────────────────────────────────────────────────┐
  │  SANITIZED PORTFOLIO CUT — this file is FAITHFUL / UNREDACTED.       │
  │  It contains no proprietary rules, so it ships intact. API keys are  │
  │  read only from environment variables; none are hard-coded.          │
  └─────────────────────────────────────────────────────────────────────┘

What this does
--------------
Reads a draft (markdown or plain text), submits it to one or more published
AI-detection services, and prints a PASS/FAIL summary against a configurable
target (default: <=50% AI). Exit code:
    0 — every queried service passed (AI% <= target)
    1 — at least one service flagged the draft (AI% > target)
    2 — no service could be queried (no API keys, or all HTTP errors)

Usage
-----
    python detect_ai.py <draft.md> [--service originality|gptzero|pangram|all] [--json]
                                   [--target-ai-pct 50]

API keys come from environment variables (never hard-coded, never committed):
    ORIGINALITY_API_KEY    — Originality.AI
    GPTZERO_API_KEY        — GPTZero
    PANGRAM_API_KEY        — Pangram Labs

How to extend with new services
-------------------------------
1. Add an entry to SERVICES (display_name, env_var, signup_url, pricing_note,
   endpoint, auth_header).
2. Write a `check_<name>(text, api_key) -> dict` returning a normalized dict:
   {ai_pct, human_pct, raw_response}. Raise DetectorError on any failure.
3. Register the function in DETECTORS.

Design note: each vendor returns a different response shape; the adapter
functions normalize them to one contract, and a parse failure raises a typed
DetectorError that surfaces the raw JSON in --json mode — so a vendor changing
their schema produces a localized, obvious break rather than a silent wrong score.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Service registry — each service is data; the adapter below is behavior.
# ---------------------------------------------------------------------------

SERVICES: dict[str, dict] = {
    "originality": {
        "display_name": "Originality.AI",
        "env_var": "ORIGINALITY_API_KEY",
        "signup_url": "https://app.originality.ai/api-access",
        "pricing_note": "~1 credit / 100 words, ~$0.01/credit (cheapest plan)",
        "endpoint": "https://api.originality.ai/api/v1/scan/ai",
        "auth_header": "X-OAI-API-KEY",
    },
    "gptzero": {
        "display_name": "GPTZero",
        "env_var": "GPTZERO_API_KEY",
        "signup_url": "https://app.gptzero.me/app/api",
        "pricing_note": "Essential plan ~$15/mo for 150k words (~$0.0001/word)",
        "endpoint": "https://api.gptzero.me/v2/predict/text",
        "auth_header": "x-api-key",
    },
    "pangram": {
        "display_name": "Pangram",
        "env_var": "PANGRAM_API_KEY",
        "signup_url": "https://www.pangram.com/solutions/api",
        "pricing_note": "Not publicly listed; contact sales",
        "endpoint": "https://text.api.pangram.com/v3",
        "auth_header": "x-api-key",
    },
}


# ---------------------------------------------------------------------------
# Errors and shared HTTP helper
# ---------------------------------------------------------------------------

class DetectorError(Exception):
    """Raised when a detector call fails (HTTP, parse, or missing fields)."""


def _post_json(url: str, body: dict, headers: dict[str, str], timeout: float = 30.0) -> dict:
    """POST JSON and return the decoded JSON response."""
    data = json.dumps(body).encode("utf-8")
    merged_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    merged_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise DetectorError(f"HTTP {e.code} {e.reason} from {url}: {body_text}") from e
    except urllib.error.URLError as e:
        raise DetectorError(f"Network error contacting {url}: {e.reason}") from e
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DetectorError(f"Malformed JSON response from {url}: {e}") from e


# ---------------------------------------------------------------------------
# Per-service adapters — normalize each vendor shape to {ai_pct, human_pct, raw}
# ---------------------------------------------------------------------------

def check_originality(text: str, api_key: str) -> dict:
    """Submit text to Originality.AI and return normalized scores."""
    svc = SERVICES["originality"]
    body = {
        "content": text,
        "title": "content-engine pre-delivery check",
        "aiModelVersion": os.environ.get("ORIGINALITY_MODEL_VERSION", "1"),
        "storeScan": "false",
    }
    headers = {svc["auth_header"]: api_key}
    raw = _post_json(svc["endpoint"], body, headers)
    try:
        score = raw["score"]
        ai = float(score["ai"])
        human = float(score.get("original", 1.0 - ai))
    except (KeyError, TypeError, ValueError) as e:
        raise DetectorError(
            f"Could not parse Originality.AI response (shape may have changed): {e}. "
            f"Raw: {json.dumps(raw)[:300]}"
        ) from e
    return {"ai_pct": ai * 100.0, "human_pct": human * 100.0, "raw_response": raw}


def check_gptzero(text: str, api_key: str) -> dict:
    """Submit text to GPTZero v2 and return normalized scores."""
    svc = SERVICES["gptzero"]
    body = {
        "document": text,
        "version": os.environ.get("GPTZERO_MODEL_VERSION", "2024-01-09"),
    }
    headers = {svc["auth_header"]: api_key}
    raw = _post_json(svc["endpoint"], body, headers)
    try:
        doc = raw["documents"][0]
        probs = doc.get("class_probabilities") or {}
        ai = float(probs.get("ai", doc.get("completely_generated_prob", 0.0)))
        human = float(probs.get("human", 1.0 - ai))
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise DetectorError(
            f"Could not parse GPTZero response (shape may have changed): {e}. "
            f"Raw: {json.dumps(raw)[:300]}"
        ) from e
    return {"ai_pct": ai * 100.0, "human_pct": human * 100.0, "raw_response": raw}


def check_pangram(text: str, api_key: str) -> dict:
    """Submit text to Pangram v3 and return normalized scores."""
    svc = SERVICES["pangram"]
    body = {"text": text, "public_dashboard_link": False}
    headers = {svc["auth_header"]: api_key}
    raw = _post_json(svc["endpoint"], body, headers)
    try:
        ai = float(raw["fraction_ai"])
        # Count AI-assisted toward the AI bucket for gate purposes.
        assisted = float(raw.get("fraction_ai_assisted", 0.0))
        human = float(raw.get("fraction_human", 1.0 - ai - assisted))
    except (KeyError, TypeError, ValueError) as e:
        raise DetectorError(
            f"Could not parse Pangram response (shape may have changed): {e}. "
            f"Raw: {json.dumps(raw)[:300]}"
        ) from e
    return {"ai_pct": (ai + assisted) * 100.0, "human_pct": human * 100.0, "raw_response": raw}


DETECTORS: dict[str, Callable[[str, str], dict]] = {
    "originality": check_originality,
    "gptzero": check_gptzero,
    "pangram": check_pangram,
}


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    return len(text.split())


def _read_draft(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _missing_key_message(service: str) -> str:
    svc = SERVICES[service]
    return (
        f"  Set ${svc['env_var']} to enable {svc['display_name']}. "
        f"Get a key at {svc['signup_url']}. Pricing: {svc['pricing_note']}."
    )


def _format_line(display_name: str, result: dict, target_ai_pct: float) -> str:
    ai = result["ai_pct"]
    human = result["human_pct"]
    verdict = "PASS" if ai <= target_ai_pct else "FAIL"
    return (
        f"{display_name + ':':<16} {ai:5.1f}% AI / {human:5.1f}% human   "
        f"{verdict}  (target: <= {target_ai_pct:.0f}% AI)"
    )


def run(draft_path: str, requested: list[str], target_ai_pct: float, as_json: bool) -> int:
    try:
        text = _read_draft(draft_path)
    except OSError as e:
        print(f"ERROR: could not read draft {draft_path}: {e}", file=sys.stderr)
        return 2

    words = _word_count(text)
    abs_path = os.path.abspath(draft_path)

    # Partition requested services into runnable vs missing-key.
    runnable: list[str] = []
    missing: list[str] = []
    for svc in requested:
        (runnable if os.environ.get(SERVICES[svc]["env_var"]) else missing).append(svc)

    # Call each runnable service.
    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for svc in runnable:
        api_key = os.environ[SERVICES[svc]["env_var"]]
        try:
            results[svc] = DETECTORS[svc](text, api_key)
        except DetectorError as e:
            errors[svc] = str(e)

    if as_json:
        payload = {
            "draft": abs_path,
            "words": words,
            "target_ai_pct": target_ai_pct,
            "results": {
                svc: {
                    "ai_pct": r["ai_pct"],
                    "human_pct": r["human_pct"],
                    "pass": r["ai_pct"] <= target_ai_pct,
                    "raw": r["raw_response"],
                }
                for svc, r in results.items()
            },
            "errors": errors,
            "missing_keys": [SERVICES[s]["env_var"] for s in missing],
        }
        print(json.dumps(payload, indent=2))
    else:
        print("DETECT_AI v1 - Detection score check")
        print(f"Draft: {abs_path}  Words: {words}")
        print("=" * 55)
        print()
        for svc in requested:
            display = SERVICES[svc]["display_name"]
            if svc in results:
                print(_format_line(display, results[svc], target_ai_pct))
            elif svc in errors:
                print(f"{display + ':':<16} ERROR - {errors[svc]}")
            else:
                print(f"{display + ':':<16} SKIPPED (no API key)")
                print(_missing_key_message(svc))
        print()
        passed = sum(1 for r in results.values() if r["ai_pct"] <= target_ai_pct)
        total = len(results)
        print(f"SUMMARY: {passed}/{total} services PASS" if total
              else "SUMMARY: no services could be queried")

    # Exit code contract
    if not results:
        return 2
    if any(r["ai_pct"] > target_ai_pct for r in results.values()):
        return 1
    return 0


def _parse_services(arg: str) -> list[str]:
    if arg == "all":
        return list(SERVICES.keys())
    chosen = [s.strip() for s in arg.split(",") if s.strip()]
    for s in chosen:
        if s not in SERVICES:
            raise SystemExit(f"Unknown service '{s}'. Known: {', '.join(SERVICES)} or 'all'.")
    return chosen


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score a draft against published AI-detection services."
    )
    parser.add_argument("draft", help="Path to the draft file (markdown or plain text)")
    parser.add_argument(
        "--service", default="all",
        help="Comma-separated subset of: " + ", ".join(SERVICES) + ", or 'all'.",
    )
    parser.add_argument(
        "--target-ai-pct", type=float, default=50.0,
        help="AI%% threshold for PASS (default 50).",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    args = parser.parse_args(argv)
    return run(args.draft, _parse_services(args.service), args.target_ai_pct, args.json)


if __name__ == "__main__":
    sys.exit(main())
