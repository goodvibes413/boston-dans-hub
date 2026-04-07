#!/usr/bin/env python3
"""
fetch_nfl.py — Fetches New England Patriots news, box score, and schedule
               from the ESPN API.

Endpoints:
    News       : https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?team=ne
    Scoreboard : https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=YYYYMMDD
    Summary    : https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={id}
    Schedule   : https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/17/schedule

Outputs:
    data/patriots_news.json      — Top 3 headlines with descriptions + dates
    data/patriots_boxscore.json  — Yesterday's game (or offseason/no-game sentinel)
    data/patriots_schedule.json  — Patriots games in the next 7 days

Offseason note:
    Box scores only exist September–February (regular season + playoffs).
    During March–August the boxscore fetcher writes {"played": false, "offseason": true}
    and returns cleanly without hitting the scoreboard API.

    TODO: Verify leader stat field names against a live regular-season game once
    the 2026 season kicks off in September, and adjust LEADER_NAMES if ESPN has
    changed the key strings.
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

NEWS_PATH     = DATA_DIR / "patriots_news.json"
BOXSCORE_PATH = DATA_DIR / "patriots_boxscore.json"
SCHEDULE_PATH = DATA_DIR / "patriots_schedule.json"

PATRIOTS_ABBREV  = "NE"
PATRIOTS_TEAM_ID = "17"

ESPN_NEWS_URL    = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?team=ne"
ESPN_SCOREBOARD  = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SUMMARY     = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
ESPN_SCHEDULE    = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/17/schedule"

NEWS_TOP_N = 3

# ESPN leader stat name strings for NFL summary response
LEADER_NAMES = {
    "passing":   "passingYards",
    "rushing":   "rushingYards",
    "receiving": "receivingYards",
}

# Quarter labels (linescores array is 0-indexed: Q1, Q2, Q3, Q4, [OT...])
QUARTER_LABELS = {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_json(url: str) -> dict:
    """
    Fetch a URL and return the parsed JSON body.

    Raises:
        RuntimeError: On HTTP error, network failure, timeout, or bad JSON.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "patriots-fanbot/1.0 "
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
    """
    Normalise an ESPN publication date string to ISO 8601 UTC.

    ESPN returns dates as "2026-04-06T18:32:00Z". If parsing fails, the
    raw string is returned as-is so downstream code always has a value.
    """
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return raw


def is_nfl_offseason() -> bool:
    """
    Return True when today falls outside the NFL playing season.

    NFL calendar:
        Aug        — Preseason (no box scores tracked here)
        Sep–Jan    — Regular season
        Jan–Feb    — Playoffs + Super Bowl
        Mar–Aug    — Offseason

    We treat March through August as offseason (months 3–8).
    """
    return 3 <= date.today().month <= 8


def find_patriots_event(events: list):
    """Return the first event in which the Patriots appear, or None."""
    for event in events:
        competitors = event.get("competitions", [{}])[0].get("competitors", [])
        for comp in competitors:
            team = comp.get("team", {})
            if (
                team.get("abbreviation") == PATRIOTS_ABBREV
                or team.get("id") == PATRIOTS_TEAM_ID
            ):
                return event
    return None


def parse_quarter_scores(competitors: list, pats_home: bool) -> list:
    """
    Build a quarter-by-quarter scoring table from competitors' linescores.

    Each competitor has a `linescores` array: [{value: "7"}, {value: "3"}, ...]
    Index 0=Q1, 1=Q2, 2=Q3, 3=Q4, 4+=OT periods.
    """
    pats_comp = None
    opp_comp  = None
    for comp in competitors:
        team = comp.get("team", {})
        if team.get("abbreviation") == PATRIOTS_ABBREV:
            pats_comp = comp
        else:
            opp_comp = comp

    if not pats_comp or not opp_comp:
        return []

    pats_ls = pats_comp.get("linescores", [])
    opp_ls  = opp_comp.get("linescores",  [])
    length  = max(len(pats_ls), len(opp_ls))

    rows = []
    for i in range(length):
        q_num   = i + 1
        label   = QUARTER_LABELS.get(q_num, f"OT{q_num - 4}" if q_num > 4 else f"Q{q_num}")
        pats_pts = int(pats_ls[i].get("value", 0)) if i < len(pats_ls) else 0
        opp_pts  = int(opp_ls[i].get("value",  0)) if i < len(opp_ls)  else 0
        rows.append({
            "quarter":   q_num,
            "label":     label,
            "patriots":  pats_pts,
            "opponent":  opp_pts,
        })
    return rows


