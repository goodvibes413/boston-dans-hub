# CLAUDE.md — Boston Dan's Hub

## What This Project Is

Boston Dan's Hub is a public-facing static website featuring an AI-generated Boston sports fan persona ("Boston Dan") that produces daily automated commentary, box scores, trend analysis, and schedules for the Celtics, Bruins, Red Sox, and Patriots. All content is pre-generated and cached — the site is fully static.

**Target operating cost: $0/month** (Gemini free tier + GitHub Actions + GitHub Pages).

---

## Repository

- **GitHub repo**: `goodvibes413/boston-dans-hub`
- **Local path**: `/Users/michaeldavey/Projects/fanbot-project/`
- **Live site** (when deployed): `https://goodvibes413.github.io/boston-dans-hub/`

---

## Directory Structure

```
/
├── scripts/          # Python data fetchers and generation scripts
├── data/             # JSON data files (gitignored: .env)
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
| LLM generation | Gemini 1.5 Flash via `google-generativeai` | Read key from `GEMINI_API_KEY` env var |
| Safety judge | Gemini 1.5 Pro | Stricter model for auditing |
| Static site | Vanilla HTML/CSS/JS | No build tools — `fetch()` loads JSON |
| CI/CD | GitHub Actions | Cron at 06:00 ET daily |
| Hosting | GitHub Pages | `/site` branch/folder |
| Sports data | Public ESPN + NHL + MLB APIs | No auth keys required |

**Never** add third-party Python packages beyond `google-generativeai` and `requests` without discussion. The goal is a minimal, auditable dependency footprint.

---

## The Daily Pipeline (in order)

```
fetch_nba.py        → data/celtics_boxscore.json + data/celtics_schedule.json
fetch_nhl.py        → data/bruins_boxscore.json  + data/bruins_schedule.json
fetch_mlb.py        → data/redsox_boxscore.json  + data/redsox_schedule.json
fetch_nfl.py        → data/patriots_news.json  (offseason: headlines only)
    ↓
update_store.py     → data/rolling_7day.json  (rolling 7-entry window)
fetch_schedule.py   → data/upcoming_schedule.json  (merged, sorted)
fetch_news.py       → data/latest_news.json  (merged, most-recent-first)
    ↓
generate_rant.py    → data/raw_dan_output.json  (Gemini 1.5 Flash + grounding)
    ↓
safety_judge.py     → PASS / FAIL + severity  (Gemini 1.5 Pro)
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
| `scripts/generate_rant.py` | ⬜ Todo | `raw_dan_output.json` |
| `scripts/safety_judge.py` | ⬜ Todo | PASS/FAIL + severity verdict |
| `scripts/publish.py` | ⬜ Todo | `site/data/daily_output.json` |
| `scripts/healthcheck.py` | ⬜ Todo | Validates all JSON files |

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
- `"preseason"` — Practice games before regular season (Sep for NBA, Aug for NFL, etc.)
- `"regular"` — Regular season play
- `"playoff"` — Postseason play (May–Jun for NBA, Oct–Nov for MLB, Feb for NFL, etc.)
- `"offseason"` — No games (typically Jan–Aug for NFL, Dec–Feb for MLB, etc.)
- `"unknown"` — Unable to classify (should be rare)

### Schedule Schemas (all sports)

