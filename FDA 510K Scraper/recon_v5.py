"""
=============================================================================
recon_v5.py  —  FDA 510(k) TAM build (widest net)
=============================================================================
Internal GTM tooling built at Astris Partners.

Turns the full openFDA 510(k) bulk export (~175k lifetime filings) into a
deduplicated, one-row-per-company Total Addressable Market of medical-device
firms that carry a real software compliance burden — the target universe for
Inferon Health, an AI-native compliance-automation platform.

Pipeline:
    load bulk JSON
      -> keep CLEARED filings only (SE-family decision codes)
      -> keep filings under software-significant regulation prefixes (whitelist)
      -> group to one row per applicant (normalised name)
      -> drop defunct companies (no cleared filing in 10+ years)
      -> write master_510k_companies.csv  +  print validation summary

This script does NOT hit the network. It reads the bulk file you already
downloaded from FDA. (The targeted API puller is fda_510k_scraper.py.)

Usage:
    python3 recon_v5.py

Inputs  (override with env vars FDA_BULK_JSON / FDA_TAM_OUT):
    ~/Downloads/device-510k-0001-of-0001.json   (openFDA 510k bulk export)

Outputs:
    ~/Downloads/master_510k_companies.csv        (the TAM, one row per company)

-----------------------------------------------------------------------------
SANITIZATION NOTE
-----------------------------------------------------------------------------
No secrets in this file (it is offline / file-only). Client reference stubbed to
the fictional "Inferon Health"; employer (Astris Partners) shown by request.
Logic, whitelist, filters, and CSV schema are unchanged from production.
=============================================================================
"""

import os
import re
import json
import csv
from collections import defaultdict
from datetime import datetime

from product_code_legend import (
    REGULATION_WHITELIST,
    EXCLUDE_PREFIXES,
    is_cleared,
)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
HOME = os.path.expanduser("~")
BULK_JSON = os.environ.get("FDA_BULK_JSON", os.path.join(HOME, "Downloads", "device-510k-0001-of-0001.json"))
TAM_OUT = os.environ.get("FDA_TAM_OUT", os.path.join(HOME, "Downloads", "master_510k_companies.csv"))

DEFUNCT_YEARS = 10           # no cleared filing in N years => drop as defunct
TODAY = datetime.today()
DEFUNCT_CUTOFF = TODAY.replace(year=TODAY.year - DEFUNCT_YEARS)

