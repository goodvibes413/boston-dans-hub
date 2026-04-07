#!/usr/bin/env python3
"""
fetch_mlb.py — Fetches Boston Red Sox game data from the MLB Stats API.

Endpoints used:
    Schedule  : https://statsapi.mlb.com/api/v1/schedule
                  ?sportId=1&date=YYYY-MM-DD&teamId=111&hydrate=linescore
    Boxscore  : https://statsapi.mlb.com/api/v1/game/{gamePk}/boxscore
    Schedule  : https://statsapi.mlb.com/api/v1/schedule
                  ?sportId=1&teamId=111&startDate=...&endDate=...

Handles: extra innings, doubleheaders, no-game days.

Outputs:
    data/redsox_boxscore.json  — Yesterday's game(s) or played:false
    data/redsox_schedule.json  — Red Sox games in the next 7 days
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

BOXSCORE_PATH = DATA_DIR / "redsox_boxscore.json"
SCHEDULE_PATH = DATA_DIR / "redsox_schedule.json"
NEWS_PATH     = DATA_DIR / "redsox_news.json"

REDSOX_TEAM_ID   = 111
REDSOX_TEAM_NAME = "Boston Red Sox"

MLB_SCHEDULE_BASE = "https://statsapi.mlb.com/api/v1/schedule"
MLB_BOXSCORE_BASE = "https://statsapi.mlb.com/api/v1/game"
ESPN_NEWS         = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/news?team=bos"
NEWS_TOP_N        = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict:
    """
    Fetch a URL and return the parsed JSON body.

    Args:
        url: Fully-formed HTTPS URL.

    Returns:
        Parsed JSON as a Python dict.

    Raises:
        RuntimeError: On HTTP error, network failure, timeout, or bad JSON.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "redsox-fanbot/1.0 "
                "(+https://github.com/goodvibes413/boston-dans-hub)"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from {url}: {e}") from e


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
    Fetch the top Red Sox news headlines from ESPN and write to
    data/redsox_news.json.
    """
    now_utc = datetime.now(timezone.utc)
    try:
        print("  Fetching Red Sox news from ESPN...")
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


def safe_float(val, default=0.0) -> float:
    """Convert val to float, returning default on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def safe_int(val, default=0) -> int:
    """Convert val to int, returning default on failure."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def classify_mlb_game(game_date: str) -> str:
    """
    Heuristic classification based on game date.

    MLB regular season: Late Mar–Sep, Playoffs: Oct–Nov.
    game_date format: "2026-04-06" (ISO 8601).

    Returns: "regular", "playoff", or "offseason".
    """
    try:
        dt = datetime.strptime(game_date, "%Y-%m-%d")
        month = dt.month
        if 3 <= month <= 9:
            return "regular"
        if month in (10, 11):
            return "playoff"
        return "offseason"
    except ValueError:
        return "unknown"


def pitcher_decision(pitching_stats: dict) -> str:
    """
    Extract the pitcher's decision from their game stats.

    The API stores decisions in `note` as text like "(W, 2-0)" or "(L, 0-1)".
    We also check integer win/loss/save/hold flags as a fallback.

    Returns one of: "W", "L", "S", "H", "BS", or "" (no decision).
    """
    note = pitching_stats.get("note", "") or ""
    note = note.strip()
    if note.startswith("(BS"):
        return "BS"
    if note.startswith("(S"):
        return "S"
    if note.startswith("(W"):
        return "W"
    if note.startswith("(L"):
        return "L"
    if note.startswith("(H"):
        return "H"
    # Fallback to numeric fields
    if safe_int(pitching_stats.get("wins"))       > 0:
        return "W"
    if safe_int(pitching_stats.get("losses"))     > 0:
        return "L"
    if safe_int(pitching_stats.get("saves"))      > 0:
        return "S"
    if safe_int(pitching_stats.get("holds"))      > 0:
        return "H"
    if safe_int(pitching_stats.get("blownSaves")) > 0:
        return "BS"
    return ""


def parse_inning_label(inning_num: int, total_innings: int) -> str:
    """Return a readable inning label, tagging extras."""
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(
        inning_num if inning_num <= 3 else 0, "th"
    )
    label = f"{inning_num}{suffix}"
    if inning_num > total_innings:
        label += " (X)"
    return label


def build_linescore(linescore_data: dict, redsox_home: bool) -> list:
    """
    Build a per-inning scoring breakdown from the linescore object.

    Returns a list of dicts:
        [{"inning": 1, "label": "1st", "redsox": 0, "opponent": 0}, ...]
    """
    innings_raw      = linescore_data.get("innings", [])
    scheduled_inn    = safe_int(linescore_data.get("scheduledInnings", 9), 9)
    rows = []
    for inn in innings_raw:
        num  = safe_int(inn.get("num", 0))
        home = inn.get("home", {})
        away = inn.get("away", {})
        redsox_runs   = safe_int((home if redsox_home else away).get("runs", 0))
        opponent_runs = safe_int((away if redsox_home else home).get("runs", 0))
        rows.append({
            "inning":   num,
            "label":    parse_inning_label(num, scheduled_inn),
            "redsox":   redsox_runs,
            "opponent": opponent_runs,
        })
    return rows


def parse_game_boxscore(game_pk: int, redsox_home: bool, opponent_name: str) -> dict:
    """
    Fetch /game/{gamePk}/boxscore and extract:
      - Starting pitcher line
      - Bullpen lines (relievers who actually pitched)
      - Top 3 Red Sox hitters by season OPS (with game stats)

    Returns a dict with keys: starting_pitcher, bullpen, top_hitters.
    """
    url  = f"{MLB_BOXSCORE_BASE}/{game_pk}/boxscore"
    data = fetch_json(url)

    teams    = data.get("teams", {})
    side     = "home" if redsox_home else "away"
    rs_block = teams.get(side, {})
    players  = rs_block.get("players", {})       # {"ID123": {...}, ...}
    pitchers = rs_block.get("pitchers", [])      # ordered list of pitcher IDs
    batters  = rs_block.get("batters", [])       # ordered list of batter IDs

    # ---- Pitchers --------------------------------------------------------

    starters  = []
    relievers = []

    for pid in pitchers:
        player = players.get(f"ID{pid}", {})
        pstats = player.get("stats", {}).get("pitching", {})

        ip_str = pstats.get("inningsPitched", "0.0") or "0.0"

        # Skip pitchers who never recorded a meaningful out
        # (Listed but did not actually appear — IP "0.0")
        if ip_str == "0.0" and safe_int(pstats.get("outs")) == 0:
            continue

        name     = (
            player.get("person", {}).get("boxscoreName")
            or player.get("person", {}).get("fullName", "Unknown")
        )
        decision = pitcher_decision(pstats)
        line = {
            "name":           name,
            "innings_pitched": ip_str,
            "hits":           safe_int(pstats.get("hits")),
            "earned_runs":    safe_int(pstats.get("earnedRuns")),
            "runs":           safe_int(pstats.get("runs")),
            "strikeouts":     safe_int(pstats.get("strikeOuts")),
            "walks":          safe_int(pstats.get("baseOnBalls")),
            "decision":       decision,
        }
        if safe_int(pstats.get("gamesStarted")) == 1:
            starters.append(line)
        else:
            relievers.append(line)

    starting_pitcher = starters[0] if starters else None

    # ---- Batters — Top 3 by season OPS ----------------------------------

    hitter_rows = []
    for bid in batters:
        player = players.get(f"ID{bid}", {})
        bstats = player.get("stats", {}).get("batting", {})
        season = player.get("seasonStats", {}).get("batting", {})

        ab = safe_int(bstats.get("atBats"))
        if ab == 0:
            continue       # Didn't bat (pinch runner, etc.)

        ops_val = safe_float(season.get("ops", "0") or "0")
        name    = (
            player.get("person", {}).get("boxscoreName")
            or player.get("person", {}).get("fullName", "Unknown")
        )

        hitter_rows.append({
            "name":       name,
            "ab":         ab,
            "hits":       safe_int(bstats.get("hits")),
            "hr":         safe_int(bstats.get("homeRuns")),
            "rbi":        safe_int(bstats.get("rbi")),
            "season_avg": season.get("avg", ".000") or ".000",
            "season_ops": season.get("ops", ".000") or ".000",
            "_ops_sort":  ops_val,
        })

    top_hitters = sorted(hitter_rows, key=lambda r: r["_ops_sort"], reverse=True)[:3]
    for h in top_hitters:
        del h["_ops_sort"]

    return {
        "starting_pitcher": starting_pitcher,
        "bullpen":          relievers,
        "top_hitters":      top_hitters,
    }


def parse_game(game: dict, game_date_iso: str) -> dict:
    """
    Parse a single game entry from the schedule response into our output
    schema. Fetches the separate /boxscore endpoint for player stats.

    Returns a fully-populated game dict.
    """
    game_pk    = game["gamePk"]
    home_team  = game["teams"]["home"]
    away_team  = game["teams"]["away"]
    redsox_home = home_team["team"]["id"] == REDSOX_TEAM_ID

    rs_side  = home_team if redsox_home else away_team
    opp_side = away_team if redsox_home else home_team

    redsox_score = safe_int(rs_side.get("score"))
    opp_score    = safe_int(opp_side.get("score"))
    opp_name     = opp_side["team"]["name"]

    # Linescore (hydrated in schedule response)
    linescore_data  = game.get("linescore", {})
    scheduled_inn   = safe_int(linescore_data.get("scheduledInnings", 9), 9)
    current_inn     = safe_int(linescore_data.get("currentInning", scheduled_inn))
    extra_innings   = current_inn > scheduled_inn
    linescore_rows  = build_linescore(linescore_data, redsox_home)

    # Totals from linescore
    ls_teams   = linescore_data.get("teams", {})
    rs_ls      = ls_teams.get("home" if redsox_home else "away", {})
    opp_ls     = ls_teams.get("away" if redsox_home else "home", {})
    total_hits = safe_int(rs_ls.get("hits"))
    total_errs = safe_int(rs_ls.get("errors"))

    # Boxscore
    print(f"    → Fetching boxscore for game {game_pk}...")
    bs = parse_game_boxscore(game_pk, redsox_home, opp_name)

    return {
        "game_pk":         game_pk,
        "game_number":     safe_int(game.get("gameNumber", 1)),
        "status":          game["status"]["detailedState"],
        "innings_played":  current_inn,
        "extra_innings":   extra_innings,
        "home":            redsox_home,
        "redsox_score":    redsox_score,
        "opponent":        opp_name,
        "opponent_score":  opp_score,
        "redsox_hits":     total_hits,
        "redsox_errors":   total_errs,
        "linescore":       linescore_rows,
        "starting_pitcher": bs["starting_pitcher"],
        "bullpen":         bs["bullpen"],
        "top_hitters":     bs["top_hitters"],
    }


# ---------------------------------------------------------------------------
# Boxscore
# ---------------------------------------------------------------------------

def fetch_boxscore() -> None:
    """
    Fetch yesterday's Red Sox game(s) and write to data/redsox_boxscore.json.

    Handles:
      - No game: writes {"played": false}
      - Single game: games array with one entry
      - Doubleheader: games array with two entries, doubleheader:true
    """
    yesterday_utc = datetime.now(timezone.utc) - timedelta(days=1)
    game_date_iso = yesterday_utc.strftime("%Y-%m-%d")

    try:
        url = (
            f"{MLB_SCHEDULE_BASE}"
            f"?sportId=1&date={game_date_iso}&teamId={REDSOX_TEAM_ID}"
            f"&hydrate=linescore"
        )
        print(f"  Fetching MLB schedule for {game_date_iso}...")
        schedule = fetch_json(url)

        dates = schedule.get("dates", [])
        if not dates:
            print(f"  No Red Sox game found for {game_date_iso}.")
            result = {
                "game_date": game_date_iso,
                "played": False,
                "season_type": classify_mlb_game(game_date_iso),
            }
            BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
            print(f"  Saved to {BOXSCORE_PATH}")
            return

        games_raw = dates[0].get("games", [])

        # Filter to only Final games (skip postponed / in-progress)
        final_games = [
            g for g in games_raw
            if g.get("status", {}).get("abstractGameState") == "Final"
        ]

        if not final_games:
            print(
                f"  Found {len(games_raw)} scheduled game(s) but none are Final yet."
            )
            result = {
                "game_date": game_date_iso,
                "played": False,
                "season_type": classify_mlb_game(game_date_iso),
            }
            BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
            print(f"  Saved to {BOXSCORE_PATH}")
            return

        is_doubleheader = len(final_games) > 1
        print(
            f"  Found {len(final_games)} completed game(s)"
            f"{' (doubleheader)' if is_doubleheader else ''}."
        )

        parsed_games = []
        for g in final_games:
            game_num = safe_int(g.get("gameNumber", 1))
            print(f"  Parsing game {game_num}...")
            parsed_games.append(parse_game(g, game_date_iso))

        result = {
            "game_date":    game_date_iso,
            "played":       True,
            "season_type":  classify_mlb_game(game_date_iso),
            "doubleheader": is_doubleheader,
            "games":        parsed_games,
        }
        BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
        print(f"  Saved boxscore ({len(parsed_games)} game(s)) to {BOXSCORE_PATH}")

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
    Fetch Red Sox schedule for the next 7 days and write to
    data/redsox_schedule.json.

    Uses the MLB schedule endpoint with startDate/endDate range.
    Handles doubleheaders (both games in the same date appear as separate
    entries, tagged with game_number).
    """
    now_utc      = datetime.now(timezone.utc)
    from_dt      = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    to_dt        = from_dt + timedelta(days=7)
    from_date    = from_dt.strftime("%Y-%m-%d")
    to_date      = to_dt.strftime("%Y-%m-%d")

    try:
        url = (
            f"{MLB_SCHEDULE_BASE}"
            f"?sportId=1&teamId={REDSOX_TEAM_ID}"
            f"&startDate={from_date}&endDate={to_date}"
        )
        print(f"  Fetching Red Sox schedule ({from_date} → {to_date})...")
        data = fetch_json(url)

        games = []
        for day in data.get("dates", []):
            for game in day.get("games", []):
                home = game["teams"]["home"]
                away = game["teams"]["away"]
                redsox_home = home["team"]["id"] == REDSOX_TEAM_ID
                opp         = away if redsox_home else home

                state  = game.get("status", {}).get("abstractGameState", "Scheduled")
                detail = game.get("status", {}).get("detailedState", state)

                game_date = game.get("officialDate", "")
                games.append({
                    "game_pk":      game["gamePk"],
                    "game_number":  safe_int(game.get("gameNumber", 1)),
                    "date":         game_date,
                    "game_time_utc": game.get("gameDate", ""),
                    "opponent":     opp["team"]["name"],
                    "home":         redsox_home,
                    "status":       detail,
                    "venue":        game.get("venue", {}).get("name", ""),
                    "day_night":    game.get("dayNight", ""),
                    "doubleheader": game.get("doubleHeader", "N") != "N",
                    "season_type":  classify_mlb_game(game_date),
                })

        print(f"  Found {len(games)} game(s) in the next 7 days.")

        result = {
            "generated_at": now_utc.isoformat(),
            "from_date":    from_date,
            "to_date":      to_date,
            "games":        games,
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
    print("  Boston Dan's Hub — MLB Red Sox Data Fetcher")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)

    print("\n[1/3] Yesterday's boxscore")
    fetch_boxscore()

    print("\n[2/3] Next 7-day schedule")
    fetch_schedule()

    print("\n[3/3] Latest Red Sox news")
    fetch_news()

    print("\nDone. Files written:")
    print(f"  {BOXSCORE_PATH}")
    print(f"  {SCHEDULE_PATH}")
    print(f"  {NEWS_PATH}")


if __name__ == "__main__":
    main()
