#!/usr/bin/env python3
"""
fetch_nba.py — Fetches Boston Celtics game data from the ESPN unofficial API.

Outputs:
    data/celtics_boxscore.json  — Yesterday's game boxscore (or empty result if no game)
    data/celtics_schedule.json  — Celtics games in the next 7 days

Usage:
    python3 scripts/fetch_nba.py
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------

SCRIPT_DIR   = Path(__file__).resolve().parent   # .../scripts/
PROJECT_ROOT = SCRIPT_DIR.parent                 # .../fanbot-project/
DATA_DIR     = PROJECT_ROOT / "data"

BOXSCORE_PATH = DATA_DIR / "celtics_boxscore.json"
SCHEDULE_PATH = DATA_DIR / "celtics_schedule.json"
NEWS_PATH     = DATA_DIR / "celtics_news.json"

CELTICS_TEAM_ID = "2"
CELTICS_ABBREV  = "BOS"

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_SUMMARY    = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
ESPN_SCHEDULE   = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/2/schedule"
ESPN_NEWS       = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news?team=bos"
NEWS_TOP_N      = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict:
    """
    Fetch a URL and return the parsed JSON body as a dict.

    Sets a custom User-Agent so ESPN's CDN doesn't reject the request (some
    reverse proxies return 403 on the default Python urllib UA).

    Args:
        url: Fully-formed HTTPS URL to fetch.

    Returns:
        Parsed JSON as a Python dict.

    Raises:
        RuntimeError: On HTTP error, network failure, timeout, or bad JSON.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "celtics-fanbot/1.0 "
                "(+https://github.com/goodvibes413/boston-dans-hub)"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error fetching {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from {url}: {e}") from e


def parse_fg_pct(fg_str: str):
    """
    Parse an ESPN "made-attempted" string into a float percentage.

    ESPN returns per-player shooting stats in "M-A" format (e.g. "10-20").
    There is no pre-computed per-player FG% field; it must be derived here.

    Args:
        fg_str: String like "10-20", "0-0", "--", or "".

    Returns:
        Float percentage rounded to 1 decimal (e.g. 50.0).
        0.0  if attempted == 0 (avoids ZeroDivisionError, semantically correct).
        None if fg_str is malformed ("--", wrong part count, non-numeric).
    """
    if not fg_str or "-" not in fg_str:
        return None
    parts = fg_str.split("-")
    if len(parts) != 2:          # "--".split("-") yields ["", "", ""] → 3 parts
        return None
    try:
        made     = int(parts[0])
        attempted = int(parts[1])
    except ValueError:
        return None
    if attempted == 0:
        return 0.0
    return round((made / attempted) * 100, 1)


# ---------------------------------------------------------------------------
# Boxscore
# ---------------------------------------------------------------------------

def classify_nba_game(game_date: str, season_type_id: int = 2) -> str:
    """
    Classify an NBA game using the ESPN seasonType id when available,
    falling back to a date heuristic.

    ESPN seasonType ids: 1=preseason, 2=regular, 3=playoffs, 4=offseason.
    NBA playoffs run mid-April through June; regular season ends mid-April.
    game_date format: "2026-04-06" (ISO 8601).

    Returns: "preseason", "regular", "playoff", or "unknown".
    """
    # Prefer the explicit ESPN season type id
    if season_type_id == 3:
        return "playoff"
    if season_type_id == 1:
        return "preseason"
    if season_type_id == 4:
        return "offseason"
    # season_type_id == 2 or unknown → fall back to month heuristic
    try:
        dt = datetime.strptime(game_date, "%Y-%m-%d")
        month = dt.month
        if month == 9:
            return "preseason"
        if month in (5, 6):
            return "playoff"
        if month in (10, 11, 12, 1, 2, 3, 4):
            return "regular"
        return "unknown"
    except ValueError:
        return "unknown"


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
    Fetch the top Celtics news headlines from ESPN and write to
    data/celtics_news.json.
    """
    now_utc = datetime.now(timezone.utc)
    try:
        print("  Fetching Celtics news from ESPN...")
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


def fetch_boxscore() -> None:
    """
    Fetch yesterday's Celtics game from the ESPN scoreboard and write a
    full boxscore JSON to data/celtics_boxscore.json.

    Uses UTC for "yesterday" because ESPN's dates= parameter is UTC-anchored.
    """
    # Define game_date_iso before the try block so the error handler can use it.
    yesterday_utc  = datetime.now(timezone.utc) - timedelta(days=1)
    date_param     = yesterday_utc.strftime("%Y%m%d")   # "20250406"
    game_date_iso  = yesterday_utc.strftime("%Y-%m-%d") # "2025-04-06"

    try:
        # --- 1. Fetch scoreboard for yesterday ---
        scoreboard_url = f"{ESPN_SCOREBOARD}?dates={date_param}"
        print(f"  Fetching scoreboard for {game_date_iso}...")
        scoreboard = fetch_json(scoreboard_url)

        # --- 2. Find the Celtics game ---
        events = scoreboard.get("events", [])
        celtics_event = None
        for event in events:
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            for competitor in competitions[0].get("competitors", []):
                team = competitor.get("team", {})
                if (
                    team.get("id") == CELTICS_TEAM_ID
                    or team.get("abbreviation") == CELTICS_ABBREV
                ):
                    celtics_event = event
                    break
            if celtics_event:
                break

        if celtics_event is None:
            print(f"  No Celtics game found for {game_date_iso}.")
            result = {
                "game_date":   game_date_iso,
                "played":      False,
                "season_type": classify_nba_game(game_date_iso),
            }
            BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
            print(f"  Saved to {BOXSCORE_PATH}")
            return

        # --- 3. Fetch game summary ---
        game_id = celtics_event["id"]
        print(f"  Found game ID {game_id}. Fetching summary...")
        summary_url = f"{ESPN_SUMMARY}?event={game_id}"
        summary = fetch_json(summary_url)

        # --- 4. Parse header: status, scores, home/away ---
        header_comp     = summary["header"]["competitions"][0]
        status          = header_comp["status"]["type"]["description"]
        celtics_score   = None
        opponent_name   = None
        opponent_score  = None
        home            = None

        for comp in header_comp.get("competitors", []):
            team_id   = comp.get("team", {}).get("id")
            score     = comp.get("score", "0")
            home_away = comp.get("homeAway", "home")
            team_name = comp.get("team", {}).get("displayName", "Unknown")

            if team_id == CELTICS_TEAM_ID:
                celtics_score = score
                home = (home_away == "home")
            else:
                opponent_score = score
                opponent_name  = team_name

        # --- 5. Parse player boxscore ---
        players = []
        boxscore_player_blocks = summary.get("boxscore", {}).get("players", [])

        celtics_block = None
        for block in boxscore_player_blocks:
            if block.get("team", {}).get("id") == CELTICS_TEAM_ID:
                celtics_block = block
                break

        if celtics_block:
            statistics = celtics_block.get("statistics", [])
            if statistics:
                stat_block = statistics[0]
                keys       = stat_block.get("keys", [])
                key_index  = {key: i for i, key in enumerate(keys)}
                athletes   = stat_block.get("athletes", [])

                for athlete_entry in athletes:
                    # Skip players who didn't play
                    if athlete_entry.get("didNotPlay", False):
                        continue

                    athlete = athlete_entry.get("athlete", {})
                    stats   = athlete_entry.get("stats", [])

                    def get_stat(key: str) -> str:
                        """Safely retrieve a stat value by key name."""
                        idx = key_index.get(key)
                        if idx is None or idx >= len(stats):
                            return ""
                        return stats[idx]

                    fg_str    = get_stat("fieldGoalsMade-fieldGoalsAttempted")
                    three_str = get_stat(
                        "threePointFieldGoalsMade-threePointFieldGoalsAttempted"
                    )

                    # Position lives on athlete_entry, not the nested athlete dict
                    position = (
                        athlete_entry.get("position", {}).get("abbreviation", "")
                    )

                    players.append({
                        "name":         athlete.get("displayName", ""),
                        "jersey":       athlete.get("jersey", ""),
                        "position":     position,
                        "minutes":      get_stat("minutes"),
                        "points":       get_stat("points"),
                        "fg":           fg_str,
                        "fg_pct":       parse_fg_pct(fg_str),
                        "three_pt":     three_str,
                        "three_pt_pct": parse_fg_pct(three_str),
                        "rebounds":     get_stat("rebounds"),
                        "assists":      get_stat("assists"),
                        "steals":       get_stat("steals"),
                        "blocks":       get_stat("blocks"),
                        "turnovers":    get_stat("turnovers"),
                        "plus_minus":   get_stat("plusMinus"),
                    })

        # --- 6. Write output ---
        home_away_label = "home" if home else "away"
        print(
            f"  Game: Celtics ({celtics_score}) vs {opponent_name} ({opponent_score}) "
            f"[{home_away_label}] — {status}"
        )
        print(f"  Parsed {len(players)} player stat lines.")

        result = {
            "game_date":      game_date_iso,
            "played":         True,
            "season_type":    classify_nba_game(game_date_iso),
            "status":         status,
            "home":           home,
            "celtics_score":  celtics_score,
            "opponent":       opponent_name,
            "opponent_score": opponent_score,
            "players":        players,
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
    Fetch the full Celtics schedule from ESPN and filter to games in the
    next 7 days (inclusive of today through today+7), writing to
    data/celtics_schedule.json.

    The ESPN schedule endpoint returns all season games — filtering is done
    in Python since the endpoint offers no date-range parameter.
    """
    now_utc      = datetime.now(timezone.utc)
    from_dt      = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    to_dt        = from_dt + timedelta(days=7)
    from_date_str = from_dt.strftime("%Y-%m-%d")
    to_date_str   = to_dt.strftime("%Y-%m-%d")

    try:
        print(f"  Fetching Celtics schedule ({from_date_str} → {to_date_str})...")

        # Fetch both regular season (seasontype=2) and playoffs (seasontype=3).
        # The team schedule endpoint only returns one season type at a time, so
        # we query both and merge. Dedup by game_id in case of overlap.
        seen_ids = set()
        games = []

        for seasontype in (2, 3):
            url = f"{ESPN_SCHEDULE}?seasontype={seasontype}"
            try:
                data   = fetch_json(url)
            except Exception as e:
                print(f"  Warning: could not fetch seasontype={seasontype}: {e}")
                continue
            events = data.get("events", [])

            for event in events:
                raw_date = event.get("date", "")
                if not raw_date:
                    continue

                # Python ≤ 3.10 fromisoformat() doesn't accept bare "Z" — replace it.
                try:
                    game_dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                except ValueError:
                    print(f"  Warning: could not parse date '{raw_date}', skipping.")
                    continue

                # Inclusive window: from today 00:00 UTC through end of day today+7
                if not (from_dt <= game_dt < to_dt + timedelta(days=1)):
                    continue

                game_id = str(event.get("id", ""))
                if game_id in seen_ids:
                    continue
                seen_ids.add(game_id)

                competitions = event.get("competitions", [])
                comp         = competitions[0] if competitions else {}
                competitors  = comp.get("competitors", [])

                opponent_name = "Unknown"
                home          = True
                for competitor in competitors:
                    team = competitor.get("team", {})
                    if team.get("id") == CELTICS_TEAM_ID:
                        home = (competitor.get("homeAway", "home") == "home")
                    else:
                        opponent_name = team.get("displayName", "Unknown")

                status_desc = (
                    comp.get("status", {})
                        .get("type", {})
                        .get("description", "Scheduled")
                )
                venue = comp.get("venue", {}).get("fullName", "")

                game_date_iso = game_dt.strftime("%Y-%m-%d")
                games.append({
                    "game_id":  game_id,
                    "date":     raw_date,
                    "opponent": opponent_name,
                    "home":     home,
                    "status":   status_desc,
                    "venue":    venue,
                    "season_type": classify_nba_game(game_date_iso, seasontype),
                })

        games.sort(key=lambda g: g["date"])
        print(f"  Found {len(games)} game(s) in the next 7 days.")

        result = {
            "generated_at": now_utc.isoformat(),
            "from_date":    from_date_str,
            "to_date":      to_date_str,
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
    print("  Boston Dan's Hub — ESPN NBA Data Fetcher")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)

    print("\n[1/3] Yesterday's boxscore")
    fetch_boxscore()

    print("\n[2/3] Next 7-day schedule")
    fetch_schedule()

    print("\n[3/3] Latest Celtics news")
    fetch_news()

    print("\nDone. Files written:")
    print(f"  {BOXSCORE_PATH}")
    print(f"  {SCHEDULE_PATH}")
    print(f"  {NEWS_PATH}")


if __name__ == "__main__":
    main()
