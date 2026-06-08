# /prep <stakeholder-or-meeting>

Read-only briefing before a meeting. Two minutes before the call, not twenty minutes of
Slack archaeology.

## Input

A stakeholder slug, a name, or "morning prep" for everyone on today's calendar.

## Does (read-only)

1. Load `context-library/research/stakeholders/<slug>.md` (or match by name).
2. Pull the touchpoint log (last interaction + date), open asks, the last unresolved concern,
   and what they care about / push on.
3. Cross-reference active `decisions/` and `hypotheses/` that touch this person's stake.

## Surfaces

- Who they are and the relationship state (last touched, cadence).
- What lands with them and what they'll push on — each tied to its source.
- The open thread you owe them, if any.
- What to lead with.
- A gap notice if the file is thin ("only one touchpoint logged — this briefing is light").

After the meeting, `/ingest` the notes so the next `/prep` is richer. The file remembers
so you don't have to.
