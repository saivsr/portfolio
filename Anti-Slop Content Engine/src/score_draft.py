#!/usr/bin/env python3
"""
score_draft.py — deterministic anti-slop checks for content-engine drafts.

  ┌─────────────────────────────────────────────────────────────────────┐
  │  SANITIZED PORTFOLIO CUT.                                            │
  │  The structure, data types, runner registry, aggregation, exit-code │
  │  contract, and CLI are the REAL implementation. Three representative │
  │  method-only checks (em-dash, burstiness, paragraph uniformity) are  │
  │  shown in full — they leak no proprietary wordlist. The banned-      │
  │  phrase DATA and the pattern-matching check BODIES are redacted and  │
  │  marked  # [REDACTED — withheld from portfolio].                     │
  │  See ../references/REDACTED.md.                                      │
  └─────────────────────────────────────────────────────────────────────┘

Purpose
-------
Post-generation filter for the anti-slop rules that the writer model cannot
reliably enforce by prompt instruction alone (em-dashes being the canonical
case — the em-dash filter cannot be enforced by instruction alone). Runs
*before* the LLM critic so cheap, mechanical failures never cost LLM tokens
or paid detector calls.

Scope
-----
- Pure stdlib (no pip dependencies).
- Regex + simple counting / statistics only. Not an NLP system.
- One function per rule; a runner aggregates results.
- Banned-phrase data lives in BANNED_PHRASES at the top of this file so the
  operator can add/remove rules without editing functions.

CLI
---
    python score_draft.py <draft.md> [--brief <brief.md>] [--json]

Exit codes:
    0 = all PASS
    1 = at least one FAIL (any hard-rule violation)
    2 = no FAIL but at least one WARN

How to extend with a new rule
-----------------------------
1. Banned phrase  → append to the appropriate list in BANNED_PHRASES.
2. Structural rule → write a new check_xxx() returning a RuleResult, then
   register it in RULE_RUNNERS at the bottom.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Rule data — edit here to add / remove banned phrases.
# ---------------------------------------------------------------------------
#
# Each list value is a tuple of (phrase, severity) where severity is
# "fail" or "warn". The check function treats "fail" as a hard rule.
# Phrases are matched case-insensitive with word-boundary regex.
#
# [REDACTED — withheld from portfolio]
# The real dict holds several hundred curated (phrase, severity) entries
# across the rule sections below. Two ILLUSTRATIVE entries per section are
# shown so the data shape is clear; the validated corpus is proprietary.
BANNED_PHRASES: dict[str, list[tuple[str, str]]] = {
    "1a_ai_tells": [
        ("in today's fast-paced world", "fail"),
        ("it's important to note that", "fail"),
        # [REDACTED — N additional entries withheld]
    ],
    "1b_corporate_filler": [
        ("at the end of the day", "warn"),
        # [REDACTED — N additional entries withheld]
    ],
    "3_transition_slop": [
        # [REDACTED — entries withheld]
    ],
    "3m_metaphorical_adverbs": [
        # [REDACTED — entries withheld]
    ],
    # … additional rule sections withheld.
}

# Throat-clearing intros, matched only at the start of a sentence.
# [REDACTED — pattern list withheld]
THROAT_CLEARING_OPENERS: list[str] = []  # populated in the private build

# "In our experience" / "We've found" without backing.
# [REDACTED — pattern withheld]

# Past-participle heuristic for passive-voice detection. Captures common
# -ed endings plus a hand-list of irregulars. A rough heuristic, not a parser.
PASSIVE_AUX = r"\b(?:am|is|are|was|were|be|been|being)\b"
PAST_PARTICIPLE = r"\b\w+ed\b"  # [REDACTED — irregular-verb list trimmed]

# Bracketed exception tag. Lines containing this token bypass banned-phrase
# checks (used when the writer is intentionally quoting AI slop).
QUOTED_TAG = "[QUOTED]"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class RuleResult:
    rule_id: str
    status: str  # "pass" | "warn" | "fail"
    headline: str
    details: list[str] = field(default_factory=list)


@dataclass
class Draft:
    path: Path
    raw: str
    lines: list[str]
    words: list[str]
    word_count: int
    sentences: list[str]
    paragraphs: list[str]


@dataclass
class BriefOverrides:
    # Some rules are tunable per brief — e.g. the em-dash cap is raised for
    # exemplar writers whose authentic voice uses more of them.
    em_dash_cap_per_1000: float = 3.0
    exemplar_writer: str | None = None


# ---------------------------------------------------------------------------
# Loading / tokenising
# ---------------------------------------------------------------------------

WORD_RE = re.compile(r"[A-Za-z0-9']+")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'\(])")

# Archive boundary marker. When present, only text BEFORE this marker is
# scored, so archived past_pieces can carry operator notes / critic snapshots
# without polluting future re-scoring runs.
SCORE_END_MARKER = re.compile(r"<!--\s*SCORE_END\s*-->", re.IGNORECASE)


def _extract_scorable_body(raw: str) -> str:
    """Trim archive metadata after the SCORE_END marker (and apply the
    past-pieces ``## Final draft`` convention). Falls back to whole-file."""
    m = SCORE_END_MARKER.search(raw)
    if m:
        raw = raw[: m.start()]
    # [REDACTED — past-pieces section-convention parsing trimmed]
    return raw


def load_draft(path: Path) -> Draft:
    raw_full = path.read_text(encoding="utf-8")
    raw = _extract_scorable_body(raw_full)
    lines = raw.splitlines()
    words = WORD_RE.findall(raw)
    sentences = [s for s in SENT_SPLIT_RE.split(raw) if s.strip()]
    paragraphs = [p for p in re.split(r"\n\s*\n", raw) if p.strip()]
    return Draft(
        path=path,
        raw=raw,
        lines=lines,
        words=words,
        word_count=len(words),
        sentences=sentences,
        paragraphs=paragraphs,
    )


def load_brief(path: Path | None) -> BriefOverrides:
    """Parse the few brief fields that tune deterministic thresholds
    (em-dash cap, exemplar writer). [REDACTED — field parsing trimmed]"""
    if not path or not path.exists():
        return BriefOverrides()
    return BriefOverrides()  # real build extracts overrides from the brief


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def line_is_quoted(line: str) -> bool:
    """Skip-list: markdown blockquote lines, explicit [QUOTED] tag."""
    stripped = line.lstrip()
    if stripped.startswith(">"):
        return True
    if QUOTED_TAG in line:
        return True
    return False


def find_phrase_hits(draft: Draft, phrase: str) -> list[tuple[int, str]]:
    """All (line_number, snippet) hits for a phrase, respecting the QUOTED
    exception. Case-insensitive, word-boundaried where the phrase ends in
    word chars."""
    escaped = re.escape(phrase)
    left = r"\b" if phrase[:1].isalnum() else ""
    right = r"\b" if phrase[-1:].isalnum() else ""
    pat = re.compile(left + escaped + right, re.IGNORECASE)
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(draft.lines, start=1):
        if line_is_quoted(line):
            continue
        if pat.search(line):
            hits.append((i, line.strip()))
    return hits


def count_em_dashes(text: str) -> int:
    return text.count("—")  # em-dash


# ---------------------------------------------------------------------------
# Rule checks — each returns a RuleResult (or a list of them).
#
# Three method-only checks below are shown IN FULL (no wordlist leaks).
# The remaining checks are present as signatures + docstrings; their bodies
# are withheld and raise NotImplementedError in this portfolio cut.
# ---------------------------------------------------------------------------

def check_em_dashes(draft: Draft, overrides: BriefOverrides) -> RuleResult:
    """§4a — em-dash rate per 1000 words against a brief-tunable cap.

    Shown in full: this is the canonical 'cannot be enforced by prompt'
    rule, and it leaks no proprietary data."""
    n = count_em_dashes(draft.raw)
    rate = (n / draft.word_count * 1000) if draft.word_count else 0
    cap = overrides.em_dash_cap_per_1000
    status = "fail" if rate > cap else "pass"
    headline = f"Em-dashes: {n} ({rate:.2f}/1000 words; cap {cap}/1000)"
    details = []
    if status == "fail":
        for i, line in enumerate(draft.lines, start=1):
            if "—" in line:
                details.append(f"Line {i}: {line.strip()[:120]}")
    return RuleResult("4a_em_dash", status, headline, details)


def check_burstiness(draft: Draft) -> RuleResult:
    """§4ll — sentence-length stddev/mean ratio. Target >= 0.50.

    Shown in full: humans vary sentence length far more than models do.
    Pure statistics, no wordlist."""
    lengths = [len(WORD_RE.findall(s)) for s in draft.sentences]
    lengths = [l for l in lengths if l > 0]
    if len(lengths) < 3:
        return RuleResult("4ll_burstiness", "pass", "Burstiness: n/a (too few sentences)")
    mean = statistics.mean(lengths)
    sd = statistics.pstdev(lengths)
    ratio = (sd / mean) if mean else 0
    status = "pass" if ratio >= 0.50 else "warn"
    headline = (
        f"Burstiness: {ratio:.2f} (target >= 0.50); "
        f"n={len(lengths)} sentences, mean={mean:.1f}, sd={sd:.1f}"
    )
    return RuleResult("4ll_burstiness", status, headline, [])


def check_paragraph_uniformity(draft: Draft) -> RuleResult:
    """§4mm — coefficient of variation of paragraph word counts.

    Shown in full: uniform paragraph lengths are a strong machine tell.
    Pure statistics, no wordlist."""
    counts = [len(WORD_RE.findall(p)) for p in draft.paragraphs]
    counts = [c for c in counts if c > 0]
    if len(counts) < 3:
        return RuleResult("4mm_para_uniform", "pass", "Paragraph uniformity: n/a")
    mean = statistics.mean(counts)
    sd = statistics.pstdev(counts)
    cv = (sd / mean) if mean else 0
    status = "warn" if cv < 0.30 else "pass"
    headline = (
        f"Paragraph CV: {cv:.2f} (target >= 0.30); "
        f"n={len(counts)} paragraphs, mean={mean:.1f}w, sd={sd:.1f}w"
    )
    return RuleResult("4mm_para_uniform", status, headline, [])


# --- The following checks are real in the private build; bodies withheld. ---

def check_banned_phrases(draft: Draft) -> list[RuleResult]:
    """§1 — scan the BANNED_PHRASES corpus, one RuleResult per hit, honoring
    the [QUOTED] / blockquote skip-list via find_phrase_hits()."""
    raise NotImplementedError  # [REDACTED — uses withheld BANNED_PHRASES corpus]


def check_throat_clearing(draft: Draft) -> RuleResult:
    """§2 — throat-clearing intros matched only at sentence start."""
    raise NotImplementedError  # [REDACTED — withheld opener list]


def check_silently_quietly(draft: Draft) -> RuleResult:
    """§3m — metaphorical 'silently/quietly + verb' adverb slop."""
    raise NotImplementedError  # [REDACTED]


def check_tricolons(draft: Draft) -> RuleResult:
    """§4b — alliterative rule-of-three lists (the 'X, Y, and Z' tell)."""
    raise NotImplementedError  # [REDACTED]


def check_passive_voice(draft: Draft) -> RuleResult:
    """§4j — passive-voice spine density via the aux + past-participle
    heuristic. Rough, deliberately not a parser."""
    raise NotImplementedError  # [REDACTED]


def check_most_plural(draft: Draft) -> RuleResult:
    """§3l — 'Most + [plural noun]' without a citation in the next 2
    sentences (URL / number+unit / quote / named entity heuristic)."""
    raise NotImplementedError  # [REDACTED]


def check_experience_claims(draft: Draft) -> RuleResult:
    """§5 — 'in our experience' / 'we've found' with no backing."""
    raise NotImplementedError  # [REDACTED]


def check_more_than_just(draft: Draft) -> RuleResult:
    """§4d — 'more than just a …' construction."""
    raise NotImplementedError  # [REDACTED]


def check_not_only_but_also(draft: Draft) -> RuleResult:
    """§4e — 'not only … but also …' construction."""
    raise NotImplementedError  # [REDACTED]


def check_redefinition_trope(draft: Draft) -> RuleResult:
    """§4d-ext — 'X isn't just Y, it's Z' redefinition trope."""
    raise NotImplementedError  # [REDACTED]


def check_self_reference_closers(draft: Draft) -> RuleResult:
    """§4ll — closers that talk about the article itself."""
    raise NotImplementedError  # [REDACTED]


def check_this_is_where_x(draft: Draft) -> RuleResult:
    """§8a — 'this is where [product] comes in' pivot."""
    raise NotImplementedError  # [REDACTED]


def check_vague_cta_closer(draft: Draft) -> RuleResult:
    """Vague 'get started today' / 'take it to the next level' closers."""
    raise NotImplementedError  # [REDACTED]


def check_vendor_neutral_mush(draft: Draft) -> RuleResult:
    """False-balance / fence-sitting language that won't drive a decision."""
    raise NotImplementedError  # [REDACTED]


def check_category_explainer(draft: Draft) -> RuleResult:
    """Padding that re-explains a category the reader already knows."""
    raise NotImplementedError  # [REDACTED]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
# Each runner is (rule_id, callable). The callable takes (draft, overrides)
# and returns RuleResult OR a list of them. In this portfolio cut the redacted
# checks raise NotImplementedError; run_all() catches that and emits a clearly
# marked "skip" result so the script still runs end-to-end on the three
# method-only checks that ARE shown in full.
RULE_RUNNERS: list[tuple[str, Callable]] = [
    ("4a_em_dash",            lambda d, o: check_em_dashes(d, o)),
    ("1_banned_phrases",      lambda d, o: check_banned_phrases(d)),
    ("2_throat_clearing",     lambda d, o: check_throat_clearing(d)),
    ("3m_silently_quietly",   lambda d, o: check_silently_quietly(d)),
    ("4b_tricolons",          lambda d, o: check_tricolons(d)),
    ("4j_passive_voice",      lambda d, o: check_passive_voice(d)),
    ("4ll_burstiness",        lambda d, o: check_burstiness(d)),
    ("4mm_para_uniform",      lambda d, o: check_paragraph_uniformity(d)),
    ("3l_most_plural",        lambda d, o: check_most_plural(d)),
    ("5_experience_claims",   lambda d, o: check_experience_claims(d)),
    ("4d_more_than_just",     lambda d, o: check_more_than_just(d)),
    ("4e_not_only_but_also",  lambda d, o: check_not_only_but_also(d)),
    ("4d_redefinition",       lambda d, o: check_redefinition_trope(d)),
    ("4ll_self_reference",    lambda d, o: check_self_reference_closers(d)),
    ("8a_this_is_where_x",    lambda d, o: check_this_is_where_x(d)),
    ("cta_vague_closer",      lambda d, o: check_vague_cta_closer(d)),
    ("vendor_neutral_mush",   lambda d, o: check_vendor_neutral_mush(d)),
    ("category_explainer",    lambda d, o: check_category_explainer(d)),
]


def run_all(draft: Draft, overrides: BriefOverrides) -> list[RuleResult]:
    results: list[RuleResult] = []
    for rule_id, runner in RULE_RUNNERS:
        try:
            out = runner(draft, overrides)
        except NotImplementedError:
            # Portfolio cut: redacted check. Marked, not counted toward the gate.
            results.append(RuleResult(rule_id, "skip", f"{rule_id}: [WITHHELD in portfolio cut]"))
            continue
        results.extend(out if isinstance(out, list) else [out])
    return results


def aggregate(results: list[RuleResult]) -> tuple[int, int, int, int]:
    f = sum(1 for r in results if r.status == "fail")
    w = sum(1 for r in results if r.status == "warn")
    p = sum(1 for r in results if r.status == "pass")
    # "skip" results (redacted checks) are ignored by the exit-code contract.
    code = 1 if f > 0 else (2 if w > 0 else 0)
    return f, w, p, code


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

STATUS_TAG = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}


