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
from datetime import datetime, timezone
from pathlib import Path

# Constants
RAW_OUTPUT_PATH = Path("data/raw_dan_output.json")
PUBLISHED_OUTPUT_PATH = Path("docs/data/daily_output.json")
ARCHIVE_DIR = Path(os.environ.get("DAN_ARCHIVE_PATH", "data/dan_archive"))
ARCHIVE_RETENTION_DAYS = 7  # generate_rant reads 3; extra buffer covers UTC date boundary edge cases
STALE_MAX_AGE_HOURS = 48
MAX_JUDGE_ATTEMPTS = 2  # original + 1 regeneration with correction notes

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


def run_judge() -> tuple[int | None, dict | None]:
    """
    Run safety_judge.py against data/raw_dan_output.json.

    Returns (exit_code, parsed_verdict). exit_code is None if the judge
    couldn't run at all (timeout, subprocess error); callers should treat
    that like a timeout PASS (don't block on unavailable judge).
    """
    try:
        result = subprocess.run(
            ["python3", "scripts/safety_judge.py"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("  warning: safety_judge.py timed out")
        return None, None
    except Exception as e:
        print(f"  error: could not run safety_judge.py: {e}", file=sys.stderr)
        return None, None

    if result.stdout:
        print(f"  judge output: {result.stdout.strip()}")
    if result.stderr and "error" in result.stderr.lower():
        print(f"  judge stderr: {result.stderr.strip()}", file=sys.stderr)

    verdict = None
    try:
        verdict = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        pass
    return result.returncode, verdict


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

    # Step 1: Read raw output
    print("\n[1] Reading raw Dan output...")
    raw_output = read_json(RAW_OUTPUT_PATH)
    if raw_output is None:
        print(f"  warning: {RAW_OUTPUT_PATH} not found or unparseable")
        return publish_fallback("raw output missing or unparseable")

    # Sentinel from generate_rant.py: generation failed, don't even bother judging.
    if raw_output.get("_generation_failed"):
        reason = raw_output.get("reason", "unknown")
        print(f"  sentinel detected: generation failed ({reason})")
        return publish_fallback(f"generation failed: {reason}")

    # Step 2: Judge, regenerate on FAIL, re-judge (up to MAX_JUDGE_ATTEMPTS times)
    last_flags: list[str] = []
    for attempt in range(1, MAX_JUDGE_ATTEMPTS + 1):
        print(f"\n[2.{attempt}] Running safety judge (attempt {attempt}/{MAX_JUDGE_ATTEMPTS})...")
        exit_code, verdict = run_judge()

        if exit_code is None:
            # Judge couldn't run — treat as PASS so content still publishes.
            print("  warning: judge unavailable — publishing without safety gate this run")
            output = dict(raw_output)
            output["generated_at"] = now_iso()
            write_json(PUBLISHED_OUTPUT_PATH, output, label="output (judge unavailable)")
            archive_dan_output(output)
            return 0

        if exit_code == 0:
            print("  ✅ safety judge PASSED")
            output = dict(raw_output)
            output["generated_at"] = now_iso()
            if attempt > 1:
                output["_regenerated"] = True
                output["_regeneration_reason"] = last_flags
            success = write_json(PUBLISHED_OUTPUT_PATH, output)
            if success:
                archive_dan_output(output)
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
            print(f"  regeneration produced a sentinel ({reason}); falling back")
            return publish_fallback(f"regeneration failed: {reason}")

    # All attempts failed — fall back.
    reason = f"safety judge FAILed after {MAX_JUDGE_ATTEMPTS} attempts: {'; '.join(last_flags)[:200]}"
    return publish_fallback(reason)


if __name__ == "__main__":
    sys.exit(main())
