#!/usr/bin/env python3
"""Unit tests for the deterministic parts of the daily pipeline.

Stdlib unittest only (project convention: no third-party packages beyond
google-genai, and these tests never touch the network or the API).

The _extract_team_games suite exists because all three rolling-store readers
shipped broken for a month in June 2026 — they assumed rolling[team]["games"]
when the real shape is rolling["days"][i][team]["boxscore"]. Ten lines of test
would have caught it. Now they do.

Run: python3 -m unittest discover -s tests -v
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_rant  # noqa: E402
import fetch_season_memory  # noqa: E402


def make_rolling(date_str, team="redsox", played=True, games=None):
    """Build a rolling_7day store shaped exactly like update_store.py writes it."""
    boxscore = {"game_date": date_str, "played": played}
    if games is not None:
        boxscore["games"] = games
    return {"days": [{"date": date_str, team: {"boxscore": boxscore}}]}


class TestExtractTeamGames(unittest.TestCase):
    def test_extracts_game_from_real_structure(self):
        rolling = make_rolling("2026-06-10", games=[
            {"home_team": "Tampa Bay Rays", "away_team": "Boston Red Sox",
             "home_score": 4, "away_score": 3}])
        games = generate_rant._extract_team_games(rolling, "redsox")
        self.assertEqual(len(games), 1)
        self.assertTrue(games[0]["played"])
        self.assertEqual(games[0]["game_date"], "2026-06-10")

    def test_no_game_when_played_false(self):
        rolling = make_rolling("2026-06-10", played=False)
        self.assertEqual(generate_rant._extract_team_games(rolling, "redsox"), [])

    def test_handles_missing_and_malformed(self):
        self.assertEqual(generate_rant._extract_team_games(None, "redsox"), [])
        self.assertEqual(generate_rant._extract_team_games({}, "redsox"), [])
        self.assertEqual(generate_rant._extract_team_games({"days": "nope"}, "redsox"), [])
        self.assertEqual(generate_rant._extract_team_games({"days": [None]}, "redsox"), [])


def make_mlb_game(redsox_score, opponent_score, game_number=1, home=True,
                  opponent="Tampa Bay Rays"):
    """A game dict shaped exactly like fetch_mlb.py's parse_game writes it —
    note: no home_team/home_score keys, and no per-game 'played' key."""
    return {"game_number": game_number, "home": home, "redsox_score": redsox_score,
            "opponent": opponent, "opponent_score": opponent_score}


class TestExtractFlatFormatKeepsScores(unittest.TestCase):
    """Celtics/Bruins/Patriots boxscores are flat (no games array). The scores
    must survive extraction — they were being stripped to {played, game_date}."""

    def test_flat_boxscore_scores_survive(self):
        rolling = {"days": [{"date": "2026-06-10", "celtics": {"boxscore": {
            "game_date": "2026-06-10", "played": True, "home": True,
            "celtics_score": 120, "opponent": "New York Knicks",
            "opponent_score": 98}}}]}
        games = generate_rant._extract_team_games(rolling, "celtics")
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["celtics_score"], 120)
        self.assertEqual(games[0]["opponent_score"], 98)


class TestEmotionalContext(unittest.TestCase):
    """compute_emotional_context shipped reading home_score/home_team keys that
    no fetcher produces — every real game read as a heartbroken 0-0 loss. On
    the 2026-07-17 doubleheader sweep that told the model 'loss, L2' against
    raw data showing two wins, and it wrote 'split' (judge FAIL, stale publish).
    These tests pin the real fetcher schema."""

    def test_mlb_single_win_is_a_win(self):
        rolling = make_rolling("2026-07-10", games=[make_mlb_game(6, 2)])
        ctx = generate_rant.compute_emotional_context(rolling, None)
        self.assertEqual(ctx["redsox"]["last_result"], "win")
        self.assertEqual(ctx["redsox"]["margin"], 4)
        self.assertEqual(ctx["redsox"]["streak"], "W1")

    def test_mlb_doubleheader_sweep_not_a_split(self):
        rolling = make_rolling("2026-07-17", games=[
            make_mlb_game(10, 0, game_number=1),
            make_mlb_game(5, 3, game_number=2),
        ])
        ctx = generate_rant.compute_emotional_context(rolling, None)
        rs = ctx["redsox"]
        self.assertIn("sweep", rs["last_result"])
        self.assertNotIn("split", rs["last_result"])
        self.assertTrue(rs["doubleheader"])
        self.assertEqual(rs["streak"], "W2")
        self.assertEqual(
            [g["result"] for g in rs["doubleheader_result"]], ["W", "W"])
        # blowout margin (10-0) should drive the register, not the close nightcap
        self.assertIn("euphoric", rs["emotional_register"])

    def test_mlb_doubleheader_split_detected(self):
        rolling = make_rolling("2026-07-17", games=[
            make_mlb_game(2, 1, game_number=1),
            make_mlb_game(3, 7, game_number=2),
        ])
        ctx = generate_rant.compute_emotional_context(rolling, None)
        self.assertIn("split", ctx["redsox"]["last_result"])

    def test_celtics_flat_format_blowout_win(self):
        rolling = {"days": [{"date": "2026-06-10", "celtics": {"boxscore": {
            "game_date": "2026-06-10", "played": True, "home": True,
            "celtics_score": 120, "opponent": "New York Knicks",
            "opponent_score": 98}}}]}
        ctx = generate_rant.compute_emotional_context(rolling, None)
        self.assertEqual(ctx["celtics"]["last_result"], "win")
        self.assertIn("euphoric", ctx["celtics"]["emotional_register"])

    def test_fixture_format_still_supported(self):
        rolling = make_rolling("2026-06-10", games=[
            {"home_team": "Tampa Bay Rays", "away_team": "Boston Red Sox",
             "home_score": 4, "away_score": 3}])
        ctx = generate_rant.compute_emotional_context(rolling, None)
        self.assertEqual(ctx["redsox"]["last_result"], "loss")
        self.assertEqual(ctx["redsox"]["margin"], 1)

    def test_score_free_entry_skipped_not_zero_zero(self):
        rolling = make_rolling("2026-06-10", played=True)  # no games, no scores
        ctx = generate_rant.compute_emotional_context(rolling, None)
        self.assertNotIn("redsox", ctx)


class TestNormalizeBoxScoresDoubleheader(unittest.TestCase):
    def test_both_games_survive_normalization(self):
        data = {"box_scores": {"redsox": {
            "game_date": "2026-07-17", "played": True, "season_type": "regular",
            "doubleheader": True,
            "games": [make_mlb_game(10, 0, game_number=1),
                      make_mlb_game(5, 3, game_number=2)],
        }}}
        out = generate_rant.normalize_box_scores(data)
        rs = out["box_scores"]["redsox"]
        self.assertTrue(rs["doubleheader"])
        self.assertEqual(len(rs["games"]), 2)
        self.assertEqual(rs["games"][1]["home_score"], 5)
        # top-level fields still present for old consumers
        self.assertEqual(rs["home_score"], 10)

    def test_single_game_keeps_flat_schema(self):
        data = {"box_scores": {"redsox": {
            "game_date": "2026-07-10", "played": True, "season_type": "regular",
            "games": [make_mlb_game(6, 2)],
        }}}
        out = generate_rant.normalize_box_scores(data)
        rs = out["box_scores"]["redsox"]
        self.assertNotIn("games", rs)
        self.assertEqual(rs["home_score"], 6)


class TestRepairBoxScoresDoubleheader(unittest.TestCase):
    """repair_box_scores_from_fetchers gated purely on 'Gemini already produced
    scores', but on a doubleheader Gemini emits ONE flat game while the fetcher
    holds both. The gate skipped repair and the second game was discarded — the
    2026-07-22 Orioles twin bill published as a lone 1-5 loss."""

    def setUp(self):
        import json as _json
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        (root / "data").mkdir()
        # Fetcher saw a doubleheader: Sox lost 1-5, then won 4-2.
        (root / "data" / "redsox_boxscore.json").write_text(_json.dumps({
            "game_date": "2026-07-22", "played": True, "season_type": "regular",
            "doubleheader": True,
            "games": [make_mlb_game(1, 5, game_number=1, opponent="Baltimore Orioles"),
                      make_mlb_game(4, 2, game_number=2, opponent="Baltimore Orioles")],
        }))
        self._orig_repo = generate_rant.REPO
        generate_rant.REPO = root
        self.addCleanup(lambda: setattr(generate_rant, "REPO", self._orig_repo))

    def test_second_game_recovered_when_model_flattened_it(self):
        # What Gemini emitted: a single flat game, scores populated.
        data = {"box_scores": {"redsox": {
            "sport": "MLB", "home_team": "Boston Red Sox",
            "away_team": "Baltimore Orioles", "home_score": 1, "away_score": 5,
            "game_date": "2026-07-22", "played": True, "season_type": "regular",
        }}}
        out = generate_rant.repair_box_scores_from_fetchers(data)
        rs = out["box_scores"]["redsox"]
        self.assertTrue(rs.get("doubleheader"))
        self.assertEqual(len(rs.get("games", [])), 2)
        self.assertEqual([g["game_number"] for g in rs["games"]], [1, 2])
        # game 2 (the win) is the one that used to vanish
        self.assertEqual(rs["games"][1]["home_score"], 4)
        self.assertEqual(rs["games"][1]["away_score"], 2)

    def test_complete_single_game_still_left_alone(self):
        import json as _json
        (Path(self._tmp.name) / "data" / "redsox_boxscore.json").write_text(_json.dumps({
            "game_date": "2026-07-10", "played": True, "season_type": "regular",
            "games": [make_mlb_game(6, 2)],
        }))
        original = {"sport": "MLB", "home_team": "Boston Red Sox",
                    "away_team": "Tampa Bay Rays", "home_score": 6, "away_score": 2,
                    "game_date": "2026-07-10", "played": True, "season_type": "regular"}
        out = generate_rant.repair_box_scores_from_fetchers(
            {"box_scores": {"redsox": dict(original)}})
        self.assertEqual(out["box_scores"]["redsox"], original)


class TestDetectSlowDay(unittest.TestCase):
    def test_not_slow_when_team_played_yesterday(self):
        rolling = make_rolling("2026-06-10", games=[{"home_score": 4, "away_score": 3}])
        self.assertFalse(generate_rant.detect_slow_day(rolling, [], [], today_iso="2026-06-11"))

    def test_slow_when_no_games_and_no_news(self):
        rolling = make_rolling("2026-06-10", played=False)
        self.assertTrue(generate_rant.detect_slow_day(rolling, [], [], today_iso="2026-06-11"))

    def test_game_two_days_ago_does_not_block_slow_day(self):
        rolling = make_rolling("2026-06-10", games=[{"home_score": 1, "away_score": 0}])
        self.assertTrue(generate_rant.detect_slow_day(rolling, [], [], today_iso="2026-06-12"))

    def test_news_blocks_slow_day(self):
        rolling = make_rolling("2026-06-10", played=False)
        news = [{"headline": "a"}, {"headline": "b"}]
        self.assertFalse(generate_rant.detect_slow_day(rolling, news, [], today_iso="2026-06-11"))


class TestDraftFreshness(unittest.TestCase):
    def test_windows(self):
        from datetime import date
        today = date(2026, 7, 13)
        cases = [
            ("2026-07-13", "active"), ("2026-07-12", "fresh"),
            ("2026-07-08", "aging"), ("2026-07-01", "stale"),
        ]
        for last_active, expected in cases:
            freshness, _ = generate_rant.compute_draft_freshness(last_active, today)
            self.assertEqual(freshness, expected, f"last_active={last_active}")

    def test_no_draft(self):
        from datetime import date
        self.assertEqual(
            generate_rant.compute_draft_freshness(None, date(2026, 7, 13)), (None, None))


class TestOverridesExpiry(unittest.TestCase):
    def _overrides(self, expires):
        entry = {"sport": "NHL", "eliminated_from": "2026 NHL Playoffs",
                 "eliminated_date": "2026-05-01", "eliminated_by": "Buffalo Sabres",
                 "series_result": "Lost 4-2", "season_over_note": "Season over."}
        if expires:
            entry["expires"] = expires
        return {"eliminations": {"bruins": entry}}

    def test_active_entry_renders(self):
        block = generate_rant._build_overrides_block(
            self._overrides("2026-09-15"), today_iso="2026-07-07")
        self.assertIn("BRUINS", block)

    def test_expired_entry_skipped(self):
        block = generate_rant._build_overrides_block(
            self._overrides("2026-09-15"), today_iso="2026-10-01")
        self.assertEqual(block, "")

    def test_no_expiry_still_renders(self):
        block = generate_rant._build_overrides_block(
            self._overrides(None), today_iso="2026-10-01")
        self.assertIn("BRUINS", block)


class TestPunchUpMerge(unittest.TestCase):
    """punch_up_draft must only ever take voice fields from the punched output."""

    DRAFT = {
        "headline": "Original headline here",
        "morning_brew": ["p1 original", "p2 original"],
        "trend_watch": [{"category": "Heater", "player": "Jarren Duran",
                         "trend": "3 hits", "dans_take": "dry take"}],
        "news_digest": [{"headline": "Real headline", "url": "https://espn.com/x",
                         "dans_take": "dry take"}],
        "box_scores": {"redsox": {"home_score": 4, "away_score": 3, "played": True}},
        "schedule": [{"date": "2026-07-08", "matchup": "Sox at Cubs"}],
    }

    def _merge_with(self, punched):
        import json as _json
        original = generate_rant.call_gemini
        generate_rant.call_gemini = lambda *a, **k: _json.dumps(punched)
        try:
            return generate_rant.punch_up_draft(dict(self.DRAFT), "sys", "model")
        finally:
            generate_rant.call_gemini = original

    def test_voice_fields_merge_and_facts_locked(self):
        punched = dict(self.DRAFT)
        punched = {
            "headline": "PUNCHED headline",
            "morning_brew": ["p1 funny", "p2 funny"],
            "trend_watch": [{"category": "HACKED", "player": "Nobody",
                             "trend": "fake", "dans_take": "funny take"}],
            "news_digest": [{"headline": "FAKE", "url": "javascript:alert(1)",
                             "dans_take": "funny take"}],
            "box_scores": {"redsox": {"home_score": 99, "away_score": 0, "played": True}},
            "schedule": [],
        }
        merged = self._merge_with(punched)
        self.assertEqual(merged["headline"], "PUNCHED headline")
        self.assertEqual(merged["morning_brew"], ["p1 funny", "p2 funny"])
        # dans_take taken, identity fields kept from the original
        self.assertEqual(merged["trend_watch"][0]["dans_take"], "funny take")
        self.assertEqual(merged["trend_watch"][0]["player"], "Jarren Duran")
        self.assertEqual(merged["news_digest"][0]["url"], "https://espn.com/x")
        # facts structurally untouchable
        self.assertEqual(merged["box_scores"], self.DRAFT["box_scores"])
        self.assertEqual(merged["schedule"], self.DRAFT["schedule"])

    def test_paragraph_count_mismatch_keeps_original_brew(self):
        punched = {"headline": "x", "morning_brew": ["only one paragraph"]}
        merged = self._merge_with(punched)
        self.assertEqual(merged["morning_brew"], self.DRAFT["morning_brew"])


class TestWatchdog(unittest.TestCase):
    """The watchdog is the only health signal that survives the pipeline never
    running — a job GitHub leaves unassigned executes no steps, so no in-job
    alerting can fire. These pin the states it must call unhealthy."""

    def setUp(self):
        import watchdog
        self.watchdog = watchdog
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "daily_output.json"
        self.now = datetime(2026, 8, 6, 19, 0, tzinfo=timezone.utc)

    def _write(self, **overrides):
        import json as _json
        payload = {
            "morning_brew": ["p"], "trend_watch": [], "news_digest": [],
            "box_scores": {}, "schedule": [],
            "generated_at": "2026-08-06T10:45:30+00:00",
        }
        payload.update(overrides)
        self.path.write_text(_json.dumps(payload))

    def _check(self):
        # site_url="" keeps the live fetch out of unit tests
        return self.watchdog.check(self.path, "", self.now)

    def test_healthy_today(self):
        self._write()
        problems, _ = self._check()
        self.assertEqual(problems, [])

    def test_missing_file_is_unhealthy(self):
        problems, _ = self._check()
        self.assertEqual(len(problems), 1)
        self.assertIn("does not exist", problems[0])

    def test_yesterdays_content_is_unhealthy(self):
        """The blind spot that motivated this: pipeline never ran, no failure
        issue exists, and the newest content is from a previous day."""
        self._write(generated_at="2026-08-05T10:45:30+00:00")
        problems, _ = self._check()
        self.assertTrue(any("No publish for today" in p for p in problems))

    def test_stale_republish_is_unhealthy(self):
        self._write(_stale=True, _stale_reason="judge FAILed after 3 attempts")
        problems, _ = self._check()
        self.assertTrue(any("stale republish" in p for p in problems))

    def test_fallback_is_unhealthy(self):
        self._write(_fallback=True)
        problems, _ = self._check()
        self.assertTrue(any("SAFE_FALLBACK" in p for p in problems))

    def test_missing_keys_flagged(self):
        import json as _json
        self.path.write_text(_json.dumps({"generated_at": "2026-08-06T10:45:30+00:00"}))
        problems, _ = self._check()
        self.assertTrue(any("Missing required keys" in p for p in problems))

    def test_malformed_json_is_unhealthy(self):
        self.path.write_text("{not json")
        problems, _ = self._check()
        self.assertTrue(any("not valid JSON" in p for p in problems))

    def test_regenerated_is_healthy_but_noted(self):
        self._write(_regenerated=True)
        problems, notes = self._check()
        self.assertEqual(problems, [])
        self.assertTrue(any("regeneration" in n for n in notes))

    def test_past_midnight_grace_accepts_yesterday(self):
        """A watchdog delayed past midnight UTC must not false-alarm on the
        publish that is still correctly the newest one."""
        self._write(generated_at="2026-08-06T10:45:30+00:00")
        early = datetime(2026, 8, 7, 2, 0, tzinfo=timezone.utc)
        problems, _ = self.watchdog.check(self.path, "", early)
        self.assertEqual(problems, [])

    def test_after_grace_hour_rejects_yesterday(self):
        self._write(generated_at="2026-08-06T10:45:30+00:00")
        later = datetime(2026, 8, 7, 19, 0, tzinfo=timezone.utc)
        problems, _ = self.watchdog.check(self.path, "", later)
        self.assertTrue(any("No publish for today" in p for p in problems))


class TestRuleTitlesSync(unittest.TestCase):
    def test_publish_imports_judge_rule_titles(self):
        import safety_judge
        import publish
        self.assertIs(publish.RULE_TITLES, safety_judge.RULE_TITLES)
        self.assertGreaterEqual(max(safety_judge.RULE_TITLES), 14)


# Workflow job timeout from .github/workflows/morning_brew.yml (timeout-minutes: 25).
# Mirrored here rather than parsed so a drop in the workflow file is a visible,
# reviewed edit in both places.
JOB_TIMEOUT_S = 25 * 60
# Wall-clock the job needs outside the model calls: 9 fetchers, update_store,
# publish, healthcheck, and the git pull/push retry loop. Observed floor is
# ~140s for a whole run; 300s is that with room to spare.
NON_MODEL_ALLOWANCE_S = 300


class TestRetryBudget(unittest.TestCase):
    """
    The 2026-07-01 incident was not a slow model — it was the job being
    force-cancelled at the 25-min timeout *before* publish.py could write a
    sentinel and pick a fallback, so the day produced no commit at all.

    That makes "worst-case retry time fits inside the job timeout, with room
    for the sentinel path to still run" a real invariant, not a style
    preference. These tests fail if someone lengthens the retry ladder, raises
    MAX_RETRIES, or bumps the per-request timeout without redoing the math.
    """

    def test_worst_case_generate_run_fits_job_timeout(self):
        import safety_judge

        # generate_rant chains up to MAX_CALLS_PER_RUN calls (grounded →
        # ungrounded fallback → punch-up), then publish.py runs the judge once.
        # The multi-regeneration path cannot coexist with a full-timeout
        # outage: a judge whose API call fails is treated as PASS and returns
        # immediately, so the regen loop only runs while the API is healthy.
        generate = generate_rant.MAX_CALLS_PER_RUN * generate_rant.worst_case_call_seconds()
        judge = safety_judge.worst_case_call_seconds()
        total = generate + judge + NON_MODEL_ALLOWANCE_S

        self.assertLess(
            total, JOB_TIMEOUT_S,
            f"worst-case pipeline {total}s exceeds the {JOB_TIMEOUT_S}s job timeout "
            f"(generate={generate}s, judge={judge}s, overhead={NON_MODEL_ALLOWANCE_S}s). "
            "Lower MAX_RETRIES/BACKOFF_DELAYS or raise timeout-minutes in morning_brew.yml.",
        )

    def test_backoff_ladder_covers_every_retry(self):
        # call_with_retry indexes backoff_delays[attempt] for attempt in
        # range(max_retries), so a ladder shorter than MAX_RETRIES would
        # IndexError on the last retry — in production, mid-outage.
        self.assertGreaterEqual(len(generate_rant.BACKOFF_DELAYS), generate_rant.MAX_RETRIES)

    def test_judge_shares_the_budget_constants(self):
        # Two copies of the ladder is how they drift. safety_judge imports
        # them; this asserts nobody re-hardcoded a local copy.
        import safety_judge

        self.assertIs(safety_judge.BACKOFF_DELAYS, generate_rant.BACKOFF_DELAYS)
        self.assertEqual(safety_judge.MAX_RETRIES, generate_rant.MAX_RETRIES)


class TestThinkingConfig(unittest.TestCase):
    """
    thinking_level is a Gemini 3.x-only parameter, and eval_models.py drives
    this same code path with Gemma and older Gemini ids. Sending the kwarg to a
    model that rejects it turns an eval run into an API error.
    """

    def test_applied_to_gemini_3x(self):
        self.assertEqual(
            generate_rant.thinking_level_for("gemini-3.1-flash-lite"),
            generate_rant.DEFAULT_THINKING_LEVEL,
        )

    def test_explicit_level_wins(self):
        self.assertEqual(
            generate_rant.thinking_level_for("gemini-3.8-flash", "low"), "low")

    def test_omitted_for_models_that_reject_it(self):
        for model in ("gemma-3-27b-it", "gemini-2.5-flash", "gemini-2.5-pro"):
            with self.subTest(model=model):
                self.assertIsNone(generate_rant.thinking_level_for(model))
                self.assertEqual(generate_rant.thinking_kwargs(model), {})

    def test_kwargs_nest_under_thinking_config(self):
        # Regression guard: GenerateContentConfig has no top-level
        # thinking_level field — it lives on a ThinkingConfig object. Passing it
        # flat raises a pydantic validation error on every Gemini 3.x call, so
        # this asserts the shape the SDK actually accepts.
        try:
            from google.genai import types
        except ImportError:
            self.skipTest("google-genai not installed")
        kwargs = generate_rant.thinking_kwargs("gemini-3.1-flash-lite")
        self.assertIn("thinking_config", kwargs)
        # The SDK coerces the string to a ThinkingLevel enum, so compare on
        # value and case-insensitively rather than to the raw literal.
        self.assertEqual(
            str(kwargs["thinking_config"].thinking_level.value).lower(),
            generate_rant.DEFAULT_THINKING_LEVEL.lower())
        # Must construct cleanly, or production dies on the first call.
        cfg = types.GenerateContentConfig(temperature=0.9, **kwargs)
        self.assertIsNotNone(cfg.thinking_config)



def race_raw(**overrides):
    """A live wild-card contender's StatsAPI fields, mid-September."""
    raw = {
        "wins": 76, "losses": 65, "games_played": 141,
        "division_rank": 3, "division_games_back": 8.0,
        "wild_card_rank": 2, "wild_card_games_back": 5.5,
        "magic_number": None,
        "elimination_number": "16", "wild_card_elimination_number": "16",
        "clinched": False, "closest_chaser": "Cleveland Guardians",
    }
    raw.update(overrides)
    return raw


