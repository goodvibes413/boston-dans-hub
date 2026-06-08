# /recall

Read-only query across the brain. The Claude Code equivalent of asking the phone bot a
question — except it runs where you already are, against files you can open, with citations
you can click.

## Input

A natural-language question: "what did we decide about pricing and why?", "what's [Company]'s
biggest concern?", "why do we keep losing to [Competitor]?", "what do we know about the
onboarding problem before I draft this PRD?"

## Does (read-only — writes nothing)

1. Start at the most relevant area. For a decision question → `context-library/decisions/`;
   customer → `research/`; competitor → `research/competitors/`; person → `research/stakeholders/`;
   strategy → `strategy/`.
2. Read the canonical files, following links into the `ingestion/` and `source/` artifacts
   behind any claim that matters.
3. Synthesize an answer. **Cite the file behind every load-bearing claim** — `[research/insights.md]`,
   `[decisions/2026-05-22-pricing.md]`. The reader should be able to verify any sentence.

## Surfaces

- The answer, with inline file citations.
- A **gap notice** when the brain doesn't have it: say so plainly and name what artifact
  would fill it ("no enterprise interviews on this — closest is the 2026-04 mid-market set").
  Do not synthesize confident answers from thin air; that's the failure mode of every
  memory system, and citations only help if they're real.

## Note

`/recall` never promotes or edits. If the answer is worth keeping, it's an `/ingest` (as an
adhoc synthesis) or a `/review` promotion — not a silent write during a read.
