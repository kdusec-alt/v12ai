# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import unittest

from analysis_speed_v1081 import fetch_news_cached, seed_news_cache
from market_command_v1081 import assess_market_command
from session_truth_v1081 import build_cross_asset_session_truth
from test_session_truth_market_command_v1081 import forecast_for_session


ROOT = Path(__file__).resolve().parent


class V1081EventRefreshTests(unittest.TestCase):
    def test_force_refreshed_watcher_rows_seed_next_full_analysis(self):
        rows = [SimpleNamespace(title="new verified event")]
        seed_news_cache("GENERIC-EVENT", rows)
        calls = []

        def stale_fetcher(symbol, force_refresh=False):
            calls.append(symbol)
            return [SimpleNamespace(title="stale row")]

        reused = fetch_news_cached(stale_fetcher, "GENERIC-EVENT", ttl_seconds=75)
        self.assertEqual(calls, [])
        self.assertEqual(reused[0].title, "new verified event")

    def test_two_current_premarket_rows_outrank_larger_previous_close_bundle(self):
        proxies = {
            "nq": 2.0,
            "vix_change": -1.0,
            "qqq": -2.0,
            "sox": -3.0,
            "smh": -2.5,
            "as_of": {
                "nq": "2026-07-31 08:30:00-04:00 pre_market",
                "vix_change": "2026-07-31 08:30:00-04:00 pre_market",
                "qqq": "2026-07-30 16:00:00-04:00 official_close",
                "sox": "2026-07-30 16:00:00-04:00 official_close",
                "smh": "2026-07-30 16:00:00-04:00 official_close",
            },
        }
        truth = build_cross_asset_session_truth(
            forecast_for_session(session="pre_market"), proxies
        )
        self.assertEqual(set(truth["eligible_keys"]), {"nq", "vix_change"})
        self.assertEqual(set(truth["excluded_keys"]), {"qqq", "sox", "smh"})
        self.assertTrue(truth["same_session"])
        self.assertEqual(truth["vote_group_session"], "pre_market")

    def test_insufficient_session_truth_does_not_fall_back_to_mixed_vote(self):
        truth = {
            "schema": "TINO_CROSS_ASSET_SESSION_TRUTH_V1081",
            "verified": False,
            "same_session": False,
            "vote_scope": "insufficient",
            "eligible_keys": ["nq"],
            "excluded_keys": ["qqq", "sox", "smh"],
        }
        row = assess_market_command("US", {
            "nq": -8.0,
            "qqq": -5.0,
            "sox": -9.0,
            "smh": -8.0,
            "_session_truth_v1081": truth,
        })
        self.assertEqual(row["code"], "WAIT_CONFIRM")
        self.assertTrue(row["session_guard_active"])
        facts = "｜".join(row["facts"])
        self.assertIn("NQ -8.00%", facts)
        self.assertNotIn("QQQ", facts)
        self.assertNotIn("SOX", facts)

    def test_app_hands_watcher_news_to_fast_cache_before_reassessment(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        watcher = source[source.index("def _event_watch_body"):source.index("def _render_event_watch_status")]
        self.assertIn("_seed_news_cache_v1081(symbol, latest_news)", watcher)
        self.assertLess(
            watcher.index("_seed_news_cache_v1081(symbol, latest_news)"),
            watcher.index("plan = assess_event_delta("),
        )


if __name__ == "__main__":
    unittest.main()
