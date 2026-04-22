#!/usr/bin/env python3
"""
fetch_season_memory.py — Fetches current-season records and playoff status
for all four Boston teams from the ESPN unofficial API.

Writes a lean per-team snapshot of the current season (wins, losses,
playoff seed, series status) to data/season_current.json. Past seasons
are kept separately in data/season_static.json (hand-curated, versioned).

Graceful degradation: on per-team failure, writes an empty object for
that team and continues. Overall failure writes {} and exits 0.

Usage:
    python3 scripts/fetch_season_memory.py
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR     = PROJECT_ROOT / "data"
OUTPUT_PATH  = DATA_DIR / "season_current.json"

# ESPN team endpoints return current-season record and standing summary.
TEAM_ENDPOINTS = {
    "celtics":  ("basketball", "nba", "2"),
    "bruins":   ("hockey",     "nhl", "1"),
    "redsox":   ("baseball",   "mlb", "2"),
    "patriots": ("football",   "nfl", "17"),
}


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict:
    """Fetch JSON from URL, raising RuntimeError on any failure."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "boston-dans-hub/1.0 "
                "(+https://github.com/goodvibes413/boston-dans-hub)"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from {url}: {e}") from e


# ---------------------------------------------------------------------------
# Status classification (date-based)
# ---------------------------------------------------------------------------

def classify_status(sport: str, now: datetime) -> str:
    """
    Rough status classifier: regular_season | in_playoffs | offseason.

    Based on typical league calendars. Good enough for Dan's context — the
    safety judge catches any factual mismatch downstream.
    """
    month = now.month
    if sport == "basketball":  # NBA
        if month in (4, 5, 6):
            return "in_playoffs"
        if month in (7, 8, 9):
            return "offseason"
        return "regular_season"
    if sport == "hockey":  # NHL
        if month in (4, 5, 6):
            return "in_playoffs"
        if month in (7, 8, 9):
            return "offseason"
        return "regular_season"
    if sport == "baseball":  # MLB
        if month in (10, 11):
            return "in_playoffs"
        if month in (12, 1, 2, 3):
            return "offseason"
        return "regular_season"
    if sport == "football":  # NFL
        if month in (1, 2):
            return "in_playoffs"
        if month in (3, 4, 5, 6, 7, 8):
            return "offseason"
        return "regular_season"
    return "regular_season"


# ---------------------------------------------------------------------------
# Team fetcher
# ---------------------------------------------------------------------------

def fetch_team_record(sport: str, league: str, team_id: str) -> dict:
    """
    Fetch current season record for a team from ESPN team endpoint.

    Returns dict with wins, losses, (ties, ot_losses), win_pct,
    playoff_seed — as available. Empty dict on failure.
    """
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/teams/{team_id}"
    try:
        data = fetch_json(url)
    except RuntimeError as e:
        print(f"  [warn] fetch failed for {league}/{team_id}: {e}", file=sys.stderr)
        return {}

    # ESPN nests the team object under data.team
    team = data.get("team", {}) or {}
    record = team.get("record", {}) or {}

    # ESPN exposes an array of "items" — each with type (total, home, away,
    # vsConf, vsDiv...) and a stats[] list. We want the "total" item.
    items = record.get("items", []) or []
    total = None
    for item in items:
        if item.get("type") == "total":
            total = item
            break
    if total is None and items:
        total = items[0]  # fallback: first item

    if not total:
        return {}

    # Extract key stats. Not all leagues have all stats (e.g. NBA has no ties).
    stats = {s.get("name"): s.get("value") for s in total.get("stats", []) or []}

    result = {
        "summary": total.get("summary", ""),      # e.g. "40-20", "33-16-7"
    }

    # Common fields — only include if ESPN provided them.
    for out_key, espn_key in [
        ("wins",            "wins"),
        ("losses",          "losses"),
        ("ties",            "ties"),
        ("ot_losses",       "OTLosses"),
        ("win_pct",         "winPercent"),
        ("playoff_seed",    "playoffSeed"),
        ("games_behind",    "gamesBehind"),
        ("streak",          "streak"),
    ]:
        val = stats.get(espn_key)
        if val is not None:
            # ESPN returns numbers as floats — cast cleanly.
            if out_key in ("wins", "losses", "ties", "ot_losses", "playoff_seed"):
                try:
                    result[out_key] = int(val)
                except (ValueError, TypeError):
                    pass
            elif out_key == "win_pct":
                try:
                    result[out_key] = round(float(val), 3)
                except (ValueError, TypeError):
                    pass
            else:
                result[out_key] = val

    # Conference/division position is useful color
    groups = team.get("groups", {}) or {}
    if groups:
        result["division"] = groups.get("name", "")
        parent = groups.get("parent", {}) or {}
        if parent:
            result["conference"] = parent.get("name", "")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_team_entry(team_key: str, sport: str, league: str, team_id: str,
                     now: datetime) -> dict:
    """Assemble a single team's current_season snapshot."""
    status = classify_status(sport, now)
    record = fetch_team_record(sport, league, team_id)

    # Status-conditional shape per the plan
    if status == "offseason":
        entry = {"status": "offseason"}
        # Preserve last-known record if ESPN still returns one
        if record.get("wins") is not None:
            entry["last_season_wins"] = record.get("wins")
        if record.get("losses") is not None:
            entry["last_season_losses"] = record.get("losses")
        if record.get("summary"):
            entry["last_season_summary"] = record.get("summary")
        return entry

    if status == "in_playoffs":
        entry = {"status": "in_playoffs"}
        if record.get("wins") is not None:
            entry["regular_season_wins"] = record.get("wins")
        if record.get("losses") is not None:
            entry["regular_season_losses"] = record.get("losses")
        if record.get("summary"):
            entry["regular_season_summary"] = record.get("summary")
        if record.get("playoff_seed") is not None:
            entry["playoff_seed"] = record.get("playoff_seed")
        # playoff_series is left out of MVP — the rolling_7day boxscores
        # already reflect series games with season_type='playoff'. Dan can
        # reason from that. Can be hand-overridden via season_static if needed.
        return entry

    # regular_season
    entry = {"status": "regular_season"}
    for k in ("wins", "losses", "ties", "ot_losses",
              "win_pct", "playoff_seed", "games_behind",
              "streak", "summary", "division", "conference"):
        v = record.get(k)
        if v is not None and v != "":
            entry[k] = v
    return entry


def main() -> None:
    print("=" * 52)
    print("  Boston Dan's Hub — Season Memory Fetcher")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)

    out = {"generated_at": now.isoformat()}

    for team_key, (sport, league, team_id) in TEAM_ENDPOINTS.items():
        print(f"\n[{team_key}] {league.upper()} / team_id={team_id}")
        try:
            entry = build_team_entry(team_key, sport, league, team_id, now)
            out[team_key] = entry
            print(f"  status={entry.get('status')} "
                  f"summary={entry.get('summary') or entry.get('regular_season_summary') or entry.get('last_season_summary') or '—'}")
        except Exception as e:
            print(f"  [error] {team_key}: {e}", file=sys.stderr)
            out[team_key] = {}

    try:
        OUTPUT_PATH.write_text(json.dumps(out, indent=2))
        print(f"\nWrote {OUTPUT_PATH}")
    except Exception as e:
        print(f"[ERROR] could not write {OUTPUT_PATH}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
