# Release Notes — Boston Dan's Hub

Running log of what shipped and why. Reverse-chronological. Updated after each session.
`CLAUDE.md` holds architecture and conventions; this file holds decisions, rationale, and context.

---

## 2026-09-04 — The Stretch Run: Dan Covers a Pennant Race, But Only in September

**Context:** It's September 4th, the Red Sox are in a live wild-card race, and Dan
doesn't speak to it. Yesterday's brew got as close as *"We have a long road ahead if
we want to stay in the postseason conversation"* — vague, unanchored, and a sentence
he could have written in June.

He couldn't do better, for a good reason: **the data wasn't there.**
`fetch_season_memory.py` wrote wins/losses/win_pct and *division* games-behind, but
nothing about the wild card, which is the entire story for this team. Division
games-back is worse than useless here — 3rd in the AL East reads as "buried" while
they comfortably hold a wild-card spot. And Stats Discipline is absolute, enforced by
judge rule 7 at HIGH severity: any number not in `rolling_7day` or `season_memory` is
a fabricated stat. Dan was structurally forced into mush.

**The risk on the other side.** Just telling Dan "talk about the playoffs" gets you
playoff math in April, and "the magic number is sixteen" as the daily ticker sentence
that goes stale by day three — the exact repetition failure the Continuity rules exist
to prevent. The feature is only worth shipping if it has an off switch that can't be
talked out of.

**The design: gate on data presence, not on instructions.** The topic is unlocked by
the presence of a `playoff_race` block on the team's `current_season` entry. No block,
no race talk. That single fact does all the work, because
`season_current.json` already feeds three consumers:

- **The prompt** — `build_season_memory()` merges it into `SEASON_MEMORY`.
- **The safety judge** — `source_data.season_memory.current_season` reads the same
  file, so the new figures become legal to cite *and nothing else does*.
- **The evals** — `eval_voice.py` points `SEASON_CURRENT_PATH` at fixture data, so a
  fixture can switch the race on or off.

One write reaches all three. No changes to `generate_rant.py`, `safety_judge.py`,
`publish.py`, the workflow, or the eval harness — the insertion point was chosen for
exactly that reason.

The gate is *structural*, which is the point. Outside the window there is no
instruction for Dan to disobey, because there is no data to reason from; and if he
invents a magic number anyway, the judge fails him for a fabricated stat. Two
independent mechanisms, neither of them a polite request.

**Two gates, in `build_playoff_race()`:**

1. **Window** — `regular_season` and `games_remaining <= STRETCH_RUN_WINDOW[sport]`
   (MLB 40, NBA/NHL 20, NFL 6). Defined in *games remaining* rather than calendar
   dates so it self-adjusts to a shortened or shifted season. For MLB that opens
   around mid-August.
2. **Elimination** — no block when StatsAPI reports `"E"` for both division and wild
   card. Actual elimination is already owned by `season_overrides.json`, whose rules
   are absolute. Two authorities on the same fact is how contradictions ship.

Five `race_status` tiers, each mapping to a distinct register in the persona:
`clinched`, `clinch_watch` (magic ≤ 10), `in_position`, `chasing`,
`playing_out_the_string`. That last one was a deliberate call — when the Sox are alive
on paper only, Dan gets honest gallows humor (the register the persona already uses
for long slumps) rather than either silence or manufactured hope. "Stranger things
have happened" is not a take, it's a cope.

**Source: MLB StatsAPI, not ESPN.** `statsapi.mlb.com` — the host `fetch_mlb.py`
already uses — returns `magicNumber`, `wildCardRank`, `wildCardGamesBack` and
`eliminationNumber` directly, so Dan cites real figures instead of derived guesses.
One call covers the whole AL, so the closest chaser ("5.5 up on Cleveland") comes from
the same payload rather than a second request.

**The parsing hazard, which is not theoretical.** StatsAPI sends these as *strings*
with sentinels: `gamesBack` can be `"-"` or `"+2.5"`, `magicNumber` is `"-"` when not
applicable, `eliminationNumber` is `"E"` when the team is out. A `"-"` reaching the
prompt is a non-number Dan would then cite as a stat. Everything goes through
`_parse_gb()` / `_parse_count()`, which return `None` for anything that isn't a real
number so the field is *omitted* rather than emitted as a sentinel.

`build_playoff_race()` re-normalizes its own inputs rather than trusting the caller.
That wasn't the original design — the test feeding it raw strings turned up a
`TypeError` on the magic-number comparison. In production `fetch_mlb_standings_race()`
pre-parses so it would never have fired, but this function is the shared entry point
for the other three sports' fetchers, and that crash would have landed at 6am in CI
the first time someone wired up the NBA.

**Direction is encoded in the field name.** `wild_card_games_up` is a cushion,
`wild_card_games_back` is a deficit; only one is ever present. Naming them apart means
Dan can't invert the sign and report a 5.5-game lead as a 5.5-game hole.

**Persona changes** (`prompts/boston_dan_system.txt`, +3.6KB / +7.7%, comfortably
inside the prompt budget the 09-04 timeout work sized):

- New **The Stretch Run** section, placed between Season Context and SEASON_OVERRIDES
  so it reads correctly top-to-bottom: general context → the race → elimination trumps
  everything.
- **One race beat per brew, tied to what last night's result did to the picture.**
  "That win knocks another one off the magic number" is a race beat; "the Sox hold the
  second wild card at 79-62" is a wire report. Never lead on the race two days running.
- Register per tier, plus a superstition rule: Dan never declares them in, which is
  both his voice and a hedge against overclaiming.
- Run it through the **bits that already exist**. The Duck Boat fund now has a real
  ledger behind it — a September bullpen meltdown is a bigger withdrawal than an April
  one — rather than adopting a new voice for the race.
- Continuity gains a **playoff-race frame rotation** rule; Stats Discipline extends to
  magic numbers, wild-card rank, and games back/up.