class TestParseGamesBehind(unittest.TestCase):
    """StatsAPI sends these as strings with sentinels. Letting "-" through as a
    number is how a non-stat ends up in the prompt as something Dan cites."""

    def test_sentinels_are_not_numbers(self):
        for sentinel in ("-", "E", "--", "", None):
            self.assertIsNone(fetch_season_memory._parse_gb(sentinel))
            self.assertIsNone(fetch_season_memory._parse_count(sentinel))

    def test_leading_plus_is_a_cushion_not_a_parse_error(self):
        self.assertEqual(fetch_season_memory._parse_gb("+2.5"), 2.5)

    def test_plain_and_numeric_values(self):
        self.assertEqual(fetch_season_memory._parse_gb("4.5"), 4.5)
        self.assertEqual(fetch_season_memory._parse_gb(3), 3.0)
        self.assertEqual(fetch_season_memory._parse_count("16"), 16)

    def test_garbage_is_dropped_rather_than_raised(self):
        self.assertIsNone(fetch_season_memory._parse_gb("n/a"))
        self.assertIsNone(fetch_season_memory._parse_count("n/a"))

    def test_unparseable_games_played_does_not_raise(self):
        """Every other field is parsed defensively; games_played feeds the
        window arithmetic, so a bad value must degrade, not crash the fetch."""
        block = fetch_season_memory.build_playoff_race(
            "baseball", "regular_season",
            race_raw(games_played="n/a"), 76, 65)
        self.assertEqual(block["games_remaining"], 162 - 141)

    def test_no_usable_game_count_returns_none(self):
        block = fetch_season_memory.build_playoff_race(
            "baseball", "regular_season",
            race_raw(games_played=None), None, None)
        self.assertIsNone(block)


