#!/usr/bin/env python3
"""generate_rant.py — call Gemini with the Boston Dan persona and structured data.

Loads the persona from prompts/boston_dan_system.txt, the rolling 7-day store,
the upcoming schedule, and the latest news. Sends them to Gemini, expects JSON
back, writes data/raw_dan_output.json.

Env vars:
  GEMINI_API_KEY        required
  GEMINI_MODEL          optional, default "gemini-3.1-flash-lite"
  THINKING_LEVEL        optional, default "minimal" — Gemini 3.x reasoning depth
  ROLLING_STORE_PATH    optional, lets eval_voice.py swap in a fixture
  SCHEDULE_PATH         optional
  NEWS_PATH             optional
  SEASON_STATIC_PATH    optional, past-seasons JSON (in git)
  SEASON_CURRENT_PATH   optional, daily-fetched current-season JSON
  DRAFT_PICKS_PATH      optional, draft picks JSON
  HISTORICAL_FACTS_PATH optional, curated Boston sports history JSON
  OUTPUT_PATH           optional, lets eval_voice.py write to evals/runs/...
"""

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO / "prompts" / "boston_dan_system.txt"
DEFAULT_STORE = REPO / "data" / "rolling_7day.json"
DEFAULT_SCHEDULE = REPO / "data" / "upcoming_schedule.json"
DEFAULT_NEWS = REPO / "data" / "latest_news.json"
DEFAULT_SEASON_STATIC = REPO / "data" / "season_static.json"
DEFAULT_SEASON_CURRENT = REPO / "data" / "season_current.json"
DEFAULT_DRAFT_PICKS = REPO / "data" / "boston_drafts.json"
DEFAULT_HISTORICAL_FACTS = REPO / "data" / "historical_facts.json"
DEFAULT_CALLERS = REPO / "data" / "callers_and_voices.json"
DEFAULT_GRUDGE_BOOK = REPO / "data" / "grudge_book.json"
DEFAULT_ROSTER = REPO / "data" / "boston_roster.json"
DEFAULT_ARCHIVE_DIR = REPO / "data" / "dan_archive"
DEFAULT_SEASON_OVERRIDES = REPO / "data" / "season_overrides.json"
DEFAULT_DAN_STORIES = REPO / "data" / "dan_stories.json"
DEFAULT_STORY_SEEDS = REPO / "data" / "story_seeds.json"
DEFAULT_OUTPUT = REPO / "data" / "raw_dan_output.json"

# Caller flavor: how many archetypes to inject per day. 2-3 keeps the prompt
# focused without locking Dan into a single voice. Picked deterministically
# from today's date so a given day always sees the same archetypes.
CALLERS_PER_DAY = 3

# Story flavor: how many recurring characters + story seeds to inject per day.
STORIES_PER_DAY = 3
SEEDS_PER_DAY = 3

# Draft freshness windows (days since last_active_date — the date when
# fetch_draft.py last saw new picks arrive). Tunable; matches Boston sports
# talk-radio news cycles.
#   active     last_active_date == today (new picks arrived today)
#   fresh      1–DRAFT_FRESH_DAYS days post-active — full recap allowed
#   aging      DRAFT_FRESH_DAYS+1 to DRAFT_AGING_DAYS — slim block, aside only
#   stale      >DRAFT_AGING_DAYS — DRAFT_PICKS not injected at all
DRAFT_FRESH_DAYS = 2
DRAFT_AGING_DAYS = 7

# Caps how many picks get full per-pick detail in the "active"/"fresh" draft
# coverage. Picks beyond this count are collapsed into a one-sentence summary by
# the persona (see the Major Milestones draft rule in boston_dan_system.txt).
# Keyed on pick COUNT, not sport — so deep drafts (MLB, ~20 Red Sox picks) get
# capped while shallow ones (NBA ~1–2, NHL ~7, NFL ~7–11) name every pick. Set to
# 12 so a compensatory-heavy NFL draft (up to ~11 picks) still names every pick;
# only MLB's ~20 reliably trips the collapse tier. The value is injected into the
# DRAFT_PICKS block as "detail_pick_count" so the prompt and the data agree.
DRAFT_DETAIL_PICKS = 12

# Continuity memory: number of past Dan outputs to inject into the prompt.
# 5 days gives Dan a long enough memory to spot recurring crutches (e.g. "18
# banners" being reused every morning) without bloating tokens — each archive
# is ~1.5KB, so 5 days adds ~7.5KB to a ~30KB prompt. Was 3; bumped 2026-05-04
# after observing daily repetition of the same historical_facts citations.
DEFAULT_MEMORY_DAYS = 5

TEAM_KEYS = ("celtics", "bruins", "redsox", "patriots")

# 2026-07-01: switched off the "-latest" alias. Google had quietly promoted
# gemini-3.5-flash to "latest," and its free tier was persistently exhausted
# for hours (three widely-spaced pipeline runs all hit 429 RESOURCE_EXHAUSTED).
# gemini-3.1-flash-lite is pinned deliberately here for its documented 500
# RPD free-tier quota — plenty for this pipeline's ~2-6 calls/day including
# retries, and no longer subject to whatever quota "latest" resolves to on
# a given day. See AGENTS.md Model Strategy for the full rationale.
DEFAULT_MODEL = "gemini-3.1-flash-lite"

# 2026-09-04: pinned explicitly rather than inherited. Every Gemini 3.x model
# accepts thinking_level, but the DEFAULT differs per model — gemini-3.1-flash-lite
# defaults to "minimal" while the full Flash models default to high/dynamic
# thinking. That meant this pipeline's latency profile depended on an undocumented
# per-model default: swapping DEFAULT_MODEL to a full Flash model would silently
# jump to high thinking, whose measured p95 time-to-first-token (~50s) does not
# fit the 90s per-request timeout below once grounding and a ~30KB prompt are
# added. Same lesson as the model pin itself — a known constant beats whatever
# Google currently maps a default to. See AGENTS.md Model Strategy.
DEFAULT_THINKING_LEVEL = "minimal"

# Per-call latency + token accounting, written to the output JSON as `_timings`
# so cost/latency questions are answerable from our own history instead of
# third-party benchmarks. Appended to by call_gemini(); read by main().
CALL_TIMINGS = []


def thinking_level_for(model_name: str, level: str | None = None) -> str | None:
    """
    Which thinking_level applies to this model, or None if it must not be sent.

    thinking_level is a Gemini 3.x parameter: earlier Gemini models return an
    error for it, and the Gemma open models rejected it along with
    system_instruction/tools/response_mime_type. eval_models.py A/Bs Gemma and
    older ids through this same code path, so the guard is load-bearing, not
    defensive padding.

    Kept free of any SDK import so it stays unit-testable without google-genai.
    """
    if not model_name.lower().startswith("gemini-3"):
        return None
    return level or os.environ.get("THINKING_LEVEL", DEFAULT_THINKING_LEVEL)


def thinking_kwargs(model_name: str, level: str | None = None) -> dict:
    """
    The GenerateContentConfig kwargs that pin thinking depth, or {} if this
    model does not take them.

    Note the nesting: the SDK has no top-level `thinking_level` field — it is
    `thinking_config=types.ThinkingConfig(thinking_level=...)`. Passing it flat
    raises a pydantic validation error on every call.
    """
    lvl = thinking_level_for(model_name, level)
    if not lvl:
        return {}
    from google.genai import types
    return {"thinking_config": types.ThinkingConfig(thinking_level=lvl)}


def record_timing(label: str, model_name: str, seconds: float, resp,
                  level: str = None) -> None:
    """
    Append one call's wall-clock latency and token usage to CALL_TIMINGS.

    thoughts_token_count is the number worth having: thinking tokens bill as
    output and are the main latency driver on Gemini 3.x, so a latency
    regression after a model or thinking_level change is otherwise invisible.
    usage_metadata is read defensively — a blocked or truncated candidate can
    leave fields absent, and instrumentation must never be what breaks the run.
    """
    usage = getattr(resp, "usage_metadata", None)
    entry = {
        "label": label,
        "model": model_name,
        # Passed in, not re-derived: call_gemini drops the kwarg and retries if
        # the model rejects it, and a timing row that claims a level the call
        # did not actually use would poison the bake-off it exists to inform.
        "thinking_level": level,
        "seconds": round(seconds, 2),
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        "thinking_tokens": getattr(usage, "thoughts_token_count", None),
    }
    CALL_TIMINGS.append(entry)
    print(f"  [timing] {label}: {entry['seconds']}s "
          f"prompt={entry['prompt_tokens']} out={entry['output_tokens']} "
          f"thinking={entry['thinking_tokens']}", file=sys.stderr)


