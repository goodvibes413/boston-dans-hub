#!/usr/bin/env python3
"""
update_store.py — Aggregates today's sport data into the rolling 7-day store.

Reads (per team):
    data/celtics_boxscore.json  + data/celtics_news.json
    data/bruins_boxscore.json   + data/bruins_news.json
    data/redsox_boxscore.json   + data/redsox_news.json
    data/patriots_boxscore.json + data/patriots_news.json

Writes:
    data/rolling_7day.json  — rolling window of the last 7 day entries

Usage:
    python3 scripts/update_store.py

Run this after all four fetch_*.py scripts have completed.
Safe to re-run: today's entry is replaced, not duplicated.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR     = PROJECT_ROOT / "data"

STORE_PATH = DATA_DIR / "rolling_7day.json"
STORE_MAX  = 7

# Each team has both a boxscore and a news file.
SPORT_FILES = {
    "celtics": {
        "boxscore": DATA_DIR / "celtics_boxscore.json",
        "news":     DATA_DIR / "celtics_news.json",
    },
    "bruins": {
        "boxscore": DATA_DIR / "bruins_boxscore.json",
        "news":     DATA_DIR / "bruins_news.json",
    },
    "redsox": {
        "boxscore": DATA_DIR / "redsox_boxscore.json",
        "news":     DATA_DIR / "redsox_news.json",
    },
    "patriots": {
        "boxscore": DATA_DIR / "patriots_boxscore.json",
        "news":     DATA_DIR / "patriots_news.json",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_sport_data(path: Path, label: str):
    """
    Load and validate a single sport data file.

    Returns the parsed dict if the file exists, is valid JSON, and does not
    contain a top-level "error" key (the sentinel written by fetch scripts
    on failure).

    Returns None (with a printed warning) on any problem so the caller can
    skip that file rather than crash.
    """
    if not path.exists():
        print(f"  [WARN] {label}: {path.name} not found — skipping.")
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"  [WARN] {label}: {path.name} is not valid JSON ({e}) — skipping.")
        return None
    if "error" in data:
        print(
            f"  [WARN] {label}: {path.name} contains an error sentinel "
            f"({data['error']!r}) — skipping."
        )
        return None
    return data


def load_store() -> dict:
    """
    Load rolling_7day.json.

    Returns {"days": []} if the file doesn't exist or is corrupt.
    """
    if not STORE_PATH.exists():
        return {"days": []}
    try:
        data = json.loads(STORE_PATH.read_text())
        if "days" not in data or not isinstance(data["days"], list):
            print("  [WARN] rolling_7day.json has unexpected structure — resetting.")
            return {"days": []}
        return data
    except json.JSONDecodeError as e:
        print(f"  [WARN] rolling_7day.json is corrupt ({e}) — resetting.")
        return {"days": []}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def build_day_entry(today: str) -> dict:
    """
    Read all sport files and build a single day entry.

    Each team gets a nested dict with "boxscore" and/or "news" keys.
    A team is omitted entirely if both of its files fail to load.

    Args:
        today: ISO date string "YYYY-MM-DD" (UTC).

    Returns:
        Dict with "date" plus one nested key per team that loaded any data.
    """
    entry = {"date": today}
    for team, files in SPORT_FILES.items():
        team_data = {}
        for file_type, path in files.items():
            data = load_sport_data(path, f"{team}/{file_type}")
            if data is not None:
                team_data[file_type] = data
        if team_data:
            entry[team] = team_data
        else:
            print(f"  [WARN] {team}: all files failed — team omitted from today's entry.")
    return entry


def apply_entry(store: dict, day_entry: dict) -> dict:
    """
    Insert today's entry into the store.

    - Removes any existing entry with the same date (idempotent).
    - Appends the new entry.
    - Sorts by date ascending and trims to the last STORE_MAX entries.

    Returns the updated store dict.
    """
    today = day_entry["date"]
    days = [d for d in store["days"] if d.get("date") != today]
    days.append(day_entry)
    days.sort(key=lambda d: d.get("date", ""))
    store["days"] = days[-STORE_MAX:]
    return store


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 52)
    print("  Boston Dan's Hub — Rolling Store Updater")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\n  Date: {today_utc}")

    # --- 1. Build today's entry ---
    print("\n[1/3] Loading sport data files...")
    day_entry = build_day_entry(today_utc)

    teams_loaded = [k for k in day_entry if k != "date"]
    teams_missing = [t for t in SPORT_FILES if t not in day_entry]

    for team in teams_loaded:
        file_types = list(day_entry[team].keys())
        print(f"  {team}: {file_types}")
    if teams_missing:
        print(f"  Skipped teams: {teams_missing}")

    if not teams_loaded:
        print("[ERROR] No sport data loaded — aborting without writing.")
        sys.exit(1)

    # --- 2. Load the existing store ---
    print("\n[2/3] Loading rolling store...")
    store = load_store()
    entries_before = len(store["days"])
    existing_dates = [d["date"] for d in store["days"]]
    replacing = today_utc in existing_dates
    print(
        f"  Current entries: {entries_before} "
        f"{'(replacing today)' if replacing else '(appending new)'}"
    )

    # --- 3. Apply and write ---
    print("\n[3/3] Updating store...")
    store = apply_entry(store, day_entry)
    entries_after = len(store["days"])

    try:
        STORE_PATH.write_text(json.dumps(store, indent=2))
    except OSError as e:
        print(f"[ERROR] Could not write {STORE_PATH}: {e}")
        sys.exit(1)

    print(f"  Store now has {entries_after} entr{'y' if entries_after == 1 else 'ies'} "
          f"(max {STORE_MAX}).")
    print(f"  Dates in store: {[d['date'] for d in store['days']]}")
    print(f"\n  Saved to {STORE_PATH}")

    print("\nDone.")


if __name__ == "__main__":
    main()