**Coverage:** 25 new unit tests. The one that matters most is `test_april_gets_no_block`
— 40 games played, 122 left, no block. That's the regression test for the whole
premise. Three eval fixtures: an in-position September day (with `recent_dan_output`
that already used the magic-number frame, to exercise the no-repeat rule), a
`playing_out_the_string` day, and `race_offseason_no_talk` — a May control where the
pass condition is that the words "playoff", "postseason" and "October" don't appear at
all.

**Scope:** MLB only. The gate, the block shape and the persona rules are
sport-agnostic and `STRETCH_RUN_WINDOW` already carries thresholds for the other
three; each needs one standings fetcher to light up. Not built on spec.

**Still open:** the live fetch is unverified — `statsapi.mlb.com` is blocked by the
egress proxy in the authoring environment, so `fetch_mlb_standings_race()` has not run
against the real endpoint. The gate logic is pure and fully tested; the field mapping
is the part that needs one real run to confirm. Verify on the next CI run that
`data/season_current.json` carries a Red Sox `playoff_race` block with a sane magic
number and no `"-"` or `"E"` in any numeric field.

---

## 2026-09-04 — Gemini Upgrade Review: Instrumentation, Pinned Thinking Level, Provable Retry Budget

**Context:** Google shipped several new Gemini models (3.5/3.6/3.8 Flash, 3.1 Pro)
since the 2026-07-01 pin. Question asked: should we upgrade? Judged on latency and
free-tier traffic rather than dollars, since at ~3 calls/day cost is $0 either way.

**What the evidence said.** Pulled 120 workflow runs from the Actions API
(07-26 → 09-03) and every published `daily_output.json` in git history:

- **Zero runs failed for a Gemini reason.** The one non-success (run `31120055179`,
  08-06, 903s, `cancelled`) has `runner_id: 0` and no runner name — GitHub capacity
  starvation, never a model timeout.
- Generation-run durations: min 143s, median ~280s, max 476s, against a 1500s job
  timeout. Worst observed run used 32% of budget.
- Quality is the actual problem: **6 `_quality_warning` publishes + 1 `_stale`
  fallback in 47 days (~15% degraded)**. The 08-29 fallback was
  `"safety judge FAILed after 3 attempts: fabricated statistics"` — HIGH severity,
  burned the full correction loop, shipped yesterday's content. This matches the
  lite-model failure modes already logged on 07-01, 07-05 and 07-07.
- Publish *lateness* since 08-27 (first run of day 12:36–18:56, early cron slots
  never firing, `run_started_at == created_at` throughout) is GitHub's scheduler
  dropping runs. Not a model problem — do not spend model changes on it.

**The trap found while scoping the upgrade.** `thinking_level` was set nowhere.
Its default **differs per model**: `gemini-3.1-flash-lite` defaults to `minimal`,
full Flash models default to high/dynamic. So swapping `DEFAULT_MODEL` to a full
Flash model would have silently moved the pipeline to high thinking, whose
published p95 time-to-first-token (~50s) sits inside the 90s per-request timeout
before grounding and a ~30KB prompt are added — reproducing the 2026-07-01 hang.
Conversely a full Flash model at `thinking_level="low"` may be both better *and*
faster than today's pin. That is the upgrade worth testing, and it is only safe
with the level pinned.

**A real latent bug, found by writing the budget down.** The old retry ladder
(`MAX_RETRIES=4`, `[5,15,30,60]`, 90s timeout, 3 chained calls + judge) had a
worst case of **2390s against a 1500s job timeout**. The pipeline could have been
force-cancelled before `publish.py` wrote a sentinel — precisely the 2026-07-01
failure the ladder was supposed to survive. It had simply never been multiplied out.

**What shipped (no model change — the pin stays until the bake-off runs):**
1. **Per-call instrumentation** — `generate_rant.py` and `safety_judge.py` log
   wall-clock latency and `usage_metadata` (including `thoughts_token_count`,
   which bills as output and drives latency) and write a `_timings` block onto the
   output JSON, so it lands in git history like `_quality_warning` does. Future
   economics questions get answered from our own data, not from blog posts.
2. **`thinking_level` pinned to `minimal`** via `DEFAULT_THINKING_LEVEL` +
   `thinking_config()`, applied only to `gemini-3*` ids — pre-3.x Gemini errors on
   the kwarg and Gemma rejects it, and `eval_models.py` drives both through the
   same path. Behavior today is unchanged; it just no longer depends on an
   undocumented per-model default.
3. **Retry budget made explicit and asserted.** `MAX_RETRIES` 4 → 2, backoff
   `[5,15]`, shared by both scripts, with `tests/test_pipeline.py::TestRetryBudget`
   failing CI if the worst case stops fitting the job timeout. Now ~1460s of 1500s.
   Less of a cut than it looks: generate_rant already makes two independent call
   paths, so a bad morning still gets 6 API attempts before the sentinel, and
   AGENTS.md was always explicit that 1–2h spikes are the cron slots' job.
4. **`eval_models.py --check-quota`** — asks the API whether a model still exists,
   is `generateContent`-capable, and answers on this key's free tier, unpacking
   `QuotaFailure` via the existing `describe_api_error()`. **`--thinking-level`**
   added so a bake-off compares like with like, and the comparison table now
   carries slowest-call latency and thinking tokens with an explicit gate
   (slowest call < 45s = half the request timeout, so one retry still fits).
5. **`check_model_health.py` + `model_health.yml`** — weekly check that the pinned
   models still exist and are still free, filing a `pipeline-degraded` issue if
   not. AGENTS.md told a human to do this manually and reactively; nobody did.
6. **`google-genai` pinned to 2.22.0** in all three workflows. It was installed
   unpinned on every production run — a breaking release would have shipped
   straight into the morning brew, the same silent-upstream-change class the
   model pin exists to prevent.
7. **Docs corrected.** `docs/under-the-hood.html` publicly said **"Never pin the
   Gemini model version"** — the exact opposite of the live decision, so anyone
   acting on the published page would have undone the fix. It also described a
   backoff ladder and a no-grounding architecture that were both wrong. `README.md`
   still said "Gemini 2.5 Flash" in four places. `AGENTS.md` misdescribed its own
   error handling ("2s → 5s → 10s … exits with code 1"; the code exits **0** with a
   sentinel on purpose). `QUALITY_ROADMAP.md`'s anti-multi-agent argument was
   re-baselined: its quota premise is dead, but its job-timeout premise is now
   provable and stronger.

