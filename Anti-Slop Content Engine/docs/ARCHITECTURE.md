# Architecture

A deeper view of the Anti-Slop Content Engine than the top-level [README](../README.md). This documents the moving parts, the control flow, and the design rationale — without reproducing the proprietary rule corpus (see [`../references/REDACTED.md`](../references/REDACTED.md)).

---

## 1. The contract: brief in, gated pipeline out

The engine refuses to start without a filled-out brief. The brief is the **only** place per-piece specifications live; the reference corpus holds **only** rules universal to every piece. That separation is the spine of the whole design — it's what lets one engine serve many clients without any client leaking into the rules.

### Required brief sections

| # | Section | Framework lens | Drives |
|---|---|---|---|
| 1 | Engagement metadata | — | client, date, deliverable, deadline |
| 2 | The piece itself | — | target keyword(s), format, length, required sections, deliverable format |
| 3 | The reader | Schwartz | awareness level, sophistication stage, one-line reader portrait → **funnel routing** |
| 4 | The positioning | Dunford | competitive alternatives, unique attributes, proof, market category, positioning style |
| 5 | The persuasion plan | Cialdini | primary + secondary lever + an ethical-line check |
| 6 | Voice overrides | — | client tone, forbidden words, required phrasings, optional exemplar-writer + intensity |
| 7 | CTA & conversion | — | primary CTA, placement, friction reducers |
| 8 | Operator notes | — | anything ad-hoc |

If any required field is left as a `<fill in>` placeholder, Stage 1 stops and flags it. There are **no engine defaults** for any of the above — silence in the brief is an error, not a default.

---

## 2. The five stages

Every stage ends at an **operator gate** — explicit human approval before the next stage runs.

### Stage 1 — Brief audit
Reads the brief, restates it back in the engine's own words, and flags ambiguities, missing fields, internal contradictions (e.g. a jaded/Stage-5-sophistication reader paired with a naïve direct-claim positioning style), anti-positioning risks, and scope-vs-length mismatches. Runs the **funnel router** on §3 to decide which playbook (if any) Stage 4 will overlay. Deliverable: an audit memo.

### Stage 2 — Research
Follows the research methodology to build three artifacts:
- **competitor map** (including the status-quo / DIY "non-consumption" alternative),
- **proof file** (every claim source-traced),
- **reader portrait**.

Captures ≥10 verbatim customer phrases (real language beats invented language), finds ≥1 disconfirming voice (to avoid a one-sided piece), and verifies the brief's persuasion-lever ethical conditions. Output: a dossier with 3–5 non-obvious insight candidates the operator chooses from. Hard rules: no invented facts, no AI-sourced "sources."

### Stage 3 — Outline
Section structure (H2/H3) per the brief's required-sections / format / length. Each section carries a **claim + evidence anchor (from the dossier) + persuasion lever (from the brief)**. CTA placed per brief §7. If the brief's format matches a known page type, a structure template governs the section spine and per-section persuasion job.

### Stage 4 — Draft
Produces the full draft. Loads — **conditionally, per brief** — the universal voice rules, headline craft, lead craft, the runtime anti-slop ruleset, output-format conventions, the matching structure template, the matching funnel playbook (TOFU/MOFU) if applicable, and a named-writer cadence fingerprint if the brief specifies one. Every claim either traces to the dossier or is tagged `[CLAIM:UNVERIFIED]` for the operator.

