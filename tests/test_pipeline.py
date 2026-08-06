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


if __name__ == "__main__":
    unittest.main()