**Open — needs `GEMINI_API_KEY`, so it runs in CI or locally, not from a review
session:** the actual bake-off (step 2–3 above). Verify a candidate's free tier
with `--check-quota`, then run the incumbent against it at `--thinking-level low`
and gate on both latency and the three documented failure modes. Only then move
`DEFAULT_MODEL`.

**Lesson learned:** "should we upgrade the model" looked like a pricing question
and was actually a latency question with a per-model default hiding in it. The
budget that made it answerable — worst-case retry time vs. job timeout — had been
described in three docs and never once multiplied out. Write the invariant as a
test, not as a sentence.

---

## 2026-07-07 — Full Audit: Voice Overhaul (Bar-Buddy Persona, PG-13, Punch-Up Pass) + Security & Ops Fixes

**Context:** full project audit requested (effectiveness, security, everything). Owner's top complaint: Dan reads deadpan, not passionate or funny. Root-cause analysis found four compounding causes: (1) `gemini-3.1-flash-lite` hedges — comedy needs commitment; (2) the 291-line prompt is overwhelmingly prohibitions; (3) 14 judge rules + three layers of repetition policing sand off personality, with zero counter-pressure (there's a safety judge but nothing ever fails Dan for being boring); (4) every correction retry drifts more conservative. Also: the quota scarcity that shaped the whole Quality Roadmap is gone — 500 RPD vs ~5 used.

**Voice changes (owner-approved in interview):**
1. **Persona reframed** from "98.5 radio caller" to "your buddy from the bar who writes a daily newsletter" — written register, direct, personally invested.
2. **PG-13 bar-talk language tier**: damn/hell/sucks/crap/pissed/friggin' explicitly ALLOWED (matches how the persona's own templates already talked — "About damn time" was simultaneously suggested by the prompt and bannable by the judge). F/s-words and slurs stay hard-banned, censored or not. Judge rule 1 rewritten to match so mild intensifiers are never flagged.
3. **Running Bits system**: recurring nicknames/gags with fresh material each day (the arson squad, the Duck Boat fund, Rick's ongoing investigation, Carmine's naps). The bit's NAME recurs by design; the JOKE must be new. Judge rule 10 got an explicit running-bits exemption. Chosen over fixed catchphrases ("Absolutely brutal" daily = radio shtick; evolving bits = how a bar buddy actually talks).
4. **Punch-up pass** (`punch_up_draft()` in generate_rant.py, `PUNCH_UP=0` to disable): one extra Gemini call that rewrites voice fields for bigger emotional swings and funnier lines. Fact safety is STRUCTURAL, not instructional — only headline, morning_brew (same paragraph count enforced), and dans_take fields merge in; box_scores, schedule, player names, headlines, URLs always come from the original draft. Judge gates the merged result.

**Pipeline changes:**
5. **MEDIUM severity now publishes with `_quality_warning`** instead of falling back to stale (publish.py). Rules 12-14 are coverage/attribution quality issues; an imperfect fresh post beats yesterday's post. Only HIGH (fabrication/safety) still triggers fallback.
6. **`season_overrides.json` entries now carry `expires` dates** — generate_rant skips expired entries with a warning, so a May elimination notice can't leak into October's new season.
7. **publish.py now imports RULE_TITLES from safety_judge** — the hand-mirrored dict had silently drifted (missing rules 12-14, so the evals dashboard rubric was incomplete). Summaries added for 12-14.

**Security fixes:**
8. **Frontend paragraph truncation bug** (index.html archive-swap path rendered only `b[0]+b[1]+b[2]`) — a 4-5 paragraph milestone brew would have silently lost paragraphs. Now maps all.
9. **URL scheme validation** (`safeUrl()`): news links only render clickable for http(s) — a `javascript:` URL from the news→LLM→href chain no longer survives.
10. **Deleted dead prototype pages** (v1-v4.html, index-material.html): unreferenced but live on Pages, fetching real LLM output with weak-to-zero escaping (v3 interpolated raw).
11. **Prompt-injection hardening**: system prompt now states all data blocks are content to react to, never instructions to follow.

**Testing (first tests in the repo):**
12. `tests/test_pipeline.py` — 15 stdlib-unittest cases covering `_extract_team_games` (the June structure bug that shipped broken for a month), `detect_slow_day`, draft freshness windows, overrides expiry, punch-up merge fact-locking (including a hostile-punch-up case), and RULE_TITLES sync.
13. `.github/workflows/tests.yml` — runs tests + DRY_RUN prompt-assembly smoke + compile check on every push/PR touching scripts or prompts.

