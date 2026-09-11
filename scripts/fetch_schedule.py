#!/usr/bin/env python3
"""
fetch_schedule.py — Merges individual team schedule files into a single
chronologically-sorted upcoming schedule.

Reads:
    data/celtics_schedule.json
    data/bruins_schedule.json
    data/redsox_schedule.json
    data/patriots_schedule.json

Writes:
    data/upcoming_schedule.json

Usage:
    python3 scripts/fetch_schedule.py

Run this after all four fetch_*.py scripts have completed.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR     = PROJECT_ROOT / "data"

OUTPUT_PATH = DATA_DIR / "upcoming_schedule.json"

ET = ZoneInfo("America/New_York")

# Maps team key → source schedule file + Boston full name + sport label
TEAMS = {
    "celtics": {
        "file":  DATA_DIR / "celtics_schedule.json",
        "name":  "Boston Celtics",
        "sport": "NBA",
    },
    "bruins": {
        "file":  DATA_DIR / "bruins_schedule.json",
        "name":  "Boston Bruins",
        "sport": "NHL",
    },
    "redsox": {
        "file":  DATA_DIR / "redsox_schedule.json",
        "name":  "Boston Red Sox",
        "sport": "MLB",
    },
    "patriots": {
        "file":  DATA_DIR / "patriots_schedule.json",
        "name":  "New England Patriots",
        "sport": "NFL",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_schedule(path: Path, team: str):
    """
    Load a team schedule JSON file.

    Returns the parsed dict on success, or None (with a warning) if the
    file is missing, unparseable, or contains a top-level "error" sentinel.
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


def normalize_dt(game: dict, sport: str) -> tuple[datetime, bool]:
    """
    Parse the game's date/time into a timezone-aware UTC datetime, plus a flag
    saying whether a real start time was known.

    Where each sport keeps the timestamp:
        NBA, NFL — ESPN returns a FULL ISO 8601 datetime in 'date'.
        NHL      — 'start_time_utc'; its 'date' is a bare YYYY-MM-DD.
        MLB      — 'game_time_utc';  its 'date' is a bare YYYY-MM-DD.

    The NFL branch used to append "T00:00:00Z" to 'date' the way the NHL and
    MLB branches do. ESPN's NFL 'date' is already a full timestamp, so that
    built "2026-09-13T17:00ZT00:00:00Z", which failed to parse and fell through
    to the year-9999 sort sentinel. Harmless while the Patriots were in the
    offseason and never appeared in the 7-day window; the moment Week 1 landed
    in range, every Patriots game shipped to the site dated "9999-12-30".

    So the shape of the value decides how it is read, not the sport: a bare
    date is anchored at midnight UTC and reported as time_known=False.
    """
    if sport == "NBA":
        raw = game.get("date", "")
    elif sport == "NHL":
        raw = game.get("start_time_utc") or game.get("date", "")
    elif sport == "MLB":
        raw = game.get("game_time_utc") or game.get("date", "")
    else:  # NFL and any future sport
        raw = game.get("start_time_utc") or game.get("date", "")

    raw = (raw or "").replace("Z", "+00:00").strip()

    # A bare YYYY-MM-DD carries no time to honour; anything with a "T" does.
    if "T" in raw:
        candidate, time_known = raw, True
    else:
        candidate, time_known = raw + "T00:00:00+00:00", False

    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        # Unparseable — sort last, and never claim a time we don't have.
        return datetime(9999, 12, 31, tzinfo=timezone.utc), False

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt, time_known


def format_time_et(dt_utc: datetime, time_known: bool = True) -> str:
    """
    Convert a UTC datetime to a human-readable ET string like '7:00 PM ET'.

    Returns 'TBD' only when the source carried no start time at all.

    This used to infer "no time announced" from the clock reading exactly
    midnight UTC, which cannot tell a missing time from a real one: under EST
    a 7:00 PM ET tip-off or puck drop IS 00:00 UTC, so every Celtics and
    Bruins game at the most common start time in the sport printed "TBD"
    from November through March.
    """
    if not time_known:
        return "TBD"
    dt_et = dt_utc.astimezone(ET)
    return dt_et.strftime("%-I:%M %p ET")


