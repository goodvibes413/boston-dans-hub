# Quality Roadmap — How We Make Dan Better at $0/month

**Status (2026-05-04):** Tier 2 freshness pre-pass + repetition judge shipped today (carefully, with DRY_RUN gate). Tier 4 partial: callers_and_voices + grudge_book shipped. Continuity memory window extended 3→5 days. Backlogged Tier 5 (product/UX). Multi-agent architecture deferred (see "When multi-agent WOULD be the right answer" below).

This document is the source of truth for how we improve Dan's content quality without breaking the $0/month operating-cost constraint. It exists because the question "should we move to a multi-agent system?" came up after a string of recurring content failures, and the answer needs to survive across sessions and machines.

---

## Context

We hit three recurring content failures in the same week:

1. **Wrong firing tone** — Dan applied the off-field-conduct framework ("I hope everyone involved is doing okay, let the league handle it") to a coaching firing, which is a business decision, not a conduct situation.
2. **False same-day causation** — Dan implied a 17-1 win was a response to a manager firing when the chronological order was actually reversed (and unverifiable from source data either way).
3. **Collapsed draft coverage** — Dan named only the first 3 picks of a 9-pick draft and summarized the rest as "added nine players in total," losing information fans care about.

All three appeared **verbatim from "Bad:" examples in the prompt** — the model was treating anti-patterns as positive templates. That's a prompt-engineering bug, not an architectural one. The fix (committed `00c0f8d`) was to remove the quoted forbidden phrases and replace them with positive structural guidance. The next run produced clean output for all three failure modes.

The user asked whether the next move should be **graduating to a 4-agent system** (stats, news/events, history/comedy, Dan-voice) that collaborates before shipping. This document is the considered answer.

**User-stated goals:**
- Primary motivation: **Quality.** Dan should be noticeably funnier, sharper, more historically grounded.
- Cost constraint: **$0/month is hard.** Must stay within Gemini free-tier daily quotas.

---

## TL;DR

**Don't build the 4-agent system.** Two reasons:

1. **Cost math doesn't work.** A 4-agent collaboration with critique loops is 5–6x API calls per day. Gemini free-tier daily quota is already strained — that's why we use `gemini-flash-latest` (highest quota allocation) and why the morning of 2026-04-26 had a retry storm that burned the job timeout. Multi-agent breaks the $0 constraint.
2. **The failures aren't context-collision failures.** A "stats agent" wouldn't have prevented "added nine players in total" — that's a draft-coverage instruction issue, not a stats issue. Each specialist would inherit the same anti-template flaw the integrator had.

**The path forward is cheaper and more effective:** structured pre-passes + judge expansion + better evals + richer source data. Tiers below.

---

## Why full multi-agent is the wrong tool here

### 1. Cost math

| Architecture | Calls/day | Free tier impact |
|---|---|---|
| Today (generate + judge + occasional correction) | 2–3 | Comfortable inside flash-latest daily quota |
| 4-agent sequential (stats → news → history → Dan + judge) | 5 | Quota strained; one retry storm = quota exhausted |
| 4-agent with critique/voting loops | 7–10 | Breaks $0 constraint outright |

The retry-budget fix (committed `b59f8b6` on 2026-04-26) was needed *because* we were already brushing the quota ceiling at 2–3 calls/day. Multiplying by 2–4x is structurally incompatible with the free-tier strategy documented in `CLAUDE.md` → "Model Strategy: `gemini-flash-latest` for Higher Daily Quota."

### 2. Coordination latency vs. 25-min job timeout

We just capped retry budgets so worst-case publish stays ~5 min inside the 25-min GitHub Actions timeout. Adding 3 sequential Gemini calls (~30–90s baseline each, more under load) eats most of that headroom before any retry happens. One bad day with mild 503s and the job times out again.

### 3. Voice IS the integration

"Stats agent → Dan-voice integrator" sounds clean, but voice is the *whole* product. Dan's appeal is HOW he reacts to a stat line, not the stat line itself. A stats agent emits dry facts; a Dan-voice agent has to do all the same work the current single prompt does, *plus* reconcile competing outputs from upstream agents. That's strictly more work, not less.

