# /ingest

**Optional explicit router.** You usually don't type this — capture is ambient (see
CLAUDE.md § Capture is ambient). Whenever you paste a transcript, share a link, or reach a
conclusion in a session, the agent captures it automatically, and the session-end Stop hook
guarantees consolidation. Use `/ingest` only when you want a *specific* artifact handled in
a *specific* shape (e.g. "ingest this 40-page transcript as an interview"), or to force a
capture you don't want to wait for. The steps below are exactly what the ambient path runs.

Route a raw artifact into the brain. Four shapes, one verb.

## Input

A pasted transcript, a file path, a screenshot, a URL, or a free-form note. Infer the shape:

- **interview** — customer call, user research, sales call with prospect signal
- **meeting** — 1:1, exec review, roadmap discussion, kickoff, retro
- **market** — competitor article, screenshot, changelog, analyst note
- **adhoc** — anything else worth capturing

If the shape is ambiguous, ask one question. Don't guess.

## Loads

- `context-library/strategy/` (the relevant strategy file) and `context-library/PROVENANCE.md`
- The matching durable area: `research/` (interview), `research/stakeholders/<slug>.md`
  (meeting), `research/competitors/` (market)
- Active `hypotheses/<slug>.md` files the artifact might touch
- The last 3 entries in `ingestion/<shape>/` for pattern comparison

## Does

1. **Copy the source verbatim** → `context-library/source/<shape>/YYYY-MM-DD-<slug>.md`.
   Immutable from here. This is non-negotiable (see CLAUDE.md § Source preservation).
2. **Synthesize** → `context-library/ingestion/<shape>/YYYY-MM-DD-<slug>.md` with each
   observation tagged (observation / interpretation / hypothesis / assumption).
3. **Promote what crosses the bar** into the durable layer — recurring across 2+ independent
   sources, or decision/strategy-relevant. One-offs stay in `ingestion/`. Every promoted
   claim wears a provenance tag (the hook will block you otherwise). Update the area INDEX.

## Surfaces (2-4 bullets — the value is in the files, not the summary)

- Where it landed (source, ingestion, which durable destinations)
- 1-3 themes promoted, or "no promotion this round — stays in ingestion until it recurs"
- Any contradiction with prior evidence (preserved and flagged, NOT resolved)
- One open question if your judgment is needed
