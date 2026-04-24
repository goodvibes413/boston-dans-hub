#!/usr/bin/env python3
"""
fetch_draft.py — Fetch draft picks for all 4 Boston teams from ESPN API.

Queries ESPN draft endpoints for all sports (NFL, NBA, NHL, MLB) and filters
picks to the 4 Boston teams (Patriots, Celtics, Bruins, Red Sox).

Endpoints:
    NFL:  https://site.api.espn.com/apis/site/v2/sports/football/nfl/draft?year=YYYY
    NBA:  https://site.api.espn.com/apis/site/v2/sports/basketball/nba/draft?year=YYYY
    NHL:  https://site.api.espn.com/apis/site/v2/sports/ice-hockey/nhl/draft?year=YYYY
    MLB:  https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/draft?year=YYYY

Output:
    data/boston_drafts.json — All Boston teams' draft picks (current year + recent years)
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"

OUTPUT_PATH = DATA_DIR / "boston_drafts.json"

# Boston teams by sport (ESPN team IDs / identifiers)
BOSTON_TEAMS = {
    "nfl": {"patriots", "ne"},
    "nba": {"celtics", "bos"},
    "nhl": {"bruins", "bos"},
    "mlb": {"redsox", "bos"},
}

# Current year for draft queries
CURRENT_YEAR = 2026

# ESPN draft endpoints
ESPN_DRAFT_URLS = {
    "NFL": f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/draft?year={CURRENT_YEAR}",
    "NBA": f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/draft?year={CURRENT_YEAR}",
    "NHL": f"https://site.api.espn.com/apis/site/v2/sports/ice-hockey/nhl/draft?year={CURRENT_YEAR}",
    "MLB": f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/draft?year={CURRENT_YEAR}",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict | None:
    """
    Fetch a URL and return parsed JSON. Return None on failure.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Boston Dan Sports Hub)"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  warning: fetch failed for {url}: {e}", file=sys.stderr)
        return None


def normalize_team_name(team_name: str) -> str:
    """Normalize team name to lowercase for comparison."""
    return team_name.lower().replace(" ", "")


def is_boston_team(team_name: str, sport: str) -> bool:
    """Check if a team name is a Boston team for the given sport."""
    if not team_name:
        return False
    normalized = normalize_team_name(team_name)
    sport_lower = sport.lower()
    boston_identifiers = BOSTON_TEAMS.get(sport_lower, set())
    return normalized in boston_identifiers


def extract_draft_picks(sport: str, draft_data: dict) -> list:
    """
    Extract picks from ESPN draft data for the given sport.
    Returns a list of dicts: {round, pick_overall, player_name, position, college, team}
    """
    picks = []
    if not draft_data:
        return picks

    # ESPN draft response structure varies by sport; try the most common path
    rounds = draft_data.get("rounds", [])
    for round_data in rounds:
        round_num = round_data.get("number", 0)
        selections = round_data.get("selections", [])
        for sel in selections:
            team_name = sel.get("team", {}).get("name", "")
            if not is_boston_team(team_name, sport):
                continue

            player = sel.get("player", {})
            player_name = player.get("fullName", "").strip()
            position = player.get("position", "").strip()
            college_dict = player.get("college", {})
            if isinstance(college_dict, dict):
                college = college_dict.get("name", "").strip()
            else:
                college = str(college_dict).strip() if college_dict else ""

            pick_overall = sel.get("overallNumber", 0)

            if player_name:
                picks.append({
                    "round": round_num,
                    "pick_overall": pick_overall,
                    "player_name": player_name,
                    "position": position,
                    "college": college,
                    "team": normalize_team_name(team_name),
                })

    return picks


def main():
    """Fetch draft picks for all Boston teams across all sports."""
    print("=" * 60)
    print("fetch_draft.py: Boston teams' draft picks")
    print("=" * 60)

    # Create data directory if needed
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    active_drafts = []

    for sport, url in ESPN_DRAFT_URLS.items():
        print(f"\n[{sport}] Fetching draft from {url[:60]}...")
        draft_data = fetch_json(url)
        if not draft_data:
            print(f"  warning: no data from {sport} draft endpoint")
            continue

        picks = extract_draft_picks(sport, draft_data)
        if not picks:
            print(f"  no Boston team picks found in {sport} draft")
            continue

        print(f"  found {len(picks)} pick(s) for Boston teams in {sport}")
        for pick in picks:
            print(f"    Round {pick['round']}, Pick {pick['pick_overall']}: "
                  f"{pick['player_name']} ({pick['position']}) from {pick['college']}")

        # Group by team and round
        for pick in picks:
            team = pick["team"]
            draft_entry = next(
                (d for d in active_drafts if d["sport"] == sport and d["team"] == team),
                None
            )
            if draft_entry is None:
                draft_entry = {
                    "sport": sport,
                    "year": CURRENT_YEAR,
                    "team": team,
                    "picks": []
                }
                active_drafts.append(draft_entry)
            draft_entry["picks"].append(pick)

    # Write output
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_drafts": active_drafts,
    }

    try:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n✅ published: {OUTPUT_PATH}")
        return 0
    except IOError as e:
        print(f"  ❌ error: could not write {OUTPUT_PATH}: {e}", file=sys.stderr)
        # Write empty-but-valid JSON as fallback
        fallback = {"generated_at": datetime.now(timezone.utc).isoformat(), "active_drafts": []}
        try:
            OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(OUTPUT_PATH, "w") as f:
                json.dump(fallback, f, indent=2)
            print(f"  fallback written: {OUTPUT_PATH}")
            return 0
        except Exception as e2:
            print(f"  ❌ fallback also failed: {e2}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
