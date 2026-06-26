#!/usr/bin/env python3
"""
detect_roster_moves.py — scan latest_news.json for trade / signing headlines and
flag when data/key_players.json may need a human refresh.

The curated key_players.json (the "expectation" axis of player salience) only
stays accurate if it's updated after a marquee player changes teams. This script
is the nudge: it scans today's Boston sports news for move-signal keywords and,
when it finds any, signals the workflow to open a `curate-key-players` GitHub
issue reminding the operator to review the file.

Read-only. NEVER fails the pipeline (always exits 0). Prints matched headlines to
stdout (for the issue body) and writes `found=true|false` to GITHUB_OUTPUT when
running in GitHub Actions.

Env:
  LATEST_NEWS_PATH   override the default data/latest_news.json (used by tests)
"""

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_NEWS = REPO / "data" / "latest_news.json"

# Strong move signals, matched on word boundaries. Kept conservative so routine
# game-recap headlines don't trigger a nudge — false positives are cheap (the
# operator just closes the issue) but daily noise erodes trust in the signal.
KEYWORDS = [
    r"trades?", r"traded", r"trading",
    r"acquires?", r"acquired", r"acquiring",
    r"signs?", r"signed", r"signing",
    r"re-?signs?", r"re-?signed",
    r"agrees?\s+to\s+(?:a\s+)?(?:deal|contract|terms)",
    r"waives?", r"waived",
    r"releases?", r"released",
    r"claimed\s+off\s+waivers",
    r"extension", r"blockbuster",
]
PATTERN = re.compile(r"\b(?:" + "|".join(KEYWORDS) + r")\b", re.IGNORECASE)


def _set_output(found: bool) -> None:
    """Write the `found` flag to GITHUB_OUTPUT if running under GitHub Actions."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    try:
        with open(out, "a") as f:
            f.write(f"found={'true' if found else 'false'}\n")
    except IOError:
        pass


def main() -> int:
    news_path = Path(os.environ.get("LATEST_NEWS_PATH", DEFAULT_NEWS))
    try:
        data = json.loads(news_path.read_text())
    except (json.JSONDecodeError, IOError, FileNotFoundError):
        print(f"  no readable news at {news_path}; nothing to check")
        _set_output(False)
        return 0

    matches = []
    for a in data.get("articles", []) or []:
        if not isinstance(a, dict):
            continue
        headline = (a.get("headline") or "").strip()
        if headline and PATTERN.search(headline):
            matches.append((a.get("team", "?"), headline, a.get("url", "")))

    if matches:
        print(f"Detected {len(matches)} possible roster move(s):")
        for team, headline, url in matches:
            print(f"  [{team}] {headline}  {url}".rstrip())
    else:
        print("No trade/signing headlines detected.")

    _set_output(bool(matches))
    return 0


if __name__ == "__main__":
    sys.exit(main())