Every game in a schedule's `"games"` array includes `"season_type"`:
```json
{
  "games": [
    {
      "date": "2025-04-14",
      "opponent": "Charlotte Hornets",
      "season_type": "regular",
      ...other fields...
    }
  ]
}
```

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
      "bruins": {
        "boxscore": { "game_date": "...", "played": false, "season_type": "unknown" },
        "news":     { "generated_at": "...", "headlines": [...] }
      },
      "redsox": {
        "boxscore": { "game_date": "...", "played": true, "season_type": "regular", "games": [...] },
        "news":     { "generated_at": "...", "headlines": [...] }
      },
      "patriots": {
        "boxscore": { "game_date": "...", "played": false, "season_type": "offseason" },
        "news":     { "generated_at": "...", "headlines": [...] }
      }
    }
  ]
}
```
Max 7 entries. Oldest entry is dropped when a new day is appended.
Each team always has both `boxscore` and `news` keys (either may be absent if the fetch script failed).

### `data/upcoming_schedule.json`
```json
{
  "generated_at": "2026-04-07T16:00:00+00:00",
  "from_date":    "2026-04-07",
  "to_date":      "2026-04-14",
  "game_count":   15,
  "games": [
    {
      "sport":        "NHL",
      "team":         "bruins",
      "game_id":      "2025021237",
      "date":         "2026-04-07",
      "time_et":      "7:00 PM ET",
      "datetime_utc": "2026-04-07T23:00:00+00:00",
      "home_team":    "Carolina Hurricanes",
      "away_team":    "Boston Bruins",
      "venue":        "Lenovo Center",
      "status":       "Scheduled",
      "season_type":  "regular",
      "broadcast":    null,
      "notes":        { "opponent_abbrev": "CAR" }
    }
  ]
}
```
Sorted chronologically. `time_et` is "TBD" when no game time has been announced.
Sport-specific extras live in `notes` (NHL: `opponent_abbrev`; MLB: `day_night`, `doubleheader`, `game_number`).

### `data/latest_news.json`
```json
{
  "generated_at":  "2026-04-07T16:00:00+00:00",
  "article_count": 12,
  "articles": [
    {
      "team":        "patriots",
      "sport":       "NFL",
      "team_name":   "New England Patriots",
      "headline":    "2026 NFL mock draft: Schrager projects 32 first-round picks",
      "description": "...",
      "published":   "2026-04-07T14:25:39+00:00",
      "url":         "https://..."
    }
  ]
}
```
Sorted newest-to-oldest by `published`. Up to 3 articles per team (inherited from each fetch script's `NEWS_TOP_N`).

### `site/data/daily_output.json` (Gemini output schema)
```json
{
  "morning_brew": ["paragraph1", "paragraph2", "paragraph3"],
  "trend_watch": [
    { "category": "Heater", "player": "Name", "trend": "...", "dans_take": "..." }
  ],
  "box_scores": { ...last night's stats by team... },
  "schedule": [ ...next 3 days of games... ]
}
```

### Safe fallback content (used when safety judge fails)
```json
{
  "morning_brew": ["Dan's takin' the mornin' off. Check back tomorrow. In the meantime, go grab a Dunks."],
  "trend_watch": [],
  "box_scores": {},
  "schedule": []
}
```

---

## Boston Dan's Persona (summary)

- **Voice**: High-energy Boston sports fan. Opinionated, cynical, salty — but radio-clean.
- **Slang**: wicked, pissah, the Garden, the Hub, Dunks, the Pike — natural, not overdone.
- **Takes**: Strong opinions on coaching, draft, rivals. No hedging.
- **The Lookback Rule**: Dan always references the full 7-day window — streaks, slumps, notable events from days ago.
- **Stats discipline**: Every cited number must exactly match the structured input data. Zero hallucination.

---

## Safety Rules (non-negotiable)

The safety judge must **FAIL** any output containing:
1. Profanity or curse words (including censored versions like s**t)
2. Racist, sexist, anti-LGBTQ+, or antisemitic content
3. Personal attacks on a player's character, family, or personal life
4. Content promoting violence or hate
5. Fabricated statistics not present in the source data

**Severity logic:**
- `low` → retry generation once with a tighter prompt → if retry passes, publish; if not, fallback
- `high` → immediate fallback, no retry

The pipeline must **never** publish unreviewed content. If in doubt, fallback.

---

## Environment Variables

| Variable | Used By | Notes |
|---|---|---|
| `GEMINI_API_KEY` | `generate_rant.py`, `safety_judge.py` | Set in `.env` locally; GitHub Actions secret in CI |

---

## Error Handling Conventions

- Every fetcher script must write an empty-but-valid JSON on failure so downstream scripts don't crash
- `generate_rant.py` retries once on API failure before exiting with error
- `publish.py` owns the safety gate and fallback logic — it is the final arbiter
- All scripts print clear status messages: what they're doing, what they found, where they saved output
- Exit code `0` = success, `1` = failure (especially important for `safety_judge.py`)

---

## Build Progress

| Week | Focus | Status |
|---|---|---|
| Week 1 | Data Foundation | 🔄 In progress (Tasks 1.1–1.5 ✅ — 1.6–1.8 remaining) |
| Week 2 | Persona & Generation | ⬜ Not started |
| Week 3 | Safety Gate & Quality | ⬜ Not started |
| Week 4 | Deployment & Automation | ⬜ Not started |

---

## How to Run the Pipeline Locally

```bash
cd /Users/michaeldavey/Projects/fanbot-project

# Run individual fetchers
python3 scripts/fetch_nba.py
python3 scripts/fetch_nhl.py
python3 scripts/fetch_mlb.py
python3 scripts/fetch_nfl.py

# Build the store and schedule
python3 scripts/update_store.py
python3 scripts/fetch_schedule.py

# Generate and publish
python3 scripts/generate_rant.py
python3 scripts/publish.py

# Validate everything
python3 scripts/healthcheck.py
```

Requires `GEMINI_API_KEY` in the environment (or a `.env` file — never commit `.env`).
