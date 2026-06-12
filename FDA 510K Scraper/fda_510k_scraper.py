"""
=============================================================================
fda_510k_scraper.py  —  Targeted openFDA 510(k) puller
=============================================================================
Internal GTM tooling built at Astris Partners.

Hits the live openFDA 510(k) API for a curated set of software-significant
product codes and returns a deduplicated, Clay-ready CSV of recent clearances.
Used for incremental / signal-based pulls (e.g. "everyone cleared in the last
12 months") — distinct from recon_v5.py, which builds the full lifetime TAM
from the bulk export.

Division of labour we settled on:
    fda_510k_scraper.py  -> LIST DISCOVERY  (batch search by code + date)
    recon_v5.py          -> FULL TAM        (offline bulk build)
    Clay (downstream)    -> ENRICHMENT      (domain, headcount, QA/RA contacts)
The FDA `contact` field is a name only — no email/title — so the buyer-level
contact discovery deliberately happens in Clay, not here.

Usage:
    python3 fda_510k_scraper.py                # last 12 months
    python3 fda_510k_scraper.py 6 0            # last 6 months
    python3 fda_510k_scraper.py 18 6           # 18 months ago -> 6 months ago

Outputs:
    fda_510k_us_<from>_to_<to>.csv             # US companies
    fda_510k_intl_<from>_to_<to>.csv           # non-US companies

-----------------------------------------------------------------------------
SANITIZATION NOTE
-----------------------------------------------------------------------------
The real openFDA API key has been replaced with the placeholder
OPENFDA_API_KEY_XXX. openFDA keys are free (open.fda.gov/apis/authentication)
and only raise the rate limit (240 -> 1,000 req/min); the scraper runs without
one. Drop your own key in via the OPENFDA_API_KEY env var. Client reference
stubbed to the fictional "Inferon Health"; employer (Astris Partners) shown by
request. Logic and API contract are unchanged from production.
=============================================================================
"""

import os
import sys
import time
import csv
from datetime import datetime
from dateutil.relativedelta import relativedelta  # python-dateutil

import requests

from product_code_legend import PRODUCT_CODES, is_cleared

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
BASE_URL = "https://api.fda.gov/device/510k.json"

# SANITIZED — real key stubbed. Optional; set OPENFDA_API_KEY to override.
OPENFDA_API_KEY = os.environ.get("OPENFDA_API_KEY", "OPENFDA_API_KEY_XXX")

PAGE_SIZE = 100            # openFDA hard cap is 1000; 100 keeps URLs short
MAX_SKIP = 25_000          # openFDA hard cap on skip
SLEEP_OK = 0.30            # polite delay between successful requests
MAX_RETRIES = 4

CLAY_FIELDS = [
    "company_name", "device_name", "k_number", "decision_code",
    "clearance_date", "clearance_month", "months_since_clearance",
    "product_code", "contact", "city", "state", "country_code",
]


# -----------------------------------------------------------------------------
# Date handling
# -----------------------------------------------------------------------------
def date_window(months_back: int, months_offset: int):
    """Return (from_str, to_str) as YYYYMMDD for the openFDA range filter."""
    today = datetime.today()
    to_date = today - relativedelta(months=months_offset)
    from_date = today - relativedelta(months=months_back)
    return from_date.strftime("%Y%m%d"), to_date.strftime("%Y%m%d")


def parse_date(raw):
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def months_since(d):
    if not d:
        return ""
    now = datetime.today()
    return (now.year - d.year) * 12 + (now.month - d.month)


# -----------------------------------------------------------------------------
# Fetch
# -----------------------------------------------------------------------------
def fetch_code(code, date_from, date_to):
    """Paginate one product code over the date window. Returns list of records."""
    out = []
    skip = 0
    search = f'product_code:"{code}"+AND+decision_date:[{date_from}+TO+{date_to}]'

    while skip <= MAX_SKIP:
        params = {"search": search, "limit": PAGE_SIZE, "skip": skip}
        if OPENFDA_API_KEY and OPENFDA_API_KEY != "OPENFDA_API_KEY_XXX":
            params["api_key"] = OPENFDA_API_KEY

        results = _request_with_retry(params)
        if results is None:        # hard failure after retries
            break
        if not results:            # no more pages
            break

        out.extend(results)
        if len(results) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
        time.sleep(SLEEP_OK)

    return out