def format_human(draft: Draft, results: list[RuleResult], overrides: BriefOverrides) -> str:
    out: list[str] = [
        "SCORE_DRAFT v1 - anti-slop check",
        f"Draft: {draft.path}  Words: {draft.word_count}",
        "=" * 60,
        "",
    ]
    for r in results:
        out.append(f"{STATUS_TAG[r.status]}  {r.headline}")
        for d in r.details:
            out.append(f"        {d}")
    f, w, p, code = aggregate(results)
    out += ["", f"SUMMARY: {f} FAIL, {w} WARN, {p} PASS", f"Exit code: {code}"]
    return "\n".join(out)


def format_json(draft: Draft, results: list[RuleResult], overrides: BriefOverrides) -> str:
    f, w, p, code = aggregate(results)
    payload = {
        "draft_path": str(draft.path),
        "word_count": draft.word_count,
        "em_dash_cap_per_1000": overrides.em_dash_cap_per_1000,
        "exemplar_writer": overrides.exemplar_writer,
        "results": [
            {"rule_id": r.rule_id, "status": r.status, "headline": r.headline, "details": r.details}
            for r in results
        ],
        "summary": {"fail": f, "warn": w, "pass": p, "exit_code": code},
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Anti-slop draft scorer")
    ap.add_argument("draft", type=Path, help="Path to draft .md file")
    ap.add_argument("--brief", type=Path, default=None, help="Optional brief .md")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args(argv)

    if not args.draft.exists():
        print(f"error: draft not found: {args.draft}", file=sys.stderr)
        return 1

    draft = load_draft(args.draft)
    overrides = load_brief(args.brief)
    results = run_all(draft, overrides)
    print(format_json(draft, results, overrides) if args.json
          else format_human(draft, results, overrides))
    _, _, _, code = aggregate(results)
    return code


if __name__ == "__main__":
    sys.exit(main())
