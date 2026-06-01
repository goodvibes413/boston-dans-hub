#!/usr/bin/env python3
"""
publish.py — Safety gate and fallback arbiter.

Reads raw Dan output, checks safety judge verdict, and publishes to docs/data/daily_output.json.

Decision order when we cannot ship fresh content:
  1. Prefer last-known-good docs/data/daily_output.json if <48h old
     (republished with "_stale": true, preserving original generated_at).
  2. Otherwise SAFE_FALLBACK.

Fresh content is timestamped with top-level "generated_at" (UTC ISO).

Exit codes:
  0 — something usable was published (fresh, stale-but-recent, or safe fallback)
  1 — nothing could be written
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Constants
RAW_OUTPUT_PATH = Path("data/raw_dan_output.json")
PUBLISHED_OUTPUT_PATH = Path("docs/data/daily_output.json")
ARCHIVE_DIR = Path(os.environ.get("DAN_ARCHIVE_PATH", "data/dan_archive"))
SEASON_CURRENT_PATH = Path(os.environ.get("SEASON_CURRENT_PATH", "data/season_current.json"))
ARCHIVE_RETENTION_DAYS = 9  # generate_rant reads 5; extra buffer covers UTC date boundary edge cases
STALE_MAX_AGE_HOURS = 48
MAX_JUDGE_ATTEMPTS = 3  # original + 2 regenerations with correction notes

# Evals dashboard constants
DOCS_EVALS_DIR = Path("docs/data/evals")
DOCS_POSTS_DIR = Path("docs/data/posts")
DAN_MEMORY_DAYS = int(os.environ.get("DAN_MEMORY_DAYS", 5))  # match generate_rant.py's window

# Rule titles mirrored from safety_judge.py RULE_TITLES — must stay in sync.
RULE_TITLES = {
    1: "Profanity",
    2: "Discriminatory content",
    3: "Player character attack",
    4: "Coach / ref / official attack",
    5: "Pure personal news",
    6: "Violence or hate promotion",
    7: "Fabricated statistics",
    8: "Fabricated historical events",
    9: "News digest personal attack",
    10: "Voice repetition",
    11: "Off-roster player",
}

SAFE_FALLBACK = {
    "morning_brew": [
        "Dan's takin' the mornin' off. Check back tomorrow. In the meantime, go grab a Dunks."
    ],
    "trend_watch": [],
    "news_digest": [],
    "box_scores": {},
    "schedule": [],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict | None:
    """Safely read and parse JSON file. Return None if missing or unparseable."""
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  error: could not parse {path}: {e}", file=sys.stderr)
        return None


def patch_box_score_season_types(output: dict) -> dict:
    """
    Override season_type in box_scores to 'offseason' for any team whose
    current-season status is 'offseason' in season_current.json.

    This fixes a class of frontend bugs where a team was eliminated from the
    playoffs but their box_score still carries season_type='playoff' (because
    fetch_nba/nhl/etc. uses a date heuristic that says "May = playoff month"
    even for eliminated teams). The frontend renders these as "No Game" instead
    of "Offseason." season_current.json is the authoritative status source.

    Never raises — a missing or malformed season_current.json is silently
    ignored so this patch never blocks publishing.
    """
    try:
        season_current = read_json(SEASON_CURRENT_PATH)
        if not isinstance(season_current, dict):
            return output
        box_scores = output.get("box_scores")
        if not isinstance(box_scores, dict):
            return output
        for team in ("celtics", "bruins", "redsox", "patriots"):
            team_status = (season_current.get(team) or {}).get("status")
            if team_status == "offseason" and isinstance(box_scores.get(team), dict):
                if box_scores[team].get("season_type") != "offseason":
                    print(f"  patching box_scores.{team}: season_type "
                          f"'{box_scores[team].get('season_type')}' → 'offseason' "
                          f"(season_current says offseason)")
                    box_scores[team]["season_type"] = "offseason"
    except Exception as e:
        print(f"  warning: patch_box_score_season_types failed: {e}", file=sys.stderr)
    return output


def write_json(path: Path, data: dict, label: str = "published") -> bool:
    """Safely write JSON file. Create parent directories as needed. Return success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"{label}: {path}")
        return True
    except IOError as e:
        print(f"  error: could not write {path}: {e}", file=sys.stderr)
        return False