def describe_api_error(e) -> str:
    """
    Pull structured fields out of a Gemini API error so logs show WHICH limit
    was hit — a grounding (Google Search) daily quota vs. a generation
    per-minute rate limit vs. transient overload — instead of an opaque
    'ClientError'. Reads the QuotaFailure violations (quotaMetric/quotaId) and
    RetryInfo (retryDelay) that Gemini returns on 429 RESOURCE_EXHAUSTED. Falls
    back to parsing str(e) when the SDK doesn't expose structured attributes.
    Never raises.
    """
    import re as _re
    import ast as _ast

    code = getattr(e, "code", None)
    status = getattr(e, "status", None)
    message = getattr(e, "message", None)
    details = getattr(e, "details", None)

    # google-genai stringifies the full error body; parse it as a fallback when
    # the structured attributes aren't populated (older/!= SDK versions).
    if details is None:
        match = _re.search(r"\{.*\}", str(e), _re.DOTALL)
        if match:
            try:
                details = _ast.literal_eval(match.group(0))
            except Exception:
                details = None

    err_obj = details.get("error", details) if isinstance(details, dict) else None
    detail_list = []
    if isinstance(err_obj, dict):
        code = code or err_obj.get("code")
        status = status or err_obj.get("status")
        message = message or err_obj.get("message")
        detail_list = err_obj.get("details", []) or []
    elif isinstance(details, list):
        detail_list = details

    quotas = []
    retry_delay = None
    for d in detail_list:
        if not isinstance(d, dict):
            continue
        dtype = d.get("@type", "")
        if "QuotaFailure" in dtype:
            for v in d.get("violations", []) or []:
                metric = v.get("quotaMetric") or v.get("quotaId") or ""
                dims = v.get("quotaDimensions") or {}
                model = dims.get("model") if isinstance(dims, dict) else None
                bit = metric + (f" (model={model})" if model else "")
                if bit:
                    quotas.append(bit)
        elif "RetryInfo" in dtype:
            retry_delay = d.get("retryDelay")

    parts = []
    if code:
        parts.append(f"code={code}")
    if status:
        parts.append(f"status={status}")
    if quotas:
        parts.append("quota=[" + "; ".join(quotas) + "]")
    if retry_delay:
        parts.append(f"retryDelay={retry_delay}")
    if message and not quotas:
        parts.append(f"msg={message[:160]}")
    return " | ".join(parts) if parts else str(e)[:200]


# --- Retry/timeout budget -------------------------------------------------
# These four constants are the whole time model of the pipeline. They are named
# (not inlined) because the invariant they satisfy is asserted in
# tests/test_pipeline.py::TestRetryBudget, so changing one without rerunning the
# math fails CI rather than production.
#
# The failure this bounds is the 2026-07-01 incident: the job hit the workflow's
# 25-min timeout and GitHub force-cancelled it *before* publish.py ran, so no
# sentinel was written, no fallback chosen, no commit made — just a red X. Every
# in-process retry has to leave enough room for the sentinel path to still run.
#
# The dominant term is REQUEST_TIMEOUT_S × attempts, not the backoff sleeps, so
# the lever that matters is MAX_RETRIES. It was lowered from 4 to 2 on
# 2026-09-04 to make the budget provably fit. That is less of a cut than it
# looks: generate_rant already makes two independent call paths (grounded, then
# ungrounded+JSON), so a bad morning still gets 2 × (1 + MAX_RETRIES) = 6 API
# attempts before the sentinel, and per AGENTS.md the 1–2h spikes were never
# meant to be absorbed in-process anyway — that is what the five spaced cron
# slots in morning_brew.yml are for.
REQUEST_TIMEOUT_S = 90          # http_options timeout on every client below
MAX_RETRIES = 2
BACKOFF_DELAYS = [5, 15]
MAX_CALLS_PER_RUN = 3           # grounded → ungrounded fallback → punch-up


def worst_case_call_seconds(max_retries=MAX_RETRIES, backoff=BACKOFF_DELAYS,
                            timeout_s=REQUEST_TIMEOUT_S) -> int:
    """Upper bound on one call_with_retry(), every attempt hitting the timeout."""
    return (max_retries + 1) * timeout_s + sum(backoff[:max_retries])


def call_with_retry(fn, max_retries=MAX_RETRIES):
    """
    Call fn() with exponential backoff retry on 503/429 errors.

    On 503 UNAVAILABLE: wait BACKOFF_DELAYS in order
    On 429 QUOTA_EXCEEDED: parse retryDelay from error, wait that duration
    On other errors: fail immediately

    See the budget block above for why the ladder is this short. If Gemini is
    down longer than the budget, the existing fallbacks (sentinel in
    generate_rant, treat-as-PASS in safety_judge) take over.
    """
    backoff_delays = BACKOFF_DELAYS

    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            error_str = str(e)
            status_code = None

            # Extract status code from error
            if "503" in error_str:
                status_code = 503
            elif "429" in error_str:
                status_code = 429

            # Don't retry permanent errors
            if status_code not in [503, 429]:
                print(f"  non-retryable API error: {describe_api_error(e)}", file=sys.stderr)
                raise

            if attempt >= max_retries:
                print(f"  retries exhausted after {attempt} attempt(s): {describe_api_error(e)}", file=sys.stderr)
                raise  # Exhausted retries

            # Calculate wait time
            if status_code == 429 and "retryDelay" in error_str:
                try:
                    delay_str = error_str.split("retryDelay")[1].split("'")[1]
                    wait_sec = float(delay_str.replace("s", ""))
                except:
                    wait_sec = backoff_delays[attempt]
            else:
                wait_sec = backoff_delays[attempt]

            print(f"  retry: {status_code}, waiting {wait_sec}s... [{describe_api_error(e)}]", file=sys.stderr)
            time.sleep(wait_sec)


def load_json(path: Path):
    if not path.exists():
        print(f"  warn: {path.name} missing — sending empty object", file=sys.stderr)
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"  warn: {path.name} invalid JSON ({e}) — sending empty object", file=sys.stderr)
        return {}


def normalize_box_scores(data: dict) -> dict:
    """
    Normalize box_scores to a consistent schema across all sports.

    Input formats (from fetchers):
    - Celtics/Bruins/Red Sox may have nested games arrays or simple score objects
    - Patriots may have different structure during offseason

    Output format (for frontend):
    {
      "sport": "NBA|NHL|MLB|NFL",
      "home_team": "...",
      "away_team": "...",
      "home_score": int,
      "away_score": int,
      "game_date": "YYYY-MM-DD",
      "played": bool,
      "season_type": "regular|playoff|preseason|offseason"
    }
    """
    if "box_scores" not in data or not data["box_scores"]:
        return data

    normalized = {}
    sport_map = {
        "celtics": "NBA",
        "bruins": "NHL",
        "redsox": "MLB",
        "patriots": "NFL",
    }

    for team_key, team_data in data["box_scores"].items():
        if not team_data:
            continue

        sport = sport_map.get(team_key, "Unknown")

        # If the team has a nested games array (Red Sox format), normalize every
        # game — a doubleheader has two entries and dropping games[1] loses half
        # the day (the 2026-07-17 Rays sweep shipped with only game 1 visible).
        if isinstance(team_data.get("games"), list) and len(team_data["games"]) > 0:
            norm_games = []
            for i, game in enumerate(team_data["games"]):
                norm_games.append({
                    "game_number": game.get("game_number", i + 1),
                    "home_team": "Boston Red Sox" if game.get("home") else game.get("opponent", "Unknown"),
                    "away_team": game.get("opponent", "Unknown") if game.get("home") else "Boston Red Sox",
                    "home_score": game.get("redsox_score") if game.get("home") else game.get("opponent_score"),
                    "away_score": game.get("opponent_score") if game.get("home") else game.get("redsox_score"),
                })
            entry = {
                "sport": sport,
                "home_team": norm_games[0]["home_team"],
                "away_team": norm_games[0]["away_team"],
                "home_score": norm_games[0]["home_score"],
                "away_score": norm_games[0]["away_score"],
                "game_date": team_data.get("game_date", ""),
                "played": team_data.get("played", False),
                "season_type": team_data.get("season_type", "unknown"),
            }
            if len(norm_games) > 1:
                entry["doubleheader"] = True
                entry["games"] = norm_games
            normalized[team_key] = entry
        else:
            # Fetcher-format (Celtics/Bruins): uses team-specific score fields and
            # a boolean "home" flag rather than home_team/away_team strings.
            # Boston score field varies by team; fall back to generic "score".
            boston_score_key = {
                "celtics": "celtics_score",
                "bruins": "bruins_score",
                "patriots": "patriots_score",
            }.get(team_key, "score")
            boston_full_name = {
                "celtics": "Boston Celtics",
                "bruins": "Boston Bruins",
                "patriots": "New England Patriots",
            }.get(team_key, "Boston")

            boston_score = team_data.get(boston_score_key)
            opp_score = team_data.get("opponent_score")
            opponent = team_data.get("opponent", "")
            is_home = team_data.get("home")  # None if not present

            if is_home is not None:
                # Real fetcher format — we know home/away
                home_team = boston_full_name if is_home else opponent
                away_team = opponent if is_home else boston_full_name
                home_score = boston_score if is_home else opp_score
                away_score = opp_score if is_home else boston_score
            else:
                # Gemini may have used home_team/away_team directly, or omitted scores
                home_team = team_data.get("home_team", "")
                away_team = team_data.get("away_team", "")
                home_score = team_data.get("home_score")
                away_score = team_data.get("away_score")

            normalized[team_key] = {
                "sport": sport,
                "home_team": home_team,
                "away_team": away_team,
                "home_score": home_score,
                "away_score": away_score,
                "game_date": team_data.get("game_date", ""),
                "played": team_data.get("played", False),
                "season_type": team_data.get("season_type", "unknown"),
            }

    data["box_scores"] = normalized
    return data


