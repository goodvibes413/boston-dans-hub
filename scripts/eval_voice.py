#!/usr/bin/env python3
"""eval_voice.py — run generate_rant.py against a fixture N times for manual review.

Usage:
  python scripts/eval_voice.py --fixture evals/fixtures/accuracy_tatum_22pts.json --n 5

Writes each output to evals/runs/{label}_{N}.json and prints a summary table.
There is no automated scoring — read the JSON files yourself. That IS the eval.

Fixture schemas supported:
  Legacy: the fixture IS the rolling_7day payload (a dict with a "days" key or similar)
  New:    {"rolling_7day": {...}, "season_memory": {"past_seasons": {...},
                                                    "current_season": {...}},
           "_fixture_notes": "..."}
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "evals" / "runs"


def stat_numbers_from(text: str):
    """Crude extraction of digit-strings for the accuracy eyeball check."""
    import re
    return re.findall(r"\d+(?:\.\d+)?%?", text)


def summarize(path: Path):
    try:
        d = json.loads(path.read_text())
    except Exception as e:
        return {"error": str(e)}
    brew = d.get("morning_brew", [])
    brew_text = " ".join(brew) if isinstance(brew, list) else str(brew)
    news_digest = d.get("news_digest", []) or []
    return {
        "keys": sorted(d.keys()),
        "brew_paragraphs": len(brew) if isinstance(brew, list) else 0,
        "brew_words": len(brew_text.split()),
        "trend_count": len(d.get("trend_watch", []) or []),
        "news_count": len(news_digest),
        "news_headlines": [n.get("headline", "")[:60] for n in news_digest],
        "stat_numbers": stat_numbers_from(brew_text)[:20],
    }


def split_fixture(fixture_data: dict):
    """
    Detect fixture shape and return
    (rolling_7day, season_static, season_current, recent_dan_output,
     boston_drafts, latest_news, today_iso, boston_roster, key_players).

    New shape: has explicit "rolling_7day" key → split into sections. May also
    carry "boston_drafts" (DRAFT_PICKS source), "latest_news" (LATEST_NEWS
    source), "today" (pins TODAY_OVERRIDE for freshness-sensitive tests),
    "boston_roster" (ROSTER_PATH source for off-roster player checks), and
    "key_players" (KEY_PLAYERS_PATH source for the PLAYERS_TO_WATCH block).
    Legacy shape: whole fixture IS the rolling_7day payload; everything else blank.
    """
    if isinstance(fixture_data, dict) and "rolling_7day" in fixture_data:
        rolling = fixture_data.get("rolling_7day", {}) or {}
        season_memory = fixture_data.get("season_memory", {}) or {}
        past = season_memory.get("past_seasons", {}) or {}
        current = season_memory.get("current_season", {}) or {}
        recent = fixture_data.get("recent_dan_output", []) or []
        drafts = fixture_data.get("boston_drafts", {}) or {}
        news = fixture_data.get("latest_news", {}) or {}
        today = fixture_data.get("today")
        roster = fixture_data.get("boston_roster", {}) or {}
        key_players = fixture_data.get("key_players", {}) or {}
        return rolling, past, current, recent, drafts, news, today, roster, key_players
    # Legacy: the fixture IS the rolling_7day payload
    return fixture_data, {}, {}, [], {}, {}, None, {}, {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True, help="Path to a fixture JSON")
    ap.add_argument("--n", type=int, default=5, help="Number of generations to run")
    ap.add_argument("--label", help="Label for output filenames (default: fixture stem)")
    args = ap.parse_args()

    fixture = Path(args.fixture).resolve()
    if not fixture.exists():
        sys.exit(f"error: fixture not found: {fixture}")

    label = args.label or fixture.stem
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # Parse and split the fixture into its sections
    try:
        fixture_data = json.loads(fixture.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"error: fixture not valid JSON: {e}")
    rolling, season_past, season_current, recent_output, drafts, news, fixture_today, roster, key_players = split_fixture(fixture_data)

    # Write split sections to tmp files so generate_rant.py can read them via env vars
    tmp_rolling = RUNS_DIR / f"{label}_tmp_rolling.json"
    tmp_static = RUNS_DIR / f"{label}_tmp_season_static.json"
    tmp_current = RUNS_DIR / f"{label}_tmp_season_current.json"
    tmp_drafts = RUNS_DIR / f"{label}_tmp_drafts.json"
    tmp_news = RUNS_DIR / f"{label}_tmp_news.json"
    tmp_roster = RUNS_DIR / f"{label}_tmp_roster.json"
    tmp_key_players = RUNS_DIR / f"{label}_tmp_key_players.json"
    tmp_rolling.write_text(json.dumps(rolling, indent=2))
    tmp_static.write_text(json.dumps(season_past, indent=2))
    tmp_current.write_text(json.dumps(season_current, indent=2))
    tmp_drafts.write_text(json.dumps(drafts, indent=2))
    tmp_news.write_text(json.dumps(news, indent=2))
    tmp_roster.write_text(json.dumps(roster, indent=2))
    tmp_key_players.write_text(json.dumps(key_players, indent=2))

    # Write empty stub for schedule — fixtures don't usually exercise it.
    stub_schedule = RUNS_DIR / f"{label}_stub_schedule.json"
    stub_schedule.write_text('{"games": []}')

    # Continuity memory: recent_dan_output is a list of {date, headline, ...}
    # entries. generate_rant.py reads these from a directory of <date>.json
    # files (mirrors the production layout in data/dan_archive/).
    tmp_archive_dir = RUNS_DIR / f"{label}_tmp_archive"
    tmp_archive_dir.mkdir(parents=True, exist_ok=True)
    # Clear any stale fixture archive files from prior runs
    for old in tmp_archive_dir.glob("*.json"):
        old.unlink()
    for entry in recent_output:
        date = entry.get("date")
        if not date:
            continue
        slim = {k: v for k, v in entry.items() if k != "date"}
        (tmp_archive_dir / f"{date}.json").write_text(json.dumps(slim, indent=2))

    print(f"eval_voice: fixture={fixture.name} label={label} n={args.n}")
    summaries = []
    for i in range(1, args.n + 1):
        out_path = RUNS_DIR / f"{label}_{i}.json"
        env = os.environ.copy()
        env["ROLLING_STORE_PATH"] = str(tmp_rolling)
        env["SCHEDULE_PATH"] = str(stub_schedule)
        env["NEWS_PATH"] = str(tmp_news)
        env["SEASON_STATIC_PATH"] = str(tmp_static)
        env["SEASON_CURRENT_PATH"] = str(tmp_current)
        env["DRAFT_PICKS_PATH"] = str(tmp_drafts)
        env["ROSTER_PATH"] = str(tmp_roster)
        env["KEY_PLAYERS_PATH"] = str(tmp_key_players)
        env["DAN_ARCHIVE_PATH"] = str(tmp_archive_dir)
        env["OUTPUT_PATH"] = str(out_path)
        if fixture_today:
            env["TODAY_OVERRIDE"] = fixture_today
        print(f"  run {i}/{args.n} → {out_path.name}")
        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "generate_rant.py")],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"    FAIL: {result.stderr.strip()}", file=sys.stderr)
            summaries.append({"run": i, "error": result.stderr.strip()[:200]})
            continue
        summaries.append({"run": i, **summarize(out_path)})

    print("\nSummary:")
    for s in summaries:
        print(f"  {s}")
    print(f"\nOpen evals/runs/{label}_*.json in your editor to read the outputs.")


if __name__ == "__main__":
    main()
