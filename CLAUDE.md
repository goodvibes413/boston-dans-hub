# CLAUDE.md — Boston Dan's Hub

## What This Project Is

Boston Dan's Hub is a public-facing static website featuring an AI-generated Boston sports fan persona ("Boston Dan") that produces daily automated commentary, box scores, trend analysis, and schedules for the Celtics, Bruins, Red Sox, and Patriots. All content is pre-generated and cached — the site is fully static.

**Target operating cost: $0/month** (Gemini free tier + GitHub Actions + GitHub Pages).

---

## Repository

- **GitHub repo**: `goodvibes413/boston-dans-hub`
- **Local path**: your local clone of `goodvibes413/boston-dans-hub`
- **Live site** (when deployed): `https://goodvibes413.github.io/boston-dans-hub/`

---

## Directory Structure

```
/
├── scripts/          # Python data fetchers and generation scripts
├── prompts/          # Persona / system prompt source-of-truth (boston_dan_system.txt)
├── data/             # JSON data files (gitignored — changes daily)
├── evals/
│   ├── fixtures/     # Hand-crafted rolling_7day-shaped test inputs
│   └── runs/         # Generated outputs for manual review (gitignored)
├── site/             # Static website files (deployed via GitHub Pages)
│   └── data/         # daily_output.json served to the frontend
├── docs/             # Internal documentation (e.g., SAFETY.md)
├── .github/
│   └── workflows/    # GitHub Actions (morning_brew.yml)
├── CLAUDE.md         # This file
└── README.md
```

---

## Tech Stack & Constraints

| Layer | Tool | Notes |
|---|---|---|
| Data fetching | Python stdlib only (`urllib`, `json`) | No third-party HTTP libs |
| LLM generation | `gemini-flash-latest` via `google-genai` | Read key from `GEMINI_API_KEY` env var; override via `GEMINI_MODEL`. **IMPORTANT: Use the `-latest` alias, not pinned versions like `gemini-2.5-flash`** (see Model Strategy below) |
| Safety judge | `gemini-flash-latest` via `google-genai` | Same model, separate call. Override via `JUDGE_MODEL` |
| Frontend | Vanilla HTML/CSS/JS | No build tools — pure `fetch()` loads `daily_output.json`, renders dynamically |
| CI/CD | GitHub Actions | Daily cron at 03:00 ET (08:00 UTC) — moved from 06:00 ET to avoid peak API demand |
| Hosting | GitHub Pages | `/site` folder → https://goodvibes413.github.io/boston-dans-hub/ |
| Sports data | Public ESPN + NHL + MLB APIs | No auth keys required |

**SDK**: Use `google-genai` (`from google import genai; from google.genai import types`). The old `google-generativeai` package is fully deprecated — do not use it.

**Never** add third-party Python packages beyond `google-genai` without discussion. The goal is a minimal, auditable dependency footprint.

### Model Strategy: `gemini-flash-latest` for Higher Daily Quota

**Decision: Always use `gemini-flash-latest` alias, NOT pinned versions like `gemini-2.5-flash`.**

**Why:**
- **Higher daily request limit**: Google's free tier grants higher daily request quotas to `gemini-flash-latest` (the officially recommended latest model) compared to older pinned versions. This allows our pipeline (generation + safety judge = 2 calls/day, plus retries on transient failures) to stay within free tier limits.
- **Pinned versions have lower quotas**: Once a Flash model is pinned (e.g., `gemini-2.5-flash`), Google allocates it a lower daily quota. Using pinned versions would exhaust our quota faster and force paid upgrades.
- **API demand spike resilience**: When `gemini-flash-latest` experiences high load (503 UNAVAILABLE), the retry logic in `generate_rant.py` and `safety_judge.py` (7 retries, up to 180s backoff) handles transient spikes. These are expected during peak times — the system is designed to wait it out.

