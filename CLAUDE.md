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

| Script | Status | Purpose |
|---|---|---|
| `scripts/fetch_nba.py` | ✅ Done | Celtics boxscore + 7-day schedule from ESPN |
| `scripts/fetch_nhl.py` | ⬜ Todo | Bruins boxscore + schedule from NHL API |
| `scripts/fetch_mlb.py` | ⬜ Todo | Red Sox boxscore + schedule from MLB Stats API |
| `scripts/fetch_nfl.py` | ⬜ Todo | Patriots headlines from ESPN (offseason) |
| `scripts/update_store.py` | ⬜ Todo | Merge daily data → rolling_7day.json (7-entry max) |
| `scripts/fetch_schedule.py` | ⬜ Todo | Merge team schedules → upcoming_schedule.json |
| `scripts/generate_rant.py` | ⬜ Todo | Call Gemini with rolling store → raw_dan_output.json |
| `scripts/safety_judge.py` | ⬜ Todo | Audit raw output → PASS/FAIL + severity |
| `scripts/publish.py` | ⬜ Todo | Safety gate → publish or fallback |
| `scripts/healthcheck.py` | ⬜ Todo | Validate all JSON files are parseable |

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

### `data/rolling_7day.json`
```json
{
  "days": [
    {
      "date": "2025-04-06",
      "celtics": { ...celtics_boxscore.json contents... },
      "bruins":  { ...bruins_boxscore.json contents... },
      "redsox":  { ...redsox_boxscore.json contents... },
      "patriots": { ...patriots_news.json contents... }
    }
  ]
}
```
Max 7 entries. Oldest entry is dropped when a new day is appended.

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
| Week 1 | Data Foundation | 🔄 In progress (Tasks 1.1 ✅, 1.2 ✅ — 1.3–1.8 remaining) |
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
