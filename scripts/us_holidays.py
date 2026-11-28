"""
US national (and Boston-local) holiday calendar for the daily brew.

Why this exists: on Thanksgiving 2026 Dan published a straight Bruins recap with
no mention of the holiday. Nothing in the pipeline told the model the date was
special — TODAY said "2026-11-26 (Thursday)" and that was it — and the persona
prompt forbade personal stories outside SLOW_DAY_MODE, so even a model that
noticed had no permission to be human about it.

This module is the deterministic half of the fix: given a date, say which
holiday (if any) it is, plus yesterday and tomorrow, so generate_rant.py can
inject a HOLIDAY block. The model never has to work out that the fourth
Thursday of November is Thanksgiving, for the same reason it never has to
count weekdays back from an ISO date.

Stdlib only (AGENTS.md: no third-party packages).

Each holiday carries a TONE, which the system prompt's Holiday Mode section
turns into rules:
  festive    — open morning_brew with a short, human holiday beat: plans and
               anticipation on the morning of (phase "morning_of"), the
               fictional Dan-family story on the morning after ("look_back").
  light      — a passing nod or one quip; no story.
  respectful — one sincere sentence; no jokes, no fictional anecdote, and never
               a sports loss framed against the day's meaning.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_TRADITIONS = REPO / "data" / "holiday_traditions.json"

FESTIVE, LIGHT, RESPECTFUL = "festive", "light", "respectful"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th `weekday` (Mon=0) of a month; n=-1 means the last one."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    # last occurrence
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


MON, THU, SUN = 0, 3, 6

# (key, display name, tone, scope, date-rule)
# scope "federal" = US federal holiday; "boston" = Massachusetts/Boston
# tradition; "cultural" = widely observed, not a day off.
_RULES = [
    ("new_years_day", "New Year's Day", FESTIVE, "federal", lambda y: date(y, 1, 1)),
    ("mlk_day", "Martin Luther King Jr. Day", RESPECTFUL, "federal", lambda y: _nth_weekday(y, 1, MON, 3)),
    ("presidents_day", "Presidents' Day", LIGHT, "federal", lambda y: _nth_weekday(y, 2, MON, 3)),
    ("st_patricks_day", "St. Patrick's Day", FESTIVE, "boston", lambda y: date(y, 3, 17)),
    ("patriots_day", "Patriots' Day (Marathon Monday)", FESTIVE, "boston", lambda y: _nth_weekday(y, 4, MON, 3)),
    ("mothers_day", "Mother's Day", LIGHT, "cultural", lambda y: _nth_weekday(y, 5, SUN, 2)),
    ("memorial_day", "Memorial Day", RESPECTFUL, "federal", lambda y: _nth_weekday(y, 5, MON, -1)),
    ("fathers_day", "Father's Day", FESTIVE, "cultural", lambda y: _nth_weekday(y, 6, SUN, 3)),
    ("juneteenth", "Juneteenth", RESPECTFUL, "federal", lambda y: date(y, 6, 19)),
    ("independence_day", "Fourth of July", FESTIVE, "federal", lambda y: date(y, 7, 4)),
    ("labor_day", "Labor Day", LIGHT, "federal", lambda y: _nth_weekday(y, 9, MON, 1)),
    ("indigenous_peoples_day", "Indigenous Peoples' Day", RESPECTFUL, "federal", lambda y: _nth_weekday(y, 10, MON, 2)),
    ("halloween", "Halloween", LIGHT, "cultural", lambda y: date(y, 10, 31)),
    ("veterans_day", "Veterans Day", RESPECTFUL, "federal", lambda y: date(y, 11, 11)),
    ("thanksgiving", "Thanksgiving", FESTIVE, "federal", lambda y: _nth_weekday(y, 11, THU, 4)),
    ("christmas_eve", "Christmas Eve", FESTIVE, "cultural", lambda y: date(y, 12, 24)),
    ("christmas", "Christmas Day", FESTIVE, "federal", lambda y: date(y, 12, 25)),
    ("new_years_eve", "New Year's Eve", FESTIVE, "cultural", lambda y: date(y, 12, 31)),
]


def holiday_on(day: date) -> dict | None:
    """The holiday falling on `day`, or None. At most one per date in this table."""
    for key, name, tone, scope, rule in _RULES:
        if rule(day.year) == day:
            return {"key": key, "name": name, "tone": tone, "scope": scope}
    return None


def load_traditions(path: Path = DEFAULT_TRADITIONS) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as e:
        print(f"  warn: holiday traditions unavailable ({e})", file=sys.stderr)
        return {}


def _holiday_games(historical_facts: dict | None, key: str) -> list[dict]:
    games = (historical_facts or {}).get("holiday_games") or []
    return [g for g in games if isinstance(g, dict) and g.get("holiday") == key]


def compute_holiday_context(today_iso: str, historical_facts: dict | None = None,
                            traditions: dict | None = None) -> dict | None:
    """
    The HOLIDAY block for the user message, or None on an ordinary day.

    Covers three days because the brew straddles them: it posts the morning OF
    the holiday but recaps YESTERDAY (Dec 26 recaps the Christmas Day games),
    and the eve is worth a nod ("see you after the turkey").
    """
    try:
        today = date.fromisoformat(today_iso)
    except ValueError:
        return None
    traditions = traditions or {}

    today_h = holiday_on(today)
    yest_h = holiday_on(today - timedelta(days=1))
    tom_h = holiday_on(today + timedelta(days=1))
    if not (today_h or yest_h or tom_h):
        return None

    # The post goes out at 3 AM, so on the holiday itself nothing has happened
    # yet. The first live eval (2026-11-26) had Uncle Carmine "already snoring
    # on the couch" in a pre-dawn Thanksgiving post. So the morning OF is
    # anticipation, and the morning AFTER carries the dinner-table story.
    def entry_for(h: dict, phase: str, with_flavor: bool) -> dict:
        entry = dict(h)
        entry["phase"] = phase
        if with_flavor:
            flavor = traditions.get(h["key"]) or {}
            if flavor:
                entry["traditions"] = flavor
            games = _holiday_games(historical_facts, h["key"])
            if games:
                entry["holiday_games"] = games
        return entry

    ctx: dict = {}
    if today_h:
        ctx["today"] = entry_for(today_h, "morning_of", True)
    if yest_h:
        # When today is a holiday too (Christmas after Christmas Eve), today
        # leads and yesterday gets a line, so it carries no flavor of its own.
        ctx["yesterday"] = entry_for(yest_h, "look_back", not today_h)
    if tom_h:
        ctx["tomorrow"] = dict(tom_h)
    return ctx