**Do not change this** — it directly impacts our free tier quota and ability to run the daily pipeline. If you see a 503 error in the logs, it's a transient spike; check the logs for retry backoff messages. The pipeline will succeed on the next attempt.

**Note**: Both `gemini-flash-latest` and pinned versions are free — the difference is in the daily request quota allocation.

---

## The Daily Pipeline (in order)

```
fetch_nba.py        → data/celtics_boxscore.json + data/celtics_schedule.json
fetch_nhl.py        → data/bruins_boxscore.json  + data/bruins_schedule.json
fetch_mlb.py        → data/redsox_boxscore.json  + data/redsox_schedule.json
fetch_nfl.py        → data/patriots_news.json  (offseason: headlines only)
    ↓
update_store.py            → data/rolling_7day.json  (rolling 7-entry window)
fetch_schedule.py          → data/upcoming_schedule.json  (merged, sorted)
fetch_news.py              → data/latest_news.json  (merged, most-recent-first)
fetch_season_memory.py     → data/season_current.json  (current records/seeds/status)
    ↓
generate_rant.py    → data/raw_dan_output.json  (Gemini 2.5 Flash + grounding)
    ↓
safety_judge.py     → PASS / FAIL + severity  (Gemini 2.5 Flash)
    ↓
publish.py          → site/data/daily_output.json  (or safe fallback)
    ↓
healthcheck.py      → validates all JSON files are parseable
```

On any fetch failure: write an empty-but-valid JSON so downstream scripts don't crash.

---

## Scripts Reference

| Script | Status | Outputs |
|---|---|---|
| `scripts/fetch_nba.py` | ✅ Done | `celtics_boxscore.json`, `celtics_schedule.json`, `celtics_news.json` |
| `scripts/fetch_nhl.py` | ✅ Done | `bruins_boxscore.json`, `bruins_schedule.json`, `bruins_news.json` |
| `scripts/fetch_mlb.py` | ✅ Done | `redsox_boxscore.json`, `redsox_schedule.json`, `redsox_news.json` |
| `scripts/fetch_nfl.py` | ✅ Done | `patriots_news.json`, `patriots_boxscore.json`, `patriots_schedule.json` |
| `scripts/update_store.py` | ✅ Done | `rolling_7day.json` (7-entry rolling window) |
| `scripts/fetch_schedule.py` | ✅ Done | `upcoming_schedule.json` (merged, sorted) |
| `scripts/fetch_news.py` | ✅ Done | `latest_news.json` (merged, most-recent-first) |
| `scripts/fetch_season_memory.py` | ✅ Done | `season_current.json` (current records/seeds/status from ESPN) |
| `scripts/generate_rant.py` | ✅ Done | `raw_dan_output.json` (loads persona from `prompts/boston_dan_system.txt`) |
| `scripts/eval_voice.py` | ✅ Done | `evals/runs/{label}_{N}.json` (manual eyeball harness) |
| `scripts/safety_judge.py` | ✅ Done | PASS/FAIL + severity verdict (Gemini 2.5 Flash) |
| `scripts/publish.py` | ✅ Done | `site/data/daily_output.json` (or safe fallback on judge failure) |
| `scripts/healthcheck.py` | ✅ Done | Validates `site/data/daily_output.json` is parseable + complete |

---

## Frontend Files (Week 4)

