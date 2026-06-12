"""
=============================================================================
product_code_legend.py  —  Software-significant FDA classification reference
=============================================================================
Internal GTM tooling built at Astris Partners.

Single source of truth for two things the rest of the pipeline imports:

  1. PRODUCT_CODES        — the API-pull whitelist (used by fda_510k_scraper.py)
  2. REGULATION_WHITELIST — the CFR-21 regulation-prefix whitelist used by the
                            bulk TAM build (recon_v5.py)

The thesis behind every entry: a device belongs on this list only if it carries
a real IEC 62304 + ISO 14971 *software* burden — software making clinical
decisions, continuously processing patient data, or structuring the
requirement -> implementation -> test traceability chain. Pure hardware with
incidental firmware does not qualify.

Run it directly to print the full reference:
    python3 product_code_legend.py

-----------------------------------------------------------------------------
SANITIZATION NOTE
-----------------------------------------------------------------------------
No secrets or client data appear in this file. Client references are stubbed to
the fictional "Inferon Health"; the employer (Astris Partners) is shown by
request. Logic, codes, and CFR mappings are unchanged from production.
=============================================================================
"""

# -----------------------------------------------------------------------------
# 1. PRODUCT CODES  —  the API-pull whitelist
# -----------------------------------------------------------------------------
# These three-letter codes are the "known SaMD" set used for targeted API pulls
# (fda_510k_scraper.py). The bulk TAM build does NOT rely on this list — it works
# off regulation prefixes (section 2) so it catches codes we never hand-curated.
#
# desc / regulation / examples are documentation only; the scraper consumes the
# keys of PRODUCT_CODE_DETAIL.

PRODUCT_CODE_DETAIL = {
    # ---- Radiology / imaging AI ----
    "QIH": {"desc": "Radiological image processing software (AI/ML CADe/CADx)", "reg": "892.2050", "ex": "Aidoc, Viz.ai"},
    "QFM": {"desc": "Radiological computer-assisted prioritisation software (ML triage)", "reg": "892.2080", "ex": "triage/notification"},
    "QAS": {"desc": "Radiological computer-assisted triage & notification", "reg": "892.2080", "ex": "stroke/LVO triage"},
    "MYN": {"desc": "Medical image analyzer / CADe", "reg": "892.2050", "ex": "mammography CAD"},
    "QDQ": {"desc": "CADe/CADx for breast cancer", "reg": "892.2090", "ex": "breast density / lesion CAD"},
    "QBS": {"desc": "CADe/CADx for bony fractures", "reg": "892.2090", "ex": "fracture detection"},
    "POK": {"desc": "CAD software for cancer lesions", "reg": "892.2090", "ex": "lung nodule CAD"},
    "LLZ": {"desc": "Medical image management / PACS workstation", "reg": "892.2020", "ex": "PACS, viewers"},
    "QJU": {"desc": "AI-guided medical image acquisition", "reg": "892.2100", "ex": "scan guidance"},
    "NFJ": {"desc": "Ophthalmic image management system", "reg": "886.1100", "ex": "retinal image mgmt"},
    "PIB": {"desc": "Retinal diagnostic software (autonomous AI)", "reg": "886.1100", "ex": "diabetic retinopathy"},

    # ---- Pathology ----
    "QKQ": {"desc": "Whole-slide imaging system", "reg": "864.3700", "ex": "digital pathology scanners"},
    "PZZ": {"desc": "Digital pathology image analysis software", "reg": "864.3750", "ex": "AI pathology"},

    # ---- Cardiology / neuro signal software ----
    "DXZ": {"desc": "Electrocardiograph analysis software", "reg": "870.2340", "ex": "ECG interpretation"},
    "DPS": {"desc": "Electrocardiograph", "reg": "870.2340", "ex": "ECG hardware+sw"},
    "QHA": {"desc": "Coronary vascular physiologic simulation software", "reg": "870.1290", "ex": "FFR-CT"},
    "OLW": {"desc": "EEG index-generating software", "reg": "882.1400", "ex": "depth-of-anesthesia index"},
    "DQK": {"desc": "Diagnostic computer, programmable", "reg": "870.2400", "ex": "programmable diagnostics"},

    # ---- Remote / general monitoring ----
    "DSI": {"desc": "Cardiac monitor / arrhythmia detector software", "reg": "870.2920", "ex": "mobile cardiac telemetry"},
    "PSY": {"desc": "Remote patient physiological monitor software", "reg": "870.2910", "ex": "RPM platforms"},
    "MWJ": {"desc": "Ambulatory / Holter analysis software", "reg": "870.2800", "ex": "Holter analysis"},
    "PLR": {"desc": "Photoplethysmograph analysis software", "reg": "870.2700", "ex": "SpO2 / PPG"},
    "GWO": {"desc": "Arrhythmia detector and alarm", "reg": "870.1025", "ex": "monitor alarms"},
    "QNP": {"desc": "Clinical decision support software", "reg": "892.2070", "ex": "CDS"},
}

PRODUCT_CODES = sorted(PRODUCT_CODE_DETAIL.keys())

# -----------------------------------------------------------------------------
# 2. REGULATION WHITELIST  —  the bulk-TAM net
# -----------------------------------------------------------------------------
# Matching is by CFR-21 regulation-number *prefix* on each cleared filing's
# openfda.regulation_number. A hand-curated three-letter code list always lags
# new codes (FDA mints them constantly for novel SaMD); the regulation taxonomy
# is structural and stable — 892.x is radiology, 870.x cardiac, 862.x clinical
# chemistry, etc. We whitelist whole branches and carve out the hardware-only
# sub-sections via EXCLUDE_PREFIXES below.
#
# >>> If exact membership matters for an audit, diff this dict against the
# >>> canonical recon_v5 you ran locally — versions iterated v1..v5.

