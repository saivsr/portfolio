# Withheld proprietary corpus

This portfolio cut documents the engine's architecture in full but **withholds the proprietary rule corpus** — the part that took the most iteration and is the actual competitive edge. This file inventories what's withheld and why, so a reviewer can see the depth without the contents being exposed.

The withheld material is not vaporware: it's a ~2MB runtime corpus plus a multi-megabyte operator archive, built across multiple iteration rounds and validated against a manually-scored critic regression set. It is simply not appropriate to publish.

## What's withheld

| Withheld asset | What it is | Why it's withheld |
|---|---|---|
| **Runtime anti-slop ruleset** | The empirically-validated rules the critic actually fires on — banned phrases, AI tells, structural slop patterns, with severities. | Core IP. This is the distilled output of the whole project. |
| **Full inherited ruleset** | A multi-thousand-line archive synthesizing canonical writing/style/marketing sources, with provenance tags. Operator-consult only; never loaded at runtime. | Core IP + third-party source material. |
| **Voice rules** | The universal voice / tone / craft ruleset the writer and critic share. | Core IP — defines the house style. |
| **Critic system prompt** | The system prompt the isolated critic subagent runs under. | Core IP — the adversarial-review mechanism. |
| **Critic invocation spec** | The exact subagent invocation pattern, response parsing, and loop semantics. | Implementation detail tied to the critic prompt. |
| **Headline & lead craft guides** | Distilled patterns, tests, and anti-patterns for headlines and leads. | Core IP. |
| **Structure templates** | Per-page-type section structures with per-section persuasion jobs (the various BOFU page types). | Core IP. |
| **Funnel playbooks (TOFU / MOFU)** | Stage-specific operational playbooks the router overlays by awareness level. | Core IP. |
| **Writer cadence fingerprints (×11)** | Register/cadence overlays modeled on well-known operators and essayists, with intensity calibration. | Core IP; modeled on third parties. |
| **Sourcing recipes (per vertical)** | Curated source maps + explicit "do-not-source" lists per vertical. | Core IP. |
| **Exemplar libraries** | Annotated best-of-best pages (structural anatomy + moves-to-steal/skip + lenses). | Core IP + third-party page content. |
| **Generated client content** | All drafts and shipped pieces. | Client-confidential. |
| **Brief template + filled briefs** | The per-piece brief form and any completed briefs. | Contains client specifics. |
| **Regression sets & reports** | The critic regression set and run reports. | Tied to the withheld rules. |

## What IS included (so the work is legible)

- **`README.md`** — full architecture, two-plus Mermaid diagrams, engineering decisions & tradeoffs, honest limitations.
- **`docs/ARCHITECTURE.md`** — stage-by-stage flow, context-loading discipline, the three-layer validation gate, state model, decision rationale.
- **`docs/pipeline_contract.md`** — the sanitized skill contract: stage table, references map (with each proprietary file marked `[WITHHELD]`), critic isolation, anti-patterns.
- **`src/detect_ai.py`** — the external-detector gate, faithful and unredacted (no IP in it). Real multi-vendor adapter pattern, env-var key handling, exit-code contract.
- **`src/score_draft.py`** — the deterministic checker, **structurally faithful**: real data types, runner registry, aggregation, exit-code contract, and CLI; three method-only checks (em-dash, burstiness, paragraph uniformity) shown in full; banned-phrase data and pattern-matching check bodies redacted with `# [REDACTED …]` markers.

## Security / sanitization

- No secrets are committed. All API keys are read from environment variables; the repo contains no tokens (`eyJ…` JWTs or otherwise), passwords, or bearer credentials.
- No real client names, employer identifiers, user UUIDs, or absolute user paths appear in this cut. The one real validation client is referenced only generically.