def repair_box_scores_from_fetchers(data: dict) -> dict:
    """
    After normalize_box_scores runs, Gemini may have emitted played:false with empty
    teams/scores even though the fetcher JSONs contain real game results.  This happens
    when grounding is ON but Gemini still doesn't reliably populate the box_scores object
    (it writes the narrative correctly in morning_brew but leaves box_scores blank).

    This function reads the raw fetcher output files and, for any team whose normalized
    entry has played:false + null scores, overwrites it with the real fetcher data
    (if the fetcher says played:true).

    Fetcher schemas:
      celtics_boxscore.json : { played, home (bool), celtics_score, opponent, opponent_score, game_date, season_type }
      bruins_boxscore.json  : { played, home (bool), bruins_score,  opponent, opponent_score, game_date, season_type }
      redsox_boxscore.json  : { played, home (bool), redsox_score,  opponent, opponent_score, game_date, season_type }
                              OR { games: [{ played, home, redsox_score, opponent, opponent_score, ... }] }
      patriots_boxscore.json: { played, home (bool), patriots_score, opponent, opponent_score, game_date, season_type }
    """
    if "box_scores" not in data:
        return data

    fetcher_files = {
        "celtics":  REPO / "data" / "celtics_boxscore.json",
        "bruins":   REPO / "data" / "bruins_boxscore.json",
        "redsox":   REPO / "data" / "redsox_boxscore.json",
        "patriots": REPO / "data" / "patriots_boxscore.json",
    }
    boston_score_keys = {
        "celtics":  "celtics_score",
        "bruins":   "bruins_score",
        "redsox":   "redsox_score",
        "patriots": "patriots_score",
    }
    boston_full_names = {
        "celtics":  "Boston Celtics",
        "bruins":   "Boston Bruins",
        "redsox":   "Boston Red Sox",
        "patriots": "New England Patriots",
    }
    sport_map = {
        "celtics":  "NBA",
        "bruins":   "NHL",
        "redsox":   "MLB",
        "patriots": "NFL",
    }

    for team_key, fetcher_path in fetcher_files.items():
        existing = data["box_scores"].get(team_key, {})
        already_has_scores = (
            existing.get("played") and
            existing.get("home_score") is not None and
            existing.get("away_score") is not None
        )

        # Load the fetcher BEFORE deciding to skip. Gemini having *a* score is
        # not proof it has the whole day: on a doubleheader it typically emits
        # one flat game, and gating purely on already_has_scores discarded the
        # fetcher's second game (2026-07-22 Orioles twin bill published as a
        # single 1-5 loss). Repair also runs when the fetcher saw more games
        # than the current entry carries.
        raw = load_json(fetcher_path)
        if not raw or raw.get("error"):
            continue  # Fetcher also failed — nothing to repair from

        raw_games = raw.get("games") if isinstance(raw.get("games"), list) else None
        existing_games = existing.get("games") if isinstance(existing.get("games"), list) else []
        missing_games = bool(raw_games) and len(raw_games) > max(len(existing_games), 1)

        if already_has_scores and not missing_games:
            continue  # Gemini got it right — leave it alone

        # Red Sox may wrap in a games array. Individual game dicts carry no
        # "played" key — that lives on the top-level boxscore — so check the
        # level we're actually reading from (checking game.get("played") after
        # unwrapping made this repair a silent no-op for the games-array format).
        game = raw
        if raw_games:
            game = raw_games[0]
            if not raw.get("played"):
                continue  # Fetcher says no game — respect that
        elif not game.get("played"):
            continue  # Fetcher also says no game — respect that

        boston_score_key = boston_score_keys[team_key]
        boston_full_name = boston_full_names[team_key]
        sport = sport_map[team_key]
        is_home = game.get("home")
        boston_score = game.get(boston_score_key)
        opp_score = game.get("opponent_score")
        opponent = game.get("opponent", "")
        game_date = game.get("game_date") or raw.get("game_date", "")
        season_type = game.get("season_type") or raw.get("season_type", "unknown")

        if is_home is not None:
            home_team  = boston_full_name if is_home else opponent
            away_team  = opponent if is_home else boston_full_name
            home_score = boston_score if is_home else opp_score
            away_score = opp_score if is_home else boston_score
        else:
            home_team  = boston_full_name
            away_team  = opponent
            home_score = boston_score
            away_score = opp_score

        repaired = {
            "sport":      sport,
            "home_team":  home_team,
            "away_team":  away_team,
            "home_score": home_score,
            "away_score": away_score,
            "game_date":  game_date,
            "played":     True,
            "season_type": season_type,
        }
        if raw_games and len(raw_games) > 1:
            repaired["doubleheader"] = True
            repaired["games"] = [
                {
                    "game_number": g.get("game_number", i + 1),
                    "home_team": boston_full_name if g.get("home") else g.get("opponent", "Unknown"),
                    "away_team": g.get("opponent", "Unknown") if g.get("home") else boston_full_name,
                    "home_score": g.get(boston_score_key) if g.get("home") else g.get("opponent_score"),
                    "away_score": g.get("opponent_score") if g.get("home") else g.get(boston_score_key),
                }
                for i, g in enumerate(raw_games)
            ]
        data["box_scores"][team_key] = repaired
        suffix = f" (+{len(raw_games) - 1} more game(s), doubleheader)" if repaired.get("doubleheader") else ""
        print(f"  repaired box_score for {team_key}: {home_team} {home_score}–{away_score} {away_team}{suffix}", file=sys.stderr)

    return data


def build_season_memory(static_data: dict, current_data: dict) -> dict:
    """
    Merge hand-curated past seasons (season_static.json) with daily-fetched
    current-season snapshot (season_current.json) into a single lean dict
    keyed by team. Missing pieces → empty fields; downstream gracefully
    degrades.
    """
    merged = {}
    for team in TEAM_KEYS:
        static_entry = (static_data or {}).get(team, {}) or {}
        current_entry = (current_data or {}).get(team, {}) or {}
        merged[team] = {
            "current_season": current_entry,
            "past_seasons": static_entry.get("past_seasons", []),
        }
    return merged


def load_recent_dan_output(archive_dir: Path, days: int = DEFAULT_MEMORY_DAYS) -> list[dict]:
    """
    Load the last N days of Dan's published output for continuity memory.

    Reads `data/dan_archive/YYYY-MM-DD.json` files (written by publish.py),
    returns the most recent `days` entries newest-first, skipping today's
    UTC date if it exists (avoids self-reference on re-runs).

    Each archived entry is a slim copy: {date, headline, morning_brew,
    news_digest, generated_at}. Box scores, schedule, trend_watch are
    excluded (those are date-specific facts, not Dan's voice).

    Returns [] on missing dir, no archives, or any read error — graceful
    degradation. The continuity feature should never block generation.
    """
    if not archive_dir.exists() or not archive_dir.is_dir():
        return []

    today_iso = datetime.now(timezone.utc).date().isoformat()

    try:
        archive_files = sorted(
            (p for p in archive_dir.glob("*.json") if p.stem != today_iso),
            key=lambda p: p.stem,
            reverse=True,
        )
    except Exception as e:
        print(f"  warn: could not list archive dir ({e})", file=sys.stderr)
        return []

    entries: list[dict] = []
    for path in archive_files[:days]:
        try:
            data = json.loads(path.read_text())
            entries.append({
                "date": path.stem,
                "headline": data.get("headline", ""),
                "morning_brew": data.get("morning_brew", []),
                "news_digest": data.get("news_digest", []),
            })
        except Exception as e:
            print(f"  warn: skipping unreadable archive {path.name} ({e})", file=sys.stderr)
            continue

    return entries


