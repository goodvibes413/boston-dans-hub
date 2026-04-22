# Boston Dan's Morning Brew

> A fully automated, AI-powered Boston sports commentary site — generated fresh every morning, deployed at zero cost.

**Live site:** [goodvibes413.github.io/boston-dans-hub](https://goodvibes413.github.io/boston-dans-hub/)

---

## What It Is

Boston Dan's Morning Brew is a daily sports digest written entirely by an AI persona — Boston Dan, a high-energy, opinionated Celtics/Bruins/Red Sox/Patriots fan with a distinct voice, strong takes, and a deep distrust of anyone who calls it Jersey Street.

Every morning at 3:00 AM ET, a GitHub Actions pipeline wakes up, pulls live game data from four sports APIs, feeds it to Gemini 2.5 Flash, runs the output through a safety judge, and publishes a fresh static site to GitHub Pages — all with no server, no database, and no ongoing cost.

---

## Pipeline

```
┌─────────────────────────────────────────────────────────┐
│                   GitHub Actions Cron                   │
│                  Daily @ 03:00 AM ET                    │
└───────────────────────┬─────────────────────────────────┘
                        │
          ┌─────────────▼─────────────┐
          │       Data Fetchers       │
          │  fetch_nba.py             │  ESPN API  → Celtics scores + schedule
          │  fetch_nhl.py             │  NHL API   → Bruins scores + schedule
          │  fetch_mlb.py             │  MLB API   → Red Sox scores + schedule
          │  fetch_nfl.py             │  ESPN API  → Patriots news (offseason)
          └─────────────┬─────────────┘
                        │
          ┌─────────────▼─────────────┐
          │      Context Builders     │
          │  update_store.py          │  Rolling 7-day game window
          │  fetch_schedule.py        │  Upcoming games (all 4 teams)
          │  fetch_news.py            │  Latest headlines (all 4 teams)
          │  fetch_season_memory.py   │  Current standings + season records
          └─────────────┬─────────────┘
                        │
          ┌─────────────▼─────────────┐
          │      AI Generation        │
          │  generate_rant.py         │  Gemini 2.5 Flash + Google Search
          │                           │  grounding; persona from system prompt;
          │                           │  outputs morning_brew, trends,         │
          │                           │  news_digest, box_scores, schedule
          └─────────────┬─────────────┘
                        │
          ┌─────────────▼─────────────┐
          │       Safety Judge        │
          │  safety_judge.py          │  Second Gemini call audits output for
          │                           │  profanity, fabricated stats, personal
          │                           │  attacks — PASS or FAIL + severity
          └─────────────┬─────────────┘
                        │
          ┌─────────────▼─────────────┐
          │     Publish & Validate    │
          │  publish.py               │  Safety gate: publishes output or
          │                           │  writes a safe fallback if judge fails
          │  healthcheck.py           │  Validates all required JSON keys
          └─────────────┬─────────────┘
                        │
          ┌─────────────▼─────────────┐
          │      GitHub Pages         │
          │  site/data/               │  Static JSON served to frontend
          │  docs/index.html          │  Vanilla JS fetches + renders on load
          └───────────────────────────┘
```

---

## Key Engineering Details

**Zero-cost architecture** — Gemini free tier + GitHub Actions free tier + GitHub Pages. No server, no database, no cloud bills.

**AI persona with guardrails** — Boston Dan's voice is defined in a system prompt (`prompts/boston_dan_system.txt`). A separate safety judge runs every output through a rubric that catches profanity, fabricated statistics, personal attacks, and off-field personal news before anything is published.

**Two-attempt Gemini strategy** — The first generation call uses Google Search grounding for recency. If JSON parsing fails (grounding and `response_mime_type` are incompatible in the SDK), a second call retries with grounding off and strict JSON mode on.

**Rolling context window** — `update_store.py` maintains a 7-day rolling JSON store of game results and headlines. Dan references this window explicitly — streaks, slumps, and notable moments from earlier in the week inform every rant.

**Season memory** — `fetch_season_memory.py` pulls current standings (wins, losses, playoff seed, streak) from ESPN live APIs daily. Combined with a hand-curated `season_static.json` of the past 5 seasons per team, Dan has full historical context for his takes.

**Safe fallback** — If the safety judge fails or the LLM output is unparseable, `publish.py` writes a hardcoded fallback ("Dan's takin' the mornin' off") so the site never goes blank.

**Eval harness** — `eval_voice.py` runs the generation pipeline against hand-crafted fixtures to test persona consistency, stats accuracy, and edge cases (no games, playoff mode, offseason) without burning real API quota.

---

## Tech Stack

| Layer | Technology |
|---|---|
| AI generation | Gemini 2.5 Flash (`google-genai` SDK) |
| Safety judge | Gemini 2.5 Flash (separate call, separate rubric) |
| Sports data | Public ESPN, NHL, and MLB REST APIs (no auth) |
| CI/CD | GitHub Actions (daily cron) |
| Hosting | GitHub Pages (static) |
| Frontend | Vanilla HTML/CSS/JS — no build tools, no frameworks |
| Backend | Python stdlib only (`urllib`, `json`, `pathlib`) |

---

## Project Structure

```
scripts/           Python data fetchers and AI generation pipeline
prompts/           Boston Dan's persona system prompt (source of truth)
data/              Live JSON data files (gitignored — regenerated daily)
site/              Static site deployed to GitHub Pages
  data/            daily_output.json served to the frontend
docs/              index.html — single-page frontend
evals/
  fixtures/        Hand-crafted test inputs for eval harness
  runs/            Generated eval outputs for manual review (gitignored)
.github/workflows/ morning_brew.yml — daily cron pipeline
```

---

## Running Locally

```bash
# Fetch today's data
python3 scripts/fetch_nba.py
python3 scripts/fetch_nhl.py
python3 scripts/fetch_mlb.py
python3 scripts/fetch_nfl.py
python3 scripts/update_store.py
python3 scripts/fetch_schedule.py
python3 scripts/fetch_news.py
python3 scripts/fetch_season_memory.py

# Generate, judge, publish
python3 scripts/generate_rant.py
python3 scripts/safety_judge.py
python3 scripts/publish.py
python3 scripts/healthcheck.py

# Serve the frontend
cd docs && python3 -m http.server 7423
```

Requires `GEMINI_API_KEY` in the environment.

```bash
# Run evals against a fixture
python3 scripts/eval_voice.py --fixture evals/fixtures/voice_no_games.json --n 3
```