The only domain where specialization meaningfully helps is **validation** — and we already do that. `safety_judge.py` is a separate Gemini call with a fundamentally different job (cross-reference output against `source_data`). It catches stat hallucinations and tone violations the integrator can't self-police. The right pattern is *expand validation specialization*, not split *generation* into specialists.

### 4. The 2026-04-26 failures wouldn't have been caught by multi-agent

Walk through each:

- **"I hope everyone involved is doing okay"** — verbatim copy of a Bad: example. A "firing-tone agent" with the same flawed prompt would copy the same example.
- **"added nine players in total"** — verbatim copy of a forbidden phrase. A "draft agent" with the same prompt would do the same thing.
- **"responded with a massive 17-1 win"** — false causation inferred when chronological order wasn't in source data. A "news agent" wouldn't have known the order either.

None of these are "the model couldn't hold all the context." They're "the prompt told the model to do the wrong thing." The fix was prompt engineering, which is what we already did.

---

## What WILL move quality up at $0

### Tier 1 — Eval-driven prompt iteration (Pursuing)

**Where it stands:** `evals/fixtures/` exists with hand-crafted scenarios; `scripts/eval_voice.py` runs them. We use it reactively when something breaks.

**The upgrade:** Run the eval suite *before every prompt change*, not just after. Add fixtures for the failure modes we keep hitting (firing tones, draft coverage, same-day causation). Make eval pass/fail a gate. This is what already-shipped projects call "prompt regression tests."

- Cost: $0 (uses same flash-latest, dev-time only).
- Effort: ~half a day to add fixtures + a runner script.
- Impact: catches anti-template bugs *before* they hit production. The 2026-04-26 prompt fix should have been gated by an eval that flagged `"added nine players in total"` in the output.

### Tier 2 — Deterministic structured pre-passes (In progress)

For the outputs that keep failing in mechanical ways, generate the structured shell in Python before the LLM call, then tell Dan to *include it verbatim* with his takes interleaved.

**`scripts/build_draft_block.py`** — given `data/boston_drafts.json`, emits:
```
DRAFT_BLOCK_VERBATIM (include every line as-is, add Dan's take after each):
R1.28: Caleb Lomu, OT, Utah
R3.95: Gabe Jacas, EDGE, Illinois
R4.140: Eli Raridon, TE, Notre Dame
... (all 9 picks)
```
Dan's prompt receives this and is told: *"Echo each line verbatim, then add one Dan-voice sentence per pick. Never collapse this list."* The model can't accidentally collapse 9 picks into a summary because the structured block is already in front of it as required scaffolding.

**Continuity memory** (shipped 2026-04-27) — `scripts/publish.py` now writes a slim copy of each fresh publish to `data/dan_archive/YYYY-MM-DD.json` (committed via gitignore exception, 7-day rolling retention). `scripts/generate_rant.py` loads the last 3 archives and injects them as a `RECENT_DAN_OUTPUT` block in the prompt. The Continuity rule in `prompts/boston_dan_system.txt` instructs Dan to evolve takes rather than re-introduce stories, and to vary signature phrasing across consecutive days. Trigger: 4-26 and 4-27 outputs both led with "Alex Cora is out, along with his staff" + "I respect the run" + "Hope it works" — same template, different day. Feature address: see Continuity rule in `prompts/boston_dan_system.txt`. Regression coverage: `evals/fixtures/continuity_no_repeat_firing.json`.

**`scripts/build_causation_notes.py`** — given LATEST_NEWS timestamps + rolling_7day game start times, emits:
```
CAUSATION_NOTES: Cora firing announced at 10:30 AM ET, AFTER the 17-1 win (game ended ~10:00 PM ET previous day). Safe to say "after the game."
```
Or:
```
CAUSATION_NOTES: Order unverified. Use side-by-side framing only.
```
Dan's prompt is told this is ground truth; do not infer otherwise.

**`scripts/build_facts_of_day.py`** — given rolling_7day + latest_news, extract and chunk the "facts of the day":
```
FACTS_OF_THE_DAY:
- [box_scores] Celtics 110, Sixers 105 (Tatum 17pts, Maxey 30pts). Series tied 3-3.
- [schedule] Game 7 tonight at 7:30pm ET.
- [news] Patriots made 5-year deadline decision on 2023 draft class.
- [rolling_window] Red Sox 12-19, 3-game losing streak. Sox/Astros series begins today.
- [bruins_playoff] Bruins vs Buffalo continues. Bruins 45-20-15 regular season.
```
This "facts inventory" is then injected alongside rolling_7day in the user message, so Dan has a scannable summary of the key stories to cover. The prompt is updated to reference this block and ensure comprehensive coverage.