class TestPlayoffRaceWindow(unittest.TestCase):
    """The window gate is the whole feature: Dan talks about the race in
    September precisely because there is no block to talk about in April."""

    def build(self, **overrides):
        return fetch_season_memory.build_playoff_race(
            "baseball", "regular_season", race_raw(**overrides), 76, 65)

    def test_april_gets_no_block(self):
        # 40 games played, 122 left — far outside the 40-game window.
        block = fetch_season_memory.build_playoff_race(
            "baseball", "regular_season",
            race_raw(games_played=40, wins=22, losses=18), 22, 18)
        self.assertIsNone(block)

    def test_stretch_run_gets_a_block(self):
        block = self.build(games_played=124)  # 38 remaining
        self.assertIsNotNone(block)
        self.assertEqual(block["games_remaining"], 38)
        self.assertEqual(block["phase"], "stretch_run")

    def test_window_boundary_is_inclusive(self):
        self.assertIsNotNone(self.build(games_played=122))  # exactly 40 left
        self.assertIsNone(self.build(games_played=121))     # 41 left

    def test_offseason_and_playoffs_get_no_block(self):
        for status in ("offseason", "in_playoffs"):
            self.assertIsNone(fetch_season_memory.build_playoff_race(
                "baseball", status, race_raw(), 76, 65))

    def test_no_standings_data_means_no_block(self):
        self.assertIsNone(fetch_season_memory.build_playoff_race(
            "baseball", "regular_season", {}, 76, 65))

    def test_games_played_falls_back_to_win_loss(self):
        block = fetch_season_memory.build_playoff_race(
            "baseball", "regular_season",
            race_raw(games_played=None), 76, 65)
        self.assertEqual(block["games_remaining"], 162 - 141)

    def test_eliminated_defers_to_season_overrides(self):
        block = self.build(elimination_number="E",
                           wild_card_elimination_number="E")
        self.assertIsNone(block)

    def test_still_alive_in_division_is_not_eliminated(self):
        block = self.build(elimination_number="12",
                           wild_card_elimination_number="E")
        self.assertIsNotNone(block)


