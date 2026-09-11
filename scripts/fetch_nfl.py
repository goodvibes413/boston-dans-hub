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
    Box scores only exist from Week 1 through the Super Bowl. Outside that,
    fetch_boxscore() writes {"played": false, "season_type": "offseason"|"preseason"}
    and returns cleanly without hitting the scoreboard API. Preseason games are
    deliberately inside that short-circuit — Dan does not cover them.

    TODO (still open, 2026-09-07): Verify LEADER_NAMES against a live
    regular-season summary payload once the Patriots have actually played, and
    adjust the key strings if ESPN has changed them. This has never run against
    a real NFL game — the code path was written in the offseason and the
    offseason short-circuit below means it has not executed since. A wrong key
    here degrades quietly: parse_leaders() returns all-None rather than raising,
    so the box score publishes with no passing/rushing/receiving leaders and
    nothing goes red. Check the first Monday after Week 1.
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pipeline_dates import as_of_date, target_game_date

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

# A game belongs to the day a Boston reader watched it, which is its ET date.
ET = ZoneInfo("America/New_York")

# ESPN's season-type codes, shared across its sports endpoints. These beat any
# calendar guess about which phase a given game belongs to.
ESPN_SEASON_TYPES = {1: "preseason", 2: "regular", 3: "playoff", 4: "offseason"}

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


def espn_season_type(event: dict) -> str | None:
    """
    Read ESPN's OWN season type off a scoreboard or schedule event.

    ESPN knows exactly which phase a game belongs to and says so; a calendar
    guess is only ever an approximation of that. Prefer this whenever the
    payload carries it.

    The value turns up as `event["season"]["type"]` on the scoreboard and as
    `event["seasonType"]` on the team schedule — sometimes a dict with a
    "type" key, sometimes the bare code — and has shipped as both an int and
    a numeric string. Returns None for anything unrecognised so the caller
    falls back to the calendar (AGENTS.md Rule #5: ESPN shapes change without
    warning).
    """
    if not isinstance(event, dict):
        return None

    candidates = []
    for holder in (event.get("season"), event.get("seasonType")):
        if isinstance(holder, dict):
            candidates.append(holder.get("type"))
        elif holder is not None:
            candidates.append(holder)

    for raw in candidates:
        if isinstance(raw, bool):  # bool subclasses int — not a season code
            continue
        try:
            return ESPN_SEASON_TYPES[int(raw)]
        except (TypeError, ValueError, KeyError):
            continue
    return None


def classify_nfl_season(today: date | None = None) -> str:
    """
    Calendar fallback for the current NFL season phase, used whenever ESPN
    does not hand us a season type (no game found, or a shape we don't know).

    Month alone is too coarse at both ends of the season and had two windows
    wrong:
      - January returned "regular" for the whole month, so every Wild Card and
        Divisional game would have been written with season_type="regular".
        fetch_season_memory.classify_status() already called January
        "in_playoffs", so the two classifiers actively disagreed.
      - September returned "regular" from the 1st, though Week 1 does not kick
        off until after Labor Day.

    Boundaries are deliberately approximate: ESPN is the authority whenever it
    answers, so this only has to be right on the days it stands alone.
    """
    # Defaults to the run's as-of day, NOT the wall clock. The wall clock is
    # exactly what pipeline_dates exists to stop each stage re-deriving on its
    # own: with AS_OF_DATE pinned to a January playoff Saturday, a wall-clock
    # default classified the run by TODAY's real date instead, so a replay
    # stamped the wrong phase and (when today happened to be the offseason)
    # tripped the short-circuit below and recorded played:false for a game that
    # was played.
    d = today or as_of_date()
    month, day = d.month, d.day

    if 3 <= month <= 7:
        return "offseason"
    if month == 8:
        return "preseason"
    if month == 9:
        # Week 1 kicks off the Thursday after Labor Day — never before the 4th.
        return "preseason" if day < 8 else "regular"
    if month in (10, 11, 12):
        return "regular"
    if month == 1:
        # Week 18 closes out the first weekend; the playoffs run from there.
        return "regular" if day <= 7 else "playoff"
    if month == 2:
        # Super Bowl is the second Sunday at the latest; then it's over.
        return "playoff" if day <= 15 else "offseason"
    return "unknown"


