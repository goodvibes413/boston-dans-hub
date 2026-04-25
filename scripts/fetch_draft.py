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


def _extract_picks_flat_schema(sport: str, draft_data: dict) -> list:
    """
    New ESPN schema (in use as of 2026-04-25 NFL draft):
        rounds:    int (count of rounds, e.g. 7)
        picks:     [ { pick, overall, round, athlete: {...}, teamId, ... }, ... ]
        teams:     [ { id, location, name, abbreviation, ... }, ... ]
        positions: [ { id, displayName, abbreviation }, ... ]
    Picks are a flat list across all rounds; team and position must be resolved
    via lookup tables.
    """
    out = []
    picks = draft_data.get("picks")
    if not isinstance(picks, list):
        return out

    teams_by_id = {
        str(t.get("id")): t
        for t in draft_data.get("teams", []) or []
        if isinstance(t, dict) and t.get("id") is not None
    }
    positions_by_id = {
        str(p.get("id")): p
        for p in draft_data.get("positions", []) or []
        if isinstance(p, dict) and p.get("id") is not None
    }

    for sel in picks:
        if not isinstance(sel, dict):
            continue
        team_id = str(sel.get("teamId", ""))
        team_obj = teams_by_id.get(team_id, {})
        team_name = team_obj.get("name") or team_obj.get("displayName") or ""
        team_abbr = team_obj.get("abbreviation") or ""

        # Match Boston team by either the team name (e.g. "Patriots") or
        # abbreviation (e.g. "NE") — both are in BOSTON_TEAMS for safety.
        if not (is_boston_team(team_name, sport) or is_boston_team(team_abbr, sport)):
            continue

        athlete = sel.get("athlete") or {}
        if not isinstance(athlete, dict):
            athlete = {}
        player_name = (athlete.get("displayName") or athlete.get("fullName") or "").strip()
        if not player_name:
            continue  # Pick announced but athlete not yet populated — skip

        # Position: athlete.position.id → positions[].displayName
        pos_obj = athlete.get("position") or {}
        if isinstance(pos_obj, dict):
            pos_id = str(pos_obj.get("id", ""))
            pos_lookup = positions_by_id.get(pos_id, {})
            position = (pos_lookup.get("abbreviation") or pos_lookup.get("displayName") or "").strip()
        else:
            position = str(pos_obj).strip()

        # College: athlete.team.location is the most natural form ("Utah", "Indiana").
        college_obj = athlete.get("team") or {}
        if isinstance(college_obj, dict):
            college = (college_obj.get("location")
                       or college_obj.get("displayName")
                       or college_obj.get("name") or "").strip()
        else:
            college = str(college_obj).strip()

        out.append({
            "round": sel.get("round", 0),
            "pick_overall": sel.get("overall", sel.get("pick", 0)),
            "player_name": player_name,
            "position": position,
            "college": college,
            "team": normalize_team_name(team_name) if team_name else team_abbr.lower(),
        })
    return out


def _extract_picks_nested_schema(sport: str, draft_data: dict) -> list:
    """
    Legacy ESPN schema (kept as a fallback in case ESPN reverts or another sport uses it):
        rounds: [ { number, selections: [ { team, player, overallNumber } ] } ]
    """
    out = []
    rounds = draft_data.get("rounds")
    if not isinstance(rounds, list):
        return out

    for round_data in rounds:
        if not isinstance(round_data, dict):
            continue
        round_num = round_data.get("number", 0)
        selections = round_data.get("selections", []) or []
        if not isinstance(selections, list):
            continue
        for sel in selections:
            if not isinstance(sel, dict):
                continue
            team_obj = sel.get("team") or {}
            team_name = team_obj.get("name", "") if isinstance(team_obj, dict) else ""
            if not is_boston_team(team_name, sport):
                continue

            player = sel.get("player") or {}
            if not isinstance(player, dict):
                continue
            player_name = (player.get("fullName") or "").strip()
            if not player_name:
                continue
            position = (player.get("position") or "").strip() if isinstance(player.get("position"), str) else ""
            college_dict = player.get("college") or {}
            if isinstance(college_dict, dict):
                college = (college_dict.get("name") or "").strip()
            else:
                college = str(college_dict).strip() if college_dict else ""

            out.append({
                "round": round_num,
                "pick_overall": sel.get("overallNumber", 0),
                "player_name": player_name,
                "position": position,
                "college": college,
                "team": normalize_team_name(team_name),
            })
    return out


def extract_draft_picks(sport: str, draft_data: dict) -> list:
    """
    Extract picks from ESPN draft data for the given sport.
    Returns a list of dicts: {round, pick_overall, player_name, position, college, team}.

    ESPN's draft schema varies by sport and has changed over time. We try the
    new flat-list schema first, fall back to the legacy nested-rounds schema.
    Any parsing error is logged and yields an empty list — never raises, so a
    single bad endpoint can't kill the pipeline.
    """
    if not isinstance(draft_data, dict):
        return []

    try:
        picks = _extract_picks_flat_schema(sport, draft_data)
        if picks:
            return picks
        # New schema present but no Boston picks — still return empty.
        # Only fall through to legacy if the flat schema clearly doesn't apply.
        if isinstance(draft_data.get("picks"), list):
            return []
    except Exception as e:
        print(f"  warning: flat-schema parse failed for {sport}: {e}", file=sys.stderr)

    try:
        return _extract_picks_nested_schema(sport, draft_data)
    except Exception as e:
        print(f"  warning: nested-schema parse failed for {sport}: {e}", file=sys.stderr)
        return []


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
        try:
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
        except Exception as e:
            # One bad sport must NOT crash the pipeline. Log and move on.
            print(f"  ❌ unexpected error processing {sport} draft: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            continue

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