def build_notes(game: dict, sport: str) -> dict:
    """
    Extract sport-specific extra fields into a notes dict so they don't
    pollute the top-level unified schema.
    """
    if sport == "NHL":
        notes = {}
        if "opponent_abbrev" in game:
            notes["opponent_abbrev"] = game["opponent_abbrev"]
        return notes
    if sport == "MLB":
        return {
            "day_night":    game.get("day_night", ""),
            "doubleheader": game.get("doubleheader", False),
            "game_number":  game.get("game_number", 1),
        }
    return {}


def normalize_game(game: dict, team_key: str, meta: dict) -> dict:
    """
    Convert a raw game entry from any sport's schedule into the unified
    output schema.
    """
    sport      = meta["sport"]
    boston     = meta["name"]
    dt_utc, time_known = normalize_dt(game, sport)
    is_home    = game.get("home", False)

    home_team = boston        if is_home else game.get("opponent", "Unknown")
    away_team = game.get("opponent", "Unknown") if is_home else boston

    return {
        "sport":        sport,
        "team":         team_key,
        "game_id":      str(game.get("game_id") or game.get("game_pk", "")),
        "date":         dt_utc.astimezone(ET).strftime("%Y-%m-%d"),
        # Spelled out so nobody downstream has to derive it. Run #613 wrote "back
        # to the Fens on Thursday night" for a Friday game, on a Thursday: the
        # model was handed "2026-09-11" and asked, implicitly, to do calendar
        # arithmetic in its head. It is cheap to just say Friday.
        "day_of_week":  dt_utc.astimezone(ET).strftime("%A"),
        "time_et":      format_time_et(dt_utc, time_known),
        "datetime_utc": dt_utc.isoformat(),
        "home_team":    home_team,
        "away_team":    away_team,
        "venue":        game.get("venue", ""),
        "status":       game.get("status", ""),
        "season_type":  game.get("season_type", "unknown"),
        "broadcast":    None,
        "notes":        build_notes(game, sport),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 52)
    print("  Boston Dan's Hub — Unified Schedule Builder")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)

    # --- 1. Load and normalise all team schedules ---
    print("\n[1/2] Loading team schedules...")

    all_games = []
    from_dates = []
    to_dates   = []
    loaded_teams = []

    for team_key, meta in TEAMS.items():
        data = load_schedule(meta["file"], team_key)
        if data is None:
            continue

        loaded_teams.append(team_key)
        games_raw = data.get("games", [])

        if data.get("from_date"):
            from_dates.append(data["from_date"])
        if data.get("to_date"):
            to_dates.append(data["to_date"])

        count = 0
        for game in games_raw:
            try:
                all_games.append(normalize_game(game, team_key, meta))
                count += 1
            except Exception as e:
                print(f"  [WARN] {team_key}: skipping malformed game entry: {e}")

        print(f"  {team_key:<10} {count} game(s)")

    if not loaded_teams:
        print("[ERROR] No schedule files loaded — aborting.")
        sys.exit(1)

    # --- 2. Sort and write ---
    print(f"\n[2/2] Merging and writing ({len(all_games)} total games)...")

    all_games.sort(key=lambda g: g["datetime_utc"])

    now_utc   = datetime.now(timezone.utc)
    from_date = min(from_dates) if from_dates else now_utc.strftime("%Y-%m-%d")
    to_date   = max(to_dates)   if to_dates   else now_utc.strftime("%Y-%m-%d")

    result = {
        "generated_at": now_utc.isoformat(),
        "from_date":    from_date,
        "to_date":      to_date,
        "game_count":   len(all_games),
        "games":        all_games,
    }

    try:
        OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    except OSError as e:
        print(f"[ERROR] Could not write {OUTPUT_PATH}: {e}")
        sys.exit(1)

    print(f"  Saved {len(all_games)} game(s) to {OUTPUT_PATH}")
    print("\nDone.")


if __name__ == "__main__":
    main()