def parse_leaders(summary: dict, pats_abbrev: str = PATRIOTS_ABBREV) -> dict:
    """
    Extract passing, rushing, and receiving leaders from the ESPN NFL summary.

    The NFL summary["leaders"] is a list of per-TEAM blocks, unlike NBA which
    is per-category. Structure:
        leaders[i] = {
            "team": {"abbreviation": "NE", ...},
            "leaders": [
                {"name": "passingYards", "leaders": [{"athlete": {...}, "displayValue": "..."}]},
                ...
            ]
        }

    We find the Patriots' team block, then extract their category leaders.

    Returns: {"passing": {...}, "rushing": {...}, "receiving": {...}}
    All keys always present; value is None if the data isn't found.
    """
    result = {k: None for k in LEADER_NAMES}
    raw_blocks = summary.get("leaders", [])

    # Find the Patriots' team block
    pats_block = None
    for block in raw_blocks:
        if block.get("team", {}).get("abbreviation") == pats_abbrev:
            pats_block = block
            break

    if pats_block is None:
        return result

    for category in pats_block.get("leaders", []):
        cat_name = category.get("name", "")
        for key, espn_name in LEADER_NAMES.items():
            if cat_name == espn_name:
                entries = category.get("leaders", [])
                if entries:
                    top = entries[0]
                    result[key] = {
                        "name":    top.get("athlete", {}).get("displayName", "Unknown"),
                        "display": top.get("displayValue", ""),
                    }
                break
    return result


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