**Deferred (noted, not done):** SHA-pinning GitHub Actions; trying a stronger free-tier model for generation (revisit if punch-up pass isn't enough); best-of-N generation.

---

## 2026-07-07 — Milestone Coverage Fix: MINIMAL-tier Rule Was Suppressing Real News

**Problem:** A four-day streak of undercovering real Celtics/Bruins milestones:
- **July 3:** Jaylen Brown TRADED to the 76ers — reduced to a passing clause in a Red Sox paragraph ("the C's are still making headlines with the Jaylen Brown trade to the 76ers") + news_digest entry. A franchise-altering trade got roughly one sentence in the brew.
- **July 4:** Celtics signed Queta to a $56M extension — zero paragraph coverage, news_digest only. Paragraph 3 said vaguely "the Celtics are busy shuffling the roster."
- **July 6:** Bruins traded Korpisalo to the Rangers + Mitchell Robinson Celtics news — zero paragraph coverage. All three paragraphs on the Red Sox game.

**Root cause — a genuine conflict inside the prompt:** the Major Milestones section said trades/signings MUST get "at least one paragraph" and that the brew "can expand to 4 or 5 paragraphs" (note: *can*, not *must*). The Coverage Allocation section then said eliminated teams get "MINIMAL airtime… One to two sentences MAX, buried in a paragraph about active teams" and "a brief mention in paragraph 3 at most." The lite model was resolving the conflict by treating the restrictive rule as strongest and satisfying "MUST cover" with a news_digest entry or a single passing clause. This is consistent with the "reaching for the safer/generic option" pattern flagged in the 2026-07-01 misattribution note — the same class of failure, one level deeper.

**What shipped:**
1. **Coverage Allocation section rewritten** (`prompts/boston_dan_system.txt`) with an explicit "MILESTONE EXCEPTION" clause that overrides the MINIMAL rule when LATEST_NEWS has a real breaking milestone. Includes a concrete bad/good example built from the actual July 3 output. States plainly that a news_digest entry alone does not satisfy the milestone-coverage requirement.
2. **Major Milestones "can expand to 4-5 paragraphs" → "MUST expand"**. The output-format comment mirrors this: "You MUST extend to 4 or 5 when a Major Milestone needs coverage on the same day as game recaps."
3. **New judge rule 14 — "Milestone omission"** (`scripts/safety_judge.py`, MEDIUM severity): flags when LATEST_NEWS clearly indicates a MUST-COVER milestone (trade, signing/extension, firing, suspension, major injury, retirement, HoF) that's absent from morning_brew or reduced to a single passing clause. Detection cues include verbs like "traded," "signs," "extension," "fired," "suspended," "retires," and specific dollar figures. Explicit exception if 3+ milestones stack.

**Why this matters given the model swap:** the July 3, 4, and 6 misses are consistent with the earlier July 1 flag about the lite model reaching for safer/generic options. Milestone coverage is the concrete cost of that pattern — big stories quietly disappearing. Judge rule 14 turns the failure mode into a hard gate that triggers a regeneration retry.

---

## 2026-07-01 — Cross-Team Misattribution Fix (first output under `gemini-3.1-flash-lite`)

**Problem:** The first brew generated under the new pinned model (see entry below) had a real bug: paragraph 3 was entirely a Red Sox recap ("regroup and salvage the series... against Washington... afternoon matchup"), but contained the sentence "There is plenty of chatter around the league about the upcoming free agency period" with zero attribution. The actual LATEST_NEWS item was "NBA free agency 2026: Bobby Marks' 30-team preview" — an unrelated Celtics/NBA story blended into the Red Sox paragraph with no team/sport named, reading as if the Red Sox were affected by free agency chatter.

Separately (checking Bruins/Patriots weren't glossed over): both teams' news feeds that day were 3 generic league-wide roundups each (NHL free agency rankings, NFL offseason grades, etc.) — nothing team-specific enough to warrant coverage, so their silence was correct per the existing Coverage Allocation rule. But Celtics coverage picked the vaguest of 3 available headlines (generic free-agency preview) over a more specific, harder-to-write one that was skipped entirely: "Jaylen Brown's frustrations with Celtics are valid."

**Root cause:** the Coverage Allocation rule correctly instructs Dan to bury a MINIMAL-tier team's story inside another team's paragraph — but nothing required that buried mention to be attributed by team/sport name. The mechanism worked as designed; the safeguard was missing.

**What shipped:**
1. **New system prompt section "Cross-Team Attribution"** (`prompts/boston_dan_system.txt`, after Coverage Allocation): when a MINIMAL/SECONDARY team's story is folded into another team's paragraph, it must name the team/sport explicitly — especially for cross-sport shared vocabulary (free agency, trades, the draft). If it can't be attributed naturally in one sentence, cut it rather than leave it ambiguous.
2. **`news_digest` rule addition**: prefer a team-specific headline (named player, real development) over a generic league-wide roundup when both are available — targets the "picked the vaguer, easier-to-write story" pattern.
3. **New judge rule 13 — Cross-team misattribution** (`scripts/safety_judge.py`): MEDIUM severity if a paragraph clearly about team X contains an unattributed reference to a story that actually belongs to a different team/sport per LATEST_NEWS.

**Why this matters given the model swap:** this is the first content generated under `gemini-3.1-flash-lite`, and the failure pattern (vague blending, reaching for the safer/generic option over the more specific one) is consistent with a lighter model's weaker instruction-following. Worth watching the next several days of output for similar patterns before concluding the pin is a clean swap voice-wise.

---

## 2026-07-01 — Pin to `gemini-3.1-flash-lite`; Bounded Request Timeout

**Problem:** Three widely-spaced pipeline runs (12:18, 13:32, 14:22 UTC) all hit persistent `429 RESOURCE_EXHAUSTED` for over two hours — not the short demand spike the in-process retries and spaced safety-net cron slots are designed to absorb. A quota-error detail from one attempt named the actual model: `generativelanguage.googleapis.com/generate_content_free_tier_requests (model=gemini-3.5-flash)`. Google had promoted `gemini-3.5-flash` to the `gemini-flash-latest` alias, and its free tier was tight enough (likely a fresh-launch quota) to starve the whole day's runs. Separately, the 11:26 UTC run hit a worse failure mode: a single `generate_content()` call hung for ~21 minutes with zero response, and GitHub Actions force-cancelled the job at the 25-min timeout — worse than a clean failure, because the hard cancellation happened before `publish.py` ever ran, so there was no sentinel, no fallback, no commit, just a red X.

**What shipped:**
1. **Model pin**: `GEMINI_MODEL`/`JUDGE_MODEL` default changed from the `gemini-flash-latest` alias to `gemini-3.1-flash-lite`, pinned. Documented 500 RPD free-tier quota, comfortably above this pipeline's ~2–6 calls/day. This reverses the original 2026-04-xx decision to float on `-latest` for a higher quota — that reasoning broke the moment Google silently remapped `-latest` to a model with a worse quota than what we'd pinned against.
2. **Request timeout**: `genai.Client()` now sets `http_options=types.HttpOptions(timeout=90_000)` (90s) in both `generate_rant.py` and `safety_judge.py`. A hang now surfaces as a normal exception within 90s instead of blocking indefinitely, which the existing try/except around `call_gemini()` already handles correctly (write sentinel → `publish.py` fallback).
3. Updated `AGENTS.md` Model Strategy section, the free-tier model-availability list, env var docs, and dev-only eval tool defaults (`eval_models.py`, `model_eval.yml`) to match.

**Lesson learned:** floating on a "latest" alias trades one failure mode (a stale pinned model's quota degrading over time) for a worse one (an unannounced remap to a model with an unknown, possibly much tighter, quota — with zero warning and no way to detect it except by watching production fail). A pin is a known, stable quantity; re-evaluate only when the pinned model is actually deprecated.

---

## 2026-06-11 — Game Coverage Gap Fix: Slow-Day Detection Bug + Judge Rule 12

**Problem:** First run with the voice overhaul (June 11). Dan told a fictional 2004 ALCS story instead of covering a real Red Sox game that happened on June 10. All judges passed because no rule checked game coverage completeness.

**Root causes:**
1. `detect_slow_day()` accessed `rolling.get("redsox")` but rolling_7day structure is `{"days": [{"date": "...", "redsox": {"boxscore": {...}}}]}`. The function never found the game data and incorrectly returned `True`.
2. Same structural bug affected `compute_emotional_context()` and `compute_coverage_allocation()` — all three misread the rolling_7day nesting.
3. No judge rule required Dan to cover games that actually happened.

**What shipped:**
1. **`_extract_team_games()` helper** (`generate_rant.py`): New function that correctly navigates the rolling_7day `days → team → boxscore → games` structure. All three pre-pass functions now use it.
2. **`detect_slow_day()` fix**: Now checks specifically for yesterday's date (not just "most recent game"), uses the helper, and accepts `today_iso` parameter for testability.
3. **Judge rule 12 — Game coverage gap** (`safety_judge.py`): MEDIUM severity if rolling_7day shows a team played yesterday but morning_brew doesn't mention the game. Exception: 3+ teams on one day, covering 2 is acceptable.
4. **System prompt — "Game Coverage Is Mandatory"** (`boston_dan_system.txt`): Explicit instruction that yesterday's game results always take priority over slow-day stories, offseason speculation, or historical anecdotes. SLOW_DAY_MODE cannot override game coverage.

**Lesson learned:** Pre-pass functions must be tested against the actual data structure from `update_store.py`, not assumed. The rolling_7day nesting (`days[]` → team key → `boxscore` → `games[]`) is non-obvious and all three new functions from the voice overhaul got it wrong.

---

## 2026-06-10 — Dan Voice Overhaul: Emotional Range, Humor, Slow-Day Stories

**Problem:** Dan's output was consistently flat — uniform emotional tone regardless of outcome, zero humor, a full paragraph every day on eliminated teams with no news, and slow news days stretched thin with generic offseason filler. Reading a week of archives, wins and losses sounded the same, there were no jokes or comparisons, and the Celtics/Bruins playoff exits were being rehashed daily.

**What shipped:**

1. **Emotional Range** (system prompt): New section teaching Dan to modulate intensity based on game outcomes. Wins are euphoric, losses are agonizing, streaks build momentum, slumps escalate despair, blowouts are amplified, nail-biters are cardiac events, rival games get extra intensity.

2. **Coverage Allocation** (system prompt + pre-pass): Eliminated teams now get 1-2 sentences MAX unless there's breaking news. Active in-season teams get the bulk of morning_brew. A deterministic `compute_coverage_allocation()` pre-pass classifies each team as PRIMARY/SECONDARY/MINIMAL.

3. **Humor and Comparisons** (system prompt + data): Two humor modes — invented personal comparisons (cousin Jimmy, Sully, Uncle Carmine, Dan's pops, neighbor Rick) and real historical comparisons from HISTORICAL_FACTS. New `data/dan_stories.json` with 6 recurring characters and 10 comparison templates. Target: at least one genuinely funny line per brew.

4. **Slow Day Storytelling** (system prompt + pre-pass + data): When `detect_slow_day()` finds no games and minimal news, Dan tells a fictional personal story woven around real historical stats. New `data/story_seeds.json` with 14 historical-anchor seeds (2004 ALCS at a bar in Southie, 28-3 at Sully's basement, Game 7 at Uncle Carmine's). Stories are fictional but sports facts must be verifiable.

5. **Emotional Context Pre-Pass** (`generate_rant.py`): New `compute_emotional_context()` computes streaks, score margins, and rivalry flags from rolling_7day data. Injected as `EMOTIONAL_CONTEXT` block so Gemini gets explicit mood direction.

6. **Historical Facts Deepening** (`data/historical_facts.json`): Expanded from 3 iconic moments per team to 15-25 comprehensive entries covering 1980s through present. Added collapses, humor_angle fields, and new dynasties. Covers heartbreaks (Buckner, 18-1, 17 seconds), triumphs (Roberts' steal, 28-3, Banner 18), and memorable performances (Pedro's 17K, Bloody Sock, KG's "anything is possible").

7. **Quality Roadmap Update**: Added Tier 2 pre-passes (emotional, coverage, slow-day), Tier 3 quality checks (humor, offseason coverage), Tier 4 data files (dan_stories, story_seeds), and new Tier 6 (Voice Evolution — recurring characters building Dan's persistent world).

**Design decisions:**
- All changes are $0/month — pure prompt engineering + deterministic Python pre-passes
- Recurring fictional characters (cousin Jimmy, Sully, etc.) build Dan's world over time. Readers who come back daily will recognize them.
- Slow-day stories are a treat, not a daily feature. Only activate when `SLOW_DAY_MODE` is True.
- Stats discipline still absolute — even fictional stories must reference real verifiable data from HISTORICAL_FACTS or SEASON_MEMORY.

**Files changed:** `prompts/boston_dan_system.txt` (4 new sections), `scripts/generate_rant.py` (4 new functions + expanded build_user_message), `data/dan_stories.json` (new), `data/story_seeds.json` (new), `data/historical_facts.json` (deepened), `docs/QUALITY_ROADMAP.md` (updated)

---

## TODO — Audit All Past Season Data in `season_static.json`

**Priority: High.** The 2025 Patriots entry was wrong (recorded as "8-9, Missed playoffs" when they went 14-3 and lost Super Bowl LX). This slipped through because `season_static.json` is hand-curated and there's no automated validation against a source of truth.

**Action required:** Manually verify every entry in `season_static.json` against ESPN/Pro-Football-Reference/Basketball-Reference/Hockey-Reference/Baseball-Reference. Check wins, losses, and result strings for all teams, all years. Pay special attention to the most recent season (2025) for each team — this is the one most likely to be stale if the rollover procedure was missed or done incorrectly.

**Teams to audit:** Patriots, Celtics, Bruins, Red Sox — past 5 seasons each (years 2021–2025).

---

## 2026-06-07 — Patriots 2025 Season Data Fix + Guardrail Gap Documentation

**Commits:** `0568c46`

**What shipped:**
- `season_static.json` corrected: 2025 Patriots entry updated from "8-9, Missed playoffs" to "14-3, Lost Super Bowl LX to Seattle Seahawks 29-13 — Vrabel's breakthrough year"
- `updated` timestamp bumped to 2026-06-07

**What went wrong:** Dan wrote "after a couple of lean years where we missed the postseason entirely" about the Patriots. The safety judge let it through — Rule 8 (Fabricated historical events) cross-references Dan's claims against `season_static.json`, but `season_static.json` itself had the wrong result for 2025. Dan's (incorrect) claim matched the (also incorrect) source data. No mismatch → PASS.

**Guardrail gap:** Rule 8 is only as reliable as the source data it checks against. When `season_static.json` is stale, it can't catch factual errors — it will actively corroborate them. The rollover procedure (CLAUDE.md) requires manual updates after each season concludes; this entry was missed after the February 2026 Super Bowl.

**Mitigation:** No code change needed — the guardrail logic is correct. The fix is data hygiene: update `season_static.json` promptly after each season ends. See the TODO above for a full audit of all entries.

---

## 2026-05-13 — Season Overrides + Playoff Audit

**Commits:** `2f73401`, `517d992`

**What shipped:**
- New `data/season_overrides.json` — manually maintained git-tracked file that records playoff eliminations with explicit prose rebuttals
- Both 2026 Bruins and Celtics eliminations recorded with full series context
- `generate_rant.py` loads and injects as `SEASON_OVERRIDES` block before `SEASON_MEMORY`
- `prompts/boston_dan_system.txt` — new section with explicit correct/wrong framing examples

**Why:** Dan kept writing "McAvoy can't miss games in the thick of it" even after the Bruins were eliminated. Root cause: he was inferring playoff stakes from news story language, not from team status. The schedule-based `classify_status()` fix (see below) was too fragile — it depended on timing between pipeline steps and could fail silently. A manually-maintained file with direct prose rebuttals ("this is an off-season footnote, not a crisis") is deterministic and can't be overridden by a news story's framing. Same issue applied to the Celtics after their 3-1 collapse.

**Key design decision:** Override file uses prose notes (`season_over_note`) rather than raw JSON flags. Dan reads sentences; a data field like `"status": "eliminated"` doesn't prevent narrative drift the way "Do NOT frame Bruins stories with playoff urgency" does.

**Maintenance:** Edit `data/season_overrides.json` when a Boston team is eliminated. Clear the `eliminations` object at the start of next season. One file, one push.

**Bruins series:** Lost 4-2 to Buffalo Sabres in Round 1 (eliminated May 1). Wild Card 1 seed. Lost all three home games at TD Garden. Jeremy Swayman was a Vezina finalist but no secondary scoring support.

**Celtics series:** Lost 4-3 to Philadelphia 76ers in Round 1 (eliminated May 2). 2nd seed, 56-26. Blew a 3-1 series lead — first time in franchise history. Jayson Tatum returned from torn Achilles in March but couldn't close it in Game 7 at home.

---

## 2026-05-12 — Stat Fabrication Fix (RECENT_DAN_OUTPUT as fact source)

**Commits:** `3de753b`

**What shipped:**
- New section in `boston_dan_system.txt`: "RECENT_DAN_OUTPUT is NOT a source of stats"
- Enhanced Stats Discipline section with explicit reminder about past output
- `generate_rant.py` correction notes now extract specific numbers from judge flags and tell Dan explicitly: "do NOT use these: [list]"

**Why:** Attempt 1 failed because Dan cited "Mickey Gasper had three hits and the top of the order went zero for eight" — stats not present in ROLLING_7DAY. Attempt 2 had correction notes but Dan invented different stats for the same narrative. Root cause: Dan was reading RECENT_DAN_OUTPUT (his own past commentary) and treating it as a fact source. He wrote the Gasper stats yesterday, saw them in his archive today, and repeated them as verified. The fix draws a hard line: RECENT_DAN_OUTPUT is phrasing reference only, not fact reference. Every stat must be verified in ROLLING_7DAY or SEASON_MEMORY.

---

## 2026-05-12 — Fallback Aggression Fix + Retry Budget

**Commits:** `a33657c`, `docs sync a9d0d28`

**What shipped:**
- `publish.py`: `MAX_JUDGE_ATTEMPTS` raised from 2 → 3
- New LOW-severity publish path: if all attempts exhaust with only LOW severity, publish with `_quality_warning: true` flag instead of triggering fallback
- Only HIGH severity triggers stale fallback
- Correction notes now extract flagged numbers and explicitly block re-use
- `under-the-hood.html` synced to reflect these changes

**Why:** May 10 pipeline fell back to stale content over a single voice-repetition flag (`sitting at seventeen and twenty-three` — a phrasing repeat from the prior day). That's a LOW severity violation and shouldn't serve day-old content to readers. HIGH severity (fabricated stats, profanity, character attacks) is a content integrity issue worth falling back for. LOW severity is a voice quality issue — better to publish with a warning than not publish.

---

## 2026-05-11 — Playoff Status Fix (classify_status)

**Commits:** `d1dae84`

**What shipped:**
- `fetch_season_memory.py`: added `team_has_upcoming_games()` helper that cross-references `upcoming_schedule.json`
- `classify_status()` now verifies NHL/NBA teams actually have upcoming games before labeling them "in_playoffs"
- Fail-safe: if schedule file is missing or empty, falls back to calendar heuristic rather than incorrectly declaring elimination

**Why:** Calendar-based classifier treated all of April-June as "in_playoffs" for NHL/NBA regardless of whether a team was eliminated. Bruins were out but their status still said "in_playoffs" in SEASON_MEMORY, which Dan picked up and ran with. The fix uses the already-fetched `upcoming_schedule.json` as a secondary signal. Note: this fix was necessary but not sufficient — news-story-level playoff framing required the `season_overrides.json` approach (see above).

---

## 2026-05-08 — Evals Dashboard + Under the Hood Page

**Commits:** `de41dd6`, `ab2e707`, `6de6297`

**What shipped:**
- Evals dashboard backend: `publish.py` now writes `data/dan_archive/YYYY-MM-DD.evals.json` capturing full pipeline trace (outcome, per-attempt judge verdicts, timing, pre-pass results)
- `docs/data/evals/` published to GitHub Pages: `index.json` (5-day summary + rule rubric) and per-day evals files
- Evals dashboard frontend: "How was this generated?" surface inline with each post — outcome chip (fresh/retry/fallback), judge flags per attempt, 5-day stats strip
- `docs/under-the-hood.html`: full portfolio architecture page with 7 pipeline stages, "Simulate a Run" animation, Architecture Decision Records, Evolution Timeline, Data Schema Visualizer, and live stats strip

**Why:** No visibility into why a given day's output passed or failed made debugging slow. The evals dashboard makes pipeline health observable without digging into GitHub Actions logs. The Under the Hood page is the "click here and I'll narrate the whole system" artifact for interviews — walk through each stage, the design decisions, and how the system evolved.

---

## 2026-05-07 — Roster Discipline

**Commits:** `2c7a8db`, `619d298`, `dcebe65`, `52c6f2e`, `aef5f1d`, `15659a6`

**What shipped:**
- `fetch_roster.py`: daily active roster fetch for all 4 Boston teams (ESPN + NHL API)
- `CURRENT_ROSTER` injected into prompt; Roster Discipline persona rule added
- Judge rule 11: flags MEDIUM severity when Dan implies an unlisted player is on the team
- Rosters loaded into safety judge `source_data` for cross-checking
- Eval fixture for off-roster player scenario

**Why:** Dan was writing about released or unsigned players as if they were on the current roster — treating any name in LATEST_NEWS as a team member. The fix injects an authoritative roster and adds an explicit rule: unlisted = not on the team, do not link their news to team strategy or prospects.

---

## 2026-05-04 — Continuity Memory, Caller Flavor, Repetition Pre-pass, Draft Freshness

**Commits:** `896e9b7`, `068b5fc`, `0b67007`, `c93a8ce`, `d54f5a3`, `caller/grudge 068b5fc`

**What shipped:**
- Memory window extended 3 → 5 days; historical facts rotation rule added (don't cite same championship/moment in consecutive days)
- `callers_and_voices.json` and `grudge_book.json` — voice flavor pool and rivalry context injected daily
- Deterministic repetition pre-pass: regex-based check for exact phrase repetition before LLM judge runs (~2ms, free)
- Judge rule 10: LLM-level repetition check for subtler voice repetition
- Event freshness pre-pass: draft coverage decays from active → fresh → aging → stale; `DRAFT_PICKS` omitted once stale
- Regression fixtures for freshness and repetition scenarios

**Why:** Dan was reusing "18 banners" and other historical factoids almost every day on a 3-day memory. Extending to 5 days caught weekly crutches. Caller archetypes and grudge book gave Dan raw material for texture instead of generic takes. The pre-pass catches cheap repetition without burning LLM tokens on the judge. Draft decay prevented week-long NFL draft recaps.

---

## 2026-04-27 — Continuity Memory v1

**Commits:** `3a848e4`

**What shipped:**
- Dan reads last 3 days of his own `morning_brew` + `news_digest` before writing
- `data/dan_archive/` stores slim daily snapshots; `publish.py` writes them after each fresh publish
- Continuity rules in persona: don't re-introduce stories, evolve takes, vary signature phrases

**Why:** Dan was re-introducing the Cora firing as if it were new news three days running. No memory of what he'd already said meant the same narrative arc, same closing phrases, same historical factoids every day.

---

## 2026-04-26 — Safety Judge Retry + Pipeline Reliability

**Commits:** `5fadd49`, `1adc9b5`, `b59f8b6`, `e264078`, `e410840`

**What shipped:**
- Retry on safety judge FAIL: correction notes injected via `CORRECTION_NOTES` env var, Dan regenerates
- Sentinel fallback pattern: `generate_rant.py` writes `_generation_failed` JSON instead of exiting 1, so `publish.py` is always the fallback decision point
- `generated_at` timestamps on all published output
- Concurrency guard (`concurrency: morning-brew`) prevents duplicate runs
- Safety-net cron at 04:30 ET catches failed 03:00 ET runs
- Gemini retry budgets capped so retries can't burn the full job timeout
- Draft coverage deepened; firing tone fixed; same-day causation rule added

**Why:** Single-attempt pipeline meant one bad output = stale fallback. Multiple failure modes (Gemini 503, bad JSON, judge FAIL) were all routing to the same undifferentiated failure. Separating sentinel from real failure gave `publish.py` the information it needed to make the right call. Causation rule added after Dan wrote "the team responded to the firing with a 7-2 win" when the game happened before the firing.

---

## 2026-04-24 — Season Memory Module

**Commits:** `3db33fc`

**What shipped:**
- `fetch_season_memory.py`: daily ESPN fetch for current-season record, seed, status for all 4 teams
- `season_static.json`: hand-curated last 5 seasons per team (checked into git)
- `SEASON_MEMORY` injected into prompt; judge loads it as source_data for stat verification
- Status-conditional output shape: `regular_season` / `in_playoffs` / `offseason`

**Why:** Dan was treating every game as context-free. No awareness of standings, seeds, or whether a team was in a rebuild or a championship window. Season memory gave him historical grounding — "3rd straight first-round exit" instead of just reacting to last night's score.

---

## 2026-04-24 — Historical Facts Module

**Commits:** `170cae2`

**What shipped:**
- `historical_facts.json`: curated championships, dynasties, iconic moments, curses, rivalries for all 4 teams (checked into git)
- `HISTORICAL_FACTS` injected into prompt; judge validates historical claims against it
- Persona rule: history is color (20% of a paragraph), not primary content

**Why:** Dan was either inventing history (wrong championship years, nonexistent moments) or ignoring it entirely. Curated facts give him accurate color without hallucination. Judge cross-references so invented historical claims fail.

---

## 2026-04-24 — Draft Coverage

**Commits:** `357ec9d`, `5409c17` (fetch_draft), `eade7e2` (prompt rules)

**What shipped:**
- `fetch_draft.py`: daily ESPN draft fetch for all 4 Boston teams
- `DRAFT_PICKS` injected into prompt with round/pick/player/position/college
- Persona rules: mandatory per-pick coverage when active, grade each pick in Dan's voice
- `last_active_date` differential to detect when draft is actually live (not just when ESPN serves old picks)

**Why:** The NFL draft is the biggest off-season event and Dan was either skipping it or making up player details. Explicit pick data with names, positions, and colleges gives him accurate raw material. The `last_active_date` trick was needed because ESPN serves completed picks year-round — pick count growing is the only reliable "draft is live" signal.

---

## 2026-04-23 — Safety Judge + Retry Infrastructure

**Commits:** `d6c5d28`, `1e53eb8`, `c649a35` (model switching)

**What shipped:**
- Safety judge timeout raised 60s → 300s; timeout treated as PASS (don't block publication over API latency)
- Extended retry backoff for Gemini 503s
- Model switched to `gemini-flash-latest` alias for higher daily quota vs. pinned versions
- `force` workflow input to bypass freshness gate for manual reruns

**Why:** Judge was timing out at 60s during Gemini load spikes and blocking publication. Treating timeout as PASS was the right call — the deterministic pre-pass still catches obvious violations, and losing occasional nuanced violations is better than serving no content. `gemini-flash-latest` alias gets higher free-tier quota than pinned model versions.

---

## 2026-04-22 — Frontend Polish Sprint

**Commits:** `4783f9a`, `42690bf`, `be33e8b`, `511afdb`, `d9a3c07`, `8dc43bc`, `7774ecc`, `049cc48`, `a4f1c29`

**What shipped:**
- Schedule built from fetcher data (not Gemini) — Celtics playoff games were being dropped
- Celtics playoff schedule fetch (seasontype=3 alongside regular season)
- Fenway/Yawkey Way persona fix — Dan was conflating the stadium name with the street
- Scoreboard: Boston always on left, headline truncation fixed, proportional mobile scaling
- Widget cards full-width on mobile and tablet
- Material Symbols icons replace emoji

**Why:** Gemini was selectively omitting the Celtics from the schedule during playoffs — it didn't understand that playoff games were still "upcoming." Building schedule from the authoritative fetcher data fixed this permanently. UI fixes came from mobile testing revealing multiple layout breaks.

---

## 2026-04-21 — v4 Frontend + Garden Slate Design System

**Commits:** `d55d73b`, `5e594af`, `3d71943`

**What shipped:**
- "The Broadsheet Columnist" layout: Morning Brew as the lead story, scores and schedule as supporting rail
- Garden Slate design system: CSS tokens (`--primary`, `--surface-*`, `--font-*`), Anton headlines, Inter body, JetBrains Mono for code
- README rewrite with pipeline diagram

**Why:** Earlier frontend iterations were too generic. The broadsheet layout prioritizes the thing that makes the site unique — Dan's voice — rather than treating it as a dashboard with equal-weight widgets.

---

## 2026-04-21 — Week 3 Infrastructure

**Commits:** `d08a58d`, `1cdc7ae`, `fda3a42`, `7279354`

**What shipped:**
- `publish.py`: safety gate, fallback logic, writes to `docs/data/daily_output.json`
- `healthcheck.py`: final validation before workflow succeeds
- `morning_brew.yml`: GitHub Actions cron pipeline
- Frontend moved to `/docs` for GitHub Pages

**Why:** Without `publish.py` as the single decision point, failures at any pipeline step would produce no output. The safety gate ensures either Dan's content or a clean fallback always reaches the site.

---

## 2026-04-19 — Week 2: Persona, Generation, Safety Judge

**Commits:** `9e989bc`

**What shipped:**
- `boston_dan_system.txt`: full persona definition — voice rules, stats discipline, off-field conduct framework, safety rules
- `generate_rant.py`: assembles context, calls Gemini Flash, writes structured JSON
- `safety_judge.py`: 11-rule rubric, PASS/FAIL + severity verdict
- Eval harness (`eval_voice.py`) for manual testing with fixtures

**Why:** The core product. Everything else is infrastructure around making Dan's voice consistent, factually grounded, and safe enough to publish without human review.

---

## 2026-04-17 — Week 1: Data Foundation

**Commits:** `bfa4aa2` through `6c6dc2d`

**What shipped:**
- `fetch_nhl.py`, `fetch_mlb.py`, `fetch_nfl.py`, `fetch_nba.py` — boxscores, schedules, news from ESPN + NHL + MLB APIs
- `update_store.py` — rolling 7-day aggregator
- `fetch_schedule.py` — unified upcoming schedule
- `fetch_news.py` — unified news feed
- Playoff mode detection across all fetchers
- `CLAUDE.md` with full project context and build plan

**Why:** Dan needs a 7-day memory window, not just last night's score. Scores alone miss the stories — trades, injuries, suspensions come through news, not boxscores. All fetchers write empty-but-valid JSON on failure so downstream scripts don't crash regardless of API availability.