LINE = "=" * 70


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def parse_date(raw):
    """openFDA dates come as 'YYYY-MM-DD' or 'YYYYMMDD'. Return datetime or None."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


_SUFFIX = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|gmbh|"
    r"ag|s\.?a\.?|s\.?r\.?l|b\.?v|plc|pty|kg| apS|oy|ab)\b\.?",
    re.IGNORECASE,
)
_NONWORD = re.compile(r"[^a-z0-9]+")


def normalise_company(name: str) -> str:
    """
    Collapse applicant-name variants to a single grouping key. FDA applicant
    strings are inconsistent across filings (casing, punctuation, legal suffix),
    so 'Acme Medical, Inc.' and 'ACME MEDICAL INC' must group together.
    Subsidiary roll-up (e.g. parent-branded divisions) is handled downstream in
    Clay, not here — this is intentionally a light key, not a resolver.
    """
    if not name:
        return ""
    n = name.lower()
    n = _SUFFIX.sub(" ", n)
    n = _NONWORD.sub(" ", n)
    return " ".join(n.split())


def regulation_label(reg: str):
    """
    Return the whitelist category label for a regulation number, or None if the
    filing is out of scope. Carve-outs (EXCLUDE_PREFIXES) win over the branch.
    """
    if not reg:
        return None
    reg = reg.strip()
    if any(reg.startswith(x) for x in EXCLUDE_PREFIXES):
        return None
    # Longest matching prefix wins (so '862.1' beats a bare '862' if both exist).
    best = None
    for prefix, label in REGULATION_WHITELIST.items():
        if reg.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, label)
    return best[1] if best else None


def get_regulation(rec: dict) -> str:
    """regulation_number lives under openfda; fall back to top-level if present."""
    of = rec.get("openfda") or {}
    return of.get("regulation_number") or rec.get("regulation_number") or ""


def get_country(rec: dict) -> str:
    return (rec.get("country_code") or "US").strip().upper() or "US"


# -----------------------------------------------------------------------------
# Load
# -----------------------------------------------------------------------------
def load_records(path):
    print(f"Loading {path} ...")
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)
    records = data.get("results", data) if isinstance(data, dict) else data
    print(f"Loaded {len(records):,} records")
    return records


# -----------------------------------------------------------------------------
# Build
# -----------------------------------------------------------------------------
def build_tam(records):
    companies = defaultdict(lambda: {
        "display_name": None,
        "countries": defaultdict(int),
        "whitelist_filings": 0,
        "lifetime_cleared": 0,
        "codes": set(),
        "categories": set(),
        "last_filing": None,
        "sample_device": None,
    })

    cleared_total = 0
    whitelist_total = 0
    # Lifetime cleared count per company across the WHOLE dataset (pre-whitelist).
    # Used only for the top-50 enterprise coverage validation.
    lifetime_cleared_all = defaultdict(int)

    for rec in records:
        if not is_cleared(rec.get("decision_code", "")):
            continue
        cleared_total += 1

        key = normalise_company(rec.get("applicant", ""))
        if not key:
            continue
        lifetime_cleared_all[key] += 1

        label = regulation_label(get_regulation(rec))
        if label is None:
            continue
        whitelist_total += 1

        c = companies[key]
        if c["display_name"] is None:
            c["display_name"] = (rec.get("applicant") or "").strip()
        c["countries"][get_country(rec)] += 1
        c["whitelist_filings"] += 1
        if rec.get("product_code"):
            c["codes"].add(rec["product_code"].strip())
        c["categories"].add(label)
        if rec.get("device_name") and not c["sample_device"]:
            c["sample_device"] = rec["device_name"].strip()

        d = parse_date(rec.get("decision_date"))
        if d and (c["last_filing"] is None or d > c["last_filing"]):
            c["last_filing"] = d

    # lifetime_cleared (whitelisted companies only) for the CSV column
    for key, c in companies.items():
        c["lifetime_cleared"] = lifetime_cleared_all.get(key, c["whitelist_filings"])

    print(f"Cleared filings: {cleared_total:,}")
    print(f"Filings in whitelist: {whitelist_total:,}")
    print(f"Companies in whitelist universe:           {len(companies):,}")

    # Drop defunct (no cleared whitelist filing within DEFUNCT_YEARS)
    tam = {
        k: c for k, c in companies.items()
        if c["last_filing"] is not None and c["last_filing"] >= DEFUNCT_CUTOFF
    }
    print(f"After dropping defunct ({DEFUNCT_YEARS}+yr no activity): {len(tam):,}")

    return tam, lifetime_cleared_all


# -----------------------------------------------------------------------------
# Write
# -----------------------------------------------------------------------------
def write_tam(tam, path):
    rows = []
    for c in tam.values():
        primary_country = max(c["countries"].items(), key=lambda kv: kv[1])[0] if c["countries"] else ""
        rows.append({
            "company_name": c["display_name"],
            "primary_country": primary_country,
            "total_filings_in_whitelist": c["whitelist_filings"],
            "total_cleared_filings_lifetime": c["lifetime_cleared"],
            "unique_whitelist_codes": len(c["codes"]),
            "last_filing_date": c["last_filing"].strftime("%Y-%m-%d") if c["last_filing"] else "",
            "all_categories": " | ".join(sorted(c["categories"])),
            "sample_device": c["sample_device"] or "",
        })

    # Sort by lifetime cleared volume desc — biggest filers first (enterprise top)
    rows.sort(key=lambda r: r["total_cleared_filings_lifetime"], reverse=True)

    fields = list(rows[0].keys()) if rows else []
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"-> TAM written: {path}")
    print(f"  Total companies: {len(rows):,}")
    return rows


# -----------------------------------------------------------------------------
# Validation summary
# -----------------------------------------------------------------------------
def summarise(rows, tam, lifetime_cleared_all):
    print(LINE)
    print("TAM SUMMARY")
    print(LINE)

    # Geo split (top 15)
    geo = defaultdict(int)
    for r in rows:
        geo[r["primary_country"]] += 1
    print("GEO SPLIT (top 15):")
    print("primary_country")
    for country, n in sorted(geo.items(), key=lambda kv: kv[1], reverse=True)[:15]:
        print(f"{country:<6}{n:>5}")

    # Filing-volume distribution
    buckets = {"1 filing": 0, "2-5": 0, "6-20": 0, "21-50": 0, "51+": 0}
    for r in rows:
        v = r["total_filings_in_whitelist"]
        if v == 1:
            buckets["1 filing"] += 1
        elif v <= 5:
            buckets["2-5"] += 1
        elif v <= 20:
            buckets["6-20"] += 1
        elif v <= 50:
            buckets["21-50"] += 1
        else:
            buckets["51+"] += 1
    print("FILING-VOLUME DISTRIBUTION (in whitelist):")
    print("_vol")
    for label, n in buckets.items():
        print(f"{label:<10}{n:>5}")

    # Recency distribution
    rec = {"last 12mo": 0, "12-36mo": 0, "3-10yr": 0, "10+yr (kept anyway)": 0}
    for r in rows:
        d = parse_date(r["last_filing_date"])
        if not d:
            continue
        months = (TODAY.year - d.year) * 12 + (TODAY.month - d.month)
        if months <= 12:
            rec["last 12mo"] += 1
        elif months <= 36:
            rec["12-36mo"] += 1
        elif months <= 120:
            rec["3-10yr"] += 1
        else:
            rec["10+yr (kept anyway)"] += 1
    print("LAST-FILING RECENCY DISTRIBUTION:")
    print("_rec")
    for label, n in rec.items():
        print(f"{label:<22}{n:>5}")

    # ---- THE actual validation: top-50 enterprise filer coverage ----
    # If the whitelist is missing big SaMD players (Intuitive, Brainlab, Varian,
    # Stryker, Roche, Beckman), they won't appear in the TAM. Catch that here.
    tam_keys = set(tam.keys())
    top50 = sorted(lifetime_cleared_all.items(), key=lambda kv: kv[1], reverse=True)[:50]
    caught = [k for k, _ in top50 if k in tam_keys]
    missed = [k for k, _ in top50 if k not in tam_keys]
    print("COVERAGE OF TOP 50 ENTERPRISE FILERS:")
    print(f"  Caught by whitelist: {len(caught)}/50")
    if missed:
        print(f"  Missed: {missed}")


# -----------------------------------------------------------------------------
def main():
    records = load_records(BULK_JSON)
    tam, lifetime_cleared_all = build_tam(records)
    rows = write_tam(tam, TAM_OUT)
    summarise(rows, tam, lifetime_cleared_all)
    print(f"Done. {len(rows):,} companies in TAM. Ready for Clay.")


if __name__ == "__main__":
    main()
