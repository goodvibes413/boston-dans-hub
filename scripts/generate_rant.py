#!/usr/bin/env python3
"""generate_rant.py — call Gemini with the Boston Dan persona and structured data.

Loads the persona from prompts/boston_dan_system.txt, the rolling 7-day store,
the upcoming schedule, and the latest news. Sends them to Gemini, expects JSON
back, writes data/raw_dan_output.json.

Env vars:
  GEMINI_API_KEY        required
  GEMINI_MODEL          optional, default "gemini-2.5-flash"
  ROLLING_STORE_PATH    optional, lets eval_voice.py swap in a fixture
  SCHEDULE_PATH         optional
  NEWS_PATH             optional
  SEASON_STATIC_PATH    optional, past-seasons JSON (in git)
  SEASON_CURRENT_PATH   optional, daily-fetched current-season JSON
  OUTPUT_PATH           optional, lets eval_voice.py write to evals/runs/...
"""

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO / "prompts" / "boston_dan_system.txt"
DEFAULT_STORE = REPO / "data" / "rolling_7day.json"
DEFAULT_SCHEDULE = REPO / "data" / "upcoming_schedule.json"
DEFAULT_NEWS = REPO / "data" / "latest_news.json"
DEFAULT_SEASON_STATIC = REPO / "data" / "season_static.json"
DEFAULT_SEASON_CURRENT = REPO / "data" / "season_current.json"
DEFAULT_OUTPUT = REPO / "data" / "raw_dan_output.json"

TEAM_KEYS = ("celtics", "bruins", "redsox", "patriots")

DEFAULT_MODEL = "gemini-2.5-flash"


def call_with_retry(fn, max_retries=4):
    """
    Call fn() with exponential backoff retry on 503/429 errors.

    On 503 UNAVAILABLE: wait 5s, 15s, 30s, 60s (up to ~2 min — covers demand spikes)
    On 429 QUOTA_EXCEEDED: parse retryDelay from error, wait that duration
    On other errors: fail immediately
    """
    backoff_delays = [5, 15, 30, 60]

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
                raise

            if attempt >= max_retries:
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

            print(f"  retry: {status_code}, waiting {wait_sec}s...", file=sys.stderr)
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

        # If the team has a nested games array (Red Sox format), take the first game
        if isinstance(team_data.get("games"), list) and len(team_data["games"]) > 0:
            game = team_data["games"][0]
            normalized[team_key] = {
                "sport": sport,
                "home_team": "Boston Red Sox" if game.get("home") else game.get("opponent", "Unknown"),
                "away_team": game.get("opponent", "Unknown") if game.get("home") else "Boston Red Sox",
                "home_score": game.get("redsox_score") if game.get("home") else game.get("opponent_score"),
                "away_score": game.get("opponent_score") if game.get("home") else game.get("redsox_score"),
                "game_date": team_data.get("game_date", ""),
                "played": team_data.get("played", False),
                "season_type": team_data.get("season_type", "unknown"),
            }
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


def build_user_message(rolling, schedule, news, season_memory) -> str:
    return (
        "Here is the structured data for the last 7 days of Boston sports.\n"
        "Use ONLY the numbers and facts in this data — never invent stats.\n\n"
        "ROLLING_7DAY:\n"
        f"{json.dumps(rolling, indent=2)}\n\n"
        "UPCOMING_SCHEDULE:\n"
        f"{json.dumps(schedule, indent=2)}\n\n"
        "LATEST_NEWS:\n"
        f"{json.dumps(news, indent=2)}\n\n"
        "SEASON_MEMORY:\n"
        f"{json.dumps(season_memory, indent=2)}\n\n"
        "Generate Boston Dan's Hub JSON output. Return ONLY the JSON object, "
        "no prose, no markdown fences. Keys: headline (8–12 word punchy newspaper-style headline in Dan's voice), "
        "morning_brew (3 paragraphs), "
        "trend_watch, box_scores, schedule (next 3 days)."
    )


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

    client = genai.Client(api_key=api_key)

    config_kwargs = dict(system_instruction=system_prompt, temperature=0.9)
    if use_grounding:
        # response_mime_type is incompatible with grounding — rely on prompt instruction
        config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    if force_json:
        config_kwargs["response_mime_type"] = "application/json"

    resp = call_with_retry(
        lambda: client.models.generate_content(
            model=model_name,
            contents=user_message,
            config=types.GenerateContentConfig(**config_kwargs),
        )
    )
    return resp.text


def main():
    store_path = Path(os.environ.get("ROLLING_STORE_PATH", DEFAULT_STORE))
    schedule_path = Path(os.environ.get("SCHEDULE_PATH", DEFAULT_SCHEDULE))
    news_path = Path(os.environ.get("NEWS_PATH", DEFAULT_NEWS))
    season_static_path = Path(os.environ.get("SEASON_STATIC_PATH", DEFAULT_SEASON_STATIC))
    season_current_path = Path(os.environ.get("SEASON_CURRENT_PATH", DEFAULT_SEASON_CURRENT))
    output_path = Path(os.environ.get("OUTPUT_PATH", DEFAULT_OUTPUT))
    model_name = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)

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
    season_memory = build_season_memory(season_static, season_current)

    user_message = build_user_message(rolling, schedule, news, season_memory)

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
        print(f"  warn: grounding call failed ({type(e).__name__}), retrying without grounding", file=sys.stderr)

    # Attempt 2: grounding OFF, force JSON mime type
    if parsed is None:
        raw = call_gemini(
            system_prompt,
            user_message + "\n\nReturn ONLY a valid JSON object. No markdown, no prose.",
            model_name,
            use_grounding=False,
            force_json=True,
        )
        parsed = json.loads(raw)

    # Normalize box_scores schema for consistent frontend rendering
    parsed = normalize_box_scores(parsed)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(parsed, indent=2))
    print(f"  wrote: {output_path}")
    print(f"  keys:  {list(parsed.keys())}")


if __name__ == "__main__":
    main()