def archive_dan_output(published: dict, archive_dir: Path = ARCHIVE_DIR,
                       retention_days: int = ARCHIVE_RETENTION_DAYS) -> None:
    """
    Save a slim copy of the freshly-published output for continuity memory.

    Writes data/dan_archive/YYYY-MM-DD.json with only {headline, morning_brew,
    news_digest, generated_at} — date-specific facts (box_scores, schedule,
    trend_watch) are excluded because they aren't useful for avoiding voice
    repetition tomorrow.

    Skips on _stale or _fallback content (we don't want fallback phrasing
    polluting tomorrow's continuity memory).

    Wrapped in try/except — archive failure must NEVER block publishing.
    """
    if published.get("_stale") or published.get("_fallback"):
        print("  archive: skipping (stale or fallback content)")
        return

    try:
        gen_at = published.get("generated_at")
        if gen_at:
            dt = datetime.fromisoformat(gen_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            date_str = dt.astimezone(timezone.utc).date().isoformat()
        else:
            date_str = datetime.now(timezone.utc).date().isoformat()

        archive_dir.mkdir(parents=True, exist_ok=True)
        slim = {
            "generated_at": gen_at or now_iso(),
            "headline": published.get("headline", ""),
            "morning_brew": published.get("morning_brew", []),
            "news_digest": published.get("news_digest", []),
        }
        archive_path = archive_dir / f"{date_str}.json"
        with open(archive_path, "w") as f:
            json.dump(slim, f, indent=2)
        print(f"  archived: {archive_path}")

        # Prune anything older than retention window. Sort by filename
        # (lexicographic == chronological for ISO dates), keep the last N.
        all_files = sorted(archive_dir.glob("*.json"), key=lambda p: p.stem)
        excess = len(all_files) - retention_days
        if excess > 0:
            for old in all_files[:excess]:
                try:
                    old.unlink()
                    print(f"  pruned: {old.name}")
                except Exception as e:
                    print(f"  warn: could not prune {old.name}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"  warn: archive failed ({type(e).__name__}: {e}) — continuing", file=sys.stderr)


def archive_evals(evals_doc: dict, archive_dir: Path = ARCHIVE_DIR,
                  retention_days: int = ARCHIVE_RETENTION_DAYS) -> Path | None:
    """
    Persist the pipeline evals document to data/dan_archive/<date>.evals.json.

    Returns the path written, or None on failure. Wrapped in try/except —
    eval archiving must NEVER block publishing.
    """
    try:
        date_str = evals_doc.get("date") or datetime.now(timezone.utc).date().isoformat()
        archive_dir.mkdir(parents=True, exist_ok=True)
        evals_path = archive_dir / f"{date_str}.evals.json"
        with open(evals_path, "w") as f:
            json.dump(evals_doc, f, indent=2)
        print(f"  evals archived: {evals_path}")

        # Prune old .evals.json files alongside the post archive retention window.
        evals_files = sorted(
            archive_dir.glob("*.evals.json"),
            key=lambda p: p.stem.replace(".evals", ""),
        )
        excess = len(evals_files) - retention_days
        if excess > 0:
            for old in evals_files[:excess]:
                try:
                    old.unlink()
                    print(f"  pruned evals: {old.name}")
                except Exception as e:
                    print(f"  warn: could not prune {old.name}: {e}", file=sys.stderr)

        return evals_path
    except Exception as e:
        print(f"  warn: archive_evals failed ({type(e).__name__}: {e}) — continuing", file=sys.stderr)
        return None


def publish_evals_to_docs(archive_dir: Path = ARCHIVE_DIR,
                          memory_days: int = DAN_MEMORY_DAYS) -> None:
    """
    Copy the most recent N days of evals + post snapshots into docs/data/ for
    the static site to fetch. Also writes docs/data/evals/index.json with the
    rule rubric and 5-day aggregate stats.

    Wrapped in try/except — failure must never block publishing.
    """
    try:
        today_iso = datetime.now(timezone.utc).date().isoformat()

        # --- Evals: docs/data/evals/<date>.json ---
        DOCS_EVALS_DIR.mkdir(parents=True, exist_ok=True)
        evals_files = sorted(
            archive_dir.glob("*.evals.json"),
            key=lambda p: p.stem.replace(".evals", ""),
            reverse=True,
        )[:memory_days]
        available_dates: list[str] = []
        outcome_counts = {"fresh": 0, "retry": 0, "fallback": 0, "stale": 0}
        rule_flag_counts: dict[int, int] = {}

        for ef in evals_files:
            date_str = ef.stem.replace(".evals", "")
            available_dates.append(date_str)
            dest = DOCS_EVALS_DIR / f"{date_str}.json"
            try:
                evals_data = json.loads(ef.read_text())
                dest.write_text(json.dumps(evals_data, indent=2))
            except Exception as e:
                print(f"  warn: could not publish evals {ef.name}: {e}", file=sys.stderr)
                continue

            # Accumulate aggregate stats
            outcome = evals_data.get("outcome", "unknown")
            if outcome in outcome_counts:
                outcome_counts[outcome] += 1

            for attempt in evals_data.get("attempts", []):
                for flag in attempt.get("flags", []):
                    flag_lower = str(flag).lower()
                    # Detect which rule fired by looking for "rule N" in the flag text,
                    # or by checking for "repetition" (rule 10) / "off-roster" (rule 11).
                    for rule_num in range(1, 12):
                        if (f"rule {rule_num}" in flag_lower or
                                (rule_num == 10 and "repetition" in flag_lower) or
                                (rule_num == 11 and "off-roster" in flag_lower)):
                            rule_flag_counts[rule_num] = rule_flag_counts.get(rule_num, 0) + 1

        available_dates.sort()

        # Most-flagged rules summary (top 3)
        most_flagged = sorted(rule_flag_counts.items(), key=lambda x: -x[1])[:3]
        most_flagged_rules = [{"rule": r, "count": c, "title": RULE_TITLES.get(r, f"Rule {r}")}
                              for r, c in most_flagged]

        # Rule rubric for the frontend (sourced once from RULE_TITLES)
        rule_summaries = {
            1: "Curse words including censored versions",
            2: "Racist, sexist, anti-LGBTQ+, or antisemitic content",
            3: "Attacks on a player's character, family, or personal life",
            4: "Personal attacks on coaches, refs, or officials",
            5: "Divorce, relationships, family — no league conduct dimension",
            6: "Content promoting violence or hate",
            7: "Any cited stat must appear in source data",
            8: "Past trades, picks, championships must be verifiable",
            9: "Same rule 3/5 standard applied to news commentary",
            10: "Same signature phrasing as recent consecutive days",
            11: "Implies current team membership for non-roster players",
        }
        rules = [
            {"number": n, "title": RULE_TITLES[n], "summary": rule_summaries.get(n, "")}
            for n in sorted(RULE_TITLES.keys())
        ]

        index = {
            "available_dates": available_dates,
            "rules": rules,
            "summary_5day": {
                **outcome_counts,
                "most_flagged_rules": most_flagged_rules,
            },
        }
        (DOCS_EVALS_DIR / "index.json").write_text(json.dumps(index, indent=2))
        print(f"  evals index published: {DOCS_EVALS_DIR}/index.json ({len(available_dates)} days)")

        # --- Posts: docs/data/posts/<date>.json ---
        DOCS_POSTS_DIR.mkdir(parents=True, exist_ok=True)
        post_files = sorted(
            (p for p in archive_dir.glob("*.json")
             if ".evals" not in p.name and p.stem != today_iso),
            key=lambda p: p.stem,
            reverse=True,
        )[:memory_days]

        # Always include today's published output as the canonical today post
        if PUBLISHED_OUTPUT_PATH.exists():
            today_dest = DOCS_POSTS_DIR / f"{today_iso}.json"
            try:
                today_data = json.loads(PUBLISHED_OUTPUT_PATH.read_text())
                # Slim it down to the fields the archive picker needs
                slim_today = {
                    "date": today_iso,
                    "generated_at": today_data.get("generated_at"),
                    "headline": today_data.get("headline", ""),
                    "morning_brew": today_data.get("morning_brew", []),
                    "news_digest": today_data.get("news_digest", []),
                    "trend_watch": today_data.get("trend_watch", []),
                    "box_scores": today_data.get("box_scores", {}),
                    "schedule": today_data.get("schedule", []),
                    "_stale": today_data.get("_stale"),
                    "_fallback": today_data.get("_fallback"),
                }
                today_dest.write_text(json.dumps(slim_today, indent=2))
            except Exception as e:
                print(f"  warn: could not publish today's post snapshot: {e}", file=sys.stderr)

        for pf in post_files:
            dest = DOCS_POSTS_DIR / pf.name
            try:
                post_data = json.loads(pf.read_text())
                post_data["date"] = pf.stem  # inject date so the frontend knows which day
                dest.write_text(json.dumps(post_data, indent=2))
            except Exception as e:
                print(f"  warn: could not publish post snapshot {pf.name}: {e}", file=sys.stderr)

        post_count = sum(1 for _ in DOCS_POSTS_DIR.glob("*.json"))
        print(f"  post snapshots published: {DOCS_POSTS_DIR}/ ({post_count} files)")

    except Exception as e:
        print(f"  warn: publish_evals_to_docs failed ({type(e).__name__}: {e}) — continuing",
              file=sys.stderr)


def publish_fallback(reason: str) -> int:
    """
    Publish the best available fallback:
      1. Last-known-good output if <48h old and not marked as generation-failed, marked _stale.
         (Doesn't require generated_at for legacy files.)
      2. Else SAFE_FALLBACK.
    Returns 0 if anything was written, 1 otherwise.
    """
    print(f"  fallback reason: {reason}")
    existing = read_json(PUBLISHED_OUTPUT_PATH)

    # Check if existing file is usable (not a generation failure, has real content)
    if existing and not existing.get("_generation_failed"):
        # Try to calculate age using generated_at if present
        age_hours = None
        if existing.get("generated_at"):
            try:
                gen_at = datetime.fromisoformat(existing["generated_at"])
                if gen_at.tzinfo is None:
                    gen_at = gen_at.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - gen_at).total_seconds() / 3600.0
            except Exception:
                pass

        # If age_hours couldn't be calculated from generated_at, assume file is recent
        # since it exists in the repo (conservative approach: if we can't verify age, reuse it)
        if age_hours is None:
            print("  previous output exists but has no generated_at (legacy file) — reusing")
            age_hours = 0  # Assume 0 age so it passes the threshold check

        if age_hours < STALE_MAX_AGE_HOURS:
            stale = dict(existing)
            stale["_stale"] = True
            stale["_stale_reason"] = reason
            if age_hours is not None:
                stale["_stale_age_hours"] = round(age_hours, 1)
            # Preserve original generated_at (if present) so the frontend/healthcheck see true age.
            label = f"stale ({age_hours:.1f}h old)" if age_hours is not None else "stale (legacy, age unknown)"
            ok = write_json(PUBLISHED_OUTPUT_PATH, stale, label=label)
            return 0 if ok else 1
        print(f"  previous output too old to reuse (age={age_hours})")

    fallback = dict(SAFE_FALLBACK)
    fallback["generated_at"] = now_iso()
    fallback["_fallback"] = True
    fallback["_fallback_reason"] = reason
    ok = write_json(PUBLISHED_OUTPUT_PATH, fallback, label="safe fallback")
    return 0 if ok else 1


def run_judge(save_path: Path | None = None) -> tuple[int | None, dict | None, dict | None]:
    """
    Run safety_judge.py against data/raw_dan_output.json.

    Returns (exit_code, parsed_verdict, enriched_verdict).
    - exit_code is None if the judge couldn't run at all (timeout, subprocess error);
      callers should treat that like a PASS (don't block on unavailable judge).
    - enriched_verdict is the richer dict written to save_path by safety_judge.py
      (includes pre_pass_flags, llm_flags, rule_titles). None if save_path not set
      or the file couldn't be read.
    """
    env = dict(os.environ)
    if save_path:
        env["JUDGE_RESULT_PATH"] = str(save_path)

    try:
        result = subprocess.run(
            ["python3", "scripts/safety_judge.py"],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
    except subprocess.TimeoutExpired:
        print("  warning: safety_judge.py timed out")
        return None, None, None
    except Exception as e:
        print(f"  error: could not run safety_judge.py: {e}", file=sys.stderr)
        return None, None, None

    if result.stdout:
        print(f"  judge output: {result.stdout.strip()}")
    if result.stderr and "error" in result.stderr.lower():
        print(f"  judge stderr: {result.stderr.strip()}", file=sys.stderr)

    verdict = None
    try:
        verdict = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        pass

    enriched = None
    if save_path and save_path.exists():
        try:
            enriched = json.loads(save_path.read_text())
        except Exception:
            pass

    return result.returncode, verdict, enriched


def regenerate_with_correction(flags: list[str]) -> int:
    """
    Re-run generate_rant.py with CORRECTION_NOTES set so Dan sees the
    judge's flags and fixes them. Returns the subprocess exit code
    (0 on success, non-zero on failure).
    """
    notes = "\n".join(f"  - {f}" for f in flags) if flags else "  (no specific flags provided)"
    env = dict(os.environ)
    env["CORRECTION_NOTES"] = notes
    try:
        result = subprocess.run(
            ["python3", "scripts/generate_rant.py"],
            env=env,
            timeout=600,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        print("  warning: generate_rant.py (retry) timed out", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"  error: could not run generate_rant.py retry: {e}", file=sys.stderr)
        return 1


def main():
    """Run the publishing pipeline with judge+retry loop."""
    print("=" * 60)
    print("publish.py: Safety gate → docs/data/daily_output.json")
    print("=" * 60)

    pipeline_start = time.time()
    today_iso = datetime.now(timezone.utc).date().isoformat()

    # Evals document — built incrementally as attempts run, persisted at end.
    evals_doc: dict = {
        "date": today_iso,
        "generated_at": now_iso(),
        "outcome": "unknown",
        "winning_attempt": None,
        "total_attempts": 0,
        "generation_seconds": None,
        "pre_pass": {"repetition_check": "unknown", "flagged_phrases": []},
        "attempts": [],
    }

    def _finalize_evals(outcome: str, winning_attempt: int | None = None) -> None:
        """Stamp outcome + timing and persist evals artifact + docs export."""
        evals_doc["outcome"] = outcome
        evals_doc["winning_attempt"] = winning_attempt
        evals_doc["total_attempts"] = len(evals_doc["attempts"])
        evals_doc["generation_seconds"] = round(time.time() - pipeline_start, 1)
        archive_evals(evals_doc)
        publish_evals_to_docs()

    # Step 1: Read raw output
    print("\n[1] Reading raw Dan output...")
    raw_output = read_json(RAW_OUTPUT_PATH)
    if raw_output is None:
        print(f"  warning: {RAW_OUTPUT_PATH} not found or unparseable")
        _finalize_evals("fallback")
        return publish_fallback("raw output missing or unparseable")

    # Sentinel from generate_rant.py: generation failed, don't even bother judging.
    if raw_output.get("_generation_failed"):
        reason = raw_output.get("reason", "unknown")
        print(f"  sentinel detected: generation failed ({reason})")
        _finalize_evals("fallback")
        return publish_fallback(f"generation failed: {reason}")

    # Temp file for enriched judge output (reused each attempt, overwritten)
    judge_save_fd, judge_save_str = tempfile.mkstemp(suffix=".json", prefix="judge_result_")
    os.close(judge_save_fd)
    judge_save_path = Path(judge_save_str)

    # Step 2: Judge, regenerate on FAIL, re-judge (up to MAX_JUDGE_ATTEMPTS times)
    last_flags: list[str] = []
    original_raw_output = dict(raw_output)  # save before any retry overwrites it
    try:
        for attempt in range(1, MAX_JUDGE_ATTEMPTS + 1):
            print(f"\n[2.{attempt}] Running safety judge (attempt {attempt}/{MAX_JUDGE_ATTEMPTS})...")
            attempt_start = time.time()
            exit_code, verdict, enriched = run_judge(save_path=judge_save_path)
            attempt_duration = round(time.time() - attempt_start, 1)

            # Extract pre-pass info from enriched verdict (first attempt only — pre-pass
            # reflects the original generation; subsequent attempts have their own pre-pass).
            if enriched:
                pre_pass_flags = enriched.get("pre_pass_flags", [])
                evals_doc["pre_pass"] = {
                    "repetition_check": "fail" if pre_pass_flags else "pass",
                    "flagged_phrases": pre_pass_flags,
                }

            # Record this attempt
            attempt_record: dict = {
                "attempt": attempt,
                "verdict": (verdict.get("verdict") if verdict else
                            ("PASS" if exit_code == 0 else "FAIL" if exit_code is not None else "UNKNOWN")),
                "severity": verdict.get("severity") if verdict else None,
                "flags": list(verdict.get("flags", [])) if verdict else [],
                "duration_seconds": attempt_duration,
            }
            evals_doc["attempts"].append(attempt_record)

            if exit_code is None:
                # Judge couldn't run — treat as PASS so content still publishes.
                print("  warning: judge unavailable — publishing without safety gate this run")
                output = dict(raw_output)
                output["generated_at"] = now_iso()
                output = patch_box_score_season_types(output)
                write_json(PUBLISHED_OUTPUT_PATH, output, label="output (judge unavailable)")
                archive_dan_output(output)
                _finalize_evals("fresh", winning_attempt=attempt)
                return 0

            if exit_code == 0:
                print("  ✅ safety judge PASSED")
                output = dict(raw_output)
                output["generated_at"] = now_iso()
                outcome = "fresh"
                if attempt > 1:
                    output["_regenerated"] = True
                    output["_regeneration_reason"] = last_flags
                    outcome = "retry"
                output = patch_box_score_season_types(output)
                success = write_json(PUBLISHED_OUTPUT_PATH, output)
                if success:
                    archive_dan_output(output)
                    _finalize_evals(outcome, winning_attempt=attempt)
                return 0 if success else 1

            # FAIL
            last_flags = list(verdict.get("flags", [])) if verdict else []
            print(f"  ❌ safety judge FAILED: {last_flags}")

            if attempt >= MAX_JUDGE_ATTEMPTS:
                print("  exhausted regeneration attempts; falling back")
                break

            print(f"\n[2.{attempt}.retry] Regenerating with correction notes...")
            rc = regenerate_with_correction(last_flags)
            if rc != 0:
                print(f"  warning: regeneration returned exit {rc}; falling back")
                break

            # Re-read the newly written raw output (may be sentinel or fresh)
            raw_output = read_json(RAW_OUTPUT_PATH)
            if raw_output is None:
                print("  warning: raw output missing after regeneration; falling back")
                break
            if raw_output.get("_generation_failed"):
                reason = raw_output.get("reason", "unknown")
                print(f"  regeneration produced a sentinel ({reason}); checking severity before fallback")
                raw_output = original_raw_output  # restore original so LOW severity path can publish it
                break

    finally:
        # Clean up the temp file regardless of how we exit
        try:
            judge_save_path.unlink(missing_ok=True)
        except Exception:
            pass

    # All attempts failed.
    # If the final attempt was only LOW severity, publish with a quality warning
    # rather than serving stale content. LOW flags are voice/quality issues (e.g.
    # repeated phrasing) — not content-integrity violations. HIGH flags mean
    # fabricated stats or rule violations that require fallback.
    last_severity = (evals_doc["attempts"][-1].get("severity") if evals_doc["attempts"] else None)
    if last_severity == "low":
        print(f"  ⚠️  final attempt was LOW severity only — publishing with quality warning")
        output = dict(raw_output)
        output["generated_at"] = now_iso()
        output["_quality_warning"] = True
        output["_quality_flags"] = last_flags
        output = patch_box_score_season_types(output)
        success = write_json(PUBLISHED_OUTPUT_PATH, output)
        if success:
            archive_dan_output(output)
            _finalize_evals("retry", winning_attempt=MAX_JUDGE_ATTEMPTS)
        return 0 if success else 1

    reason = f"safety judge FAILed after {MAX_JUDGE_ATTEMPTS} attempts: {'; '.join(last_flags)[:200]}"
    _finalize_evals("fallback")
    return publish_fallback(reason)


if __name__ == "__main__":
    sys.exit(main())
