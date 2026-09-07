#!/usr/bin/env python3
"""
fetch_news.py — Merges individual team news files into a single
chronologically-sorted (most recent first) latest news feed.

Reads:
    data/celtics_news.json
    data/bruins_news.json
    data/redsox_news.json
    data/patriots_news.json

Writes:
    data/latest_news.json

Usage:
    python3 scripts/fetch_news.py

Run this after all four fetch_*.py scripts have completed.
"""

import json
import sys
from datetime import datetime, timezone

from pipeline_dates import as_of_date
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR     = PROJECT_ROOT / "data"

OUTPUT_PATH = DATA_DIR / "latest_news.json"

TEAMS = {
    "celtics":  {"file": DATA_DIR / "celtics_news.json",  "name": "Boston Celtics",      "sport": "NBA"},
    "bruins":   {"file": DATA_DIR / "bruins_news.json",   "name": "Boston Bruins",       "sport": "NHL"},
    "redsox":   {"file": DATA_DIR / "redsox_news.json",   "name": "Boston Red Sox",      "sport": "MLB"},
    "patriots": {"file": DATA_DIR / "patriots_news.json", "name": "New England Patriots","sport": "NFL"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_news(path: Path, team: str):
    """
    Load a team news JSON file.

    Returns the parsed dict on success, or None (with a warning) on any
    problem — missing file, bad JSON, or fetch-script error sentinel.
    """
    if not path.exists():
        print(f"  [WARN] {team}: {path.name} not found — skipping.")
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"  [WARN] {team}: {path.name} invalid JSON ({e}) — skipping.")
        return None
    if "error" in data:
        print(f"  [WARN] {team}: {path.name} has error sentinel — skipping.")
        return None
    return data


def parse_published(raw: str) -> datetime:
    """
    Parse a published timestamp into a UTC-aware datetime for sorting.

    Returns datetime.min (sorts last) if the string is unparseable.
    """
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def drop_articles_after(articles: list, cutoff_date) -> list:
    """
    Drop articles published after the end of `cutoff_date` (UTC).

    Dan's coverage window ends with the target day's games, but the news feed is
    sorted newest-first and fetched at run time, so an afternoon re-run leads
    with a story about a game played TODAY. On 2026-09-07 that is how a result
    the structured data did not contain reached the prompt as the freshest,
    most prominent thing in it, and three generation attempts kept reaching for
    it. Out-of-window news is not a scoop, it is tomorrow's brew.

    An article with an unparseable or missing timestamp is KEPT: the feed's
    dates are best-effort, and silently dropping undated stories would quietly
    starve the digest.
    """
    end_of_day = datetime(
        cutoff_date.year, cutoff_date.month, cutoff_date.day,
        23, 59, 59, tzinfo=timezone.utc,
    )
    kept, dropped = [], 0
    for a in articles:
        raw = a.get("published", "")
        published = parse_published(raw)
        # parse_published returns datetime.min for unparseable/missing values.
        if raw and published != datetime.min.replace(tzinfo=timezone.utc) and published > end_of_day:
            dropped += 1
            continue
        kept.append(a)
    if dropped:
        print(f"  dropped {dropped} article(s) published after {cutoff_date.isoformat()} "
              f"(outside Dan's coverage window)")
    return kept


def main() -> None:
    print("=" * 52)
    print("  Boston Dan's Hub — Unified News Feed Builder")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)

    # --- 1. Load and tag all articles ---
    print("\n[1/2] Loading team news files...")

    all_articles = []
    loaded_teams = []

    for team_key, meta in TEAMS.items():
        data = load_news(meta["file"], team_key)
        if data is None:
            continue

        headlines = data.get("headlines", [])
        for article in headlines:
            all_articles.append({
                "team":        team_key,
                "sport":       meta["sport"],
                "team_name":   meta["name"],
                "headline":    article.get("headline", "").strip(),
                "description": article.get("description", "").strip(),
                "published":   article.get("published", ""),
                "url":         article.get("url", ""),
            })

        loaded_teams.append(team_key)
        print(f"  {team_key:<10} {len(headlines)} article(s)")

    if not loaded_teams:
        print("[ERROR] No news files loaded — aborting.")
        sys.exit(1)

    # --- 2. Sort (most recent first) and write ---
    print(f"\n[2/2] Merging and writing ({len(all_articles)} total articles)...")

    all_articles.sort(
        key=lambda a: parse_published(a["published"]),
        reverse=True,
    )

    # Keep the digest inside the coverage window the persona writes to.
    all_articles = drop_articles_after(all_articles, as_of_date())

    now_utc = datetime.now(timezone.utc)
    result = {
        "generated_at":  now_utc.isoformat(),
        "article_count": len(all_articles),
        "articles":      all_articles,
    }

    try:
        OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    except OSError as e:
        print(f"[ERROR] Could not write {OUTPUT_PATH}: {e}")
        sys.exit(1)

    print(f"  Saved {len(all_articles)} article(s) to {OUTPUT_PATH}")
    print("\nDone.")


if __name__ == "__main__":
    main()