class TestPlayoffRaceTiers(unittest.TestCase):
    def build(self, **overrides):
        return fetch_season_memory.build_playoff_race(
            "baseball", "regular_season", race_raw(**overrides), 76, 65)

    def test_clinched(self):
        self.assertEqual(self.build(clinched=True)["race_status"], "clinched")

    def test_clinch_watch_on_small_magic_number(self):
        self.assertEqual(self.build(magic_number=6)["race_status"], "clinch_watch")

    def test_in_position_holding_a_wild_card_spot(self):
        self.assertEqual(self.build()["race_status"], "in_position")

    def test_in_position_leading_the_division(self):
        self.assertEqual(
            self.build(division_rank=1, wild_card_rank=None)["race_status"],
            "in_position")

    def test_chasing_when_within_reach(self):
        block = self.build(wild_card_rank=5, wild_card_games_back=4.0,
                           division_rank=4)
        self.assertEqual(block["race_status"], "chasing")

    def test_playing_out_the_string_when_buried(self):
        block = self.build(games_played=150, wild_card_rank=8,
                           wild_card_games_back=11.0, division_rank=5)
        self.assertEqual(block["race_status"], "playing_out_the_string")

    def test_more_games_back_than_games_left_is_not_chasing(self):
        block = self.build(games_played=155, wild_card_rank=7,
                           wild_card_games_back=9.0, division_rank=5)
        self.assertEqual(block["race_status"], "playing_out_the_string")


