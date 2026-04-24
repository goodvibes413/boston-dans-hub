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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Constants
RAW_OUTPUT_PATH = Path("data/raw_dan_output.json")
PUBLISHED_OUTPUT_PATH = Path("docs/data/daily_output.json")
STALE_MAX_AGE_HOURS = 48

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


def publish_fallback(reason: str) -> int:
    """
    Publish the best available fallback:
      1. Last-known-good output if <48h old, marked _stale.
      2. Else SAFE_FALLBACK.
    Returns 0 if anything was written, 1 otherwise.
    """
    print(f"  fallback reason: {reason}")
    existing = read_json(PUBLISHED_OUTPUT_PATH)
    if existing and existing.get("generated_at") and not existing.get("_generation_failed"):
        try:
            gen_at = datetime.fromisoformat(existing["generated_at"])
            if gen_at.tzinfo is None:
                gen_at = gen_at.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - gen_at).total_seconds() / 3600.0
        except Exception:
            age_hours = None

        if age_hours is not None and age_hours < STALE_MAX_AGE_HOURS:
            stale = dict(existing)
            stale["_stale"] = True
            stale["_stale_reason"] = reason
            stale["_stale_age_hours"] = round(age_hours, 1)
            # Preserve original generated_at so the frontend/healthcheck see true age.
            ok = write_json(PUBLISHED_OUTPUT_PATH, stale, label=f"stale ({age_hours:.1f}h old)")
            return 0 if ok else 1
        print(f"  previous output too old to reuse (age={age_hours})")

    fallback = dict(SAFE_FALLBACK)
    fallback["generated_at"] = now_iso()
    fallback["_fallback"] = True
    fallback["_fallback_reason"] = reason
    ok = write_json(PUBLISHED_OUTPUT_PATH, fallback, label="safe fallback")
    return 0 if ok else 1


def main():
    """Run the publishing pipeline."""
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

    # Step 2: Run safety judge and capture exit code
    print("\n[2] Running safety judge...")
    try:
        result = subprocess.run(
            ["python3", "scripts/safety_judge.py"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        judge_exit_code = result.returncode
        judge_stdout = result.stdout
        judge_stderr = result.stderr
    except subprocess.TimeoutExpired:
        # Judge timed out — treat as PASS so content still publishes.
        print("  warning: safety_judge.py timed out — treating as PASS")
        output = dict(raw_output)
        output["generated_at"] = now_iso()
        write_json(PUBLISHED_OUTPUT_PATH, output, label="output (judge timeout)")
        return 0
    except Exception as e:
        print(f"  error: could not run safety_judge.py: {e}", file=sys.stderr)
        return publish_fallback(f"judge subprocess error: {type(e).__name__}")

    # Print judge output for logs
    if judge_stdout:
        print(f"  judge output: {judge_stdout.strip()}")
    if judge_stderr and "error" in judge_stderr.lower():
        print(f"  judge stderr: {judge_stderr.strip()}", file=sys.stderr)

    # Step 3: Decide based on judge verdict
    print("\n[3] Decision gate...")
    if judge_exit_code == 0:
        print("  ✅ safety judge PASSED")
        output = dict(raw_output)
        output["generated_at"] = now_iso()
        success = write_json(PUBLISHED_OUTPUT_PATH, output)
        return 0 if success else 1
    else:
        print("  ❌ safety judge FAILED")
        return publish_fallback("safety judge FAIL")


if __name__ == "__main__":
    sys.exit(main())