def compute_draft_freshness(last_active: str | None, today: date) -> tuple[str | None, int | None]:
    """
    Classify how fresh a SINGLE draft's news cycle is from its own
    last_active_date, so generate_rant.py can decide how much context to hand
    Dan for that sport's draft.

    Each draft in active_drafts carries its own last_active_date (stamped by
    fetch_draft.py only when THAT draft's pick count grows). Freshness is
    computed per draft so one sport's live draft never revives another sport's
    months-old completed draft — that global-date bug made Dan recap the stale
    NFL draft on NBA draft day.

    Returns (freshness, days_since_active):
      ("active",  0)        last_active_date == today (new picks added today)
      ("fresh",   N)        1–DRAFT_FRESH_DAYS days post-active
      ("aging",   N)        DRAFT_FRESH_DAYS+1 to DRAFT_AGING_DAYS
      ("stale",   N)        >DRAFT_AGING_DAYS — this draft omitted entirely
      (None,      None)     no/unparseable date — caller omits this draft.

    NOTE: a draft's presence in active_drafts is intentionally NOT the "active"
    signal. ESPN serves completed draft picks year-round, so a draft stays in
    active_drafts after it concludes. last_active_date is the sole source of
    truth.
    """
    if not last_active:
        return None, None

    try:
        last_dt = (
            datetime.fromisoformat(last_active).date()
            if "T" in last_active
            else date.fromisoformat(last_active)
        )
    except (ValueError, TypeError):
        return None, None

    days = max(0, (today - last_dt).days)
    if days == 0:
        return "active", 0
    if days <= DRAFT_FRESH_DAYS:
        return "fresh", days
    if days <= DRAFT_AGING_DAYS:
        return "aging", days
    return "stale", days


def select_daily_callers(callers_data: dict, today_iso: str, n: int = CALLERS_PER_DAY) -> list[dict]:
    """
    Deterministically pick N caller archetypes for today, seeded by date.
    Same day always picks the same archetypes (so re-runs / corrections match);
    different days rotate.

    Returns [] if callers_data is missing/malformed.
    """
    if not callers_data or not isinstance(callers_data, dict):
        return []
    archetypes = callers_data.get("archetypes", []) or []
    if not archetypes:
        return []

    import hashlib
    seed = int(hashlib.sha256(today_iso.encode()).hexdigest()[:8], 16)
    pool = list(archetypes)
    # Fisher-Yates with seeded indices, deterministic across runs
    for i in range(len(pool) - 1, 0, -1):
        j = (seed + i * 2654435761) % (i + 1)
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:n]


def select_daily_stories(stories_data: dict, today_iso: str, n: int = STORIES_PER_DAY) -> list[dict]:
    """
    Deterministically pick N recurring characters for today, seeded by date.
    Same pattern as select_daily_callers.
    """
    if not stories_data or not isinstance(stories_data, dict):
        return []
    characters = stories_data.get("recurring_characters", []) or []
    if not characters:
        return []

    import hashlib
    # Use a different seed offset than callers so they don't correlate
    seed = int(hashlib.sha256(("stories:" + today_iso).encode()).hexdigest()[:8], 16)
    pool = list(characters)
    for i in range(len(pool) - 1, 0, -1):
        j = (seed + i * 2654435761) % (i + 1)
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:n]


def select_daily_seeds(seeds_data: dict, today_iso: str, n: int = SEEDS_PER_DAY) -> list[dict]:
    """
    Deterministically pick N story seeds for today, seeded by date.
    Only used on slow news days.
    """
    if not seeds_data or not isinstance(seeds_data, dict):
        return []
    seeds = seeds_data.get("seeds", []) or []
    if not seeds:
        return []

    import hashlib
    seed = int(hashlib.sha256(("seeds:" + today_iso).encode()).hexdigest()[:8], 16)
    pool = list(seeds)
    for i in range(len(pool) - 1, 0, -1):
        j = (seed + i * 2654435761) % (i + 1)
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:n]


def _extract_team_games(rolling: dict | None, team_key: str) -> list[dict]:
    """
    Extract boxscore game entries for a team from rolling_7day.

    rolling_7day structure: {"days": [{"date": "...", "redsox": {"boxscore": {"played": true, "games": [...]}}, ...}]}
    Each day's team entry is: {team_key: {"boxscore": {"game_date": "...", "played": bool, "games": [...]}}}

    Returns a flat list of game dicts across all days, each annotated with
    "played" and "game_date" from the parent boxscore entry if not already present.
    """
    if not rolling or not isinstance(rolling, dict):
        return []
    days = rolling.get("days", [])
    if not isinstance(days, list):
        return []

    all_games = []
    for day_entry in days:
        if not isinstance(day_entry, dict):
            continue
        team_data = day_entry.get(team_key)
        if not team_data or not isinstance(team_data, dict):
            continue
        boxscore = team_data.get("boxscore")
        if not boxscore or not isinstance(boxscore, dict):
            continue
        played = boxscore.get("played", False)
        game_date = boxscore.get("game_date", day_entry.get("date", ""))
        games = boxscore.get("games", [])
        if played and isinstance(games, list) and games:
            for g in games:
                enriched = dict(g)
                enriched.setdefault("played", played)
                enriched.setdefault("game_date", game_date)
                all_games.append(enriched)
        elif played:
            # Flat single-game format (Celtics/Bruins/Patriots): the score
            # fields live directly on the boxscore, so carry them along —
            # appending only {played, game_date} strips the result and
            # downstream consumers (emotional context) see a 0-0 game.
            flat = {k: v for k, v in boxscore.items() if k != "games"}
            flat.setdefault("played", True)
            flat.setdefault("game_date", game_date)
            all_games.append(flat)
    return all_games


_BOSTON_NAMES = {
    "celtics": ["celtics", "boston celtics"],
    "bruins": ["bruins", "boston bruins"],
    "redsox": ["red sox", "boston red sox"],
    "patriots": ["patriots", "new england patriots"],
}
_BOSTON_SCORE_KEYS = {
    "celtics": "celtics_score",
    "bruins": "bruins_score",
    "redsox": "redsox_score",
    "patriots": "patriots_score",
}


def _game_outcome(game: dict, team_key: str) -> dict | None:
    """
    Resolve a single game dict to Boston's perspective:
    {"our_score", "their_score", "opponent", "won", "margin"}.

    Supports BOTH schemas that reach the rolling store:
      - fetcher format:  {home (bool), <team>_score, opponent, opponent_score}
        (this is what fetch_*.py actually writes — the production path)
      - normalized/fixture format: {home_team, away_team, home_score, away_score}

    Returns None if neither schema's score fields are present, so callers can
    skip entries that carry no result rather than fabricate a 0-0 game.
    """
    score_key = _BOSTON_SCORE_KEYS.get(team_key, "score")
    if score_key in game or "opponent_score" in game:
        our = game.get(score_key) or 0
        their = game.get("opponent_score") or 0
        opponent = (game.get("opponent") or "").lower()
    elif "home_score" in game or "away_score" in game:
        home = game.get("home_score", 0) or 0
        away = game.get("away_score", 0) or 0
        home_team = (game.get("home_team") or "").lower()
        away_team = (game.get("away_team") or "").lower()
        is_home = any(name in home_team for name in _BOSTON_NAMES.get(team_key, []))
        our = home if is_home else away
        their = away if is_home else home
        opponent = away_team if is_home else home_team
    else:
        return None
    return {
        "our_score": our,
        "their_score": their,
        "opponent": opponent,
        "won": our > their,
        "margin": abs(our - their),
    }


