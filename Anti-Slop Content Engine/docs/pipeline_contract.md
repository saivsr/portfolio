# Pipeline contract (sanitized)

This is a **truncated, sanitized** version of the engine's internal skill contract (`SKILL.md`). It preserves the operational contract — stages, gates, references map, scope boundaries — and redacts the proprietary rule contents those references point to. Withheld files are marked **[WITHHELD]**.

---

## Purpose

Turn a filled-out brief into an agency-grade bottom-of-funnel B2B/SaaS/tech piece. Non-negotiables: (a) reads as genuinely human, (b) passes external AI detectors, (c) demonstrates real domain depth via researched specifics, (d) drives the funnel decision the brief was commissioned for.

## Operator model

One operator. The engine proposes and flags; the operator decides. Every stage stops for explicit approval before the next runs.

## Entry condition

Point of entry is a filled-out brief at `state/current_brief.md`. Without it, the engine refuses to start. The brief is the only place per-piece specs live (length, format, structure, audience, awareness stage, keywords, CTA, voice overrides, positioning frame). The engine has no defaults for any of these — a `<fill in>` left in a required field halts Stage 1.

## References map (universal rules, phase-loaded)

| Reference | Loaded in stage | Contains | Status |
|---|---|---|---|
| `marketing_principles` | 2, 3, critic | Schwartz / Dunford / Cialdini / Hopkins frames | distillation **[WITHHELD]** |
| `research_playbook` | 2 | research methodology, artifact specs, stop criterion | **[WITHHELD]** |
| `voice_rules` | 4, critic | universal voice / tone / craft rules | **[WITHHELD]** |
| `headlines_guide` | 4 | headline patterns, tests, anti-patterns | **[WITHHELD]** |
| `leads_guide` | 4 | lead archetypes with worked examples | **[WITHHELD]** |
| `anti_slop_validated` | 4, critic | runtime anti-slop ruleset (banned phrases, AI tells, structural slop) | **[WITHHELD]** |
| `anti_slop_rules_full` | never at runtime | full inherited ruleset (operator-only archive, thousands of lines) | **[WITHHELD]** |
| `structure_templates/{type}` | 4 (conditional on format) | per-page-type section structure + per-section persuasion job | **[WITHHELD]** |
| `_funnel/router` | 1 | maps awareness level → playbook (TOFU/MOFU/none) | **[WITHHELD]** |
| `_funnel/{tofu,mofu}_playbook` | 4 (conditional on awareness) | funnel-stage operational playbooks | **[WITHHELD]** |
| `critic_system_prompt` | critic subagent only | the critic's system prompt | **[WITHHELD]** |
| `critic_invocation` | writer, before Task call | exact critic invocation + loop semantics | **[WITHHELD]** |
| `output_format` | 4 | deliverable-format conventions | **[WITHHELD]** |
| writer cadence fingerprints (×11) | 4 (conditional on brief §6) | named-writer register/cadence overlays | **[WITHHELD]** |
| `sourcing_recipes/{vertical}` | 2 (conditional) | per-vertical source maps + "do-not-source" lists | **[WITHHELD]** |

References contain **only** rules that apply to every piece regardless of length, format, audience, or topic. Anything brief-dependent lives in the brief, never in a reference.

## Scripts (runtime helpers — included in this portfolio)

| Script | Stage | Purpose |
|---|---|---|
| `score_draft.py` | 5a | deterministic, stdlib regex/statistics anti-slop checker; exit 0/1/2 |
| `detect_ai.py` | 5c | external AI-detection gate (Originality / GPTZero / Pangram); exit 0/1/2 |

## The five stages

| Stage | Engine does | Gate / deliverable |
|---|---|---|
| 1 · Brief audit | restate brief; flag gaps, contradictions, anti-positioning & scope risks; run funnel router | operator confirms or edits brief |
| 2 · Research | build competitor map / proof file / reader portrait; ≥10 verbatim phrases; ≥1 disconfirming voice; verify lever ethics | operator picks insights; approves dossier |
| 3 · Outline | section structure with claim + evidence anchor + persuasion lever per section; CTA placement | operator approves or revises |
| 4 · Draft | full draft to brief spec; phase-loaded refs; every claim traces or flags `[CLAIM:UNVERIFIED]` | operator hands to critic |
| 5 · Critic + revise | 5a deterministic → 5b isolated critic (loop max N=3) → 5c external detectors | operator approves each substage + final |

## The critic subagent

Runs via the Task tool in **isolated context**. Sees exactly six inputs: the brief, the draft, the runtime anti-slop ruleset, the voice rules, the marketing principles, and its own system prompt. It does **not** see the research dossier, outline rationale, prior drafts, operator chat, or voice-evolution notes. Returns scored feedback + APPROVE / REVISE / REJECT. The writer's working memory is never shared with the critic — otherwise the critic goes soft on its own writing.

## State

Stateless between pieces except `state/`: `current_brief.md` (the gate file, overwritten per piece), `voice_evolution.md` (operator-edited meta, read-only at runtime), `past_pieces/` (read-only archive). The engine never auto-mutates any reference or voice file.

## Anti-patterns (enforced)

1. Starting without a brief, or proceeding when a required field is `<fill in>`.
2. Loading all references at once — burns context. Load per stage.
3. Running the critic in the writer's context — defeats the entire mechanism.
4. Inventing facts not in the dossier — every claim traces or flags `[CLAIM:UNVERIFIED]`.
5. Treating reference files as a place to stash brief-specific specs.
6. Skipping operator gates because "the draft looks fine."
7. Auto-updating voice/evolution state — operator-only.
8. Running the LLM critic before the deterministic checker.
9. Shipping without the external-detector gate when API keys are configured.

## One-paragraph contract

Given a filled-out brief, the engine runs a five-stage operator-gated flow — brief audit → research → outline → draft → critic+revise — and produces an agency-grade piece that reads human, passes AI detectors, and demonstrates real domain depth. The brief is the only place per-piece specs live; reference files hold only universal rules. The critic runs as an isolated-context subagent against the brief + universal rules, never against the writer's working memory. The engine refuses to start without a brief, never invents facts, never auto-mutates state, and stops at every gate for operator approval.
