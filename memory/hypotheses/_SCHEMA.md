# Hypothesis File Schema

> **Read this before writing or editing a hypothesis file.** The PostToolUse hook
> rejects files where any `Evidence for:` / `Evidence against:` row lacks a provenance
> tag from the enum in [`../PROVENANCE.md`](../PROVENANCE.md).
>
> **Pre-save self-check:**
> 1. **COUNT-THE-TAGS.** Bullet rows under `Evidence for:` / `Evidence against:` must equal
>    the number of provenance tags. Orphans get rejected. Move non-claims to
>    `Open questions / caveats:`.
> 2. Path-typed tags are markdown links, not prose.
> 3. Each path-typed link resolves from THIS file (`hypotheses/<slug>.md`) — `../ingestion/<rest>`
>    or `../source/<rest>` (one `..`).
> 4. Commentary / gaps / inferences live under `Open questions / caveats:`, never under Evidence.
>
> The most common failure: paraphrasing a claim from an ingestion record into an Evidence
> row and forgetting the tag. If you can name where the claim came from, you can tag it.

Hypotheses are **feature-scoped.** One file per feature, named `<feature-slug>.md`, in
`context-library/hypotheses/`.

Two modes:
- **Pre-ship** — generated proactively, organized by the 5 risk areas, tested via experiments.
- **Post-ship** — generated from observed analytics/interview data after launch ("why is
  retention dropping in week 2?"). Same schema; `Origin` marks them data-derived.

The 5 risk areas: value, usability, feasibility, viability, and **other** (regulatory,
ethical, partnership-dependency, brand, security, internal-political — real risks that
don't fit the canonical four and should be hypothesized about explicitly, not buried).

## File structure

```markdown
# Hypotheses — <feature-name>

<!-- Paths relative to hypotheses/<slug>.md -->

## Meta
- Feature: `../prds/<slug>.md` (or `../strategy/<file>.md`)
- Status: one of `active | partially-validated | promoted | demoted | archived`
- Created: YYYY-MM-DD
- Last updated: YYYY-MM-DD

## Value risk
### H-V1: <one-sentence belief>
- **Origin:** proactive | data-derived (from <source>)
- **Confidence:** low | medium | high
- **Evidence for:**
  - <claim>  `<provenance-tag>`
- **Evidence against:**
  - _(none yet)_
- **Open questions / caveats:** <!-- gaps, inferences, things not yet established. No tags needed. -->
  - <what we don't know that would change confidence>
- **Test:** <experiment, interview, analysis>
- **Decision trigger:** <what result promotes? what demotes?>
- **Status:** active | promoted | demoted | killed
- **Resolution:** <if resolved, what happened — link to decision>

## Usability risk
### H-U1: …

## Feasibility risk
### H-F1: …

## Viability risk
### H-B1: …

## Other risk
### H-O1: <name the risk type in the heading>
```

## Empty-state — don't write meta-rows as evidence

When a section has no claims, leave it empty or write `_(none yet)_`. Do NOT write
"No counter-evidence in current data" as a bullet — that's commentary on the absence of
claims, it has no provenance tag, and it misleads future readers into thinking absence was
documented as evidence. If you actively looked and found nothing, note it under
`Open questions / caveats:` ("Searched Q1 interviews; no Saver-persona objections found —
gap, not negative evidence").

## Lifecycle

- **active** — being tested, evidence accumulating
- **promoted** — confirmed; spawn a `pending` decision in `../decisions/`
- **demoted** — contradicted but kept for context; document why
- **killed** — no longer relevant (feature reshaped, market shifted)
- **archived** — feature shipped and measured; move to `hypotheses/archive/`

## Promotion rule

When evidence is sufficient to promote:
1. Mark the hypothesis `promoted` with a resolution note.
2. Create a `pending` decision in `../decisions/` referencing the hypothesis.
3. Surface the promotion in the task summary.

Promotion is **judgment work, not automation.** The bar (default): a pattern recurring across
2+ *independent* sources from the same population, or one that directly informed a decision.
A single fresh interview adds evidence, not a verdict. An analytics snapshot is correlational
by default — don't bump confidence on it without a sample size and a confounder check.
