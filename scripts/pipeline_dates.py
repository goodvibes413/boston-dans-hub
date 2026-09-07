#!/usr/bin/env python3
"""pipeline_dates.py — one definition of "which day is this run about".

Every fetcher used to derive its own target from the wall clock
(`datetime.now(timezone.utc) - timedelta(days=1)`), and update_store.py keyed the
rolling store from `datetime.now(timezone.utc)`. Five independent copies meant a
run could not be pinned to a day, and a forced re-run later in the same day was
not reproducible — which is how the 2026-09-07 re-run at 20:42 UTC ended up
publishing that afternoon's game as if it were the previous day's.

Two functions, one env var:

    AS_OF_DATE=YYYY-MM-DD    the run's "today"

`as_of_date()` is that day; `target_game_date()` is the slate being recapped, one
day earlier. Deriving the game day from the run day rather than carrying two env
vars keeps the rolling-store key and the box-score date from drifting apart.

Stdlib only, per the project's dependency rule.
"""

import os
from datetime import date, datetime, timedelta, timezone

AS_OF_ENV = "AS_OF_DATE"


def _parse(raw: str | None) -> date | None:
    """Parse YYYY-MM-DD, returning None for anything unusable.

    A malformed override must never silently become "today" without saying so —
    a wrong date is the whole bug class this module exists to prevent.
    """
    if not raw or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        print(f"  warn: {AS_OF_ENV}={raw!r} is not YYYY-MM-DD — falling back to UTC today")
        return None


def as_of_date() -> date:
    """The run's "today" — the AS_OF_DATE override, else the current UTC date."""
    return _parse(os.environ.get(AS_OF_ENV)) or datetime.now(timezone.utc).date()


def target_game_date() -> date:
    """The day whose games this run recaps: the day before as_of_date()."""
    return as_of_date() - timedelta(days=1)


def as_of_iso() -> str:
    return as_of_date().isoformat()


def target_game_iso() -> str:
    return target_game_date().isoformat()
