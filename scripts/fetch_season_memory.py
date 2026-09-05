#!/usr/bin/env python3
"""
fetch_season_memory.py — Fetches current-season records and playoff status
for all four Boston teams from the ESPN unofficial API.

Writes a lean per-team snapshot of the current season (wins, losses,
playoff seed, series status) to data/season_current.json. Past seasons
are kept separately in data/season_static.json (hand-curated, versioned).

During a team's stretch run the entry also carries a "playoff_race" block
(magic number, wild-card position, games remaining) sourced from MLB
StatsAPI. That block is the ONLY thing that unlocks playoff talk in the
persona, so it is written only inside STRETCH_RUN_WINDOW and only while the
team is still alive — see build_playoff_race().

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

SCRIPT_DIR        = Path(__file__).resolve().parent
PROJECT_ROOT      = SCRIPT_DIR.parent
DATA_DIR          = PROJECT_ROOT / "data"
OUTPUT_PATH       = DATA_DIR / "season_current.json"
SCHEDULE_PATH     = DATA_DIR / "upcoming_schedule.json"

# ESPN team endpoints return current-season record and standing summary.
TEAM_ENDPOINTS = {
    "celtics":  ("basketball", "nba", "2"),
    "bruins":   ("hockey",     "nhl", "1"),
    "redsox":   ("baseball",   "mlb", "2"),
    "patriots": ("football",   "nfl", "17"),
}

# MLB StatsAPI standings — the same host fetch_mlb.py already uses. ESPN's team
# endpoint carries DIVISION games-behind only, which is actively misleading for
# a wild-card team (3rd in the AL East reads as "buried" while they hold a
# wild-card spot). StatsAPI returns magic number, wild-card rank and elimination
# number directly, so Dan cites real figures instead of guessing.
MLB_STANDINGS_BASE = "https://statsapi.mlb.com/api/v1/standings"
MLB_AL_LEAGUE_ID   = 103   # American League
MLB_REDSOX_ID      = 111   # Boston, in StatsAPI ids (not the ESPN id above)

# Total games in a regular season, per sport. Used to derive games remaining.
SEASON_LENGTH = {"baseball": 162, "basketball": 82, "hockey": 82, "football": 17}

# The stretch run: how many games left before the playoff race becomes a topic
# Dan is allowed to raise. Defined in games remaining rather than calendar dates
# so it self-adjusts to a shortened or shifted season. For MLB, 40 games lands
# around mid-August.
#
# This is the "not all year" guarantee, and it is structural: outside the
# window no playoff_race block is written, so there is no data for Dan to
# reason from and no instruction for him to disobey.
STRETCH_RUN_WINDOW = {"baseball": 40, "basketball": 20, "hockey": 20, "football": 6}

# A magic number at or below this puts Dan on clinch watch.
CLINCH_WATCH_MAGIC = 10

# Furthest back a team can be and still count as "chasing" rather than
# playing out the string.
CHASING_MAX_GAMES_BACK = 8.0


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

def team_has_upcoming_games(team_key: str, today_str: str) -> bool | None:
    """
    Check upcoming_schedule.json to see if a team has any games on or after
    today.

    Returns:
        True   — schedule file exists, has games, and this team has at least
                 one game on/after today.
        False  — schedule file exists, has games, and this team has NONE
                 on/after today (i.e. they are done for the season).
        None   — schedule file missing, empty, or unreadable (caller should
                 treat as "unknown" — fail safe to the calendar heuristic).
    """
    try:
        if not SCHEDULE_PATH.exists():
            return None
        schedule = json.loads(SCHEDULE_PATH.read_text())
        games = schedule.get("games", [])
        if not games:
            # Fetch may have failed and written []  — can't conclude anything.
            return None
        for g in games:
            if g.get("team") == team_key and (g.get("date") or "") >= today_str:
                return True
        return False  # Schedule loaded and has entries, but none for this team
    except Exception:
        return None  # Unreadable — fail safe


def classify_status(sport: str, now: datetime, team_key: str = "") -> str:
    """
    Current-season status classifier: regular_season | in_playoffs | offseason.

    Uses typical league-calendar windows as the first pass, then verifies
    playoff status against upcoming_schedule.json so that eliminated teams
    are not mis-labelled as "in_playoffs" for the rest of the postseason.

    The schedule check is a *fail-safe*: if the file is missing or empty we
    fall back to the calendar heuristic rather than incorrectly declaring
    a team eliminated.
    """
    month = now.month
    if sport == "basketball":  # NBA
        if month in (4, 5, 6):
            calendar_status = "in_playoffs"
        elif month in (7, 8, 9):
            return "offseason"
        else:
            return "regular_season"
    elif sport == "hockey":  # NHL
        if month in (4, 5, 6):
            calendar_status = "in_playoffs"
        elif month in (7, 8, 9):
            return "offseason"
        else:
            return "regular_season"
    elif sport == "baseball":  # MLB
        if month in (10, 11):
            calendar_status = "in_playoffs"
        elif month in (12, 1, 2, 3):
            return "offseason"
        else:
            return "regular_season"
    elif sport == "football":  # NFL
        if month in (1, 2):
            calendar_status = "in_playoffs"
        elif month in (3, 4, 5, 6, 7, 8):
            return "offseason"
        else:
            return "regular_season"
    else:
        return "regular_season"

    # calendar_status is "in_playoffs" — verify the team actually has games.
    # Only basketball and hockey have mid-season elimination; MLB/NFL playoffs
    # are short enough that date-based is reliable. For NHL and NBA we do the
    # extra check because teams can be eliminated weeks before the postseason ends.
    if sport in ("hockey", "basketball") and team_key:
        today_str = now.strftime("%Y-%m-%d")
        has_games = team_has_upcoming_games(team_key, today_str)
        if has_games is False:
            # Schedule file is valid, team has no upcoming games → eliminated.
            print(f"  [{team_key}] no upcoming games in {SCHEDULE_PATH.name} "
                  f"→ treating as offseason (eliminated from playoffs)")
            return "offseason"
        if has_games is None:
            print(f"  [{team_key}] schedule check unavailable — "
                  f"falling back to calendar heuristic (in_playoffs)")
        # has_games is True or None → keep calendar status

    return calendar_status


# ---------------------------------------------------------------------------
# Playoff race (stretch run)
# ---------------------------------------------------------------------------

def _parse_gb(value) -> float | None:
    """
    Parse a StatsAPI games-behind style field into a float.

    These arrive as STRINGS with sentinel values, and letting one through as a
    number is how "-" ends up in the prompt as a stat Dan then cites:
        "-"     team leads / not applicable   -> None
        "+2.5"  games AHEAD of the cut line   -> 2.5
        "4.5"   games behind                  -> 4.5
        "E"     eliminated                    -> None
    Returns None for anything that is not a real number, so callers omit the
    field rather than emit a sentinel.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lstrip("+")
    if not text or text in ("-", "E", "--"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_count(value) -> int | None:
    """Parse magicNumber / eliminationNumber into an int. "-" and "E" -> None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or text in ("-", "E", "--"):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def fetch_mlb_standings_race(team_id: int = MLB_REDSOX_ID) -> dict:
    """
    Fetch the AL standings and return the raw race fields for one team, plus
    the closest team on the outside of the wild-card cut.

    One call covers every AL team, so the chaser ("5.5 up on Cleveland") comes
    from the same payload rather than a second request.

    Returns {} on any failure — the caller then omits the playoff_race block
    entirely and Dan degrades to qualitative season talk, which is the
    pre-existing behaviour.
    """
    year = datetime.now(timezone.utc).year
    url = (
        f"{MLB_STANDINGS_BASE}?leagueId={MLB_AL_LEAGUE_ID}"
        f"&season={year}&standingsTypes=regularSeason"
    )
    try:
        data = fetch_json(url)
    except RuntimeError as e:
        print(f"  [warn] MLB standings fetch failed: {e}", file=sys.stderr)
        return {}

    records = []
    for division in data.get("records", []) or []:
        for tr in division.get("teamRecords", []) or []:
            records.append(tr)
    if not records:
        print("  [warn] MLB standings returned no team records", file=sys.stderr)
        return {}

    ours = None
    for tr in records:
        if (tr.get("team") or {}).get("id") == team_id:
            ours = tr
            break
    if ours is None:
        print(f"  [warn] team id {team_id} absent from MLB standings", file=sys.stderr)
        return {}

    raw = {
        "wins":                       ours.get("wins"),
        "losses":                     ours.get("losses"),
        "games_played":               ours.get("gamesPlayed"),
        "division_rank":              _parse_count(ours.get("divisionRank")),
        "division_games_back":        _parse_gb(ours.get("divisionGamesBack")
                                                or ours.get("gamesBack")),
        "wild_card_rank":             _parse_count(ours.get("wildCardRank")),
        "wild_card_games_back":       _parse_gb(ours.get("wildCardGamesBack")),
        "magic_number":               _parse_count(ours.get("magicNumber")),
        "elimination_number":         ours.get("eliminationNumber"),
        "wild_card_elimination_number": ours.get("wildCardEliminationNumber"),
        "clinched":                   bool(ours.get("clinched")),
    }

    # Closest chaser: the best team currently OUTSIDE the three wild-card spots.
    # Only meaningful when we are inside one; omitted otherwise.
    chaser = None
    outside = [
        tr for tr in records
        if (_parse_count(tr.get("wildCardRank")) or 99) > 3
        and (tr.get("team") or {}).get("id") != team_id
    ]
    if outside:
        outside.sort(key=lambda tr: _parse_count(tr.get("wildCardRank")) or 99)
        chaser = (outside[0].get("team") or {}).get("name")
    if chaser:
        raw["closest_chaser"] = chaser

    return raw


def build_playoff_race(sport: str, status: str, raw: dict,
                       wins: int | None, losses: int | None) -> dict | None:
    """
    Gate and shape the playoff_race block. Pure — no network, no clock.

    Returns None (meaning: Dan gets no playoff race topic at all) unless the
    team is in its sport's stretch run AND still mathematically alive.

    Two gates:
      1. WINDOW — regular season, with games_remaining inside
         STRETCH_RUN_WINDOW for the sport. Outside it there is no block, which
         is what stops Dan doing playoff math in April.
      2. ELIMINATION — a team eliminated from both the division and the wild
         card gets no block. Actual elimination is owned by
         data/season_overrides.json, whose rules are absolute; two authorities
         on the same fact is how contradictions ship.

    race_status tiers, checked in order:
      clinched               already in
      clinch_watch           magic number at or below CLINCH_WATCH_MAGIC
      in_position            holding a wild-card spot or leading the division
      chasing                within reach of a spot
      playing_out_the_string alive on paper, ugly in practice
    """
    if status != "regular_season":
        return None
    if not raw:
        return None

    total = SEASON_LENGTH.get(sport)
    window = STRETCH_RUN_WINDOW.get(sport)
    if total is None or window is None:
        return None

    played = _parse_count(raw.get("games_played"))
    if played is None:
        w, l = _parse_count(wins), _parse_count(losses)
        if w is None or l is None:
            return None
        played = w + l
    games_remaining = total - played
    if games_remaining < 0 or games_remaining > window:
        return None

    # Mathematically out of both routes — SEASON_OVERRIDES territory.
    div_out = str(raw.get("elimination_number") or "").strip().upper() == "E"
    wc_out = str(raw.get("wild_card_elimination_number") or "").strip().upper() == "E"
    if div_out and wc_out:
        return None

    # Normalize here rather than trusting the caller. fetch_mlb_standings_race
    # already parses, but this function is the shared entry point for the other
    # three sports' fetchers, and a raw "-" reaching a comparison below is a
    # TypeError at 6am in CI.
    div_rank = _parse_count(raw.get("division_rank"))
    wc_rank = _parse_count(raw.get("wild_card_rank"))
    wc_gb = _parse_gb(raw.get("wild_card_games_back"))
    div_gb = _parse_gb(raw.get("division_games_back"))
    magic = _parse_count(raw.get("magic_number"))
    in_spot = (wc_rank is not None and wc_rank <= 3) or div_rank == 1

    if raw.get("clinched"):
        race_status = "clinched"
    elif magic is not None and magic <= CLINCH_WATCH_MAGIC:
        race_status = "clinch_watch"
    elif in_spot:
        race_status = "in_position"
    elif (wc_gb is not None
          and wc_gb <= CHASING_MAX_GAMES_BACK
          and wc_gb <= games_remaining):
        race_status = "chasing"
    else:
        race_status = "playing_out_the_string"

    block = {
        "phase": "stretch_run",
        "games_remaining": games_remaining,
        "race_status": race_status,
    }
    if div_rank is not None:
        block["division_rank"] = div_rank
    if div_gb is not None:
        block["division_games_back"] = div_gb
    if wc_rank is not None:
        block["wild_card_rank"] = wc_rank
    if wc_gb is not None:
        # Inside a spot StatsAPI reports the cushion; outside it, the deficit.
        # Name the field for what the number means so Dan cannot invert it.
        if in_spot:
            block["wild_card_games_up"] = wc_gb
        else:
            block["wild_card_games_back"] = wc_gb
    if magic is not None:
        block["magic_number"] = magic
    if raw.get("closest_chaser") and in_spot:
        block["closest_chaser"] = raw["closest_chaser"]

    return block


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
    status = classify_status(sport, now, team_key)
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

    # Stretch-run playoff race. Only MLB is wired to a standings source today;
    # the other three sports share the same gate and block shape and need only
    # a fetcher each. Outside the window build_playoff_race returns None and
    # nothing is written, which is what keeps the race out of Dan's April.
    if sport == "baseball":
        raw = fetch_mlb_standings_race()
        race = build_playoff_race(sport, status, raw,
                                  record.get("wins"), record.get("losses"))
        if race:
            entry["playoff_race"] = race
            print(f"  playoff_race: {race['race_status']}, "
                  f"{race['games_remaining']} games left")

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
