"""Pure-function classifiers for SEP campaigns, threads, and contacts.

Adapted from the script that built the client's baseline PDF report. All logic
is verbatim from that script — keep it that way so the dashboard's numbers
reconcile to the baseline.

─────────────────────────────────────────────────────────────────────
Sanitization note. This is the most data-heavy module in the project, so a few
things are sanitized while every classification rule is preserved verbatim:
  * Candidate names and the candidate→rep map are FULL-SCALE but fictional
    (e.g. "Mateo Cruz", reps Dylan / Andre / Marcus / Owen / Ryan / Carol).
  * The ~24 no-campaign prospect emails are fictional stand-ins mapped to the
    same fictional candidates.
  * `_DOMAIN_OVERRIDES` holds ~120 hand-curated prospect domains in production;
    here it is a fictional representative sample (the real list is the client's
    confidential lead universe). The lookup structure is identical.
  * A handful of brand-name keyword tokens (specific target companies) were
    removed from the industry/title cascades; the generic vocabulary that does
    the actual classifying is unchanged.
─────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import re


# ── Campaign-name structural rules ──────────────────────────────────

# Prefixes before "|" that look like MPC candidates but aren't.
NON_MPC_PREFIXES = {
    "Recruiter Scrape", "Recruiters Scrape", "General Management",
    "Sales Leadership", "Blue Collar", "Information Technology", "LI Jobs",
    "OOO Reactivation", "Recruiter Scrape Test", "Marcus Reactivation",
    "Rafa",
}

# Candidate-name canonicalisations.
NAME_OVERRIDES = {
    "Niko": "Niko Petrovic",
    "Brennan": "Brennan Keller",
    "Theo Marchetto": "Theo Marchetti",
    "Theo Marcheti": "Theo Marchetti",
}


# ── Email → candidate override (no campaign attached) ───────────────
# Used when a Missive thread's sender account isn't tied to any MPC campaign in
# PV (typically because PV deleted the campaign or the candidate ran via a
# secondary mailbox). Verbatim port from the baseline script (emails fictional).
NO_CAMP_MPC = {
    "jdrake@lumenmedia.com": "Damon Tan",
    "k.harmon@brightpathaba.com": "Mira Lowell",
    "dboyd@metropublicradio.org": "Damon Tan",
    "kflynn@meridianjets.com": "Trevor Aldridge",
    "ben@lexiqlabs.ai": "Mira Lowell",
    "casey@verbalink.ai": "Cody Halloran",
    "carl@haleworthco.com": "Jonah Nicholls",
    "dlang@skybridgeaero.aero": "Trevor Aldridge",
    "marcus.allen@helixmolecular.com": "Miles Hauser",
    "kevin.shea@meshstack.io": "Mateo Cruz",
    "varun@cognivault.com": "Serena Feldman",
    "scott.brennan@apexcarehealth.com": "Rohan Thakkar",
    "mason.grant@rentpointpro.com": "Peter Grossman",
    "christian.kohl@novarithm.com": "Anton Volkov",
    "nolan@unifimobile.com": "Cody Halloran",
    "bford@altairjet.com": "Trevor Aldridge",
    "jc@zephyrloop.com": "Mira Lowell",
    "jkramer@fairlake.bank": "Marshall Pace",
    "ivan@tutorpal.ai": "Mira Lowell",
    "gramsey@flynova.com": "Trevor Aldridge",
    "adam@unlockedu.com": "Mira Lowell",
    "seth@propwingpilots.com": "Trevor Aldridge",
    "shelby.cho@techlattice.com": "Anton Volkov",
    "megan.archer@isgroupone.com": "Reed Chong",
}


# ── Candidate → SEP rep ─────────────────────────────────────────────
# Loaded at import time from etl/mpc_assignments.json (the Notion-synced
# "All MPCs New" database). If the file is missing or empty, falls back to
# the hardcoded baseline below — keeps the dashboard alive when the Notion
# sync hasn't been run yet, but the JSON file is the source of truth in
# production. Sync it with `python -m etl.sync_notion_mpcs`.
_REP_BY_CANDIDATE_FALLBACK = {
    "Nathan Maxfield": "Dylan", "Hayden Briggs": "Dylan",
    "Marshall Pace": "Dylan", "Jaylen Holt": "Dylan",
    "Avery Bardin": "Dylan", "Bennett Devano": "Dylan",
    "Mateo Cruz": "Dylan",
    "Camille Hjelm": "Andre", "Tristan Ellison": "Andre",
    "Damon Tan": "Andre", "Cody Halloran": "Andre",
    "Pranay Doshi": "Andre", "Dorian Carver": "Andre",
    "Kellan Brzezinski": "Andre", "Joel Kang": "Andre",
    "Mira Lowell": "Andre", "Reed Chong": "Andre",
    "Leona Carrick": "Andre",
    "Elliot Zhao": "Marcus", "Logan Biesel": "Marcus",
    "Carson Judd": "Marcus", "Samir Kashani": "Marcus",
    "Curtis Cummings": "Marcus", "Nikhil Rao": "Marcus",
    "Preston Macklin": "Marcus", "Miles Hauser": "Marcus",
    "Rohan Thakkar": "Marcus", "Henry Hsu": "Marcus",
    "Vikram Srinath": "Owen", "Wade Safir": "Owen",
    "Pierce Devlin": "Owen", "Aaron Bly": "Owen",
    "Divya Sharma": "Owen", "Anton Volkov": "Owen",
    "Peter Grossman": "Owen", "Serena Feldman": "Owen",
    "Fabian Romero": "Ryan", "Trevor Aldridge": "Ryan",
    "Nolan Fleischer": "Ryan",
    "Dean Gallo": "Carol", "Jonah Nicholls": "Carol",
}


def _load_notion_synced_assignments() -> dict[str, str]:
    """Merge the Notion-synced MPC assignments over the hardcoded fallback.

    Notion wins on conflict — it's the canonical source. The fallback only
    provides values for candidates Notion doesn't list (legacy MPCs whose
    pages have been archived).
    """
    import json
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "mpc_assignments.json")
    merged = dict(_REP_BY_CANDIDATE_FALLBACK)
    if not os.path.exists(path):
        return merged
    try:
        with open(path) as f:
            data = json.load(f)
        for row in data.get("rows", []):
            name = (row.get("name") or "").strip()
            owner = (row.get("owner") or "").strip()
            if name and owner:
                merged[name] = owner
                # Also map last-name-only for fuzzy matches like
                # "Samir Kashami" vs Notion's "Samir Kashani".
                # (Not bidirectional — only adds extra keys, never overwrites.)
    except Exception:
        # Bad/missing JSON: stick with the fallback rather than crashing.
        pass
    return merged


REP_BY_CANDIDATE = _load_notion_synced_assignments()


# ── Candidate-name canonicalisation (near-dupe merge) ───────────────
# The comment resolver sometimes recovers a candidate as a first-name-only
# token ("Reed", "Camille", "Rohan", "Carson") or a near-miss spelling
# ("Rick Chong" for roster "Reed Chong", "Trevor Aldrige" for "Trevor
# Aldridge"). Those split per-candidate rollups even though they're the same
# person. canonicalize_candidate() folds such a token onto the FULL Notion-
# roster spelling, with hard ambiguity guards so we never mismerge two
# distinct candidates:
#   - an exact roster full name is returned unchanged (it IS canonical);
#   - a first-name-only token maps to a roster full name ONLY when exactly
#     one roster candidate shares that first name (else left as-is);
#   - a "<First> <Last>" near-miss maps to a roster full name ONLY when
#     exactly one roster candidate shares the surname AND the first names
#     match (equal / nickname-prefix >=3 chars / edit-distance <=1).
# Everything is computed off REP_BY_CANDIDATE so it tracks the live roster.


def _levenshtein(a: str, b: str) -> int:
    """Plain Levenshtein edit distance (case-insensitive), small-string use."""
    a, b = a.lower(), b.lower()
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def _first_names_match(a: str, b: str) -> bool:
    """True when two first-name tokens denote the same given name.

    Equal, OR one is a >=3-char prefix of the other ("Trev"/"Trevor",
    "Ro"/"Rohan" nickname forms), OR within one edit ("Reid"/"Reed" typo).
    """
    a, b = a.lower(), b.lower()
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return _levenshtein(a, b) <= 1


def _build_canon_indexes() -> tuple[dict, dict, dict]:
    """Pre-compute roster lookup indexes for canonicalize_candidate().

    Returns (exact_lower, first_name_index, surname_index) where the two
    indexes map a lowercased first-name / surname to the list of roster full
    names carrying it.
    """
    exact = {n.lower(): n for n in REP_BY_CANDIDATE}
    by_first: dict[str, list[str]] = {}
    by_last: dict[str, list[str]] = {}
    for n in REP_BY_CANDIDATE:
        parts = n.split()
        if not parts:
            continue
        by_first.setdefault(parts[0].lower(), []).append(n)
        if len(parts) >= 2:
            by_last.setdefault(parts[-1].lower(), []).append(n)
    return exact, by_first, by_last


_CANON_EXACT, _CANON_BY_FIRST, _CANON_BY_LAST = _build_canon_indexes()


def canonicalize_candidate(name: str | None) -> str | None:
    """Fold a recovered MPC candidate onto its full Notion-roster spelling.

    Pure function. Returns the canonical roster full name when the input is an
    unambiguous first-name-only or near-miss of exactly one roster candidate;
    otherwise returns the input unchanged (passthrough for None / empty too).
    Never merges two distinct roster candidates — ambiguity always leaves the
    name as-is.
    """
    if not name:
        return name
    raw = name.strip()
    if not raw:
        return name
    low = raw.lower()

    # 1) Already a canonical roster full name -> return the roster spelling.
    if low in _CANON_EXACT:
        return _CANON_EXACT[low]

    parts = raw.split()

    # 2) First-name-only token -> map iff exactly one roster candidate shares it.
    if len(parts) == 1:
        hits = _CANON_BY_FIRST.get(low, [])
        if len(hits) == 1:
            return hits[0]
        return raw

    # 3) "<First> <Last...>" near-miss -> anchor on the surname, require a
    #    unique roster candidate with that surname whose first name matches.
    surname = parts[-1].lower()
    first = parts[0]
    candidates = [
        r for r in _CANON_BY_LAST.get(surname, [])
        if _first_names_match(first, r.split()[0])
    ]
    if len(candidates) == 1:
        return candidates[0]
    return raw


# ── Structural archetype + MPC candidate extraction ─────────────────

def classify_archetype(name: str | None) -> str:
    """Bucket a campaign by structural archetype.

    Order matters: Recruiter Scrape and Master Recruitment must beat MPC,
    since some legacy names contain both substrings.
    """
    n = (name or "").lower()
    if "recruiter scrape" in n or "recruiters scrape" in n:
        return "Recruiter Scrape"
    if "master recruitment" in n:
        return "Master Recruitment"
    if "li jobs" in n:
        return "LI Jobs"
    # Whole-token match so an embedded "mpc" (e.g. "Olympco") can't be
    # mis-flagged MPC. The substring tests above are intentional (they catch
    # multi-word archetypes); only the bare MPC token needs a word boundary.
    if re.search(r"\bmpc\b", n):
        return "MPC"
    return "Other"


def mpc_candidate(name: str | None) -> str | None:
    """Pull a candidate name out of an MPC campaign name."""
    return extract_candidate(name)


def extract_candidate(name: str | None) -> str | None:
    """Extract candidate name from MPC campaign name patterns.

    Verbatim port from the baseline script.
    """
    if not name:
        return None
    m = re.search(r"^([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\s+MPC\b", name)
    if m:
        c = m.group(1)
        return NAME_OVERRIDES.get(c, c)
    m = re.search(r"\|\s*([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\s+MPC\b", name)
    if m:
        c = m.group(1)
        return NAME_OVERRIDES.get(c, c)
    if "MPC" in name:
        m = re.match(r"^([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\s*\|", name)
        if m:
            c = m.group(1)
            return NAME_OVERRIDES.get(c, c)
    m = re.match(r"^([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3})\s*\|", name)
    if m:
        c = m.group(1)
        if c not in NON_MPC_PREFIXES:
            return NAME_OVERRIDES.get(c, c)
    return None


# ── Industry classification ─────────────────────────────────────────

def classify_industry(domain_or_text: str | None) -> str | None:
    """Classify industry from a campaign name OR free-form company string.

    Returns industry bucket like "Tech / SaaS / VC-backed", "Healthcare /
    Life Sciences", etc. Verbatim port from the baseline script.

    NOTE: Technique-bucket campaigns (Recruiter Scrape, Master Recruitment,
    Reactivation, generic Job Scrape) return None so the dim_promote
    cascade can fall through to domain / subject / title hints — matching
    the baseline's exact behaviour. The baseline only uses
    "Cross-functional Roles (industry-ambiguous)" as the residual bucket
    for these, never "Recruiter Scrape (generic)" as a final industry.
    """
    n_raw = domain_or_text or ""
    n = n_raw.lower()
    if not n:
        return None
    # Technique buckets (campaign structure) — return None so the cascade
    # can keep searching for a real vertical from domain/title/subject.
    if "recruiter scrape" in n or "recruiters scrape" in n:
        return None
    if "operations | master recruitment" in n or "master recruitment" in n:
        return None
    if "marcus reactivation" in n or "reactivation" in n:
        return None
    if "job scrape" in n:
        if "ai/ml" in n or "ai engineer" in n:
            return "AI / ML"
        if "saas/tech" in n:
            return "Tech / SaaS / VC-backed"
        if "solutions engineering" in n or "fde" in n:
            return "Tech / SaaS / VC-backed"
        # Generic Job Scrape with no vertical — fall through to cascade.
        return None
    # Industry buckets
    if "aviation" in n or "airline" in n or "aero" in n or "jets" in n \
            or "completion centre" in n or "completion center" in n \
            or "completions centre" in n:
        return "Aviation / Aerospace"
    if ("patient engagement" in n or "health" in n or "medical" in n
            or "pharma" in n or "biotech" in n or "life sci" in n
            or "nutraceutical" in n or "screening lab" in n):
        return "Healthcare / Life Sciences"
    if "insurtech" in n or "insurance" in n:
        return "Insurance / InsurTech"
    if ("pe-backed" in n or "pe backed" in n or "pe investor" in n
            or "portfolio operator" in n or "alternative asset" in n):
        return "Private Equity / Alternative Assets"
    if ("fintech" in n or "finance" in n or "bank" in n or "credit" in n
            or "aml" in n or "billing/revenue" in n):
        return "Financial Services"
    if ("streaming" in n or "broadcast" in n or "publish" in n
            or "entertainment compan" in n or "vfx" in n
            or "digital media" in n):
        return "Media / Streaming / Entertainment"
    if "gaming" in n or "game" in n:
        return "Gaming"
    if "food" in n or "beverage" in n or "grocery" in n or "f&b" in n:
        return "Food / Beverage / Grocery"
    if ("cpg" in n or "apparel" in n or "branded merchandise" in n
            or "retail" in n or "consumer" in n):
        return "Retail / Consumer / CPG"
    if "edtech" in n or "education" in n:
        return "EdTech / Education"
    if "gov " in n or "government" in n or "gov software" in n:
        return "Government / GovTech"
    if "telecom" in n:
        return "Telecom"
    if "isr companies" in n or "defense" in n:
        return "Defense / ISR"
    if ("edge ai" in n or "ai/ml" in n or "ai engineer" in n
            or "machine learning" in n or "data eng" in n
            or "ai product" in n):
        return "AI / ML"
    if "cyber" in n or "security" in n:
        return "Cybersecurity"
    if "iot" in n or "smart home" in n or "vision system" in n:
        return "IoT / Hardware"
    if "real estate" in n or "proptech" in n or "property" in n:
        return "Real Estate / PropTech"
    if ("logistics" in n or "supply" in n or "shipping" in n
            or "transport" in n or "distribution" in n):
        return "Logistics / Transportation / Distribution"
    if "hvac" in n or "blue collar" in n or "trade" in n:
        return "Trades / HVAC"
    if ("mfg" in n or "manufactur" in n or "industrial" in n
            or "construction" in n):
        return "Manufacturing / Construction"
    if ("energy" in n or "oil" in n or "gas" in n or "utility" in n
            or "utilities" in n):
        return "Energy / Utilities"
    if ("agency" in n or "agencies" in n or "consult" in n
            or "talent agenc" in n):
        return "Agencies / Consulting / Talent"
    if "legal" in n:
        return "Legal"
    if "affiliate platform" in n or "marketing" in n:
        return "Marketing / Affiliate Platforms"
    if "crypto" in n or "web3" in n or "blockchain" in n:
        return "Crypto / Web3"
    if ("sales exec" in n or "general management" in n
            or "sales leader" in n or "gtm" in n):
        return "GTM / Sales Leadership"
    if "information technology" in n or "li jobs" in n:
        return "Information Technology"
    if "professional services" in n:
        return "Professional Services"
    if ("yc compan" in n or "series a" in n or "series b" in n
            or "early sa" in n or "growth stage tech" in n
            or "growth stage software" in n or "saas" in n
            or "subscription" in n or "platform" in n):
        return "Tech / SaaS / VC-backed"
    if "lifestyle apps" in n:
        return "Tech / SaaS / VC-backed"
    if "lookalike" in n:
        return "Lookalike Companies (other)"
    # No signal — return None so the cascade can keep searching.
    # "Other / Unclassified" is reserved as a true last-resort label set
    # only in dim_promote's terminal fallback for fully orphan contacts.
    return None


# ── Domain-based industry hint ──────────────────────────────────────
# Sanitization note: production holds ~120 hand-curated prospect domains here
# (the client's confidential lead universe). Below is a fictional representative
# sample spanning the same industry buckets — the lookup structure and its role
# in the cascade are identical.
_DOMAIN_OVERRIDES = {
    # Healthcare
    "brightpathaba.com": "Healthcare / Life Sciences",
    "apexcarehealth.com": "Healthcare / Life Sciences",
    "helixmolecular.com": "Healthcare / Life Sciences",
    # Aviation
    "meridianjets.com": "Aviation / Aerospace",
    "skybridgeaero.aero": "Aviation / Aerospace",
    # Tech / SaaS / AI
    "meshstack.io": "Tech / SaaS / VC-backed",
    "novarithm.com": "Tech / SaaS / VC-backed",
    "techlattice.com": "Tech / SaaS / VC-backed",
    "cognivault.com": "AI / ML",
    # Media
    "lumenmedia.com": "Media / Streaming / Entertainment",
    "metropublicradio.org": "Media / Streaming / Entertainment",
    # Financial
    "fairlake.bank": "Financial Services",
    # Manufacturing / Construction
    "rolandworks.com": "Manufacturing / Construction",
    # Food / Beverage
    "harvesttablefoods.com": "Food / Beverage / Grocery",
    # Cybersecurity
    "sentryloop.com": "Cybersecurity",
    # Telecom
    "unifimobile.com": "Telecom",
    # EdTech
    "unlockedu.com": "EdTech / Education",
}


# Recruiting / staffing firm domains — these are extremely common in the
# Recruiter Scrape "Not Interested" leads. They should bucket as
# "Agencies / Consulting / Talent" rather than NULL. (Public staffing firms;
# abridged here — production carries ~75.)
_RECRUITING_FIRMS = {
    "hays.com", "harnham.com", "jobot.com", "kforce.com", "kelly.com",
    "kellyservices.com", "roberthalf.com", "rht.com", "manpower.com",
    "manpowergroup.com", "experis.com", "adecco.com", "randstad.com",
    "randstadusa.com", "aerotek.com", "actalentservices.com", "robertwalters.com",
    "michaelpage.com", "pagepersonnel.com", "addisongroup.com",
    "insightglobal.com", "teksystems.com", "judge.com", "judgegroup.com",
    "collabera.com", "lhh.com", "kornferry.com", "heidrick.com",
    "russellreynolds.com", "spencerstuart.com", "egonzehnder.com",
    "dhrglobal.com", "employbridge.com",
}


def domain_industry_hint(email: str | None) -> str | None:
    """Industry hint based on email domain. None if unable to classify.

    Order: exact override → recruiting-firm list → pattern keywords. Keywords
    were expanded substantially to cover the long tail of corporate domains
    that appear in Recruiter Scrape "Not Interested" leads.
    """
    if not email:
        return None
    if "@" in email:
        dom = email.rsplit("@", 1)[1].strip().lower()
    else:
        dom = email.strip().lower()
    if not dom:
        return None
    if dom in _DOMAIN_OVERRIDES:
        return _DOMAIN_OVERRIDES[dom]
    if dom in _RECRUITING_FIRMS:
        return "Agencies / Consulting / Talent"
    # Free-mail / generic providers — can't classify
    if dom in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
               "icloud.com", "aol.com", "me.com", "msn.com", "live.com",
               "protonmail.com", "proton.me", "comcast.net", "att.net",
               "verizon.net", "sbcglobal.net", "mac.com"):
        return None
    # Healthcare / Life Sciences
    if any(k in dom for k in ("health", "medical", "pharma", "bio", "clinic",
                              "hospital", "treatment", "psych", "medic",
                              "dental", "dent", "veterin", "wellness",
                              "therapy", "rehab", "diagnostic", "diagnos",
                              "care.", "cardio", "neuro",
                              "ortho", "oncolog", "patient", "surgery",
                              "physician", "doctor", "nurse", "aba",
                              "behavior", "autism", "lifesci", "genom",
                              "vaccine", "labs.", "biotech",
                              "molecule", "ehr", "telehealth")):
        return "Healthcare / Life Sciences"
    # Cybersecurity
    if any(k in dom for k in ("cyber", "security", "secur", "infosec",
                              "siem", "edr", "soc.", "threat", "vuln")):
        return "Cybersecurity"
    # AI / ML — .ai TLD, common AI/ML keywords
    if dom.endswith(".ai") or any(k in dom for k in (
            "ml.", "mlops", "deepl", "ai-", "ai.",
            "neural", "intelligen", "cognit", "vector", "embedding")):
        return "AI / ML"
    # Construction / AEC
    if any(k in dom for k in ("architects", "architect", "contractor",
                              "construction", "engineering", "roofing",
                              "windows", "builders", "build.", "constructors",
                              "aec.", "civil-", "civils", "masonry",
                              "concrete", "drywall", "plumbing", "hvac",
                              "electric.", "electrical", "mechanical",
                              "structural", "renovation", "restoration",
                              "remodel", "carpentry", "paving", "asphalt",
                              "trees", "landscap", "fencing", "siding",
                              "flooring", "millwork", "facade", "geotech",
                              "epc.", "epcm")):
        return "Construction / AEC"
    # Aviation / Aerospace
    if any(k in dom for k in ("aero", "jet", "aviation", "airline", "airport",
                              "aircraft", "flight", "rotorcraft",
                              "helicopter", "uav", "drone", "avionics",
                              "rocket")):
        return "Aviation / Aerospace"
    # Defense / ISR
    if any(k in dom for k in ("defense", "defence", "isr.", "tactical",
                              "munitions")):
        return "Defense / ISR"
    # Financial Services
    if any(k in dom for k in ("bank", "wealth", "advisory", "advisor",
                              "capital", "finance", "financial", "credit",
                              "lending", "loan", "treasury", "asset",
                              "invest", "fund.", "funds.", "fintech",
                              "payments", "payroll", "trust.", "fiduc",
                              "cpa.", "cpas.", "accounting", "tax.",
                              "audit", "creditunion",
                              "fcu.", "ccu.", "fsb.", "mutual", "annuity",
                              "actuari")):
        return "Financial Services"
    # Insurance
    if any(k in dom for k in ("insurance", "insurtech", "insur", "icw",
                              "policy", "underwrit", "broker", "reinsur",
                              "claims", "actuari")):
        return "Insurance / InsurTech"
    # Real Estate / PropTech
    if any(k in dom for k in ("realestate", "realty", "homes", "homefinder",
                              "property", "properties", "proptech",
                              "mortgage", "appraisal", "lease", "leasing",
                              "rental", "rent.", "apartments", "housing",
                              "resort", "stay.")):
        return "Real Estate / PropTech"
    # Media / Streaming / Entertainment
    if any(k in dom for k in ("media", "broadcast", "streaming", "studios",
                              "studio.", "studio-", "publish", "publisher",
                              "publishing", "magazine", "news.", "tv.",
                              "radio.", "podcast",
                              "entertain", "music", "records.",
                              "production", "film")):
        return "Media / Streaming / Entertainment"
    # Gaming
    if any(k in dom for k in ("games.", "gaming", "studios.io", "playstud")):
        return "Gaming"
    # Food / Beverage / Grocery
    if any(k in dom for k in ("foods", "food.", "beverage", "brew", "wine",
                              "winery", "dairy", "farms", "farm.",
                              "grocery", "groceries", "restaurant", "kitchen",
                              "coffee", "tea.", "snack", "deli",
                              "bakery", "candy", "chocolate", "spice",
                              "petfood")):
        return "Food / Beverage / Grocery"
    # Retail / Consumer / CPG
    if any(k in dom for k in ("retail", "retailer", "cpg.", "consumer",
                              "store.", "shop.", "shop-", "apparel",
                              "fashion", "clothing", "outfit", "boutique",
                              "merchandise", "products.",
                              "brands.", "lifestyle", "cosmetics", "beauty",
                              "skincare", "haircare", "petcare")):
        return "Retail / Consumer / CPG"
    # Energy / Utilities
    if any(k in dom for k in ("energy", "solar", "wind.", "renew",
                              "power.", "powergen", "electric.", "electric-",
                              "utility", "utilities", "petroleum", "oil.",
                              "gas.", "lng", "nuclear",
                              "battery", "hydro")):
        return "Energy / Utilities"
    # Manufacturing / Industrial
    if any(k in dom for k in ("manufacturing", "manufactur", "industries",
                              "industrial", "industries.", "industry",
                              "industrial.", "fabrication", "fabricator",
                              "mfg.", "machining", "tooling", "foundry",
                              "smelting", "metal.", "metals", "steel",
                              "alloy", "polymer", "plastics", "chemicals",
                              "chemical", "lumber", "paper.", "packaging")):
        return "Manufacturing / Construction"
    # Logistics / Transportation / Distribution
    if any(k in dom for k in ("logistics", "transport", "transportation",
                              "freight", "shipping", "trucking", "rail",
                              "supplychain", "warehouse", "distribution",
                              "fulfillment", "delivery", "couriers",
                              "courier", "fleet", "express", "cargo")):
        return "Logistics / Transportation / Distribution"
    # Telecom
    if any(k in dom for k in ("telecom", "wireless", "broadband", "cellular",
                              "fiber.", "spectrum")):
        return "Telecom"
    # EdTech / Education
    if (".edu" in dom or dom.endswith(".edu")
            or any(k in dom for k in ("edtech", "edu.", "education",
                                      "school", "schools", "academy",
                                      "college", "university", "univ.",
                                      "k12.", "learning", "lerning",
                                      "tutor", "course", "courses", "study",
                                      "studies", "scholar",
                                      "campus"))):
        return "EdTech / Education"
    # Legal
    if any(k in dom for k in ("legal", "law.", "lawyers", "lawfirm",
                              "attorneys", "attorney", "counsel", "court",
                              "litigation", "paralegal", "ipfirm",
                              "esq.", "llp.")) or dom.endswith("law"):
        return "Legal"
    # Marketing / Affiliate / Agency
    if any(k in dom for k in ("marketing", "digital.", "creative.", "agency",
                              "agencies", "studio", "branding", "brand.",
                              "design.", "designer", "advertis", "promo",
                              "pr.", "publicrel", "communications")):
        return "Marketing / Affiliate Platforms"
    # Crypto / Web3
    if any(k in dom for k in ("crypto", "web3", "blockchain", "defi",
                              "bitcoin", "ethereum", "nft.")):
        return "Crypto / Web3"
    if dom.endswith(".law"):
        return "Legal"
    # Government / GovTech
    if dom.endswith(".gov") or dom.endswith(".mil") or any(
            k in dom for k in ("govtech", "publicworks", "municipal",
                               "county.", "city.", "state.", "federal")):
        return "Government / GovTech"
    # Nonprofit / Org-style — many .org domains are nonprofits
    if dom.endswith(".org") and not any(
            k in dom for k in ("media", "labs", "academy", "horizon")):
        return "Nonprofit / Mission-driven"
    # Tech / SaaS catch-all (run LAST among industry buckets so it doesn't
    # eat legitimate verticals)
    if any(k in dom for k in ("software", "techcorp", "infotech",
                              ".tech", "tech.", "saas",
                              "cloud", "data.", "analytics", "ai-",
                              "labs.", "labs-", "io.", "platform",
                              "systems", "solutions", "solution.",
                              "technologies", "technology", "digital")):
        return "Tech / SaaS / VC-backed"
    return None


# ── Job-title / job-function classification ─────────────────────────

def _clean_title(s: str) -> str:
    s = re.sub(r"\s*\[(external|ext|external email)\]\s*[-:]?\s*", "", s,
               flags=re.IGNORECASE)
    s = re.sub(r"\s*<外部邮件>\s*", "", s)
    s = re.sub(r"\s*\[caution[^\]]*\]\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*\(job\)\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*(re|fw|fwd):\s*", "", s, flags=re.IGNORECASE)
    return s.strip()


def classify_title(subject_or_role: str | None):
    """Classify a job title / subject line into (job_function, industry).

    Mirrors the baseline's classify_title(title) -> (function, industry) tuple.
    `industry` is set ONLY when the title is industry-specific (Healthcare
    Nurse → Healthcare / Life Sciences; Construction Estimator →
    Construction / AEC; Mortgage Banker → Financial Services). For
    industry-agnostic titles (Sales Manager, Project Manager, Account
    Executive, etc.) `industry` is None — the title doesn't carry a
    vertical signal, so the cascade will bucket them as
    "Cross-functional Roles (industry-ambiguous)".

    Returns (None, None) for clearly-MPC intro subjects.
    Backwards-compat: callers that only want the function can do
    classify_title(x)[0]; callers that want the industry hint use [1].
    """
    if not subject_or_role:
        return ("Unknown", None)
    t = _clean_title(subject_or_role).lower()
    # MPC outbound intro subjects (not job titles)
    if (t in ("intro", "exec intro", "media exec intro", "streaming vp intro",
              "aviation mro intro", "re: cto", "hiring at growth agency",
              "sales leader candidate", "sales leader", "clinic - bcba", "qa / qc")
            or t.startswith("intro") or "exec intro" in t):
        return (None, None)
    # Generic auto-reply / OOO subjects — strip and reclassify remainder
    if t.startswith("automatic reply") or t.startswith("out of office") \
            or "out of office" in t or "auto-reply" in t \
            or t.startswith("meeting booked"):
        t = re.sub(r"^(automatic reply|out of office|auto-reply|"
                   r"meeting booked)\s*[:\-]?\s*", "", t)
        if not t:
            return (None, None)
    # Healthcare (title implies industry)
    if any(k in t for k in ("nurse practitioner", "psychologist",
                            "clinical research", "pharmacist", "mri tech",
                            "child psych", "behavior consultant",
                            "behavior analyst", "utilization management",
                            "medical director", "pharma", "staff pharmacist",
                            "scientist", "physician", "nurse", "rn ",
                            "registered nurse", "clinician", "therapist",
                            "dentist", "veterinarian", "pharmacy",
                            "patient", "behavioral health", "healthcare",
                            "health care", "medical",
                            "surgical tech", "surgical technologist",
                            "surgeon", "sonographer", "radiolog",
                            "phlebotom", "anesthesia", "anesthetist",
                            "respiratory therap", "occupational therap",
                            "physical therap", "speech therap",
                            "athletic trainer", "dietitian", "dietician",
                            "nutritionist", "nursing", "cna ", "lpn ",
                            "caregiver", "case manager", "case management",
                            "care coordinator", "patient access",
                            "revenue cycle", "medical assistant",
                            "clinical operations", "clinical pharmacist",
                            "epidemiolog", "biostatistic", "genetic counselor",
                            "infusion", "oncology", "cardiology", "radiology",
                            "orthopedic", "pediatric", "hospice", "home health",
                            "long-term care", "assisted living",
                            "health information", "him ", "medical coder",
                            "medical billing", "medical biller")):
        return ("Healthcare", "Healthcare / Life Sciences")
    # Construction / AEC (title implies industry)
    if any(k in t for k in ("construction", "architect", "land surveyor",
                            "landscape architect", "resident engineer",
                            "project architect", "site supervisor",
                            "superintendent", "general contractor",
                            "civil engineer", "structural engineer",
                            "carpenter", "electrician", "plumber",
                            "hvac tech", "hvac technician", "trades",
                            "civil engineering", "structural engineering",
                            "geotechnical", "mep engineer", "mep design",
                            "building envelope", "facade", "preconstruction",
                            "construction manager", "design-build",
                            "design build", "field engineer", "site engineer",
                            "concrete", "masonry", "millwork", "drywall",
                            "roofing", "paving", "scaffold", "surveyor",
                            "building inspector", "code inspector",
                            "permitting", "subcontract")) \
            or "estimator" in t or "controls technician" in t:
        return ("Construction / AEC", "Construction / AEC")
    # Insurance (title implies industry)
    if any(k in t for k in ("surety", "actuary", "actuarial", "underwriter",
                            "underwriting", "commercial lines",
                            "personal lines", "insurance account",
                            "claims adjuster", "claims examiner",
                            "claims specialist", "claims manager",
                            "claims representative", "claims processor",
                            "claims analyst", "loss control", "reinsurance",
                            "policy administration", "insurance agent",
                            "insurance broker", "insurance sales")):
        return ("Insurance", "Insurance / InsurTech")
    # Real Estate / Mortgage (title implies industry)
    if "real estate" in t or "appraiser" in t or "mortgage" in t:
        return ("Real Estate / Mortgage", "Real Estate / PropTech")
    # Finance & Accounting (title implies Financial Services)
    if any(k in t for k in ("banker", "banking", "controller", "tax manager",
                            "accountant", "accounting", "financial operations",
                            "valuation", "wealth advisor", "financial analyst",
                            "financial services professional", "client partner",
                            "total rewards", "plant controller",
                            "credit analyst", "treasury", "consolidation",
                            "revenue management", "benefits manager",
                            "finance manager", "finance associate",
                            "finance director", "finance lead",
                            "finance partner", "fp&a", "fpa", "auditor",
                            "audit ", "tax ", "bookkeeper", "clerk",
                            "billing", "payroll", "ar ", "ap ",
                            "accounts payable", "accounts receivable",
                            "cpa", " cfo", "vp finance", "head finance",
                            "finance head", "specialty finance",
                            "investment banking", "investment banker",
                            "investment associate", "private equity")):
        return ("Finance & Accounting", "Financial Services")
    # AI / ML (title implies industry)
    if any(k in t for k in ("ai engineer", "ml engineer", "machine learning",
                            "artificial intelligence", "staff ai",
                            "ai developer")):
        return ("AI / ML Engineering", "AI / ML")
    # Engineering & Tech (title implies Tech / SaaS) — software / data /
    # cloud roles that carry a genuine SaaS vertical signal.
    if any(k in t for k in ("solutions architect", "solution architect",
                            "platform engineer", "staff engineer",
                            "principal engineer",
                            "aws", "application developer", ".net",
                            "full stack", "fullstack", "backend", "frontend",
                            "full-stack", "embedded", "devops",
                            "software engineer", "dynamics", "ms dynamics",
                            "power bi", "bi trainer", "applications engineer",
                            "sre", "data engineer", "data pipeline",
                            "etl engineer", "java developer", "ui/ux",
                            "network technician",
                            "software developer", "software architect",
                            "web developer", "frontend developer",
                            "front-end developer", "backend developer",
                            "back-end developer", "mobile developer",
                            "ios developer", "android developer",
                            "python developer", "golang", "node developer",
                            "react developer", "ruby developer",
                            "php developer", "c# developer", "c++ developer",
                            "salesforce developer", "salesforce admin",
                            "sharepoint developer", "cloud engineer",
                            "cloud architect", "site reliability",
                            "data scientist", "data architect",
                            "machine learning engineer", "qa automation",
                            "test automation", "sdet", "scrum master",
                            "product owner", "technical lead",
                            "tech lead", "engineering lead")):
        return ("Engineering & Tech", "Tech / SaaS / VC-backed")
    # Industrial / Engineering (title implies Manufacturing)
    if any(k in t for k in ("electrical engineer", "electrical design",
                            "tooling design", "industrial engineer",
                            "stress engineer", "optical test", "transmission",
                            "hydrometallurgist", "solar engineering",
                            "utility scale", "production manager",
                            "manufacturing engineer", "process engineer",
                            "production engineer", "quality engineer",
                            "quality manager", "quality assurance manager",
                            "manufacturing manager", "plant manager",
                            "plant controller", "plant engineer",
                            "maintenance technician", "maintenance manager",
                            "machine operator", "machinist", "cnc ",
                            "tool and die", "fabricat", "welding", "welder",
                            "assembly", "production supervisor",
                            "manufacturing supervisor", "shop supervisor",
                            "ehs ", "ehs manager", "continuous improvement",
                            "lean manufacturing", "supplier quality",
                            "mechanical design engineer")):
        return ("Industrial / Engineering", "Manufacturing / Construction")
    # Cybersecurity (title implies industry)
    if any(k in t for k in ("cyber security", "cybersecurity",
                            "security architect", "infrastructure security",
                            "security engineer", "security consultant",
                            "security analyst", "soc analyst", "penetration",
                            "pen tester", "incident response",
                            "information security", "infosec", "appsec",
                            "vulnerability", "ciso")):
        return ("Cybersecurity", "Cybersecurity")
    # Engineering & Tech — FUNCTION ONLY (industry stays ambiguous).
    # Network/systems/hardware/QA engineering and generic IT roles carry a
    # clear job-function signal but NOT a vertical (they exist in every
    # industry), so we resolve the function and leave industry None — the
    # cascade keeps these as Cross-functional on the industry axis, which is
    # correct, while pulling them OUT of the Cross-functional job-function
    # bucket.
    if any(k in t for k in ("network engineer", "systems engineer",
                            "system engineer", "hardware engineer",
                            "firmware engineer", "qa engineer",
                            "quality assurance engineer", "test engineer",
                            "automation engineer", "controls engineer",
                            "field engineer", "support engineer",
                            "implementation engineer", "integration engineer",
                            "product engineer", "design engineer",
                            "rf engineer", "validation engineer",
                            "engineering manager", "engineering director",
                            "engineering lead", "director of engineering",
                            "vp of engineering", "vp engineering",
                            "head of engineering", "chief engineer",
                            "principal architect", "enterprise architect",
                            "technical architect", "infrastructure engineer",
                            "system administrator", "systems administrator",
                            "network administrator", "database administrator",
                            "dba", "it manager", "it director",
                            "it operations", "it support", "help desk",
                            "helpdesk", "desktop support", "developer",
                            "programmer", "qa analyst", "qa tester",
                            "technical analyst", "technical writer")):
        return ("Engineering & Tech", None)
    # Legal (title implies industry)
    if any(k in t for k in ("counsel", "attorney", "paralegal")):
        return ("Legal", "Legal")
    # Executive Leadership — industry-agnostic
    if re.search(r"\b(cto|cio|coo|cfo|cro|ceo|svp|evp|president|"
                 r"executive director|growth vp|head of|reach vp|"
                 r"finance head)\b", t):
        return ("Executive Leadership", None)
    # Industry-tied sales
    if "charter sales" in t:
        return ("Sales & BD", "Aviation / Aerospace")
    if "padel" in t:
        return ("Operations & PM", "Retail / Consumer / CPG")
    if "mdu sales" in t:
        return ("Sales & BD", "Telecom")
    if "distribution center" in t:
        return ("Operations & PM", "Logistics / Transportation / Distribution")
    # Industry-agnostic — Sales & BD
    if any(k in t for k in ("account executive", "sales", "client manager",
                            "client service", "business development",
                            "business developer", "enterprise account",
                            "key account", "sales engineer", "sales advisor",
                            "sales executive", "independent sales",
                            "sr. account", "alliances director",
                            "commercial director", "client success",
                            "account manager",
                            "account director", "account management",
                            "strategic account", "named account",
                            "territory manager", "territory sales",
                            "regional sales", "national account",
                            "channel sales", "channel manager",
                            "channel partner", "partnerships director",
                            "partner manager", "partnerships manager",
                            "gtm ", "go-to-market", "go to market",
                            "revenue officer", "revenue manager",
                            "revenue lead", "customer success",
                            "customer engagement", "customer experience",
                            "client partner", "client director",
                            "client engagement", "acquisitions director",
                            "acquisition manager", "inside sales",
                            "field sales", "outside sales",
                            "solutions consultant", "solution consultant",
                            "pre-sales", "presales", "deal desk",
                            "sales operations", "sales enablement",
                            "sales development", "sdr ", "bdr ",
                            "relationship manager", "relationship director",
                            "client relationship")):
        return ("Sales & BD", None)
    # Industry-agnostic — Marketing
    if any(k in t for k in ("marketing", "digital sales",
                            "strategic partnerships",
                            "partnerships ambassador", "advancement",
                            "tiktok", "social media",
                            "brand manager", "brand director", "branding",
                            "content manager", "content strategist",
                            "content marketing", "communications manager",
                            "communications director", "public relations",
                            "pr manager", "demand generation", "demand gen",
                            "growth marketing", "performance marketing",
                            "seo ", "sem ", "paid media", "media buyer",
                            "campaign manager", "product marketing",
                            "field marketing", "events manager",
                            "event manager", "event coordinator",
                            "creative director", "copywriter",
                            "direct response", "influencer",
                            "marketing communications", "cmo")):
        return ("Marketing", None)
    # Industry-agnostic — Operations & PM
    if any(k in t for k in ("project manager", "program manager", "operations",
                            "site manager", "docket", "general manager",
                            "repair project", "club manager", "shift manager",
                            "operations supervisor", "operations director",
                            "business analyst", "product manager",
                            "technical manager", "admissions",
                            "assistant manager", "business operations",
                            "business operations support", "ops manager",
                            "operations support",
                            "project engineer", "project lead",
                            "project coordinator", "project director",
                            "project development", "program director",
                            "program coordinator", "product owner",
                            "product director", "portfolio manager",
                            "delivery manager", "delivery lead",
                            "service delivery", "managing director",
                            "office manager", "facilities manager",
                            "facility manager", "facilities director",
                            "branch manager", "store manager",
                            "district manager", "regional manager",
                            "area manager", "plant supervisor",
                            "logistics manager", "supply chain",
                            "procurement", "purchasing manager",
                            "buyer", "inventory manager", "warehouse manager",
                            "distribution manager", "fulfillment",
                            "dispatch", "scheduler", "planner",
                            "coordinator", "supervisor", "administrator",
                            "executive assistant", "administrative assistant",
                            "office administrator", "community manager",
                            "concierge", "service manager",
                            "customer service", "customer operations",
                            "contracts manager", "contract manager",
                            "contract administrator", "vendor manager",
                            "category manager", "transformation",
                            "business intelligence", "business analytics",
                            "data analyst", "operations analyst",
                            "operations associate", "operations lead",
                            "operations engineer", "operations specialist",
                            "operations coordinator", "process improvement")):
        return ("Operations & PM", None)
    # Industry-agnostic — Compliance / Risk
    if "compliance" in t or "risk manager" in t or "risk analyst" in t \
            or "risk officer" in t or "risk director" in t \
            or "regulatory" in t or "regulatory affairs" in t \
            or "internal audit" in t or "governance" in t \
            or "quality compliance" in t:
        return ("Compliance / Risk", None)
    # Industry-agnostic — HR / Talent
    if any(k in t for k in ("human resources", " hr ", " hr/", " hr,",
                            "hr generalist", "hr director", "hr manager",
                            "hr business partner", "hr coordinator",
                            "recruiter", "recruiting", "talent acquisition",
                            "people operations", "people partner",
                            "chief people",
                            "hr officer", "hr specialist", "hr partner",
                            "hr lead", "human capital", "talent management",
                            "talent development", "learning and development",
                            "l&d ", "organizational development",
                            "compensation", "benefits manager",
                            "total rewards", "employee experience",
                            "employee relations", "people & culture",
                            "people and culture", "chro", "head of people",
                            "head of talent", "vp people", "vp of people")):
        return ("HR / Talent", None)
    # No generic / catch-all job-function bucket. Titles that don't match a
    # named function fall through to the CROSS_FUNCTIONAL_FUNCTION label
    # below, mirroring the industry side.
    return (CROSS_FUNCTIONAL_FUNCTION, None)


# ── Job-title → industry hint (thin wrapper around classify_title) ──

def title_industry_hint(title: str | None) -> str | None:
    """Industry hint derived from a job title — convenience wrapper.

    classify_title() now returns (job_function, title_industry) as a tuple
    matching the baseline. This helper exposes just the industry half so the
    dim_promote cascade can stay readable.
    """
    if not title:
        return None
    _func, industry = classify_title(title)
    return industry


# ── Subject / campaign-name → industry hint ─────────────────────────

def subject_industry_hint(subject: str | None) -> str | None:
    """Pull an industry signal out of a Missive thread subject or campaign
    name. Returns None when nothing matches.

    Recruiter-scrape campaigns generate subjects like
    "seattle finance manager" or "san diego patent prosecution paralegal".
    These map cleanly to a vertical via the same keyword space classify_industry
    already uses — we just want the industry bucket, not the technique bucket.
    """
    if not subject:
        return None
    t = _clean_title(subject).lower()
    if not t:
        return None
    # Strip OOO / auto-reply prefixes
    t = re.sub(r"^(automatic reply|out of office|auto-reply)\s*[:\-]?\s*",
               "", t)
    # Direct keyword passes (re-uses classify_industry vocabulary plus
    # role-word → vertical mapping for recruiter-scrape subjects)
    if any(k in t for k in ("patient", "clinical", "nurse", "physician",
                            "pharma", "medical", "healthcare", "health care",
                            "behavior analyst", "psych", "dentist",
                            "veterinarian",
                            "surgical tech", "surgical technologist",
                            "surgeon", "sonographer", "radiolog",
                            "phlebotom", "anesthe", "respiratory therap",
                            "occupational therap", "physical therap",
                            "speech therap", "athletic trainer",
                            "dietitian", "dietician", "nutritionist",
                            "nursing", "caregiver", "care coordinator",
                            "revenue cycle", "medical assistant",
                            "medical coder", "medical biller",
                            "medical billing", "clinical operations",
                            "epidemiolog", "oncology", "cardiology",
                            "orthopedic", "pediatric", "hospice",
                            "home health", "assisted living", "infusion",
                            "behavioral health")):
        return "Healthcare / Life Sciences"
    if any(k in t for k in ("paralegal", "attorney", "counsel", "patent",
                            "litigation", "legal ", "lawyer", "law clerk",
                            "compliance counsel", "general counsel")):
        return "Legal"
    if any(k in t for k in ("underwriter", "underwriting", "actuary",
                            "actuarial", "insurance", "insurtech",
                            "commercial lines", "personal lines", "surety",
                            "claims adjuster", "claims examiner",
                            "claims specialist", "claims representative",
                            "claims processor", "claims analyst", "adjuster",
                            "loss control", "reinsurance", "policy admin")):
        return "Insurance / InsurTech"
    if any(k in t for k in ("mortgage", "real estate", "appraiser",
                            "property manager", "leasing")):
        return "Real Estate / PropTech"
    if any(k in t for k in ("finance", "accounting", "accountant",
                            "controller", "treasury", "auditor", "audit ",
                            "banker", "banking", "credit analyst",
                            "tax manager", "fp&a", "investment banker",
                            "wealth advisor", "private equity",
                            "financial advisor", "financial planner",
                            "loan officer", "loan originator",
                            "mortgage loan", "financial analyst")):
        return "Financial Services"
    if any(k in t for k in ("software engineer", "data engineer", "devops",
                            "sre", "full stack", "fullstack", "backend",
                            "frontend", "platform engineer", ".net",
                            "java developer", "solutions architect",
                            "applications engineer")):
        return "Tech / SaaS / VC-backed"
    if any(k in t for k in ("ai engineer", "ml engineer", "machine learning",
                            "ai developer", "artificial intelligence")):
        return "AI / ML"
    if any(k in t for k in ("security engineer", "cyber", "security architect",
                            "infosec")):
        return "Cybersecurity"
    if any(k in t for k in ("construction", "architect", "estimator",
                            "superintendent", "land surveyor", "civil engineer",
                            "structural engineer")):
        return "Construction / AEC"
    if any(k in t for k in ("aviation", "aircraft", "pilot", "avionics",
                            "completions centre", "completion center",
                            "airline", "jet")):
        return "Aviation / Aerospace"
    if any(k in t for k in ("manufacturing", "industrial engineer",
                            "production manager", "plant manager",
                            "machining")):
        return "Manufacturing / Construction"
    if any(k in t for k in ("logistics", "supply chain", "warehouse",
                            "distribution center", "transportation",
                            "trucking", "freight")):
        return "Logistics / Transportation / Distribution"
    if any(k in t for k in ("media", "broadcast", "streaming", "publish",
                            "production", "studio", "vfx", "digital media")):
        return "Media / Streaming / Entertainment"
    if any(k in t for k in ("retail", "consumer", "cpg", "apparel",
                            "merchandising")):
        return "Retail / Consumer / CPG"
    if any(k in t for k in ("education", "edtech", "k-12", "k12",
                            "principal", "teacher", "professor", "campus")):
        return "EdTech / Education"
    if any(k in t for k in ("food", "beverage", "grocery", "f&b",
                            "restaurant", "kitchen")):
        return "Food / Beverage / Grocery"
    if any(k in t for k in ("energy", "solar", "wind", "utility",
                            "utilities", "oil ", "gas ", "petroleum")):
        return "Energy / Utilities"
    if any(k in t for k in ("telecom", "wireless", "broadband")):
        return "Telecom"
    if any(k in t for k in ("government", "gov ", "govtech", "municipal",
                            "county ", "state of")):
        return "Government / GovTech"
    if any(k in t for k in ("defense", "isr ", "tactical")):
        return "Defense / ISR"
    if any(k in t for k in ("marketing", "advertising", "social media",
                            "branding")):
        return "Marketing / Affiliate Platforms"
    return None


def campaign_industry_hint(campaign_name: str | None) -> str | None:
    """Industry hint from a PV campaign name.

    classify_industry now already returns None for technique-bucket
    campaigns (Recruiter Scrape, Master Recruitment, Reactivation, generic
    Job Scrape). For those we additionally peek at subject_industry_hint
    on the campaign name itself — recruiter-scrape names like
    "Recruiter Scrape | Healthcare Tech | Apr 24" carry the vertical in
    the pipe-separated segments.
    """
    if not campaign_name:
        return None
    industry = classify_industry(campaign_name)
    if industry:
        return industry
    return subject_industry_hint(campaign_name)


# Cross-functional fallback label — verbatim from the baseline. Used by
# dim_promote when a thread/lead exists in the outbound funnel but no
# domain / title / subject / campaign hint produced a vertical signal.
CROSS_FUNCTIONAL_LABEL = "Cross-functional Roles (industry-ambiguous)"
# Same idea on the job-function side — no generic catch-all bucket
# ("Other Specialist") allowed.
CROSS_FUNCTIONAL_FUNCTION = "Cross-functional"
# We never surface a literal "Other / Unclassified" bucket on the dashboard.
# Orphan contacts (no funnel presence) fall through to the same residual
# bucket the baseline used for un-industry-specific outbound. The
# contact_dim.in_missive / in_pv flags still distinguish them downstream.
ORPHAN_LABEL = CROSS_FUNCTIONAL_LABEL