def fetch_news() -> None:
    """
    Fetch the top Patriots news headlines from ESPN and write to
    data/patriots_news.json.

    Each article entry contains:
        headline    — Article headline / title
        description — Short summary / subheadline (may be empty)
        published   — Publication datetime (ISO 8601 UTC)
        url         — Link to full article on ESPN
    """
    now_utc = datetime.now(timezone.utc)

    try:
        print("  Fetching Patriots news from ESPN...")
        data     = fetch_json(ESPN_NEWS_URL)
        articles = data.get("articles", [])

        headlines = []
        for article in articles[:NEWS_TOP_N]:
            links    = article.get("links", {})
            url      = (
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

        result = {
            "generated_at": now_utc.isoformat(),
            "headlines":    headlines,
        }
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


# ---------------------------------------------------------------------------
# Box score
# ---------------------------------------------------------------------------

def fetch_boxscore() -> None:
    """
    Fetch yesterday's Patriots game and write to data/patriots_boxscore.json.

    During the NFL offseason (March–August) writes a clean sentinel and returns
    without hitting the scoreboard API. In-season, follows the same two-step
    ESPN pattern as fetch_nba.py: scoreboard → find game → summary → parse.

    Parses:
        - Final score + status
        - Quarter-by-quarter scoring
        - Passing / rushing / receiving leaders (name + display string)
    """
    yesterday_utc = datetime.now(timezone.utc) - timedelta(days=1)
    game_date_iso = yesterday_utc.strftime("%Y-%m-%d")

    try:
        # ── Offseason short-circuit ───────────────────────────────────────
        if is_nfl_offseason():
            print(f"  NFL offseason — no game data available for {game_date_iso}.")
            result = {
                "game_date": game_date_iso,
                "played":    False,
                "offseason": True,
            }
            BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
            print(f"  Saved to {BOXSCORE_PATH}")
            return

        # ── Regular season: fetch scoreboard ─────────────────────────────
        date_param = yesterday_utc.strftime("%Y%m%d")
        print(f"  Fetching NFL scoreboard for {game_date_iso}...")
        scoreboard = fetch_json(f"{ESPN_SCOREBOARD}?dates={date_param}")

        events     = scoreboard.get("events", [])
        pats_event = find_patriots_event(events)

        if pats_event is None:
            print(f"  No Patriots game found for {game_date_iso}.")
            result = {
                "game_date": game_date_iso,
                "played":    False,
                "offseason": False,
            }
            BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
            print(f"  Saved to {BOXSCORE_PATH}")
            return

        # ── Parse game identity from scoreboard ───────────────────────────
        game_id  = pats_event["id"]
        comp     = pats_event.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])

        status = (
            comp.get("status", {})
                .get("type", {})
                .get("description", "Unknown")
        )

        pats_score   = None
        opp_score    = None
        opp_name     = None
        pats_home    = False

        for c in competitors:
            team     = c.get("team", {})
            score    = c.get("score", "0")
            home_away = c.get("homeAway", "away")
            if (
                team.get("abbreviation") == PATRIOTS_ABBREV
                or team.get("id") == PATRIOTS_TEAM_ID
            ):
                pats_score = score
                pats_home  = (home_away == "home")
            else:
                opp_score = score
                opp_name  = team.get("displayName", "Unknown")

        # ── Fetch full summary for leaders ────────────────────────────────
        print(f"  Found game ID {game_id}. Fetching summary...")
        summary = fetch_json(f"{ESPN_SUMMARY}?event={game_id}")

        # Quarter scores come from the scoreboard competitors' linescores
        quarter_scores = parse_quarter_scores(competitors, pats_home)
        leaders        = parse_leaders(summary)

        loc = "home" if pats_home else "away"
        print(
            f"  Patriots {pats_score} — {opp_name} {opp_score} "
            f"[{loc}] — {status}"
        )

        result = {
            "game_date":      game_date_iso,
            "played":         True,
            "offseason":      False,
            "status":         status,
            "home":           pats_home,
            "patriots_score": pats_score,
            "opponent":       opp_name,
            "opponent_score": opp_score,
            "quarter_scores": quarter_scores,
            "leaders":        leaders,
        }
        BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
        print(f"  Saved boxscore to {BOXSCORE_PATH}")

    except Exception as e:
        print(f"[ERROR] fetch_boxscore failed: {e}")
        err = {"game_date": game_date_iso, "error": str(e)}
        try:
            BOXSCORE_PATH.write_text(json.dumps(err, indent=2))
        except Exception:
            pass
        sys.exit(1)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def fetch_schedule() -> None:
    """
    Fetch the Patriots schedule and filter to games in the next 7 days,
    writing to data/patriots_schedule.json.

    The ESPN team schedule endpoint returns the full season. We filter
    client-side by date, same pattern as fetch_nba.py's fetch_schedule().
    """
    now_utc   = datetime.now(timezone.utc)
    from_dt   = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    to_dt     = from_dt + timedelta(days=7)

    try:
        print(f"  Fetching Patriots schedule...")
        data   = fetch_json(ESPN_SCHEDULE)
        events = data.get("events", [])

        games = []
        for event in events:
            raw_date = event.get("date", "")
            if not raw_date:
                continue
            try:
                game_dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            except ValueError:
                continue

            if not (from_dt <= game_dt < to_dt + timedelta(days=1)):
                continue

            comp        = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])

            opp_name  = "Unknown"
            pats_home = True
            for c in competitors:
                team = c.get("team", {})
                if (
                    team.get("abbreviation") == PATRIOTS_ABBREV
                    or team.get("id") == PATRIOTS_TEAM_ID
                ):
                    pats_home = (c.get("homeAway", "home") == "home")
                else:
                    opp_name = team.get("displayName", "Unknown")

            status = (
                comp.get("status", {})
                    .get("type", {})
                    .get("description", "Scheduled")
            )
            venue = (
                comp.get("venue", {}).get("fullName", "")
            )

            games.append({
                "game_id":  event.get("id", ""),
                "date":     raw_date,
                "opponent": opp_name,
                "home":     pats_home,
                "status":   status,
                "venue":    venue,
            })

        print(f"  Found {len(games)} game(s) in the next 7 days.")

        result = {
            "generated_at": now_utc.isoformat(),
            "from_date":    from_dt.strftime("%Y-%m-%d"),
            "to_date":      to_dt.strftime("%Y-%m-%d"),
            "games":        games,
        }
        SCHEDULE_PATH.write_text(json.dumps(result, indent=2))
        print(f"  Saved schedule to {SCHEDULE_PATH}")

    except Exception as e:
        print(f"[ERROR] fetch_schedule failed: {e}")
        err = {"error": str(e), "games": []}
        try:
            SCHEDULE_PATH.write_text(json.dumps(err, indent=2))
        except Exception:
            pass
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run all three fetchers in sequence."""
    print("=" * 52)
    print("  Boston Dan's Hub — NFL Patriots Data Fetcher")
    print("=" * 52)

    DATA_DIR.mkdir(exist_ok=True)

    print("\n[1/3] Latest Patriots headlines")
    fetch_news()

    print("\n[2/3] Yesterday's boxscore")
    fetch_boxscore()

    print("\n[3/3] Next 7-day schedule")
    fetch_schedule()

    print("\nDone. Files written:")
    print(f"  {NEWS_PATH}")
    print(f"  {BOXSCORE_PATH}")
    print(f"  {SCHEDULE_PATH}")


if __name__ == "__main__":
    main()