### Stage 5 — Critic + revise
The three-layer validation gate. See [§4](#4-the-three-layer-validation-gate).

### Ship
Approved draft is archived to `past_pieces/` with operator notes appended after a `<!-- SCORE_END -->` marker (so the deterministic checker can re-score archived pieces without false-positiving on the appended metadata).

---

## 3. Context-loading discipline

In an LLM agent, the context window is the scarce resource. The engine treats reference loading as a per-stage budget, not a one-time dump:

| Reference category | Loaded in | Notes |
|---|---|---|
| Marketing principles | Stage 2, 3, critic | universal frames |
| Research methodology | Stage 2 | |
| Voice / headlines / leads / anti-slop (runtime) | Stage 4 + critic | |
| Output-format conventions | Stage 4 | |
| Structure template (1 of N) | Stage 4 | **conditional** on brief §2 format |
| Funnel playbook (TOFU or MOFU) | Stage 4 | **conditional** on brief §3 awareness |
| Writer cadence fingerprint (1 of 11) | Stage 4 | **conditional** on brief §6 |
| Sourcing recipe (1 of N verticals) | Stage 2 | **conditional** on brief vertical |
| Full inherited ruleset (thousands of lines) | **never at runtime** | operator-only archive |

The rule: load the smallest correct set for the current stage, never everything up front.

---

## 4. The three-layer validation gate

Cheapest check first; an expensive check never runs on a draft a cheaper one rejects.

### 4a — Deterministic (`score_draft.py`)
Pure stdlib, sub-100ms. One function per rule; a runner aggregates `RuleResult`s into FAIL/WARN/PASS counts and an exit code:

```
exit 0 = all pass            → proceed to 4b
exit 1 = ≥1 hard FAIL        → revise & re-run (no LLM tokens spent)
exit 2 = WARNs only          → operator decides whether to proceed
```

Rule families: em-dash rate cap, banned phrases/openers (data-driven), alliterative tricolons, passive-voice spine (heuristic), sentence-length *burstiness*, paragraph-length uniformity, and ~15 named structural slop patterns (throat-clearing intros, redefinition trope, self-reference closers, "this is where X comes in," vague CTA closers, vendor-neutral mush, category-explainer padding, …). The rule *names* describe the categories; the rule *contents* are withheld.

### 4b — LLM critic (isolated subagent)
Spawned via the agent runtime's Task tool with a **deliberately minimal context** — exactly six inputs:

1. the brief,
2. the draft under review,
3. the runtime anti-slop ruleset,
4. the voice rules,
5. the marketing principles (its strategic-coherence checks depend on this),
6. its own system prompt.

It does **not** see the research dossier, the outline rationale, prior drafts, operator chat, or the voice-evolution notes. It returns scored findings (rule ID, severity BLOCKER/SERIOUS/MINOR, line refs, suggested fix) and a verdict:

- **APPROVE** → proceed to 4c.
- **REVISE** → writer revises, re-runs 4a → 4b. Capped at **N=3**; after the third unresolved REVISE, escalate with a "critic and writer disagree" report rather than looping forever.
- **REJECT** → do not loop at the draft level; the piece goes back to Stage 3 (outline) or Stage 1 (brief).

### 4c — External detectors (`detect_ai.py`)
Submits the draft to whichever of Originality.AI / GPTZero / Pangram have keys configured; gates on a configurable AI% target (default ≤50%).

```
exit 0 = every queried service ≤ target   → ship-ready
exit 1 = ≥1 service flagged                → revise (re-enter 4b with the report) & re-run
exit 2 = no service available              → operator decides whether to ship unverified
```

---

## 5. State model

The engine is **stateless between pieces** except for a small `state/` directory:

- `current_brief.md` — the per-piece brief; the operator overwrites it each engagement. This is the gate file: no brief, no run.
- `voice_evolution.md` — operator-meta (evolution protocol, open questions, negative scope, dated learnings appended after accepted pieces). **Read-only at runtime; operator-edited only.** The voice *baseline* lives in the voice rules; this file holds only what mutates.
- `past_pieces/` — accepted pieces, archived read-only for reference.

Nothing else mutates at runtime. The engine never auto-updates its voice profile, banned-phrase lists, or any reference file — that's a deliberate guardrail against an agent silently drifting its own rules.

---

## 6. Why these choices

| Decision | Why | Tradeoff accepted |
|---|---|---|
| Brief is the only per-piece truth | reusable across clients; rules stay universal | operator must fully specify each brief |
| Deterministic gate before LLM gate | free check kills cheap failures; saves tokens & detector $ | regex can't judge meaning — scoped to mechanical tells |
| Critic in isolated context | prevents the critic going soft on the writer's own prose | extra subagent round-trip per revise loop |
| Phase-loaded references | preserves a coherent context over a long run | more orchestration logic than "load everything" |
| Operator gate at every stage | human owns the judgment calls and the ship decision | not autonomous; throughput bounded by operator |
| Pluggable detector registry | add a vendor in two edits; keys env-only | vendor response drift handled by typed errors |
| Funnel-aware, BOFU-default | one engine spans the funnel | TOFU/MOFU exemplar depth is honestly thinner |

---

See [`pipeline_contract.md`](pipeline_contract.md) for the sanitized stage-by-stage contract, and [`../references/REDACTED.md`](../references/REDACTED.md) for the inventory of withheld proprietary material.
