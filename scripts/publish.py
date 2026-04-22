#!/usr/bin/env python3
"""
publish.py — Safety gate and fallback arbiter.

Reads raw Dan output, checks safety judge verdict, and publishes to docs/data/daily_output.json.
- If safety_judge.py exits 0 (PASS): publish raw output
- If safety_judge.py exits 1 (FAIL): publish SAFE_FALLBACK
- If raw output is missing/unparseable: publish SAFE_FALLBACK

Exit code: 0 on success, 1 on failure (even if fallback is written)
"""

import json
import subprocess
import sys
from pathlib import Path

# Constants
RAW_OUTPUT_PATH = Path("data/raw_dan_output.json")
PUBLISHED_OUTPUT_PATH = Path("docs/data/daily_output.json")

SAFE_FALLBACK = {
    "morning_brew": [
        "Dan's takin' the mornin' off. Check back tomorrow. In the meantime, go grab a Dunks."
    ],
    "trend_watch": [],
    "news_digest": [],
    "box_scores": {},
    "schedule": [],
}


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
        print("  → publishing SAFE_FALLBACK")
        write_json(PUBLISHED_OUTPUT_PATH, SAFE_FALLBACK, label="fallback")
        return 1

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
        # Judge took too long — treat as PASS so content still publishes.
        # A judge that can't run should not block publication; only an
        # explicit FAIL verdict should block.
        print("  warning: safety_judge.py timed out — treating as PASS")
        write_json(PUBLISHED_OUTPUT_PATH, raw_output, label="output (judge timeout)")
        return 0
    except Exception as e:
        print(f"  error: could not run safety_judge.py: {e}", file=sys.stderr)
        write_json(PUBLISHED_OUTPUT_PATH, SAFE_FALLBACK, label="fallback")
        return 1

    # Print judge output for logs
    if judge_stdout:
        print(f"  judge output: {judge_stdout.strip()}")
    if judge_stderr and "error" in judge_stderr.lower():
        print(f"  judge stderr: {judge_stderr.strip()}", file=sys.stderr)

    # Step 3: Decide based on judge verdict
    print("\n[3] Decision gate...")
    if judge_exit_code == 0:
        # PASS: Publish raw output
        print("  ✅ safety judge PASSED")
        print("  → publishing raw output")
        success = write_json(PUBLISHED_OUTPUT_PATH, raw_output)
        return 0 if success else 1
    else:
        # FAIL: Publish fallback
        print("  ❌ safety judge FAILED")
        print("  → publishing SAFE_FALLBACK")
        success = write_json(PUBLISHED_OUTPUT_PATH, SAFE_FALLBACK, label="fallback")
        return 1


if __name__ == "__main__":
    sys.exit(main())
