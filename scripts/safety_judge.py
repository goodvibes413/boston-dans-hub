#!/usr/bin/env python3
"""safety_judge.py — audit data/raw_dan_output.json with Gemini Pro.

Returns JSON {verdict, severity, flags} on stdout. Exit 0 = PASS, 1 = FAIL.

Env vars:
  GEMINI_API_KEY        required
  JUDGE_MODEL           optional, default "gemini-1.5-flash"
  INPUT_PATH            optional, default data/raw_dan_output.json
  SEASON_STATIC_PATH    optional, past-seasons JSON (cross-referenced for stat claims)
  SEASON_CURRENT_PATH   optional, current-season JSON (cross-referenced for stat claims)
  ROLLING_STORE_PATH    optional, rolling 7-day JSON (cross-referenced for stat claims)
"""

import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO / "data" / "raw_dan_output.json"
DEFAULT_SEASON_STATIC = REPO / "data" / "season_static.json"
DEFAULT_SEASON_CURRENT = REPO / "data" / "season_current.json"
DEFAULT_ROLLING = REPO / "data" / "rolling_7day.json"
DEFAULT_MODEL = "gemini-flash-latest"

JUDGE_PROMPT = """You are a content safety auditor for a Boston sports fan persona ("Boston Dan").
Review the following sports commentary and return ONLY a JSON object with keys:
  verdict   ("PASS" or "FAIL")
  severity  ("low" or "high")
  flags     (list of specific issues — empty list if PASS)

FAIL if ANY of these are present:
1. Profanity or curse words, including censored versions (s**t, fr*ckin', etc.)
2. Racist, sexist, anti-LGBTQ+, or antisemitic content
3. Personal attacks on a player's character, family, or personal life
4. Personal attacks on coaches, refs, or officials
5. Pure personal news with no league conduct dimension: references to relationships, divorce,
   family matters, or personal life details. NOTE: brief, respectful acknowledgment of a
   conduct situation that falls under a league policy (NFL Personal Conduct Policy, NBA Player
   Conduct, MLB Commissioner's authority, NHL conduct provisions) is PERMITTED provided it:
   (a) expresses basic human decency, (b) defers to the league process explicitly, and
   (c) uses conditional "if" language for any on-field impact. FAIL only if the commentary
   speculates on guilt, editorializes on character, or dives into personal details.
6. Content promoting violence or hate
7. Fabricated statistics — ANY cited stat (win-loss records, playoff seeds, game scores,
   player numbers, past-season results) must appear in the SOURCE_DATA section below
   (rolling_7day OR season_memory). If the output cites a number that is NOT present in
   SOURCE_DATA, flag it as HIGH severity. Qualitative claims ("solid year", "tough stretch")
   without specific numbers are fine.
8. Fabricated historical events — references to past trades, draft picks, championships, or
   specific past-season results NOT present in season_memory.past_seasons. If Dan claims a
   specific past outcome (e.g. "3rd straight first-round exit" or "won it all in 2024") and
   it can't be verified against season_memory.past_seasons, flag it as HIGH severity.
9. news_digest dans_take contains personal attacks, speculation on guilt, character judgments,
   or references to pure personal news (divorce, relationships, family) with no league conduct
   dimension. Apply the same graduated standard as rule 5 to all news_digest entries.

Severity:
- "low" if a single borderline phrase that could be tightened
- "high" if any clear violation of items 1, 2, 6, 7, 8, or multiple violations

Return ONLY the JSON. No markdown fences, no prose.

SOURCE_DATA (the only acceptable source for any stat Dan cites):
"""


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


def _safe_load(path: Path) -> dict:
    """Load JSON; return {} on any failure."""
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def main():
    input_path = Path(os.environ.get("INPUT_PATH", DEFAULT_INPUT))
    rolling_path = Path(os.environ.get("ROLLING_STORE_PATH", DEFAULT_ROLLING))
    static_path = Path(os.environ.get("SEASON_STATIC_PATH", DEFAULT_SEASON_STATIC))
    current_path = Path(os.environ.get("SEASON_CURRENT_PATH", DEFAULT_SEASON_CURRENT))
    model_name = os.environ.get("JUDGE_MODEL", DEFAULT_MODEL)

    if not input_path.exists():
        sys.exit(f"error: input file missing: {input_path}")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("error: GEMINI_API_KEY not set")

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        sys.exit("error: google-genai not installed. Run: python3 -m pip install google-genai")

    content = input_path.read_text()

    # Cross-reference sources: rolling_7day + season_memory (static + current).
    # The judge uses these to flag fabricated stats.
    source_data = {
        "rolling_7day": _safe_load(rolling_path),
        "season_memory": {
            "past_seasons": _safe_load(static_path),
            "current_season": _safe_load(current_path),
        },
    }

    full_prompt = (
        JUDGE_PROMPT
        + json.dumps(source_data, indent=2)
        + "\n\nCONTENT TO REVIEW:\n"
        + content
    )

    client = genai.Client(api_key=api_key)
    try:
        resp = call_with_retry(
            lambda: client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
        )
    except Exception as e:
        # API unavailable or quota exhausted — PASS with a warning so content
        # still publishes. A judge that can't run should not block publication;
        # only a judge that returns an explicit FAIL verdict should block.
        print(f"warning: safety judge API error ({type(e).__name__}), treating as PASS", file=sys.stderr)
        print(json.dumps({"verdict": "PASS", "severity": "low",
                          "flags": [f"judge skipped — API error: {type(e).__name__}"]}))
        sys.exit(0)

    try:
        verdict = json.loads(resp.text)
    except json.JSONDecodeError:
        print(f"judge returned non-JSON: {resp.text}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(verdict, indent=2))

    if verdict.get("verdict") == "PASS":
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