def is_nfl_offseason() -> bool:
    """Backward compatibility: True if in offseason or preseason."""
    return classify_nfl_season() in ("offseason", "preseason")


def is_patriots_event(event: dict) -> bool:
    """True if the Patriots are one of the competitors in this event."""
    if not isinstance(event, dict):
        return False
    try:
        competitors = event.get("competitions", [{}])[0].get("competitors", [])
    except (AttributeError, IndexError, TypeError):
        return False
    for comp in competitors or []:
        if not isinstance(comp, dict):
            continue
        team = comp.get("team", {}) or {}
        if (
            team.get("abbreviation") == PATRIOTS_ABBREV
            or team.get("id") == PATRIOTS_TEAM_ID
        ):
            return True
    return False


def find_patriots_event(events: list):
    """Return the first event in which the Patriots appear, or None."""
    for event in events or []:
        if is_patriots_event(event):
            return event
    return None


def event_et_date(event: dict) -> date | None:
    """
    The calendar day this game belongs to, in Eastern time.

    ESPN stamps every event with a full UTC timestamp in "date". Converting
    that to ET is what makes the day unambiguous: a Sunday night kickoff at
    8:20 PM ET is 00:20 UTC on Monday, and it is emphatically a Sunday game.

    Returns None for a missing or unparseable value so callers can decide,
    rather than guessing a day (AGENTS.md Rule #5).
    """
    raw = (event or {}).get("date") if isinstance(event, dict) else None
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date()


def select_patriots_event(events: list, target: date,
                          primary_ids: set | None = None):
    """
    Pick the Patriots game actually PLAYED on `target`, judged by each event's
    own kickoff time rather than by which query returned it.

    ESPN's `dates=` bucketing is not documented, and the two plausible
    conventions disagree exactly where the NFL lives. Under UTC bucketing a
    Sunday Night Football kickoff (00:20 UTC Monday) lands in MONDAY's bucket,
    so querying Sunday alone returns nothing and the fetcher records
    played:false. Every other Boston team plays near-daily, so a misfiled game
    is a one-day blip the 7-day window absorbs; the NFL plays once a week, so
    it would silently erase the only Patriots game of that week — and
    check_coverage_window skips played:false, so nothing would flag it.

    Caller therefore hands us both days' events and this filters by ET date,
    which is correct under EITHER convention and needs no assumption about
    which one ESPN uses.

    `primary_ids` are the ids that came from the target day's own query. An
    event whose timestamp will not parse is only trusted if it came from
    there, so a malformed record from the following day can never be promoted
    into the slot of a game that was not played.
    """
    candidates = [e for e in (events or []) if is_patriots_event(e)]
    if not candidates:
        return None

    for event in candidates:
        if event_et_date(event) == target:
            if primary_ids is not None and str(event.get("id")) not in primary_ids:
                # Only reachable under UTC bucketing. Worth saying loudly: it
                # means fetch_nba.py's "ESPN's dates= parameter is UTC-anchored"
                # comment is right and the NBA/NHL fetchers need this same fix.
                print(f"  NOTE: the {target.isoformat()} game was filed under the "
                      f"following day's scoreboard — ESPN buckets by UTC, so "
                      f"fetch_nba.py and fetch_nhl.py need this same day-pair fix.")
            return event

    for event in candidates:
        if event_et_date(event) is None and (
            primary_ids is None or str(event.get("id")) in primary_ids
        ):
            print(f"  warn: Patriots event {event.get('id')} has no parseable "
                  f"date; accepting it because the {target.isoformat()} query "
                  f"returned it.")
            return event

    return None


