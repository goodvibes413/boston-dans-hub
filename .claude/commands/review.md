# /review

The weekly maintenance sweep — the human-in-loop "night cycle." A phone-side brain runs this
as a 3am cron on a host that has to be awake. Here you run it Friday in 20 minutes, in the
same context that did the week's work, with you in the loop on every judgment call.

Memory systems fail at month three because nothing sweeps. This is the most important
operation in the layer.

## Input

None, or a scope (`/review hypotheses`, `/review stakeholders`) to run one check.

## Loads

- `CLAUDE.md § Memory Layer` (promotion bar, evidence hierarchy, escalation rules)
- All durable areas in scope: `research/`, `hypotheses/`, `decisions/`, `research/stakeholders/`,
  `strategy/`
- Recent `ingestion/` for promotion candidates
- The last 2 `context-library/maintenance/` reports to compare deltas

## The six checks

1. **Stale knowledge** — files not updated in 6+ weeks. Still true? Archive?
2. **Stale evidence** — market intel past 30-60 days, interviews past 90, stakeholder
   assumptions past 30, strategy assumptions past the quarter. Flag, don't auto-decay.
3. **Hypothesis & decision hygiene** — actives with no evidence in 30+ days; promoted
   hypotheses without a decision; decisions whose "what would reverse this" condition has
   triggered; `pending` decisions older than 14 days with blocker impact.
4. **Stakeholder cadence & strategy tensions** — high-influence people not touched in 3+
   weeks; recent decisions drifting from strategy.
5. **Knowledge synthesis (compression)** — recurring patterns AND recurring contradictions.
   Compression is additive; minority signals are preserved, never flattened.
6. **Archival sweep** — shipped features inactive 90+ days; resolved hypotheses; closed asks.
   Extract durable lessons before archiving anything.

## Does

- Write the dated report → `context-library/maintenance/YYYY-MM-DD-review.md`.
- **Edit directly** where confidence is high: update stakeholder `Last touched`, archive
  90-day-old shipped features, compress duplicate insights, promote candidates that clearly
  meet the bar (with complete audit trails).
- **Draft, don't commit** what needs your judgment: stale strategy assumptions, unresolved
  tensions, decision debt, demotion of a `promoted` hypothesis.

## Surfacing drift — cite, don't paraphrase

When fresh evidence contradicts a `promoted` hypothesis or a `decided` decision, name the
**specific** contradicting signals with dates and links — not "the original premise no
longer holds." Distinguish two layers: the artifact is still valid (that interview really
happened) but the *claim* it supported no longer matches the world. Do NOT resolve in this
turn — surface it, leave the status, let the next turn (with the PM) decide.
