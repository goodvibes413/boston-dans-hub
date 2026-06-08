# Decision Record Schema

> **Read this before writing or editing a decision file.** The PostToolUse hook
> (`.claude/hooks/validate_memory_file.py`) rejects decision files where any `## Evidence`
> or `## Explicitly NOT doing` row lacks a provenance tag from the enum in
> [`../PROVENANCE.md`](../PROVENANCE.md).
>
> **Pre-save self-check** (run mentally before every write):
> 1. **COUNT-THE-TAGS.** Count bullet rows under `## Evidence` and `## Explicitly NOT doing`.
>    Count provenance tags. The two numbers must match. 6 rows + 4 tags = 2 orphans — add
>    tags or move the bullets to `## Remaining ambiguities`.
> 2. Path-typed tags are markdown links (`[ingestion/...](../ingestion/...)`), not prose
>    like `(Acme interview, 2026-04-22)`.
> 3. Each path-typed link resolves from THIS file (`decisions/<file>.md`) — i.e.
>    `../ingestion/<rest>` or `../source/<rest>` (one `..`).
> 4. `## Status` is one of `pending | decided | superseded`.
> 5. `## What would reverse this` is present and **specific and observable** — a metric
>    threshold, a stakeholder signal, a date. Not "if things change."

Filename: `YYYY-MM-DD-<slug>.md` in `context-library/decisions/`.

```markdown
# Decision: <one-line statement>

## Status
pending | decided | superseded

## Date
YYYY-MM-DD <!-- decided date, or date opened if pending -->

## Context
<!-- 2-4 sentences. What problem / fork in the road. -->

## Options considered
1.
2.
3.

## Decision
<!-- What we picked. Empty for pending. -->

## Why
<!-- The actual reasoning. Be specific. Empty for pending. -->

## Evidence
<!-- HARD RULE: every row ends with one provenance tag. Examples:
  - Acme ops lead said weekly batches are unusable  [source/interviews/2026-04-22-acme-ops.md](../source/interviews/2026-04-22-acme-ops.md)
  - Three customers asked for the same flow  [ingestion/interviews/2026-05-02-synthesis.md](../ingestion/interviews/2026-05-02-synthesis.md)
  - Naomi confirmed Q3 priority in 1:1  (stakeholder-verbal, Naomi, 2026-05-13)
  - Checkout friction reduces conversion  (industry-knowledge)
-->
- <claim>  `<provenance-tag>`

## Explicitly NOT doing
<!-- Same provenance-tag requirement. Each "not-doing" wears its source. -->
- <not-doing>  `<provenance-tag>`

## What would reverse this
<!-- The most valuable field. The observable condition under which we'd revisit. -->

## Remaining ambiguities
<!-- Things we know we don't know. Stale evidence, untested assumptions, meta rows. -->

## For pending decisions only
- **Blocker impact:** <what this is currently blocking>
- **Deadline:** <when it needs resolving, or "no hard deadline">
- **Owner:** <who's driving resolution>
- **Missing evidence:** <what we'd need to learn to decide>

## Linked
<!-- Paths relative to decisions/YYYY-MM-DD-<slug>.md -->
- Hypotheses: `../hypotheses/<slug>.md`
- Strategy: `../strategy/<file>.md` § <section>
- Stakeholders informed: `../research/stakeholders/<slug>.md`
```

## Rules

- Every shipped feature should have at least one decision record.
- When a hypothesis is `promoted`, a decision is auto-drafted (status `pending`; PM confirms).
- Decisions are append-only. To reverse, write a NEW decision that references and supersedes
  the old one (set the old one's status to `superseded`).
- **Decision debt:** `pending` decisions are unresolved forks. `/review` surfaces them —
  especially when blocker impact is high or the deadline is approaching.
- A decision rendered from mixed-trust evidence (common during migration) must wear that
  mix on its face. A reader should not have to dig to learn how much of the reasoning is
  inherited-and-not-revalidated vs. freshly collected.