REGULATION_WHITELIST = {
    # Radiology — imaging, CAD, PACS, AI image analysis (all of 892 except sources)
    "892": "Radiology — imaging / CAD / PACS / AI image analysis",
    # Cardiovascular — monitors, ECG, implants, hemodynamic (drop catheters, see EXCLUDE)
    "870": "Cardiovascular — monitors / ECG / implantable / hemodynamic",
    # Neurology — EEG, neuro monitoring & stimulation
    "882": "Neurology — EEG / neuro monitoring / stimulation",
    # Clinical chemistry & toxicology analyzers (IVD instruments)
    "862.1": "Clinical chemistry — test systems / analyzers",
    "862.2": "Clinical lab instruments",
    "862.3": "Clinical toxicology test systems",
    # Hematology analyzers & systems
    "864.5": "Hematology — automated cell-counting / analyzers",
    "864.7": "Hematology test kits / systems",
    "864.3": "Pathology — whole-slide imaging / image analysis",
    # Microbiology / immunology instruments & systems
    "866.2": "Microbiology — culture / identification instruments",
    "866.3": "Microbiology test systems",
    "866.5": "Immunology test systems",
    # GI / urology endoscopy — scopes, video processors, capsule endoscopy
    "876.1": "GI/Urology — endoscopes / video processors / capsule endoscopy",
    # OB/GYN diagnostic & monitoring
    "884.2": "OB/GYN — diagnostic / monitoring devices",
    # ENT — hearing aids, cochlear, audiometric software
    "874.3": "ENT — hearing aids / cochlear / audiometric software",
    # General hospital — infusion pumps, multiparameter patient monitors
    "880.5": "General hospital — infusion pumps / patient monitors",
    # Anesthesiology — ventilators, anesthesia machines (software-controlled)
    "868.5": "Anesthesiology — ventilators / anesthesia delivery",
    # Ophthalmic — diagnostic imaging + software-controlled surgical lasers
    "886.1": "Ophthalmic — diagnostic imaging / analysis",
    "886.4": "Ophthalmic surgical — software-controlled lasers / systems",
    # Orthopedic SURGICAL — navigation / robotics (NOT implants, see note)
    "888.4": "Orthopedic surgical — navigation / robotics",
    # Physical medicine — powered prosthetics / exoskeletons
    "890.3": "Physical medicine — powered prosthetics / exoskeletons",
}

# Carve-outs: more-specific prefixes that fall *inside* a whitelisted branch but
# are hardware-only with no meaningful software lifecycle.
EXCLUDE_PREFIXES = {
    "870.1": "Cardiac catheters / introducers — hardware, no SW lifecycle",
    "892.57": "Radioactive sources / therapy hardware",
}

# Branches deliberately NOT whitelisted (documented so the choice is auditable):
#   888.3  Orthopedic IMPLANTS          — passive hardware
#   872.x  Dental                       — hardware / materials
#   878.x  General & plastic surgery    — surgical hardware / sutures
#   862.x reagent-only assays           — thin SW; tolerated as TAM noise, not targeted
# Notable single-device exclusions seen during validation:
#   QZS    DermaSensor                  — cleared via De Novo, not 510(k); separate DB
#   Surgical navigation HARDWARE jigs   — kept only where software-controlled (888.4)


# -----------------------------------------------------------------------------
# 3. DECISION CODES  —  what counts as "cleared"
# -----------------------------------------------------------------------------
# decision_date marks ANY decision, not a clearance. The cleared set is the
# "substantially equivalent" family (decision_code starting with "SE").
DECISION_CODES = {
    "SESE": ("Substantially Equivalent", True),
    "SESD": ("SE — different technological characteristics", True),
    "SESI": ("SE — with conditions / indications", True),
    "SESU": ("SE — subject to limitations", True),
    "SEKN": ("SE — by applicant notification (Special)", True),
    "NESE": ("Not Substantially Equivalent (denied)", False),
    "NSED": ("Not SE — different", False),
    "DENG": ("Denied", False),
}

CLEARED_PREFIX = "SE"  # decision_code.startswith("SE") == cleared


def is_cleared(decision_code: str) -> bool:
    """A filing is cleared iff its decision_code is in the SE family."""
    return bool(decision_code) and decision_code.upper().startswith(CLEARED_PREFIX)


if __name__ == "__main__":
    line = "=" * 78
    print(line)
    print("SOFTWARE-SIGNIFICANT FDA CLASSIFICATION REFERENCE")
    print("Astris Partners — internal GTM tooling")
    print(line)

    print(f"\nAPI-PULL PRODUCT CODES ({len(PRODUCT_CODES)}):\n")
    for code in PRODUCT_CODES:
        d = PRODUCT_CODE_DETAIL[code]
        print(f"  {code}  {d['reg']:<10}  {d['desc']}")
        print(f"        e.g. {d['ex']}")

    print(f"\nREGULATION-PREFIX WHITELIST ({len(REGULATION_WHITELIST)} branches):\n")
    for prefix, label in REGULATION_WHITELIST.items():
        print(f"  {prefix:<8}  {label}")

    print(f"\nCARVE-OUTS ({len(EXCLUDE_PREFIXES)}):\n")
    for prefix, why in EXCLUDE_PREFIXES.items():
        print(f"  {prefix:<8}  {why}")

    print("\nDECISION CODES:\n")
    for code, (desc, cleared) in DECISION_CODES.items():
        mark = "CLEARED" if cleared else "denied"
        print(f"  {code}  [{mark:>7}]  {desc}")
    print(f"\nCleared rule: decision_code.startswith('{CLEARED_PREFIX}')")
    print(line)