def compute_emotional_context(rolling: dict, grudges: dict | None) -> dict:
    """
    Pre-compute emotional signals from rolling_7day data so the prompt gets
    explicit mood direction instead of relying on Gemini to infer it.

    Returns a dict keyed by team with: last_result, margin, streak, rival_game,
    rival, emotional_register (plus doubleheader/doubleheader_result when the
    most recent day had two games — MLB twin bills must never be summarized
    from just one of the games).
    """
    context = {}
    # Build a lookup of rivals from grudge_book
    rival_lookup = {}
    if grudges and isinstance(grudges, dict):
        for team_key, entries in grudges.items():
            if isinstance(entries, list):
                for entry in entries:
                    rival_name = entry.get("rival", "")
                    if rival_name:
                        rival_lookup.setdefault(team_key.lower(), []).append(rival_name.lower())

    for team_key in TEAM_KEYS:
        games = _extract_team_games(rolling, team_key)
        if not games:
            continue

        # Keep only played games that carry a resolvable result.
        outcomes = []
        for g in games:
            if not g.get("played"):
                continue
            o = _game_outcome(g, team_key)
            if o is not None:
                o["game_date"] = g.get("game_date", "")
                o["game_number"] = g.get("game_number", 1)
                outcomes.append(o)
        if not outcomes:
            continue

        # Most recent first; game 2 of a doubleheader is the later game.
        outcomes.sort(key=lambda o: (o["game_date"], o["game_number"]), reverse=True)
        latest = outcomes[0]
        latest_day = [o for o in outcomes if o["game_date"] == latest["game_date"]]
        is_doubleheader = len(latest_day) > 1

        if is_doubleheader:
            day_wins = sum(1 for o in latest_day if o["won"])
            if day_wins == len(latest_day):
                last_result = "doubleheader sweep (won both games)"
            elif day_wins == 0:
                last_result = "swept in doubleheader (lost both games)"
            else:
                last_result = "doubleheader split (won one, lost one)"
            # The day's mood rides on the pair; use the most lopsided game for
            # blowout/nail-biter checks so a 10-0 opener isn't muted by a close
            # nightcap (and vice versa on a swept day).
            margin = max(o["margin"] for o in latest_day)
            won = day_wins > len(latest_day) / 2
        else:
            last_result = "win" if latest["won"] else "loss"
            margin = latest["margin"]
            won = latest["won"]

        # Count streak game-by-game (each doubleheader game counts).
        streak_count = 0
        streak_type = "W" if outcomes[0]["won"] else "L"
        for o in outcomes:
            if (o["won"] and streak_type == "W") or (not o["won"] and streak_type == "L"):
                streak_count += 1
            else:
                break

        # Check if rival game
        rival_game = False
        rival_name = ""
        for r in rival_lookup.get(team_key, []):
            if r in latest["opponent"]:
                rival_game = True
                rival_name = r
                break

        # Determine emotional register
        sport = {"celtics": "NBA", "bruins": "NHL", "redsox": "MLB", "patriots": "NFL"}.get(team_key, "")
        blowout_thresholds = {"NBA": 15, "NHL": 3, "MLB": 5, "NFL": 14}
        nail_biter_thresholds = {"NBA": 3, "NHL": 1, "MLB": 1, "NFL": 3}
        is_blowout = margin >= blowout_thresholds.get(sport, 5)
        is_nail_biter = margin <= nail_biter_thresholds.get(sport, 1)

        register_parts = []
        if won:
            register_parts.append("euphoric" if is_blowout else ("agonized relief" if is_nail_biter else "satisfied"))
        else:
            register_parts.append("disgusted" if is_blowout else ("heartbroken" if is_nail_biter else "frustrated"))
        if streak_count >= 3:
            register_parts.append(f"{'momentum' if streak_type == 'W' else 'despair'} ({streak_type}{streak_count})")
        if rival_game:
            register_parts.append(f"rival game vs {rival_name}")

        entry = {
            "last_result": last_result,
            "margin": margin,
            "streak": f"{streak_type}{streak_count}",
            "rival_game": rival_game,
            "rival": rival_name,
            "emotional_register": ", ".join(register_parts),
        }
        if is_doubleheader:
            entry["doubleheader"] = True
            entry["doubleheader_result"] = [
                {"game_number": o["game_number"],
                 "result": "W" if o["won"] else "L",
                 "score": f"{o['our_score']}-{o['their_score']}"}
                for o in sorted(latest_day, key=lambda o: o["game_number"])
            ]
        context[team_key] = entry

    return context


def compute_coverage_allocation(
    season_overrides: dict | None,
    season_current: dict | None,
    rolling: dict | None,
) -> dict:
    """
    Classify each Boston team as PRIMARY, SECONDARY, or MINIMAL based on
    season status, recent game activity, and news relevance.

    Returns {"primary": [...], "secondary": [...], "minimal": [...]}.
    """
    primary = []
    secondary = []
    minimal = []

    eliminations = (season_overrides or {}).get("eliminations", {})

    for team_key in TEAM_KEYS:
        is_eliminated = team_key in eliminations

        # Check if team played recently (within rolling_7day)
        games = _extract_team_games(rolling, team_key)
        played_recently = len(games) > 0

        # Check season_current status
        status = "offseason"
        if season_current and isinstance(season_current, dict):
            team_season = season_current.get(team_key)
            if team_season and isinstance(team_season, dict):
                status = team_season.get("status", "offseason")

        if is_eliminated:
            minimal.append(team_key)
        elif status == "offseason" and not played_recently:
            secondary.append(team_key)
        else:
            primary.append(team_key)

    return {"primary": primary, "secondary": secondary, "minimal": minimal}


def detect_slow_day(rolling: dict | None, news: dict | list | None, schedule: dict | list | None, today_iso: str | None = None) -> bool:
    """
    Detect a slow news day: no Boston team played YESTERDAY AND fewer than 2
    relevant news headlines AND no games today.

    Uses _extract_team_games() to correctly navigate the rolling_7day structure.
    """
    if today_iso is None:
        today_iso = datetime.now(timezone.utc).date().isoformat()
    yesterday = (date.fromisoformat(today_iso) - timedelta(days=1)).isoformat()

    # Check if any team played yesterday
    for team_key in TEAM_KEYS:
        games = _extract_team_games(rolling, team_key)
        for g in games:
            if g.get("game_date", "").startswith(yesterday) and g.get("played"):
                return False

    # Check news count
    news_items = []
    if isinstance(news, list):
        news_items = news
    elif isinstance(news, dict):
        news_items = news.get("stories", []) or news.get("headlines", []) or []
    if len(news_items) >= 2:
        return False

    # Check if any games today
    games_today = []
    if isinstance(schedule, list):
        games_today = schedule
    elif isinstance(schedule, dict):
        games_today = schedule.get("games", []) or []
    today_games = [g for g in games_today if g.get("date", "").startswith(today_iso)]
    if today_games:
        return False

    return True


def _build_overrides_block(season_overrides: dict, today_iso: str | None = None) -> str:
    """
    Render season_overrides.json into plain-prose override text for the prompt.

    Converts each elimination entry into direct, imperative language that
    counteracts the playoff framing Dan might infer from news stories.
    Returns empty string if no eliminations are active.

    Entries may carry an "expires" ISO date. Past-expiry entries are skipped
    with a loud warning — this file is hand-maintained, and an elimination
    notice left over from LAST season becomes actively wrong the moment the
    new season starts (the trap: nobody remembers to clear it in October).
    """
    eliminations = season_overrides.get("eliminations") or {}
    if not eliminations:
        return ""
    if today_iso is None:
        today_iso = datetime.now(timezone.utc).date().isoformat()

    lines = []
    for team_key, info in eliminations.items():
        expires = info.get("expires")
        if expires and expires < today_iso:
            print(f"  warn: season_overrides entry '{team_key}' expired {expires} — skipping "
                  f"(clear it from data/season_overrides.json)", file=sys.stderr)
            continue
        team_label = f"{team_key.upper()} ({info.get('sport', '')})"
        elim_from = info.get("eliminated_from", "playoffs")
        elim_date = info.get("eliminated_date", "recently")
        elim_by = info.get("eliminated_by", "")
        series = info.get("series_result", "")
        note = info.get("season_over_note", "")

        line = (
            f"{team_label}: ELIMINATED from {elim_from} on {elim_date}."
        )
        if elim_by:
            line += f" Lost to {elim_by}"
            if series:
                line += f" ({series})"
            line += "."
        lines.append(line)
        if note:
            lines.append(note)
        lines.append("")  # blank line between teams

    return "\n".join(lines).strip()