- Cost: $0. Pure Python chunking.
- Effort: ~1 day (schema design + extraction logic).
- Impact: ensures Dan never misses major storylines by burying them in nested JSON; makes coverage gaps visible in evals.

### Tier 3 — Specialist judge expansion (Pursuing)

The safety_judge already exists. Expand its rubric from "is this safe?" to "is this *good*?":

- Voice consistency: did Dan use slang naturally, or sound generic?
- Stat anchoring: is every claim grounded in source data?
- Comedic moments: did he land at least one specific take, or hedge?
- Historical color: did he use HISTORICAL_FACTS where it would have fit?

If any quality check fails, route to one correction pass (the existing pattern). This is **+1 call/day at most**, not +4. Same architecture, more rigorous gate.

- Cost: same as today — judge runs once, correction runs at most once.
- Effort: 2–3 days of rubric design + eval validation.
- Impact: catches "Dan is technically correct but boring" outputs that the current safety judge passes through.

### Tier 4 — Richer source data (Considering)

Quality often comes from what Dan *knows*, not how the prompt is structured. Concrete additions:

- **`historical_facts.json` deepening**: more iconic moments, more rivalries, neighborhood-level Boston culture (Greenway/Big Dig/Dunks references the prompt currently mentions only abstractly).
- **`callers_and_voices.json`** (new): a rotating set of WEEI/98.5 caller archetypes, common phrasings, regional dialect samples. Inject 2–3 per day so Dan varies his voice.
- **`grudge_book.json`** (new): durable rivalries and feuds (Bruins vs. Habs, Sox-Yankees specific years, Pats-Jets) so Dan has concrete enemies to reference.

- Cost: $0 (curation effort, no API).
- Effort: ongoing — 1 hour every couple of weeks adds material.
- Impact: directly raises ceiling on "noticeably funnier, more grounded."

### Tier 5 — Product & UX (Backlog)

Quality isn't just about generation — it's about trust and exploration. This tier adds user-facing features that make Dan's work transparent and archive-able.

**Readable archive of past posts** — Create a browsable `/archive` page that lists all published Dan posts by date with headline + first paragraph. Each entry is clickable to view the full post. Archive is auto-generated from `data/dan_archive/` (which already exists — we just need a frontend to display it). Benefits: (1) fans can re-read past commentary, (2) shows Dan's consistency over time, (3) builds community around the daily routine.

- Cost: $0 (no new data fetches, re-use existing archive).
- Effort: ~1 day (HTML/CSS for archive page, JavaScript to iterate the archive directory).
- Impact: users see the "living project" aspect; drives repeat visits.

**Eval data drill-down** — Each post gets a small **[eval data]** link that shows: (1) the raw `source_data` (rolling_7day + season_memory + latest_news + draft_picks) that was fed to Gemini, (2) the raw `raw_dan_output.json` before safety judge, (3) the judge verdict + flags if any, (4) the final published output. Clicking through lets obsessive fans see *what Dan was working with* and *why he said what he said*. This is also invaluable for debugging quality issues — when someone reports "Dan got the score wrong," you can immediately show them the source_data.

- Cost: $0 (exposing existing artifacts).
- Effort: ~1–2 days (JSON viewer UI, navigation, one-click drill-down from post to eval data).
- Impact: transparency builds trust; unblocks debugging; gives fans insight into the generation process.

---

## When multi-agent WOULD be the right answer

So this isn't a forever-no — document the trigger conditions:

1. **Daily quota raised or paid tier accepted.** If $0 softens to "$5–20/mo OK," a **2-agent shape** (Dan-generator + voice-critic with rewrite authority) becomes feasible. Note: 2 agents, not 4. Voice-critic is the 80/20 — adding stats/news/history specialists adds cost without proportional quality gain.
2. **Context-window pressure.** When source data exceeds ~50–100KB and a single call can't hold all of rolling_7day + season_memory + historical_facts + draft_picks + news + game color + voice rules, *that's* when you split. We're at ~30KB today.
3. **Multi-team scaling.** If Dan starts covering NCAA football + Bruins farm system + Sox minor leagues + Patriots draft prospects in depth, the per-domain prompt complexity might genuinely require specialization.
4. **Three+ recurring quality misses per week** that prompt engineering + structured pre-passes + judge rubric expansion (Tiers 1–3 above) cannot close.

