#!/usr/bin/env python3
"""
healthcheck.py — Final validation of published output.

Checks that site/data/daily_output.json exists, is valid JSON, and has all required keys.
- If fallback content is detected, prints warning but exits 0 (fallback is valid)
- Exits 0 on all validations pass, 1 on any failure

This is the final gate before the cron is considered successful.
"""

import json
import sys
from pathlib import Path

# Constants
PUBLISHED_OUTPUT_PATH = Path("site/data/daily_output.json")
REQUIRED_KEYS = {"morning_brew", "trend_watch", "news_digest", "box_scores", "schedule"}
FALLBACK_MARKER = "Dan's takin' the mornin' off"


def main():
    """Validate the published output."""
    print("=" * 60)
    print("healthcheck.py: Publication validation")
    print("=" * 60)

    # Step 1: Check file exists
    print(f"\n[1] Checking {PUBLISHED_OUTPUT_PATH} exists...")
    if not PUBLISHED_OUTPUT_PATH.exists():
        print(f"  ❌ FAILED: {PUBLISHED_OUTPUT_PATH} not found")
        return 1
    print(f"  ✅ file exists")

    # Step 2: Parse JSON
    print("\n[2] Parsing JSON...")
    try:
        with open(PUBLISHED_OUTPUT_PATH) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"  ❌ FAILED: not valid JSON: {e}")
        return 1
    except IOError as e:
        print(f"  ❌ FAILED: could not read file: {e}")
        return 1
    print(f"  ✅ valid JSON")

    # Step 3: Validate required keys
    print("\n[3] Checking required keys...")
    missing_keys = REQUIRED_KEYS - set(data.keys())
    if missing_keys:
        print(f"  ❌ FAILED: missing keys: {missing_keys}")
        return 1
    print(f"  ✅ all required keys present: {sorted(REQUIRED_KEYS)}")

    # Step 4: Check for fallback content
    print("\n[4] Checking for fallback content...")
    is_fallback = (
        isinstance(data.get("morning_brew"), list)
        and len(data["morning_brew"]) > 0
        and FALLBACK_MARKER in data["morning_brew"][0]
    )
    if is_fallback:
        print(
            f"  ⚠️  fallback content detected (safety judge failed or recovery attempted)"
        )
        print(f"  → This is expected if safety_judge.py failed; still valid for publication")
    else:
        print(f"  ✅ real content (not fallback)")

    # Step 5: Summary
    print("\n[5] Summary...")
    print(f"  ✅ {PUBLISHED_OUTPUT_PATH} is valid and complete")
    if is_fallback:
        print(f"  ⚠️  Note: fallback content is in use")

    return 0


if __name__ == "__main__":
    sys.exit(main())