def build_user_message(rolling, schedule, news, season_memory, draft_picks=None, historical_facts=None, recent_output=None, callers=None, grudges=None, roster=None, season_overrides=None, today_iso: str | None = None, emotional_context=None, coverage_allocation=None, slow_day=False, stories=None, story_seeds=None) -> str:
    if today_iso is None:
        today_iso = datetime.now(timezone.utc).date().isoformat()

    message = (
        f"TODAY: {today_iso}\n\n"
    )
    if slow_day:
        message += (
            "SLOW_DAY_MODE: TRUE\n"
            "No Boston team played yesterday and there is minimal news. This is a slow news day.\n"
            "Instead of stretching thin material, tell a SHORT FICTIONAL STORY woven around REAL stats.\n"
            "See the Slow Day Storytelling section in the system prompt for rules.\n\n"
        )
    message += (
        "Here is the structured data for the last 7 days of Boston sports.\n"
        "Use ONLY the numbers and facts in this data — never invent stats.\n\n"
        "ROLLING_7DAY:\n"
        f"{json.dumps(rolling, indent=2)}\n\n"
    )
    if emotional_context:
        message += (
            "EMOTIONAL_CONTEXT (pre-computed mood signals — use these to calibrate "
            "emotional intensity per team; see Emotional Range in the system prompt):\n"
            f"{json.dumps(emotional_context, indent=2)}\n\n"
        )
    message += (
        "UPCOMING_SCHEDULE:\n"
        f"{json.dumps(schedule, indent=2)}\n\n"
        "LATEST_NEWS:\n"
        f"{json.dumps(news, indent=2)}\n\n"
    )
    if recent_output:
        message += (
            "RECENT_DAN_OUTPUT (last few days of YOUR OWN writing — DO NOT REPEAT phrasing or "
            "re-introduce stories you already covered. See the Continuity rule in the system "
            "prompt for what counts as acceptable callbacks vs. forbidden self-repetition):\n"
            f"{json.dumps(recent_output, indent=2)}\n\n"
        )

    # Freshness-aware DRAFT_PICKS injection, computed PER DRAFT from each draft's
    # own last_active_date. One sport's live draft must never revive another
    # sport's stale completed draft (the global-date bug that made Dan recap the
    # months-old NFL draft on NBA draft day). See compute_draft_freshness() and
    # the Major Milestones section of boston_dan_system.txt for the per-state rules.
    today_date = date.fromisoformat(today_iso)
    included_drafts = []
    for draft in (draft_picks or {}).get("active_drafts", []):
        if not isinstance(draft, dict):
            continue
        freshness, days_since = compute_draft_freshness(
            draft.get("last_active_date"), today_date
        )
        if freshness in ("active", "fresh"):
            entry = dict(draft)
            entry["freshness"] = freshness
            entry["days_since_active"] = days_since
            included_drafts.append(entry)
        elif freshness == "aging":
            # Keep this draft's player names available for the safety judge's
            # source-data check, but tell Dan explicitly NOT to recap it.
            entry = dict(draft)
            entry["freshness"] = "aging"
            entry["days_since_active"] = days_since
            entry["_note"] = (
                "Draft is OVER and the news cycle has moved on. Do NOT "
                "proactively recap these picks. Reference a specific draftee "
                "only if LATEST_NEWS surfaces them by name."
            )
            included_drafts.append(entry)
        # freshness in ("stale", None): omit THIS draft entirely.

    if included_drafts:
        block = {
            "generated_at": (draft_picks or {}).get("generated_at"),
            "detail_pick_count": DRAFT_DETAIL_PICKS,
            "active_drafts": included_drafts,
        }
        message += (
            "DRAFT_PICKS:\n"
            f"{json.dumps(block, indent=2)}\n\n"
        )
    # No non-stale drafts: omit DRAFT_PICKS entirely.
    if season_overrides:
        overrides_text = _build_overrides_block(season_overrides, today_iso=today_iso)
        if overrides_text:
            message += (
                "SEASON_OVERRIDES (authoritative — takes precedence over any playoff "
                "framing you might infer from news stories or LATEST_NEWS):\n"
                f"{overrides_text}\n\n"
            )
    if coverage_allocation:
        primary = ", ".join(coverage_allocation.get("primary", [])) or "none"
        secondary = ", ".join(coverage_allocation.get("secondary", [])) or "none"
        minimal = ", ".join(coverage_allocation.get("minimal", [])) or "none"
        message += (
            "COVERAGE_ALLOCATION (follow these priorities for morning_brew airtime):\n"
            f"- PRIMARY (bulk of morning_brew): {primary}\n"
            f"- SECONDARY (1-2 sentences if news warrants): {secondary}\n"
            f"- MINIMAL (skip unless breaking news in LATEST_NEWS): {minimal}\n\n"
        )
    message += (
        "SEASON_MEMORY:\n"
        f"{json.dumps(season_memory, indent=2)}\n\n"
    )
    if historical_facts:
        message += (
            "HISTORICAL_FACTS:\n"
            f"{json.dumps(historical_facts, indent=2)}\n\n"
        )
    if grudges:
        message += (
            "GRUDGE_BOOK:\n"
            f"{json.dumps(grudges, indent=2)}\n\n"
        )
    if callers:
        message += (
            "CALLER_FLAVOR (today's archetypes — use AT MOST one phrasing per "
            "morning_brew, only if it fits the moment; do not stack):\n"
            f"{json.dumps(callers, indent=2)}\n\n"
        )
    if stories:
        message += (
            "DAN_STORIES (today's recurring characters — use AT MOST one character "
            "reference per morning_brew. Adapt to the actual story, don't force it):\n"
            f"{json.dumps(stories, indent=2)}\n\n"
        )
    if slow_day and story_seeds:
        message += (
            "STORY_SEEDS (historical anchors for today's slow-day story — pick one "
            "as your starting point, weave a fictional personal story around it):\n"
            f"{json.dumps(story_seeds, indent=2)}\n\n"
        )
    if roster and roster.get("rosters"):
        message += (
            "CURRENT_ROSTER (active players only as of today — do NOT imply any "
            "unlisted player is currently on the team):\n"
            f"{json.dumps(roster['rosters'], indent=2)}\n\n"
        )
    message += (
        "Generate Boston Dan's Hub JSON output. Return ONLY the JSON object, "
        "no prose, no markdown fences. Keys: headline (punchy newspaper-style headline in Dan's voice — complete thought, no cut-off phrases, 10–16 words max), "
        "morning_brew (3 paragraphs), "
        "trend_watch (array of objects with category, player — always use FULL first and last name, never initials or abbreviations, trend, dans_take), "
        "box_scores, schedule (next 3 days)."
    )
    return message


def _strip_json_fence(text: str) -> str:
    """Strip a leading/trailing markdown code fence that some models emit even
    when asked for raw JSON (Gemma does this). Already-clean JSON is untouched."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def call_gemini(system_prompt: str, user_message: str, model_name: str,
                use_grounding: bool = True, force_json: bool = False) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        sys.exit("error: google-genai not installed. Run: python3 -m pip install google-genai")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("error: GEMINI_API_KEY not set")

    # Without an explicit request timeout, an HTTP call that never returns (as
    # opposed to one that returns a 503/429) blocks forever — call_with_retry's
    # backoff budget never even starts because fn() itself hasn't raised. This
    # bit the pipeline on 2026-07-01: a single hung generate_content() call ate
    # ~21 minutes with zero output until GitHub Actions force-cancelled the whole
    # job at the 25-min mark, skipping publish.py's sentinel/fallback path
    # entirely (no commit, no _generation_failed marker — just a red X). A
    # bounded per-request timeout turns that hang into a normal exception, which
    # the existing try/except around call_gemini() already handles by writing
    # the sentinel and letting publish.py's fallback take over.
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_S * 1000))

    # Gemma open models are served through the same Gemini API + GEMINI_API_KEY,
    # but unlike the Gemini models they reject system_instruction, tools (Google
    # Search grounding), and response_mime_type JSON mode. So fold the system
    # prompt into the user turn, call plain, and strip any stray markdown fence.
    # This lets eval_models.py A/B a free open model against Gemini with no extra
    # dependency. use_grounding/force_json are intentionally ignored for Gemma.
    if model_name.lower().startswith("gemma"):
        _t0 = time.perf_counter()
        resp = call_with_retry(
            lambda: client.models.generate_content(
                model=model_name,
                contents=f"{system_prompt}\n\n{user_message}",
                config=types.GenerateContentConfig(
                    temperature=0.9,
                    max_output_tokens=8192,
                ),
            )
        )
        record_timing("generate[gemma]", model_name, time.perf_counter() - _t0, resp)
        # resp.text can be empty/None when a candidate is blocked or truncated;
        # accessing .text may even raise. Capture defensively and, under
        # LLM_DEBUG_RAW, log finish_reason + a snippet so eval failures are
        # diagnosable (the model returning prose vs being cut off vs blocked).
        try:
            text = resp.text or ""
        except Exception as e:
            text = ""
            if os.environ.get("LLM_DEBUG_RAW"):
                print(f"  [gemma-debug] resp.text raised: {e}", file=sys.stderr)
        if os.environ.get("LLM_DEBUG_RAW"):
            finish = prompt_fb = None
            try:
                finish = resp.candidates[0].finish_reason
            except Exception:
                pass
            try:
                prompt_fb = resp.prompt_feedback
            except Exception:
                pass
            print(f"  [gemma-debug] finish_reason={finish} prompt_feedback={prompt_fb} "
                  f"text_len={len(text)}", file=sys.stderr)
            print(f"  [gemma-debug] raw head: {text[:600]!r}", file=sys.stderr)
        return _strip_json_fence(text)

    config_kwargs = dict(system_instruction=system_prompt, temperature=0.9)
    if use_grounding:
        # response_mime_type is incompatible with grounding — rely on prompt instruction
        config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    if force_json:
        config_kwargs["response_mime_type"] = "application/json"
    config_kwargs.update(thinking_kwargs(model_name))

    label = f"generate[{'grounded' if use_grounding else 'nogrounding'}]"

    def _call(kwargs):
        return call_with_retry(
            lambda: client.models.generate_content(
                model=model_name,
                contents=user_message,
                config=types.GenerateContentConfig(**kwargs),
            )
        )

    _t0 = time.perf_counter()
    try:
        resp = _call(config_kwargs)
    except Exception as e:
        # thinking_level is newer than some SDK/model combinations and, at
        # "minimal", can 400 on a model that wants thought signatures. That is a
        # config problem, not a content problem, so drop the kwarg and try once
        # more rather than failing the day's generation over an optional knob.
        if "thinking" not in str(e).lower() or "thinking_config" not in config_kwargs:
            raise
        print(f"  warn: thinking_level rejected ({describe_api_error(e)}); "
              f"retrying without it", file=sys.stderr)
        config_kwargs.pop("thinking_config")
        _t0 = time.perf_counter()
        resp = _call(config_kwargs)

    record_timing(label, model_name, time.perf_counter() - _t0, resp,
                  thinking_level_for(model_name) if "thinking_config" in config_kwargs else None)
    return resp.text


PUNCH_UP_INSTRUCTION = """PUNCH-UP MODE. Below is today's complete, fact-checked draft of Dan's daily post as JSON.
This is a rewrite pass for VOICE ONLY: crank up the emotional swings (see Emotional Range) and land
funnier lines (see Humor and Running Bits) while keeping every fact identical. The draft is competent
but flat — your job is to make it sound like Dan actually FELT the game: euphoric, devastated,
exasperated, superstitious, whatever last night earned. Big swings. Make the reader laugh at least once.

