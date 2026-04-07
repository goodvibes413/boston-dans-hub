#!/usr/bin/env python3
"""
fetch_nhl.py — Fetches Boston Bruins game data from the NHL API.

Endpoints used:
    Scoreboard : https://api-web.nhle.com/v1/score/YYYY-MM-DD
    Boxscore   : https://api-web.nhle.com/v1/gamecenter/{id}/boxscore
    Schedule   : https://api-web.nhle.com/v1/club-schedule-season/BOS/{season}

Outputs:
    data/bruins_boxscore.json  — Yesterday's game (or played:false if no game)
    data/bruins_schedule.json  — Bruins games in the next 7 days
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR     = PROJECT_ROOT / "data"

BOXSCORE_PATH = DATA_DIR / "bruins_boxscore.json"
SCHEDULE_PATH = DATA_DIR / "bruins_schedule.json"
NEWS_PATH     = DATA_DIR / "bruins_news.json"

BRUINS_ABBREV = "BOS"
BRUINS_ID     = 6

NHL_SCORE_BASE    = "https://api-web.nhle.com/v1/score"
NHL_BOXSCORE_BASE = "https://api-web.nhle.com/v1/gamecenter"
NHL_SCHEDULE_BASE = "https://api-web.nhle.com/v1/club-schedule-season"
ESPN_NEWS         = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/news?team=bos"
NEWS_TOP_N        = 3

# Period number → readable label
PERIOD_LABELS = {1: "1st", 2: "2nd", 3: "3rd", 4: "OT", 5: "SO"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict:
    """
    Fetch a URL and return the parsed JSON body as a dict.

    The NHL API occasionally returns 307 redirects (e.g. the /now season
    shortcut). Python's urllib follows 301/302 automatically but not 307,
    so we handle 307 manually here by re-requesting the Location header.

    Args:
        url: Fully-formed HTTPS URL.

    Returns:
        Parsed JSON as a Python dict.

    Raises:
        RuntimeError: On HTTP error, network failure, timeout, or bad JSON.
    """
    headers = {
        "User-Agent": (
            "bruins-fanbot/1.0 "
            "(+https://github.com/goodvibes413/boston-dans-hub)"
        )
    }
    target = url
    # Follow up to 3 redirects (handles 307 which urllib won't auto-follow)
    for _ in range(3):
        req = urllib.request.Request(target, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 307, 308):
                location = e.headers.get("Location")
                if location:
                    target = location
                    continue
            raise RuntimeError(
                f"HTTP {e.code} fetching {target}: {e.reason}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Network error fetching {target}: {e.reason}"
            ) from e
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Invalid JSON from {target}: {e}") from e
    raise RuntimeError(f"Too many redirects fetching {url}")


def current_nhl_season() -> str:
    """
    Return the current NHL season as an 8-digit string.

    The NHL season starts in October, so months Jan–Sep belong to a season
    that started the previous calendar year.

    Examples:
        April  2026  →  "20252026"
        October 2026 →  "20262027"
    """
    today = date.today()
    start_year = today.year if today.month >= 10 else today.year - 1
    return f"{start_year}{start_year + 1}"


def get_team_name(team: dict) -> str:
    """
    Extract the best human-readable team name from an NHL API team dict.

    The scoreboard endpoint uses `name.default`; the schedule endpoint uses
    `commonName.default`. We try both, falling back to the abbreviation.
    """
    # Schedule endpoint: commonName.default = "Capitals" (no city prefix)
    common = team.get("commonName", {}).get("default", "")
    if common:
        return common
    # Scoreboard endpoint: name.default = "Sabres" or full "Buffalo Sabres"
    name = team.get("name", {}).get("default", "")
    if name:
        return name
    return team.get("abbrev", "Unknown")


def get_team_full_name(team: dict) -> str:
    """
    Build a full 'City TeamName' string where possible.

    Schedule endpoint has placeName + commonName; scoreboard sometimes has
    the full name in name.default already.
    """
    place  = team.get("placeName", {}).get("default", "")
    common = team.get("commonName", {}).get("default", "")
    if place and common:
        return f"{place} {common}"
    return get_team_name(team)


def parse_pub_date(raw: str) -> str:
    """Normalise an ESPN date string like '2026-04-07T18:32:00Z' to ISO 8601."""
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return raw


def fetch_news() -> None:
    """
    Fetch the top Bruins news headlines from ESPN and write to
    data/bruins_news.json.
    """
    now_utc = datetime.now(timezone.utc)
    try:
        print("  Fetching Bruins news from ESPN...")
        data     = fetch_json(ESPN_NEWS)
        articles = data.get("articles", [])
        headlines = []
        for article in articles[:NEWS_TOP_N]:
            links = article.get("links", {})
            url   = (
                links.get("web", {}).get("href", "")
                or links.get("api", {}).get("news", {}).get("href", "")
            )
            headlines.append({
                "headline":    article.get("headline",    "").strip(),
                "description": article.get("description", "").strip(),
                "published":   parse_pub_date(article.get("published", "")),
                "url":         url,
            })
        print(f"  Captured {len(headlines)} headline(s).")
        for i, h in enumerate(headlines, 1):
            print(f"  {i}. {h['headline'][:72]}")
        result = {"generated_at": now_utc.isoformat(), "headlines": headlines}
        NEWS_PATH.write_text(json.dumps(result, indent=2))
        print(f"  Saved to {NEWS_PATH}")
    except Exception as e:
        print(f"[ERROR] fetch_news failed: {e}")
        err = {"generated_at": now_utc.isoformat(), "error": str(e), "headlines": []}
        try:
            NEWS_PATH.write_text(json.dumps(err, indent=2))
        except Exception:
            pass
        sys.exit(1)


def find_bruins_game(games: list):
    """Return the first game in which the Bruins appear, or None."""
    for game in games:
        away = game.get("awayTeam", {})
        home = game.get("homeTeam", {})
        if (
            away.get("abbrev") == BRUINS_ABBREV
            or away.get("id") == BRUINS_ID
            or home.get("abbrev") == BRUINS_ABBREV
            or home.get("id") == BRUINS_ID
        ):
            return game
    return None


def build_status(game_state: str, period_type: str) -> str:
    """Translate NHL gameState + periodType into a readable status string."""
    if game_state in ("OFF", "FINAL"):
        if period_type == "OT":
            return "Final/OT"
        if period_type == "SO":
            return "Final/SO"
        return "Final"
    if game_state in ("LIVE", "CRIT"):
        return "In Progress"
    if game_state in ("PRE", "FUT", "SCHEDULED"):
        return "Scheduled"
    return game_state


def parse_period_scores(goals: list) -> list:
    """
    Build a period-by-period scoring breakdown from the scoreboard goals list.

    Each goal has `period` (int) and `teamAbbrev` (str).  We count goals
    per team per period and return a sorted list of dicts.

    Returns list like:
        [
            {"period": 1, "label": "1st", "bruins": 2, "opponent": 0},
            {"period": 2, "label": "2nd", "bruins": 1, "opponent": 4},
            {"period": 3, "label": "3rd", "bruins": 0, "opponent": 2},
        ]
    """
    buckets: dict[int, dict] = {}
    for goal in goals:
        p    = goal.get("period", 0)
        team = goal.get("teamAbbrev", "")
        if p not in buckets:
            buckets[p] = {
                "period":   p,
                "label":    PERIOD_LABELS.get(p, f"P{p}"),
                "bruins":   0,
                "opponent": 0,
            }
        if team == BRUINS_ABBREV:
            buckets[p]["bruins"] += 1
        else:
            buckets[p]["opponent"] += 1

    return [buckets[k] for k in sorted(buckets)]


# ---------------------------------------------------------------------------
# Boxscore
# ---------------------------------------------------------------------------

def fetch_boxscore() -> None:
    """
    Fetch yesterday's Bruins game from the NHL scoreboard and write a full
    boxscore JSON to data/bruins_boxscore.json.

    Two API calls are made when a game is found:
        1. /v1/score/{date}              — game summary, goals, period data
        2. /v1/gamecenter/{id}/boxscore  — detailed goalie stat lines
    """
    yesterday_utc = datetime.now(timezone.utc) - timedelta(days=1)
    game_date_iso = yesterday_utc.strftime("%Y-%m-%d")

    try:
        # --- 1. Fetch daily scoreboard ---
        print(f"  Fetching NHL scoreboard for {game_date_iso}...")
        score_data = fetch_json(f"{NHL_SCORE_BASE}/{game_date_iso}")

        games       = score_data.get("games", [])
        bruins_game = find_bruins_game(games)

        if bruins_game is None:
            print(f"  No Bruins game found for {game_date_iso}.")
            result = {"game_date": game_date_iso, "played": False}
            BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
            print(f"  Saved to {BOXSCORE_PATH}")
            return

        # --- 2. Extract teams, score, and status ---
        game_id  = bruins_game["id"]
        away     = bruins_game.get("awayTeam", {})
        home     = bruins_game.get("homeTeam", {})

        bruins_home = home.get("abbrev") == BRUINS_ABBREV
        b_team  = home   if bruins_home else away
        op_team = away   if bruins_home else home

        bruins_score = b_team.get("score", 0)
        opp_score    = op_team.get("score", 0)
        opp_name     = get_team_full_name(op_team)
        opp_abbrev   = op_team.get("abbrev", "")

        game_state  = bruins_game.get("gameState", "")
        period_type = bruins_game.get("periodDescriptor", {}).get("periodType", "REG")
        status      = build_status(game_state, period_type)

        # --- 3. Goal scorers + assists ---
        raw_goals    = bruins_game.get("goals", [])
        goal_scorers = []
        for goal in raw_goals:
            assists = [
                a.get("name", {}).get("default", "Unknown")
                for a in goal.get("assists", [])
            ]
            goal_scorers.append({
                "team":      goal.get("teamAbbrev", ""),
                "scorer":    goal.get("name", {}).get("default", "Unknown"),
                "assists":   assists,
                "period":    goal.get("period", 0),
                "period_label": PERIOD_LABELS.get(goal.get("period", 0), ""),
                "time":      goal.get("timeInPeriod", ""),
                "strength":  goal.get("strength", "ev"),   # ev / pp / sh
                "empty_net": goal.get("goalModifier", "none") == "empty-net",
            })

        # --- 4. Period-by-period scoring ---
        period_scores = parse_period_scores(raw_goals)

        # --- 5. Goalie stats (separate boxscore endpoint) ---
        print(f"  Found game ID {game_id}. Fetching boxscore for goalie stats...")
        boxscore     = fetch_json(f"{NHL_BOXSCORE_BASE}/{game_id}/boxscore")
        player_stats = boxscore.get("playerByGameStats", {})
        side         = "homeTeam" if bruins_home else "awayTeam"
        raw_goalies  = player_stats.get(side, {}).get("goalies", [])

        goalies = []
        for g in raw_goalies:
            save_pct = g.get("savePctg", 0.0)
            goalies.append({
                "name":         g.get("name", {}).get("default", "Unknown"),
                "decision":     g.get("decision", ""),       # "W", "L", "OTL", ""
                "saves":        g.get("saves", 0),
                "shots_against": g.get("shotsAgainst", 0),
                "save_pct":     round(float(save_pct), 3),
                "toi":          g.get("toi", ""),
            })

        # --- 6. Summarise + write ---
        loc = "home" if bruins_home else "away"
        print(
            f"  Game: Bruins {bruins_score} — {opp_name} {opp_score} "
            f"[{loc}] — {status}"
        )
        print(
            f"  {len(goal_scorers)} goal(s) parsed, "
            f"{len(goalies)} Bruins goalie(s) found."
        )

        result = {
            "game_date":      game_date_iso,
            "played":         True,
            "status":         status,
            "home":           bruins_home,
            "bruins_score":   bruins_score,
            "opponent":       opp_name,
            "opponent_abbrev": opp_abbrev,
            "opponent_score": opp_score,
            "period_scores":  period_scores,
            "goal_scorers":   goal_scorers,
            "goalies":        goalies,
        }
        BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
        print(f"  Saved boxscore to {BOXSCORE_PATH}")

    except Exception as e:
        print(f"[ERROR] fetch_boxscore failed: {e}")
        error_result = {"game_date": game_date_iso, "error": str(e)}
        try:
            BOXSCORE_PATH.write_text(json.dumps(error_result, indent=2))
        except Exception:
            pass
        sys.exit(1)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def fetch_schedule() -> None:
    """
    Fetch the full Bruins season schedule and filter to games in the next
    7 days (inclusive of today through today+7), writing to
    data/bruins_schedule.json.

    The schedule endpoint returns the full season — we filter in Python.
    Only regular-season (gameType=2) and playoff (gameType=3) games are
    included; pre-season (gameType=1) is excluded.
    """
    now_utc      = datetime.now(timezone.utc)
    from_dt      = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    to_dt        = from_dt + timedelta(days=7)
    from_date    = from_dt.date()
    to_date      = to_dt.date()
    season       = current_nhl_season()

    try:
        url = f"{NHL_SCHEDULE_BASE}/{BRUINS_ABBREV}/{season}"
        print(
            f"  Fetching Bruins {season} schedule "
            f"({from_date} → {to_date})..."
        )
        data     = fetch_json(url)
        all_games = data.get("games", [])

        upcoming = []
        for game in all_games:
            game_date_str = game.get("gameDate", "")
            if not game_date_str:
                continue

            # gameDate is "YYYY-MM-DD" with no timezone — compare as date
            try:
                game_date = datetime.strptime(game_date_str, "%Y-%m-%d").date()
            except ValueError:
                print(f"  Warning: unparseable date '{game_date_str}', skipping.")
                continue

            if not (from_date <= game_date <= to_date):
                continue

            # Skip pre-season (gameType 1); keep regular (2) and playoffs (3)
            if game.get("gameType", 2) == 1:
                continue

            away = game.get("awayTeam", {})
            home = game.get("homeTeam", {})
            bruins_home = home.get("abbrev") == BRUINS_ABBREV
            opp         = away if bruins_home else home

            state       = game.get("gameState", "FUT")
            period_type = game.get("periodDescriptor", {}).get("periodType", "REG")
            status      = build_status(state, period_type)

            upcoming.append({
                "game_id":       game.get("id"),
                "date":          game_date_str,
                "start_time_utc": game.get("startTimeUTC", ""),
                "opponent":      get_team_full_name(opp),
                "opponent_abbrev": opp.get("abbrev", ""),
                "home":          bruins_home,
                "status":        status,
                "venue":         game.get("venue", {}).get("default", ""),
            })

        print(f"  Found {len(upcoming)} game(s) in the next 7 days.")

        result = {
            "generated_at": now_utc.isoformat(),
            "from_date":    str(from_date),
            "to_date":      str(to_date),
            "games":        upcoming,
        }
        SCHEDULE_PATH.write_text(json.dumps(result, indent=2))
        print(f"  Saved schedule to {SCHEDULE_PATH}")

    except Exception as e:
        print(f"[ERROR] fetch_schedule failed: {e}")
        error_result = {"error": str(e), "games": []}
        try:
            SCHEDULE_PATH.write_text(json.dumps(error_result, indent=2))
        except Exception:
            pass
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run all three fetchers in sequence."""
    print("=" * 52)
    print("  Boston Dan's Hub — NHL Bruins Data Fetcher")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)

    print("\n[1/3] Yesterday's boxscore")
    fetch_boxscore()

    print("\n[2/3] Next 7-day schedule")
    fetch_schedule()

    print("\n[3/3] Latest Bruins news")
    fetch_news()

    print("\nDone. Files written:")
    print(f"  {BOXSCORE_PATH}")
    print(f"  {SCHEDULE_PATH}")
    print(f"  {NEWS_PATH}")


if __name__ == "__main__":
    main()