Until any of these trip, Tiers 1–4 are cheaper and faster.

---

## Roadmap status

| Tier | Description | Status | Effort | Cost |
|---|---|---|---|---|
| 1 | Eval-driven prompt iteration (regression fixtures + gating) | **Pursuing** | ~half day | $0 |
| 2 | Deterministic structured pre-passes (continuity memory **shipped 2026-04-27**; **draft freshness shipped 2026-05-04** with DRY_RUN gate; causation pending; facts chunking deferred) | **In progress** | 1–2 days | $0 |
| 3 | Voice/quality rubric expansion in `safety_judge.py` (**repetition pre-pass + bullet 10 shipped 2026-05-04**) | **Pursuing** | 2–3 days | +0–1 calls/day |
| 4 | Richer source data (callers_and_voices + grudge_book **shipped 2026-05-04**; deeper history ongoing) | **Partial** | Ongoing | $0 |
| 5 | Product & UX (readable archive, eval data drill-down) | **Backlog** | 2–3 days | $0 |
| Multi-agent | 2+ Gemini calls collaborating on generation | **Conditional** | — | Breaks $0 |

---

## Critical files (for Tiers 2–5 implementation)

### Generation & validation (Tiers 2–3)
- `scripts/generate_rant.py` — extend `build_user_message()` to inject `DRAFT_BLOCK_VERBATIM`, `CAUSATION_NOTES`, and `FACTS_OF_THE_DAY` fields
- `scripts/build_draft_block.py` — new (deterministic Python from `data/boston_drafts.json`)
- `scripts/build_causation_notes.py` — new (deterministic Python from `data/latest_news.json` + `data/rolling_7day.json` timestamps)
- `scripts/build_facts_of_day.py` — new (deterministic Python to chunk rolling_7day + latest_news for scannable overview)
- `scripts/safety_judge.py` — extend `JUDGE_PROMPT` with voice/quality rubric items
- `prompts/boston_dan_system.txt` — add references to verbatim blocks and facts inventory

### Source data (Tier 4)
- `data/historical_facts.json` — deepen (Tier 4)
- `data/callers_and_voices.json` — new (Tier 4)
- `data/grudge_book.json` — new (Tier 4)

### Evals (Tier 1)
- `evals/fixtures/` — add fixtures for firing-tone, draft-collapse, same-day-causation regression tests

### Frontend (Tier 5)
- `site/archive.html` — new (browsable archive of past Dan posts)
- `site/app.js` — extend to: (1) load and render archive from `data/dan_archive/`, (2) add eval data drill-down links to each post, (3) JSON viewer modal for source_data/raw_output/judge_verdict
- `site/style.css` — add styles for archive list, drill-down modal, JSON viewer

---

## Verification (when Tiers 1–3 ship)

1. Run eval suite against new fixtures: `python3 scripts/eval_voice.py --fixture evals/fixtures/firing_tone.json --n 3` — confirm Dan never produces "I hope everyone involved is doing okay" for a firing scenario.
2. Run draft fixture: confirm all 9 picks appear by name/round/college in output.
3. Run causation fixture (firing + game on same day, order unknown): confirm no "responded with" / "answered with" / "fired up by" phrasing.
4. Force a daily run end-to-end: `gh workflow run "Morning Brew — Daily Dan Commentary" --ref main -f force=true`. Confirm fresh `chore: daily Dan output` lands on `origin/main` within 10 min.
5. Cross-reference per CLAUDE.md Troubleshooting Rule #8 before reporting outcome to user.

---

## Bottom line

Build the cheap things first. The 2026-04-26 prompt fix resolved the immediate failures (verified in run `24959583325` — all 9 draft picks named, firing tone is "I respect the run, but team needed a jolt," no false causation). If quality issues persist, structured pre-passes + judge expansion + richer source data are dramatically cheaper than multi-agent and address the actual failure modes we've seen. Multi-agent is a real pattern, but it's the right answer for "the model can't hold the context" or "I have budget for 5x API calls" — neither describes this project today.