HARD CONSTRAINTS:
- Do NOT change, add, or remove any stat, score, record, player name, team name, or event.
  Every number and every name stays exactly as written in the draft.
- Keep the SAME number of morning_brew paragraphs, covering the same stories in the same order.
- Keep the same trend_watch and news_digest entries (same players, same headlines, same URLs);
  you may rewrite only their dans_take text — and those SHOULD get funnier too.
- Do not touch box_scores or schedule.
- All voice rules still apply: PG-13 tier, no em dashes, never start a sentence with a digit.

Return ONLY the complete JSON object with the same keys. No markdown fences, no prose."""


def punch_up_draft(parsed: dict, system_prompt: str, model_name: str) -> dict:
    """
    One extra Gemini call that amplifies emotion/humor in the voice fields only.

    Fact safety is structural, not instructional: the punched output is MERGED
    into the original draft — only headline, morning_brew (same paragraph count
    required), and per-entry dans_take fields are taken from the punch-up.
    box_scores, schedule, trend_watch stats, and news_digest headlines/URLs
    always come from the original draft, so a misbehaving punch-up can't alter
    facts the frontend renders. The safety judge runs on the merged result, so
    any prose-level stat drift still gets caught by rule 7.

    Raises on API/parse failure — caller keeps the original draft.
    """
    user_message = PUNCH_UP_INSTRUCTION + "\n\nDRAFT:\n" + json.dumps(parsed, indent=2)
    raw = call_gemini(system_prompt, user_message, model_name, use_grounding=False, force_json=True)
    punched = json.loads(raw)

    merged = dict(parsed)
    if isinstance(punched.get("headline"), str) and punched["headline"].strip():
        merged["headline"] = punched["headline"]

    pb, ob = punched.get("morning_brew"), parsed.get("morning_brew")
    if (isinstance(pb, list) and isinstance(ob, list) and len(pb) == len(ob)
            and all(isinstance(p, str) and p.strip() for p in pb)):
        merged["morning_brew"] = pb

    # dans_take-only merges: entry identity (player/trend/headline/url) stays original.
    for key in ("trend_watch", "news_digest"):
        pl, ol = punched.get(key), parsed.get(key)
        if isinstance(pl, list) and isinstance(ol, list) and len(pl) == len(ol):
            new_list = []
            for orig, pun in zip(ol, pl):
                entry = dict(orig) if isinstance(orig, dict) else orig
                if (isinstance(entry, dict) and isinstance(pun, dict)
                        and isinstance(pun.get("dans_take"), str) and pun["dans_take"].strip()):
                    entry["dans_take"] = pun["dans_take"]
                new_list.append(entry)
            merged[key] = new_list

    return merged


def build_schedule_from_fetcher(schedule_path: Path) -> list:
    """
    Build the schedule list directly from upcoming_schedule.json instead of
    relying on Gemini, which selectively omits teams (e.g. Celtics in playoffs).

    Returns a list of {date, matchup, time_et} dicts for the next 5 days,
    sorted chronologically. Falls back to [] if the file is missing/broken.
    """
    try:
        data = json.loads(schedule_path.read_text()) if schedule_path.exists() else {}
        games = data.get("games", [])
        if not games:
            return []

        result = []
        for g in games:
            home = g.get("home_team", "")
            away = g.get("away_team", "")
            if not home and not away:
                continue
            matchup = f"{away} at {home}" if away and home else (home or away)
            result.append({
                "date":     g.get("date", ""),
                "matchup":  matchup,
                "time_et":  g.get("time_et", "TBD"),
            })

        # Already sorted by upcoming_schedule.json; just return all games
        return result
    except Exception as e:
        print(f"  warn: could not build schedule from fetcher ({e})", file=sys.stderr)
        return []


def main():
    store_path = Path(os.environ.get("ROLLING_STORE_PATH", DEFAULT_STORE))
    schedule_path = Path(os.environ.get("SCHEDULE_PATH", DEFAULT_SCHEDULE))
    news_path = Path(os.environ.get("NEWS_PATH", DEFAULT_NEWS))
    season_static_path = Path(os.environ.get("SEASON_STATIC_PATH", DEFAULT_SEASON_STATIC))
    season_current_path = Path(os.environ.get("SEASON_CURRENT_PATH", DEFAULT_SEASON_CURRENT))
    output_path = Path(os.environ.get("OUTPUT_PATH", DEFAULT_OUTPUT))
    # LLM_MODEL overrides GEMINI_MODEL for isolated model evals (e.g. testing a
    # Gemma open model) without clobbering the production GEMINI_MODEL knob.
    model_name = os.environ.get("LLM_MODEL") or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

    print(f"generate_rant: model={model_name}")
    print(f"  store:          {store_path}")
    print(f"  season_static:  {season_static_path}")
    print(f"  season_current: {season_current_path}")
    print(f"  output:         {output_path}")

    if not PROMPT_PATH.exists():
        sys.exit(f"error: persona file missing: {PROMPT_PATH}")
    system_prompt = PROMPT_PATH.read_text()

    rolling = load_json(store_path)
    schedule = load_json(schedule_path)
    news = load_json(news_path)
    season_static = load_json(season_static_path)
    season_current = load_json(season_current_path)
    draft_picks_path = Path(os.environ.get("DRAFT_PICKS_PATH", DEFAULT_DRAFT_PICKS))
    draft_picks = load_json(draft_picks_path)
    historical_facts_path = Path(os.environ.get("HISTORICAL_FACTS_PATH", DEFAULT_HISTORICAL_FACTS))
    historical_facts = load_json(historical_facts_path)
    callers_path = Path(os.environ.get("CALLERS_PATH", DEFAULT_CALLERS))
    callers_data = load_json(callers_path)
    grudge_path = Path(os.environ.get("GRUDGE_BOOK_PATH", DEFAULT_GRUDGE_BOOK))
    grudges = load_json(grudge_path)
    roster_path = Path(os.environ.get("ROSTER_PATH", DEFAULT_ROSTER))
    roster = load_json(roster_path)
    season_overrides_path = Path(os.environ.get("SEASON_OVERRIDES_PATH", DEFAULT_SEASON_OVERRIDES))
    season_overrides = load_json(season_overrides_path)
    stories_path = Path(os.environ.get("DAN_STORIES_PATH", DEFAULT_DAN_STORIES))
    stories_data = load_json(stories_path)
    seeds_path = Path(os.environ.get("STORY_SEEDS_PATH", DEFAULT_STORY_SEEDS))
    seeds_data = load_json(seeds_path)
    season_memory = build_season_memory(season_static, season_current)

    archive_dir = Path(os.environ.get("DAN_ARCHIVE_PATH", DEFAULT_ARCHIVE_DIR))
    memory_days = int(os.environ.get("DAN_MEMORY_DAYS", DEFAULT_MEMORY_DAYS))
    recent_output = load_recent_dan_output(archive_dir, memory_days)
    print(f"  archive_dir:    {archive_dir} ({len(recent_output)} prior day(s) loaded)")

    # TODAY_OVERRIDE lets eval fixtures pin "today" to a specific date so
    # freshness-sensitive scenarios (e.g. 5 days post-draft) stay reproducible
    # as the real calendar moves forward. Production leaves this unset.
    today_iso = os.environ.get("TODAY_OVERRIDE") or datetime.now(timezone.utc).date().isoformat()
    todays_callers = select_daily_callers(callers_data, today_iso)
    todays_stories = select_daily_stories(stories_data, today_iso)
    print(f"  today:          {today_iso}")
    print(f"  callers:        {len(todays_callers)} archetype(s) picked")
    print(f"  stories:        {len(todays_stories)} character(s) picked")

    # Pre-compute emotional context, coverage allocation, and slow-day detection
    emotional_context = compute_emotional_context(rolling, grudges)
    coverage_allocation = compute_coverage_allocation(season_overrides, season_current, rolling)
    slow_day = detect_slow_day(rolling, news, schedule, today_iso=today_iso)
    todays_seeds = select_daily_seeds(seeds_data, today_iso) if slow_day else []
    print(f"  emotional:      {len(emotional_context)} team(s) with context")
    print(f"  coverage:       primary={coverage_allocation['primary']}, minimal={coverage_allocation['minimal']}")
    print(f"  slow_day:       {slow_day}")

    user_message = build_user_message(
        rolling, schedule, news, season_memory,
        draft_picks=draft_picks,
        historical_facts=historical_facts,
        recent_output=recent_output,
        callers=todays_callers,
        grudges=grudges,
        roster=roster,
        season_overrides=season_overrides,
        today_iso=today_iso,
        emotional_context=emotional_context,
        coverage_allocation=coverage_allocation,
        slow_day=slow_day,
        stories=todays_stories,
        story_seeds=todays_seeds,
    )

    # DRY_RUN=1 prints the assembled prompt and exits before any LLM call.
    # Used for the look-before-leap pass during risky deploys. Costs nothing,
    # gives a chance to eyeball changes against real production data before
    # generation runs.
    if os.environ.get("DRY_RUN") == "1":
        print("=" * 60, file=sys.stderr)
        print("DRY_RUN=1 — assembled user_message follows on stdout.", file=sys.stderr)
        print("Exiting before Gemini call. No raw_dan_output.json written.", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"--- SYSTEM PROMPT ({len(system_prompt)} chars) ---")
        print(system_prompt)
        print(f"--- USER MESSAGE ({len(user_message)} chars) ---")
        print(user_message)
        return

    # If the safety judge rejected a previous attempt this run, publish.py
    # re-invokes us with CORRECTION_NOTES set. Append the judge's flags to
    # the user message so Dan sees exactly what to fix.
    correction_notes = os.environ.get("CORRECTION_NOTES", "").strip()
    if correction_notes:
        print(f"  correction mode: regenerating with judge feedback")
        # Extract any numbers from the flags so Dan can't re-use them
        import re as _re
        flagged_numbers = sorted(set(_re.findall(r"\b\d+(?:\.\d+)?\b", correction_notes)))
        numbers_warning = ""
        if flagged_numbers:
            numbers_warning = (
                f"- The following specific numbers appeared in your rejected output and "
                f"could NOT be verified in SOURCE_DATA — do NOT use them: "
                f"{', '.join(flagged_numbers)}. If you cannot find an exact number in "
                f"SOURCE_DATA, use qualitative language instead "
                f"('solid outing', 'tough stretch', 'working innings').\n"
            )
        user_message += (
            "\n\n---\n"
            "IMPORTANT — YOUR PREVIOUS RESPONSE WAS REJECTED BY THE SAFETY JUDGE.\n\n"
            "Flags from the judge:\n"
            f"{correction_notes}\n\n"
            "Regenerate your response and fix ALL of the above issues. Hard rules:\n"
            "- Every stat, score, game number, record, and date you cite MUST appear "
            "verbatim in the rolling_7day OR season_memory data provided above. "
            "No exceptions. Search the data before writing any number.\n"
            f"{numbers_warning}"
            "- Do NOT reference games that haven't happened yet, or speculate on "
            "upcoming game numbers/series scores. Use 'tonight', 'later this week', "
            "'coming up' — never 'Game 3' or 'down 2-1' unless those exact figures "
            "are in the data.\n"
            "- Do NOT repeat phrasing, sentences, or player references that appear in "
            "RECENT_DAN_OUTPUT. Read those past outputs and avoid any phrases you used "
            "in the last 5 days.\n"
            "- If you're unsure whether a stat is in the source data, leave it out "
            "and stick to qualitative commentary ('solid night', 'tough stretch').\n"
            "- Keep Dan's voice and the rest of the structure (headline, 3 paragraphs, "
            "trend_watch, etc.) — just fix the flagged issues.\n"
            "---\n"
        )

    # Attempt 1: grounding ON so Dan can pull live storylines
    # If grounding fails (503 exhausted) or returns bad JSON → fall back to attempt 2
    parsed = None
    try:
        raw = call_gemini(system_prompt, user_message, model_name, use_grounding=True)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            print("  warn: grounding response was not valid JSON, retrying without grounding", file=sys.stderr)
    except Exception as e:
        print(f"  warn: grounding call failed ({type(e).__name__}: {describe_api_error(e)}), retrying without grounding", file=sys.stderr)

    # Attempt 2: grounding OFF, force JSON mime type
    if parsed is None:
        try:
            raw = call_gemini(
                system_prompt,
                user_message + "\n\nReturn ONLY a valid JSON object. No markdown, no prose.",
                model_name,
                use_grounding=False,
                force_json=True,
            )
            parsed = json.loads(raw)
        except (json.JSONDecodeError, Exception) as e:
            # Do NOT exit 1 — that short-circuits the workflow and prevents
            # publish.py from running its fallback logic. Instead, write a
            # sentinel so publish.py becomes the single decision point.
            reason = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"error: attempt 2 failed ({reason})", file=sys.stderr)
            print("error: writing _generation_failed sentinel; publish.py will decide fallback", file=sys.stderr)
            sentinel = {
                "_generation_failed": True,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                # Keep timings even on the failure path — a day that burned the
                # full retry budget is exactly the day the latency numbers matter.
                "_timings": CALL_TIMINGS,
            }
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(sentinel, indent=2))
            print(f"  wrote sentinel: {output_path}")
            return

    # Punch-up pass (PUNCH_UP=0 disables): one extra call that amps emotion and
    # humor in voice fields only — see punch_up_draft() for the fact-safety merge.
    # On any failure the original draft ships as-is; the judge gates either way.
    if os.environ.get("PUNCH_UP", "1") != "0":
        try:
            parsed = punch_up_draft(parsed, system_prompt, model_name)
            print("  punch-up pass applied")
        except Exception as e:
            print(f"  warn: punch-up pass failed ({type(e).__name__}: {str(e)[:120]}); keeping original draft", file=sys.stderr)

    # Normalize box_scores schema for consistent frontend rendering
    parsed = normalize_box_scores(parsed)
    # Repair any entries where Gemini left played:false despite fetcher data showing a real game
    parsed = repair_box_scores_from_fetchers(parsed)
    # Always overwrite Gemini's schedule with data directly from upcoming_schedule.json.
    # Gemini selectively drops teams (e.g. Celtics during playoffs) — the fetcher data
    # is authoritative and complete, so we never let Gemini own this field.
    parsed["schedule"] = build_schedule_from_fetcher(schedule_path)

    # Per-call latency/token record. Underscore-prefixed like _quality_warning
    # and _stale, so publish.py's existing marker handling and the frontend both
    # ignore it, but it lands in git history where it can be read back later.
    parsed["_timings"] = CALL_TIMINGS

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(parsed, indent=2))
    print(f"  wrote: {output_path}")
    print(f"  keys:  {list(parsed.keys())}")
    total = sum(t["seconds"] for t in CALL_TIMINGS)
    print(f"  model calls: {len(CALL_TIMINGS)}, {total:.1f}s total")


if __name__ == "__main__":
    main()