def safe_int(val, default=0) -> int:
    """
    Convert val to int, returning default on failure.

    ESPN reports competitor scores as strings ("20"), while the NHL API and
    fetch_mlb.py store ints. Downstream readers do arithmetic and comparisons
    on these fields, so the four boxscore files must agree on the type: a
    string score crashed generate_rant.compute_emotional_context on the
    2026-09-10 run (Patriots opener). Coerce here, at the source.
    """
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


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
        return  # graceful degradation — error sentinel already written; don't crash the pipeline


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
    target        = target_game_date()
    game_date_iso = target.isoformat()

    try:
        # ── Offseason short-circuit ───────────────────────────────────────
        # Classified by the day being RECAPPED, not the day the run happens.
        # A game played Jan 7 is a Week 18 regular-season game even when the
        # pipeline fires on Jan 8, by which date the calendar says "playoff".
        season = classify_nfl_season(target)
        if season in ("offseason", "preseason"):
            print(f"  NFL {season} — no game data available for {game_date_iso}.")
            result = {
                "game_date":   game_date_iso,
                "played":      False,
                "season_type": season,
            }
            BOXSCORE_PATH.write_text(json.dumps(result, indent=2))
            print(f"  Saved to {BOXSCORE_PATH}")
            return

        # ── Regular season: fetch scoreboard ─────────────────────────────
        # The target day, plus the day after it. See select_patriots_event():
        # a Sunday or Monday night kickoff is already past midnight UTC, so
        # under UTC bucketing it sits in the FOLLOWING day's scoreboard. The
        # ET-date filter then keeps whichever events actually belong to the
        # target day, so this is correct under either bucketing convention.
        date_param = target.strftime("%Y%m%d")
        print(f"  Fetching NFL scoreboard for {game_date_iso}...")
        scoreboard = fetch_json(f"{ESPN_SCOREBOARD}?dates={date_param}")

        events      = scoreboard.get("events", []) or []
        primary_ids = {str(e.get("id")) for e in events if isinstance(e, dict)}

        # Supplementary and best-effort: a failure here must never cost us the
        # target day's game, which the primary call above already has.
        next_day = target + timedelta(days=1)
        try:
            spill = fetch_json(
                f"{ESPN_SCOREBOARD}?dates={next_day.strftime('%Y%m%d')}"
            ).get("events", []) or []
            events = events + [
                e for e in spill
                if isinstance(e, dict) and str(e.get("id")) not in primary_ids
            ]
        except RuntimeError as e:
            print(f"  warn: follow-up scoreboard for {next_day.isoformat()} "
                  f"failed ({e}); a late-night kickoff could be missed.")

        pats_event = select_patriots_event(events, target, primary_ids)

        if pats_event is None:
            print(f"  No Patriots game found for {game_date_iso}.")
            result = {
                "game_date":   game_date_iso,
                "played":      False,
                "season_type": classify_nfl_season(target),
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
                pats_score = safe_int(score)
                pats_home  = (home_away == "home")
            else:
                opp_score = safe_int(score)
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
            # ESPN tags the event itself — a January playoff game says so
            # rather than inheriting whatever the calendar guessed.
            "season_type":    espn_season_type(pats_event) or classify_nfl_season(target),
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
        return  # graceful degradation — error sentinel already written; don't crash the pipeline


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
    # Window anchor is the RUN's day, not the wall clock. These four fetchers
    # already take their boxscore date from pipeline_dates; fetch_schedule was
    # the copy that kept deriving its own, so an AS_OF_DATE replay produced a
    # schedule window starting *after* the day being replayed. Dan then read a
    # tomorrow-anchored schedule while being told today was yesterday, and
    # called the next day's opener "tonight" — the 2026-09-10 phantom game,
    # reproduced by the very re-run meant to verify its fix. now_utc stays for
    # generated_at, which is a real timestamp and should track the clock.
    _as_of   = as_of_date()
    from_dt   = datetime(_as_of.year, _as_of.month, _as_of.day, tzinfo=timezone.utc)
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
                "game_id":    event.get("id", ""),
                "date":       raw_date,
                "opponent":   opp_name,
                "home":       pats_home,
                "status":     status,
                "venue":      venue,
                "season_type": espn_season_type(event) or classify_nfl_season(),
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
        return  # graceful degradation — error sentinel already written; don't crash the pipeline


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