| File | Purpose |
|---|---|
| `site/index.html` | Main page structure — sections for Morning Brew, Trends, News, Scores, Schedule |
| `site/style.css` | Boston Dan aesthetic — dark theme, Celtics green (#00A651), Red Sox red (#BD3039), Anton font for headings |
| `site/app.js` | Fetch `data/daily_output.json`, render sections, fallback detection, XSS protection |
| `site/data/daily_output.json` | Published Dan output (generated daily by GitHub Actions cron) |

**Deployment**: GitHub Pages auto-deploys from `/site` folder on every `git push` to `main`.

---

## Design System: "The Garden Slate"

The UI uses a cohesive design system with a Boston sports color palette, clean typography, and component hierarchy.

### Color Palette

| Role | Token | Hex | Usage |
|---|---|---|---|
| **Primary** | `--primary` | `#00D084` | Accent color, active states, CTAs, success states |
| **Secondary** | `--secondary` | `#008456` | Secondary accents, muted interactions |
| **Tertiary** | `--tertiary` | `#4A5568` | Borders, dividers, subtle UI elements |
| **Neutral** | `--neutral` | `#1E1E1E` | Dark backgrounds, high contrast text |
| **Surface Highest** | `--surface-highest` | — | Widget card backgrounds (slightly elevated) |
| **Surface High** | `--surface-high` | — | Subtle dividers, second-level surfaces |
| **On Surface Muted** | `--on-surface-muted` | — | Muted text, labels, secondary content |

**Dark Theme**: All UI is dark-theme optimized. Primary green (#00D084) provides the only bright accent.

### Typography

| Role | Font | Weight | Size (desktop) | Size (mobile) | Usage |
|---|---|---|---|---|---|
| **Headline** | Anton | 400 (regular, bold by design) | 3.25rem | 2.5rem | Page titles, headlines, major beats — all-caps or title-case |
| **Body** | Inter | 400 (regular) | 1rem (16px) | 0.875rem (14px) | Paragraphs, descriptions, news text |
| **Label** | Inter | 500 (medium) | 0.75rem (12px) | 0.6875rem (11px) | Widget headers, category tags, metadata |

**Line Height**: 1.6 for body text (readability); 1.05–1.2 for headlines (compact, bold presence).

**Font Notes**:
- **Anton** is a bold sans-serif with strong geometric letterforms — requires minimal weight to feel impactful
- **Inter** is a clean, readable sans-serif optimized for body and UI text

### Component Patterns

#### Buttons
- **Primary**: Solid background (`--primary`), white/dark text, 6px border-radius
- **Secondary**: Solid background (`--secondary`), white/dark text, 6px border-radius
- **Outlined**: Transparent background, `--primary` border (2px), `--primary` text, 6px border-radius
- **Inverted**: Light background on dark theme, high contrast text

#### Cards & Widgets
- Background: `--surface-highest` with subtle elevation
- Border radius: 6px
- Padding: 16px internal, 12px between rows
- Dividers: 1px `--surface-high` color
- Hover state: Slight brightening of background or primary accent on interactive elements

#### Text Hierarchy
- **Primary text** (on-surface): High contrast, legible
- **Muted text** (on-surface-muted): Secondary information, metadata, timestamps

#### Spacing Scale
Use these consistent intervals for padding, margins, and gaps:
- 4px, 6px, 8px, 10px, 12px, 16px, 20px, 24px, 32px

### Implementation in docs/index.html

The CSS variables are defined in the `<style>` block and consumed throughout:

```css
:root {
  --primary: #00D084;
  --secondary: #008456;
  --tertiary: #4A5568;
  --neutral: #1E1E1E;
  --surface-highest: /* dark gray, slightly lighter than bg */;
  --surface-high: /* medium gray, used for dividers */;
  --on-surface-muted: /* light gray, used for muted text */;
}
```

**Always use CSS variables, never hardcode hex colors.** This ensures consistency and makes theme changes (e.g., light mode) trivial.

### Responsive Design Tiers

- **Mobile** (≤767px): Compact spacing (8–12px), reduced font sizes, simplified component layouts
- **Tablet** (768–1023px): Medium spacing (12–16px), moderate font sizes
- **Desktop** (≥1024px): Full spacing (16–24px), full-size fonts, rich layouts

Override tokens inside `@media` queries as needed. **Keep mobile-first**: define base styles for mobile, then use `@media (min-width: ...)` to enhance for larger screens.

### Design Principles

1. **Hierarchy Through Color**: Primary green is the *only* bright color. Use it sparingly for actions and highlights.
2. **Dark Theme**: All surfaces are dark; text is light. High contrast ensures readability.
3. **Consistency**: Every interactive element uses the same button/card treatment. No one-offs.
4. **Density**: Tight spacing on mobile, generous spacing on desktop — respect screen real estate constraints.
5. **Boston Aesthetic**: The green (#00D084) echoes Boston sports (Celtics green). Paired with dark backgrounds, it feels modern and bold.

---

## Gemini API Patterns

### Client setup (new SDK)
```python
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
resp = client.models.generate_content(
    model=model_name,
    contents=user_message,
    config=types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.9,
    )
)
```

### Grounding + JSON conflict
`response_mime_type="application/json"` is incompatible with search grounding in the new SDK.
**Two-attempt strategy**: first call with grounding ON (no mime type), retry with grounding OFF + `force_json=True` if JSON parse fails.

### Retry logic (503/429)
Both `generate_rant.py` and `safety_judge.py` use `call_with_retry()`:
- 503 UNAVAILABLE: exponential backoff 2s → 5s → 10s (3 retries)
- 429 QUOTA_EXCEEDED: parse `retryDelay` from error response and wait that duration
- Other errors (400, 401): fail immediately, no retry
- On exhaustion: exit with code 1, let next cron run retry

### Free tier model availability
- `gemini-2.5-flash`: ✅ free tier available
- `gemini-2.5-pro`: ❌ no free tier (limit: 0) — do not use as default

---

## The Eval Workflow

Fixtures are synthetic test cases — they don't need to match real game data. They're designed to test specific behaviors.

```bash
# Run a single fixture once
python3 scripts/eval_voice.py --fixture evals/fixtures/accuracy_tatum_22pts.json --n 1

# Run multiple times to check consistency
python3 scripts/eval_voice.py --fixture evals/fixtures/voice_no_games.json --n 3
```

**Reading the summary output:**

| Field | What to check | Red flag |
|---|---|---|
| `keys` | All 5 keys present? | Missing any of: morning_brew, trend_watch, news_digest, box_scores, schedule |
| `brew_paragraphs` | Should be 3 | Anything other than 3 |
| `brew_words` | 150–300 is healthy | Under 120 = too thin; over 400 = rambling |
| `news_count` | ≥0; matches relevant headlines in fixture | 0 when fixture has relevant news = Dan missed it |
| `news_headlines` | Cross-check against fixture | Personal news (divorce etc.) should NOT appear |
| `stat_numbers` | Every number must exist in fixture data | Number present with no fixture match = hallucination |

**Fixture design rules:**
- Use fictional player names for any sensitive scenarios (conduct violations, off-field news)
- Real player names only for stats/performance fixtures (accuracy, memory, voice)
- Synthetic dates and scores are fine — fixtures test behavior, not real game data
- Document pass/fail criteria in a `_fixture_notes` key

**Taking action on eval results** — all persona changes go in `prompts/boston_dan_system.txt`:
- Dan cites wrong stats → tighten Stats Discipline section
- Dan mentions off-field personal news → add specific pattern to Safety section
- Dan sounds generic → add specific Boston-isms or phrasings
- Dan repeats catchphrases → add "vary your expressions" rule
- Safety judge FAILs → read flags, trace to output line, tighten persona AND judge rubric

---

## Sports API Endpoints

| Team | Endpoint |
|---|---|
| Celtics (NBA) | `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=YYYYMMDD` |
| Celtics schedule | `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/2/schedule` |
| Celtics boxscore | `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={id}` |
| Bruins (NHL) | `https://api-web.nhle.com/v1/score/YYYY-MM-DD` |
| Red Sox (MLB) | `https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=YYYY-MM-DD&teamId=111&hydrate=linescore,boxscore` |
| Patriots (NFL) | `https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?team=ne` |

**Celtics team ID**: `"2"` (string). Always use UTC for date parameters — ESPN is UTC-anchored.

---

## Key Data Schemas

### Boxscore Schemas (all sports)

Every boxscore output includes a `"season_type"` field:
```json
{
  "game_date": "2025-04-06",
  "played": true,
  "season_type": "regular",
  ...other fields...
}
```

**Season type values:**
- `"preseason"` — Practice games before regular season
- `"regular"` — Regular season play
- `"playoff"` — Postseason play
- `"offseason"` — No games
- `"unknown"` — Unable to classify (should be rare)

### `data/rolling_7day.json`
```json
{
  "days": [
    {
      "date": "2025-04-06",
      "celtics": {
        "boxscore": { "game_date": "...", "played": false, "season_type": "regular" },
        "news":     { "generated_at": "...", "headlines": [...] }
      },
      "bruins":   { "boxscore": {...}, "news": {...} },
      "redsox":   { "boxscore": {...}, "news": {...} },
      "patriots": { "boxscore": {...}, "news": {...} }
    }
  ]
}
```
Max 7 entries. Oldest entry dropped when a new day is appended.

### `data/upcoming_schedule.json`
```json
{
  "generated_at": "2026-04-07T16:00:00+00:00",
  "games": [
    {
      "sport": "NHL", "team": "bruins", "date": "2026-04-07",
      "time_et": "7:00 PM ET", "home_team": "Carolina Hurricanes",
      "away_team": "Boston Bruins", "season_type": "regular"
    }
  ]
}
```

### `data/season_static.json` (in git — hand-curated past seasons)
```json
{
  "updated": "2026-04-21",
  "celtics": {
    "past_seasons": [
      { "year": 2024, "wins": 64, "losses": 18, "result": "NBA Champions — beat Mavericks 4-1 in Finals" }
    ]
  },
  "bruins":   { "past_seasons": [ { "year": 2024, "record": "47-20-15", "result": "Lost Round 2 vs Panthers 4-2" } ] },
  "redsox":   { "past_seasons": [ { "year": 2024, "wins": 81, "losses": 81, "result": "Missed playoffs" } ] },
  "patriots": { "past_seasons": [ { "year": 2024, "wins": 4, "losses": 13, "result": "Missed playoffs" } ] }
}
```
**Year convention**: end-year of the season (e.g. 2024 = 2023–24 NBA/NHL season, or 2024 MLB/NFL season).
**Versioning**: checked into git via a `!data/season_static.json` exception in `.gitignore`.
**Rollover procedure**: once per year after a season concludes, edit this file to prepend the just-finished season and drop the oldest entry (keep 5 seasons), bump `updated`, commit with message `chore: rollover season_static after {sport} {year}`.

### `data/season_current.json` (gitignored — fetched daily)
Shape is status-conditional. `fetch_season_memory.py` writes one entry per team plus a `generated_at` timestamp.

**Regular season**:
```json
{ "status": "regular_season", "wins": 40, "losses": 20, "win_pct": 0.667, "playoff_seed": 1, "conference": "Eastern Conference", "division": "Atlantic Division", "streak": "W4" }
```
**In playoffs**:
```json
{ "status": "in_playoffs", "regular_season_wins": 52, "regular_season_losses": 30, "regular_season_summary": "52-30", "playoff_seed": 1 }
```
**Offseason**:
```json
{ "status": "offseason", "last_season_wins": 7, "last_season_losses": 10, "last_season_summary": "7-10" }
```

**Runtime merge**: `generate_rant.py` loads both files via `build_season_memory()` and injects a `SEASON_MEMORY` block into the prompt:
```json
{ "celtics": { "current_season": {...}, "past_seasons": [...] }, ... }
```
The safety judge also loads both files as `source_data` — any stat Dan cites must appear in `rolling_7day` OR `season_memory`, otherwise it's flagged as a fabricated stat (HIGH severity).

### `site/data/daily_output.json` (Gemini output schema)
```json
{
  "morning_brew": ["paragraph1", "paragraph2", "paragraph3"],
  "trend_watch": [
    { "category": "Heater|Cold Snap|Bullpen Watch|Streak|Slump", "player": "...", "trend": "...", "dans_take": "..." }
  ],
  "news_digest": [
    { "headline": "...", "url": "...", "dans_take": "one sentence in Dan's voice" }
  ],
  "box_scores": { "celtics": {...}, "bruins": {...}, "redsox": {...}, "patriots": {...} },
  "schedule": [ { "date": "...", "matchup": "...", "time_et": "..." } ]
}
```

`news_digest` rules:
- Only relevant Boston sports headlines — no pure personal news (divorce, relationships, family)
- Conduct/legal headlines get a deferential dans_take; defer to league process
- Empty list `[]` if no relevant headlines

### Safe fallback content (used when safety judge fails)
```json
{
  "morning_brew": ["Dan's takin' the mornin' off. Check back tomorrow. In the meantime, go grab a Dunks."],
  "trend_watch": [],
  "news_digest": [],
  "box_scores": {},
  "schedule": []
}
```

---

## Boston Dan's Persona (summary)

Full persona lives in `prompts/boston_dan_system.txt` — that is the source of truth. This is a summary only.

- **Voice**: High-energy Boston sports fan. Opinionated, cynical, salty — but radio-clean and never cruel.
- **Slang**: wicked, pissah, the Garden, the Hub, Dunks, the Pike — natural, not overdone.
- **Yawkey Way**: Dan calls it Yawkey Way. Always. He refuses to say Jersey Street and will grumble about the rename if it comes up.
- **Takes**: Strong opinions on coaching, draft, rivals. No hedging.
- **The Lookback Rule**: Dan always references the full 7-day window — streaks, slumps, notable events from days ago.
- **Season Memory** (deferred to Week 4+): Dan is aware of current season context (record, playoff position, key injuries) and past season trends (rebuilds, streaks, notable trades). This gives his takes historical grounding beyond the 7-day window.
- **Stats discipline**: Every cited number must exactly match the structured input data. Zero hallucination.
- **Off-field conduct**: Dan uses a league-policy-based framework — not a blanket ban. Pure personal news (divorce, relationships) = silence. Conduct situations covered by league policy (NFL Personal Conduct Policy, NBA/MLB/NHL conduct rules) = brief human decency + defer to process + conditional "if" language for on-field impact. Never speculates on guilt or editorializes on character.

---

## Safety Rules (non-negotiable)

The safety judge (`safety_judge.py`) audits both `morning_brew` and `news_digest`. It must **FAIL** any output containing:

1. Profanity or curse words (including censored versions like s**t, fr*ckin')
2. Racist, sexist, anti-LGBTQ+, or antisemitic content
3. Personal attacks on a player's character, family, or personal life
4. Personal attacks on coaches, refs, or officials
5. Pure personal news with no league conduct dimension (divorce, relationships, family). NOTE: brief deferential acknowledgment of a conduct situation under a league policy is PERMITTED if it expresses basic human decency, defers to process, and uses conditional "if" language for on-field impact
6. Content promoting violence or hate
7. Fabricated statistics not present in the source data
8. `news_digest` dans_take containing personal attacks, guilt speculation, or character judgments

**Severity logic:**
- `low` → borderline phrase; retry once with tighter prompt
- `high` → clear violation; immediate fallback, no retry

---

## Environment Variables

| Variable | Used By | Notes |
|---|---|---|
| `GEMINI_API_KEY` | `generate_rant.py`, `safety_judge.py` | Set in `~/.zshrc` locally; GitHub Actions secret in CI |
| `GEMINI_MODEL` | `generate_rant.py` | Default: `gemini-2.5-flash` |
| `JUDGE_MODEL` | `safety_judge.py` | Default: `gemini-2.5-flash` |
| `ROLLING_STORE_PATH` | `generate_rant.py` | Default: `data/rolling_7day.json`; override in evals to point at fixtures |
| `OUTPUT_PATH` | `generate_rant.py` | Default: `data/raw_dan_output.json`; override in evals |
| `INPUT_PATH` | `safety_judge.py` | Default: `data/raw_dan_output.json` |
| `SEASON_STATIC_PATH` | `generate_rant.py`, `safety_judge.py` | Default: `data/season_static.json`; override in evals |
| `SEASON_CURRENT_PATH` | `generate_rant.py`, `safety_judge.py` | Default: `data/season_current.json`; override in evals |

---

## Error Handling Conventions

- Every fetcher script must write an empty-but-valid JSON on failure so downstream scripts don't crash
- `generate_rant.py` uses exponential backoff retry (2s → 5s → 10s) on 503/429, then exits with code 1
- `safety_judge.py` uses the same retry pattern
- `publish.py` owns the safety gate and fallback logic — it is the final arbiter
- All scripts print clear status messages: what they're doing, what they found, where they saved output
- Exit code `0` = success, `1` = failure

---

## Week 3: Publish & Health Check Infrastructure

### `publish.py` — Safety Gate & Fallback Arbiter

**Responsibility**: Final decision gate. Reads `data/raw_dan_output.json`, runs `safety_judge.py`, and either publishes the output or writes a safe fallback.

**Flow**:
1. Check if `data/raw_dan_output.json` exists and is parseable
   - If missing/unparseable → write SAFE_FALLBACK, exit 1
2. Run `safety_judge.py` and capture exit code
3. If exit code 0 (PASS):
   - Validate JSON again
   - Write to `site/data/daily_output.json`
   - Exit 0
4. If exit code 1 (FAIL):
   - Write SAFE_FALLBACK to `site/data/daily_output.json`
   - Exit 1

**Error handling**:
- Creates `site/data/` directory if missing (using `Path.mkdir(parents=True)`)
- Gracefully handles malformed JSON with clear error messages
- All output goes to stdout (visible in GitHub Actions logs)
- Always returns an exit code: 0 (success) or 1 (failure)

### `healthcheck.py` — Final Validation

**Responsibility**: Last gate before the cron is considered successful. Validates that `site/data/daily_output.json` is well-formed and complete.

**Checks**:
1. File exists
2. Valid JSON
3. All required keys present: `morning_brew`, `trend_watch`, `news_digest`, `box_scores`, `schedule`
4. Detects fallback content and prints warning (but still exits 0 — fallback is valid)

**Output**:
- Exit code 0 = success (even if fallback detected)
- Exit code 1 = validation failed
- Clear status messages in stdout

### `.github/workflows/morning_brew.yml` — Daily Cron

**Trigger**: `0 8 * * *` (03:00 ET = 08:00 UTC) — moved from 06:00 ET to reduce Gemini API contention

**Pipeline** (runs all steps in order):
```
fetch_nba.py
fetch_nhl.py
fetch_mlb.py
fetch_nfl.py
update_store.py
fetch_schedule.py
fetch_news.py
generate_rant.py
safety_judge.py
publish.py
healthcheck.py
```

**Success criteria**: `healthcheck.py` exits 0

**On failure**: 
- Workflow exits 1 (shows red ❌ in GitHub)
- Logs visible for debugging
- Next day's run will retry

---

## Build Progress

| Week | Focus | Status |
|---|---|---|
| Week 1 | Data Foundation | ✅ Complete |
| Week 2 | Persona & Generation | ✅ Complete (pivoted away from AI Studio — direct Gemini API) |
| Week 3 | Publish & Health Check | ✅ Complete (publish.py, healthcheck.py, morning_brew.yml workflow) |
| Week 4 | Frontend & Deployment | 🔄 In progress (static site, GitHub Pages) |
| Week 4+ | Season Memory Module | ✅ Phase 1 complete (season_static.json + fetch_season_memory.py, judge + evals wired in) |

---

## Product Roadmap (Post-Week 3)

**Deferred: Enhanced Dan Knowledge & Comedic Depth**

Once the end-to-end pipeline is live (Week 3 complete, daily cron running), expand Dan's persona with:

### Season Memory Module (Priority)
- **Current Season Context**: Wins/losses, playoff positioning, key injuries, rebuild vs. contention status for each team
- **Past Seasons Context**: Last 5 seasons' records, draft picks, notable trades, streaks (e.g., "3rd straight losing season")
- **Format**: `data/season_memory.json` with structure: `{ "celtics": { "current_season": {...}, "past_seasons": [...] }, ... }`
- **Usage**: Dan uses current season context to frame games ("Celtics are 40-20, fighting for the 1 seed") and past seasons to comment on trends ("3rd straight year of first-round exits")
- **Benefit**: Rants feel historically grounded and team-aware, not just game-to-game reactions

### Boston Sports History Module
- Red Sox: 86-year curse (1918–2004), 2004 World Series, Impossible Dream (1967)
- Celtics: 17 championships, Big Three era (2007–2012), Kyrie/Jayson timeline
- Bruins: 1970 & 1972 Cups, Original Six, Big Bad Bruins era
- Patriots: Brady/Belichick dynasty (2000–2019), Super Bowl runs, post-Brady transition
- **Format**: `data/historical_facts.json` injected at runtime; Dan references these for color

### Boston Culture & Comedic References
- Dunkin' as a religion, MBTA complaints, Big Dig trauma, Greenway recovery
- Regional dialect depth: "Bostonian profanity" (radio-clean versions), neighborhood pride (Southie, Dot, etc.)
- Rivalries: Yankees, Habs, Heat, Jets, Ravens
- **Format**: new section in `prompts/boston_dan_system.txt` with cultural guidelines and reference patterns

### Implementation Strategy
1. Build `data/historical_facts.json` with curated Boston sports moments (dates, stats, narrative)
2. Update `prompts/boston_dan_system.txt` with cultural guidelines and reference patterns
3. Modify `build_user_message()` in `generate_rant.py` to inject historical + cultural context
4. Test with evals to ensure Dan uses history for color *without* hallucinating stats or inventing fake historical events
5. Safety gate: `safety_judge.py` must FAIL any invented historical claims (e.g., "Red Sox won in 1899")

**Why deferred:** The end-to-end pipeline must work flawlessly first. Adding knowledge depth adds complexity to evals and persona tuning. Ship a working daily Dan first; enhance his depth in Week 4 or later.

### Email Newsletter (Deferred)
- **Goal**: Allow readers to subscribe and receive daily Dan commentary via email
- **Tech**: Integrate with email service (SendGrid, Mailgun, or Substack API)
- **Frontend**: Add email input + "Subscribe" button to v4 design (currently stubbed out in hero CTA section)
- **Backend**: Store emails, trigger daily send via GitHub Actions after publish.py completes
- **Safety**: Ensure unsubscribe links work; comply with CAN-SPAM
- **Why deferred**: Current focus is on perfecting the daily generation pipeline and frontend design. Newsletter infrastructure (database, email service, compliance) can come later once the core product is stable and gaining traction.

---

## How to Run the Pipeline Locally

```bash
cd boston-dans-hub

# Run individual fetchers
python3 scripts/fetch_nba.py
python3 scripts/fetch_nhl.py
python3 scripts/fetch_mlb.py
python3 scripts/fetch_nfl.py

# Build the store and schedule
python3 scripts/update_store.py
python3 scripts/fetch_schedule.py
python3 scripts/fetch_news.py

# Generate and judge
python3 scripts/generate_rant.py
python3 scripts/safety_judge.py

# Publish and validate
python3 scripts/publish.py
python3 scripts/healthcheck.py
```

Requires `GEMINI_API_KEY` in the environment. Set once in `~/.zshrc` — never commit it.
