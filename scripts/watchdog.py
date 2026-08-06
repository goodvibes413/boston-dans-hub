#!/usr/bin/env python3
"""
watchdog.py — answers the only question that actually matters: is Dan's
content live and current right now?

Exists because per-run failure alerting has a structural blind spot. The
Morning Brew workflow opens an issue from a step inside its own job, so it
can only report failures that happen *while that job is running*. A green
run history is no proof of health: a run that publishes stale or fallback
content exits 0 by design, and a run that leaves the file dated to an
earlier day passes just as quietly.

This check reads the published artifact instead of the pipeline that writes
it, so it holds regardless of how many runs fired or how delayed they were.

SCOPE LIMIT — this does not cover runner starvation. When GitHub never
assigns a job a runner (runner_id 0, empty runner_name, no steps array,
cancelled after ~15 min queued — 2026-07-24 and 2026-08-06) nothing in the
repo runs, including this script. Being a small job does not help: queue
admission is about obtaining a runner at all, not how long the job would
hold one, so this waits in the same line as the 25-minute pipeline. Its own
first production run was stranded that way. Closing that gap needs a
monitor outside GitHub Actions — see docs/MONITORING.md.

Checks (in order):
  1. docs/data/daily_output.json exists and parses
  2. required keys present
  3. generated_at is today (UTC), not a previous day
  4. not a stale republish (_stale) or safe fallback (_fallback)
  5. the live site serves content of the same vintage (advisory — a fetch
     failure never fails the run, but serving an older day does)

Exit codes:
  0  healthy
  1  unhealthy — the caller should open/refresh an alert

Env vars:
  OUTPUT_PATH   optional, defaults to docs/data/daily_output.json
  SITE_URL      optional, live daily_output.json URL. Empty string skips
                the live check entirely.
  GITHUB_OUTPUT optional, when set receives status= and summary= for the
                workflow to key its issue text off.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OUTPUT = Path("docs/data/daily_output.json")
DEFAULT_SITE_URL = (
    "https://goodvibes413.github.io/boston-dans-hub/data/daily_output.json"
)
REQUIRED_KEYS = {"morning_brew", "trend_watch", "news_digest", "box_scores", "schedule"}
SITE_TIMEOUT_SECONDS = 20

# The watchdog is itself a scheduled job and inherits the same queue delays as
# the pipeline (observed 85-115 min on the contended afternoon slots). Fire it
# late enough that every publish slot has had its chance, but if a delay pushes
# it past midnight UTC "today" has rolled over and yesterday's publish is the
# correct, healthy answer. Accept the previous day only that early in the day.
LATE_DELAY_GRACE_HOUR_UTC = 6


def _parse_iso(raw: str) -> datetime | None:
    """Parse an ISO timestamp to an aware UTC datetime; None if unparseable."""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _acceptable_dates(now: datetime) -> list:
    """Publish dates considered current for a check running at `now` (UTC)."""
    today = now.date()
    if now.hour < LATE_DELAY_GRACE_HOUR_UTC:
        # Watchdog slipped past midnight — yesterday's publish is still correct.
        from datetime import timedelta

        return [today, today - timedelta(days=1)]
    return [today]


def fetch_live(url: str) -> tuple:
    """
    Fetch the live site's daily_output.json.

    Returns (data, None) on success or (None, reason) on any failure. Never
    raises: the live check is advisory and must not turn a transient CDN
    hiccup into a false alarm about Dan being down.
    """
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=SITE_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"unreachable ({e.reason})"
    except json.JSONDecodeError as e:
        return None, f"invalid JSON ({e})"
    except Exception as e:  # timeouts, DNS, TLS — all advisory
        return None, f"{type(e).__name__}: {e}"


def check(output_path: Path, site_url: str, now: datetime) -> tuple:
    """
    Run every check and return (problems, notes).

    problems -> list of strings; non-empty means unhealthy (exit 1)
    notes    -> list of strings; advisory context for the alert body
    """
    problems: list[str] = []
    notes: list[str] = []

    if not output_path.exists():
        return [f"`{output_path}` does not exist — nothing has ever published."], notes

    try:
        data = json.loads(output_path.read_text())
    except json.JSONDecodeError as e:
        return [f"`{output_path}` is not valid JSON: {e}"], notes
    except OSError as e:
        return [f"`{output_path}` could not be read: {e}"], notes

    missing = REQUIRED_KEYS - set(data)
    if missing:
        problems.append(f"Missing required keys: {sorted(missing)}")

    gen_raw = data.get("generated_at")
    gen_at = _parse_iso(gen_raw) if gen_raw else None
    accepted = _acceptable_dates(now)

    if gen_at is None:
        problems.append(
            f"`generated_at` is missing or unparseable ({gen_raw!r}) — cannot "
            "confirm today's content published."
        )
    else:
        age_hours = (now - gen_at).total_seconds() / 3600.0
        if gen_at.date() not in accepted:
            problems.append(
                f"No publish for today. Newest content is from "
                f"**{gen_at.date()}** ({age_hours:.1f}h old); expected "
                f"{' or '.join(str(d) for d in accepted)}."
            )
        else:
            notes.append(f"Published {gen_at.isoformat()} ({age_hours:.1f}h old).")

    if data.get("_stale"):
        problems.append(
            f"Serving a **stale republish**: {data.get('_stale_reason', 'no reason recorded')}"
        )
    if data.get("_fallback"):
        problems.append(
            f"Serving **SAFE_FALLBACK**: {data.get('_fallback_reason', 'no reason recorded')}"
        )
    if data.get("_regenerated"):
        notes.append("Content passed only after a regeneration attempt.")

    # --- Live site (advisory) ---
    if not site_url:
        notes.append("Live-site check skipped (SITE_URL empty).")
        return problems, notes

    live, reason = fetch_live(site_url)
    if live is None:
        notes.append(f"Live-site check inconclusive: {reason}.")
        return problems, notes

    live_at = _parse_iso(live.get("generated_at", ""))
    if live_at is None:
        notes.append("Live site served content with no readable `generated_at`.")
    elif gen_at is not None and live_at < gen_at:
        behind = (gen_at - live_at).total_seconds() / 3600.0
        problems.append(
            f"Live site is serving older content than the repo: site "
            f"{live_at.isoformat()} vs repo {gen_at.isoformat()} ({behind:.1f}h "
            "behind). GitHub Pages may have failed to deploy."
        )
    else:
        notes.append(f"Live site is current ({live_at.isoformat()}).")

    return problems, notes


def main() -> int:
    output_path = Path(os.environ.get("OUTPUT_PATH", DEFAULT_OUTPUT))
    site_url = os.environ.get("SITE_URL", DEFAULT_SITE_URL)
    now = datetime.now(timezone.utc)

    print("=" * 60)
    print(f"watchdog.py — content health at {now.isoformat()}")
    print("=" * 60)

    problems, notes = check(output_path, site_url, now)

    for n in notes:
        print(f"  note: {n}")
    for p in problems:
        print(f"  PROBLEM: {p}")

    status = "unhealthy" if problems else "healthy"
    summary = (
        "; ".join(problems)
        if problems
        else (notes[0] if notes else "Content is current.")
    )
    print(f"\n  => {status.upper()}")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        # Newlines would break the key=value protocol; the issue body is
        # rebuilt from these single-line fields.
        one_line = summary.replace("\n", " ").replace("\r", " ")
        with Path(gh_output).open("a") as fh:
            fh.write(f"status={status}\n")
            fh.write(f"summary={one_line}\n")
            fh.write(f"details={' | '.join(problems + notes).replace(chr(10), ' ')}\n")
            fh.flush()
            os.fsync(fh.fileno())

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