def _request_with_retry(params):
    """
    GET with backoff. openFDA returns 404 (not an error) when a query has zero
    results, 429 on rate limit, and intermittent 5xx under load. Treat 404 as
    'empty', retry 429/5xx, surface everything else.
    """
    # Build the query string manually so the '+' operators in `search` survive.
    search = params.pop("search")
    qs = f"search={search}&limit={params['limit']}&skip={params['skip']}"
    if "api_key" in params:
        qs += f"&api_key={params['api_key']}"
    url = f"{BASE_URL}?{qs}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=30)
        except requests.RequestException as e:
            wait = 2 ** attempt
            print(f"    network error ({e}); retry in {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            return resp.json().get("results", [])
        if resp.status_code == 404:
            return []  # openFDA's "no matches"
        if resp.status_code in (429, 500, 502, 503):
            wait = 2 ** attempt
            print(f"    HTTP {resp.status_code}; backing off {wait}s "
                  f"(attempt {attempt}/{MAX_RETRIES})")
            time.sleep(wait)
            continue
        print(f"    HTTP {resp.status_code}: {resp.text[:200]}")
        return None

    print("    gave up after retries")
    return None


# -----------------------------------------------------------------------------
# Transform
# -----------------------------------------------------------------------------
def to_row(rec):
    d = parse_date(rec.get("decision_date"))
    return {
        "company_name": (rec.get("applicant") or "").strip(),
        "device_name": (rec.get("device_name") or "").strip(),
        "k_number": (rec.get("k_number") or "").strip(),
        "decision_code": (rec.get("decision_code") or "").strip(),
        "clearance_date": d.strftime("%Y-%m-%d") if d else "",
        "clearance_month": d.strftime("%Y-%m") if d else "",
        "months_since_clearance": months_since(d),
        "product_code": (rec.get("product_code") or "").strip(),
        "contact": (rec.get("contact") or "").strip(),  # name only — no email
        "city": (rec.get("city") or "").strip(),
        "state": (rec.get("state") or "").strip(),
        "country_code": (rec.get("country_code") or "US").strip().upper() or "US",
    }


def dedupe_keep_recent(rows):
    """One row per company; keep the most recent clearance."""
    best = {}
    for r in rows:
        key = r["company_name"].lower().strip()
        if not key:
            continue
        cur = best.get(key)
        if cur is None or (r["clearance_date"] > cur["clearance_date"]):
            best[key] = r
    return list(best.values())


# -----------------------------------------------------------------------------
def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CLAY_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows):,} -> {path}")


def main():
    months_back = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    months_offset = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    date_from, date_to = date_window(months_back, months_offset)

    print("=" * 60)
    print("FDA 510(k) SaMD SCRAPER — Astris Partners (Inferon Health)")
    print("=" * 60)
    print(f"Date range: {date_from} -> {date_to}")
    print(f"Product codes: {len(PRODUCT_CODES)}")
    key_state = "set" if OPENFDA_API_KEY not in ("", "OPENFDA_API_KEY_XXX") else "none (slower rate limit)"
    print(f"API key: {key_state}")
    print("-" * 60)

    raw = []
    for code in PRODUCT_CODES:
        recs = fetch_code(code, date_from, date_to)
        print(f"  {code}: {len(recs):,} filings")
        raw.extend(recs)

    # Keep cleared only (defensive — query is date-bounded, not decision-bounded)
    rows = [to_row(r) for r in raw if is_cleared(r.get("decision_code", ""))]
    rows = dedupe_keep_recent(rows)

    us = sorted([r for r in rows if r["country_code"] == "US"],
                key=lambda r: r["clearance_date"], reverse=True)
    intl = sorted([r for r in rows if r["country_code"] != "US"],
                  key=lambda r: r["clearance_date"], reverse=True)

    print("-" * 60)
    print(f"Total unique companies: {len(rows):,}  (US {len(us):,} / intl {len(intl):,})")
    write_csv(us, f"fda_510k_us_{date_from}_to_{date_to}.csv")
    write_csv(intl, f"fda_510k_intl_{date_from}_to_{date_to}.csv")
    print("Done. Ready for Clay enrichment.")


if __name__ == "__main__":
    main()