class TestPlayoffRaceFields(unittest.TestCase):
    def build(self, **overrides):
        return fetch_season_memory.build_playoff_race(
            "baseball", "regular_season", race_raw(**overrides), 76, 65)

    def test_cushion_and_deficit_are_named_differently(self):
        """Dan reads the field name to know which way the number points, so a
        cushion must never be published under the deficit's name."""
        holding = self.build()
        self.assertEqual(holding["wild_card_games_up"], 5.5)
        self.assertNotIn("wild_card_games_back", holding)

        chasing = self.build(wild_card_rank=5, wild_card_games_back=4.0,
                             division_rank=4)
        self.assertEqual(chasing["wild_card_games_back"], 4.0)
        self.assertNotIn("wild_card_games_up", chasing)

    def test_absent_fields_are_omitted_not_nulled(self):
        block = self.build(magic_number=None, division_games_back=None,
                           wild_card_games_back=None)
        self.assertNotIn("magic_number", block)
        self.assertNotIn("division_games_back", block)
        self.assertNotIn("wild_card_games_up", block)

    def test_chaser_only_when_we_hold_a_spot(self):
        self.assertEqual(self.build()["closest_chaser"], "Cleveland Guardians")
        chasing = self.build(wild_card_rank=5, wild_card_games_back=4.0,
                             division_rank=4)
        self.assertNotIn("closest_chaser", chasing)

    def test_no_sentinel_strings_reach_the_block(self):
        block = self.build(division_games_back="-", wild_card_games_back="-",
                           magic_number="-")
        for value in block.values():
            self.assertNotIn(value, ("-", "E", "--"))


class TestStretchRunWindowCoversAllSports(unittest.TestCase):
    """The other three sports share the gate and block shape; each needs only
    a standings fetcher to light up."""

    def test_every_sport_has_a_window_and_a_season_length(self):
        for sport in fetch_season_memory.SEASON_LENGTH:
            self.assertIn(sport, fetch_season_memory.STRETCH_RUN_WINDOW)

    def test_window_is_a_fraction_of_the_season(self):
        for sport, total in fetch_season_memory.SEASON_LENGTH.items():
            window = fetch_season_memory.STRETCH_RUN_WINDOW[sport]
            self.assertLess(window, total / 2,
                            f"{sport} window is more than half the season")


if __name__ == "__main__":
    unittest.main()
