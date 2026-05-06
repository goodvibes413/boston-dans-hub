#!/usr/bin/env python3
"""
fetch_roster.py — Fetch current active rosters for all 4 Boston teams.

Queries ESPN (NFL/NBA/MLB) and the NHL official API for current-season
rosters. Output is intentionally slim: player name + position only.
This gives generate_rant.py enough to ground Dan's commentary ("check
that the player is actually on the team") without bloating the prompt.

Endpoints:
    Patriots: https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/17/roster
    Celtics:  https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/2/roster
    Red Sox:  https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/2/roster
    Bruins:   https://api-web.nhle.com/v1/roster/BOS/current

Output:
    data/boston_roster.json
"""

import json
import os
import ssl
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

OUTPUT_PATH = Path(
    os.environ.get("ROSTER_PATH", str(DATA_DIR / "boston_roster.json"))
)

# Build an SSL context that works across environments (macOS dev + Ubuntu CI).
# Try to use certifi's CA bundle if available; fall back to system defaults.
try:
    import certifi as _certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

ESPN_ROSTER_URLS = {
    "patriots": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/17/roster",
    "celtics":  "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/2/roster",
    "redsox":   "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/2/roster",
}

NHL_BRUINS_URL = "https://api-web.nhle.com/v1/roster/BOS/current"

# NHL positionCode → readable abbreviation
NHL_POSITION_MAP = {
    "L": "LW",
    "C": "C",
    "R": "RW",
    "D": "D",
    "G": "G",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict | None:
    """Fetch a URL and return parsed JSON. Returns None on any failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Boston Dan Sports Hub)"},
        )
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, TimeoutError) as e:
        print(f"  warning: fetch failed for {url}: {e}", file=sys.stderr)
        return None


def _slim(name: str, position: str) -> dict:
    return {"name": name.strip(), "position": position.strip()}


# ---------------------------------------------------------------------------
# Per-sport parsers
# ---------------------------------------------------------------------------

def parse_espn_roster_grouped(data: dict) -> list[dict]:
    """
    ESPN schema where athletes are grouped by position category:
      athletes: [{position: "offense", items: [{fullName, position.abbreviation, ...}]}]
    Used by NFL and MLB endpoints.
    """
    players: list[dict] = []
    for group in data.get("athletes") or []:
        if not isinstance(group, dict):
            continue
        for athlete in group.get("items") or []:
            if not isinstance(athlete, dict):
                continue
            name = (athlete.get("fullName") or "").strip()
            if not name:
                continue
            pos_obj = athlete.get("position") or {}
            position = (
                pos_obj.get("abbreviation")
                or pos_obj.get("displayName")
                or ""
            ).strip() if isinstance(pos_obj, dict) else ""
            players.append(_slim(name, position))
    return players


def parse_espn_roster_flat(data: dict) -> list[dict]:
    """
    ESPN schema where athletes are a flat array (NBA endpoint):
      athletes: [{fullName, position: {abbreviation, ...}, ...}]
    Position is present as a nested object; extract abbreviation if available.
    """
    players: list[dict] = []
    for athlete in data.get("athletes") or []:
        if not isinstance(athlete, dict):
            continue
        name = (athlete.get("fullName") or "").strip()
        if not name:
            continue
        pos_obj = athlete.get("position") or {}
        position = (
            pos_obj.get("abbreviation") or pos_obj.get("displayName") or ""
        ).strip() if isinstance(pos_obj, dict) else ""
        players.append(_slim(name, position))
    return players


def parse_nhl_roster(data: dict) -> list[dict]:
    """
    NHL official API schema:
      {forwards: [...], defensemen: [...], goalies: [...]}
    Names are language-aware dicts: firstName.default + lastName.default
    Position from positionCode ("L"/"C"/"R"/"D"/"G").
    """
    players: list[dict] = []
    for group_key, players_list in data.items():
        if not isinstance(players_list, list):
            continue
        for player in players_list:
            if not isinstance(player, dict):
                continue
            first = (player.get("firstName") or {})
            last = (player.get("lastName") or {})
            if isinstance(first, dict):
                first = first.get("default", "")
            if isinstance(last, dict):
                last = last.get("default", "")
            name = f"{first} {last}".strip()
            if not name:
                continue
            pos_code = player.get("positionCode", "")
            position = NHL_POSITION_MAP.get(pos_code, pos_code)
            players.append(_slim(name, position))
    return players


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("fetch_roster.py: Boston teams' active rosters")
    print("=" * 60)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    rosters: dict[str, list[dict]] = {}

    # --- ESPN teams (NFL, NBA, MLB) ---
    espn_parsers = {
        "patriots": parse_espn_roster_grouped,  # NFL: grouped by position
        "redsox":   parse_espn_roster_grouped,  # MLB: grouped by position
        "celtics":  parse_espn_roster_flat,      # NBA: flat array
    }

    for team, url in ESPN_ROSTER_URLS.items():
        print(f"\n[{team.upper()}] Fetching from {url[:70]}...")
        try:
            data = fetch_json(url)
            if not data:
                print(f"  warning: no data for {team}", file=sys.stderr)
                rosters[team] = []
                continue
            parser = espn_parsers[team]
            players = parser(data)
            rosters[team] = players
            print(f"  found {len(players)} player(s)")
            for p in players[:3]:
                print(f"    {p['name']} ({p['position']})")
            if len(players) > 3:
                print(f"    ... and {len(players) - 3} more")
        except Exception as e:
            print(f"  ❌ unexpected error for {team}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            rosters[team] = []

    # --- Bruins (NHL official API) ---
    print(f"\n[BRUINS] Fetching from {NHL_BRUINS_URL[:70]}...")
    try:
        data = fetch_json(NHL_BRUINS_URL)
        if not data:
            print("  warning: no data for bruins", file=sys.stderr)
            rosters["bruins"] = []
        else:
            players = parse_nhl_roster(data)
            rosters["bruins"] = players
            print(f"  found {len(players)} player(s)")
            for p in players[:3]:
                print(f"    {p['name']} ({p['position']})")
            if len(players) > 3:
                print(f"    ... and {len(players) - 3} more")
    except Exception as e:
        print(f"  ❌ unexpected error for bruins: {type(e).__name__}: {e}",
              file=sys.stderr)
        rosters["bruins"] = []

    # --- Write output ---
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rosters": rosters,
    }

    total = sum(len(v) for v in rosters.values())
    print(f"\n✅ total players: {total} across {len(rosters)} teams")

    try:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(output, indent=2))
        print(f"✅ published: {OUTPUT_PATH}")
        return 0
    except IOError as e:
        print(f"❌ error writing {OUTPUT_PATH}: {e}", file=sys.stderr)
        # Write empty-but-valid fallback so downstream scripts don't crash
        fallback = {"generated_at": datetime.now(timezone.utc).isoformat(), "rosters": {}}
        try:
            OUTPUT_PATH.write_text(json.dumps(fallback, indent=2))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
