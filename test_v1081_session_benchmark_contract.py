# -*- coding: utf-8 -*-
from types import SimpleNamespace
import unittest

from market_command_v1081 import assess_market_command
from models import NewsItem
from session_truth_v1081 import build_cross_asset_session_truth
from decision_architecture_v1081 import assess_entry_opportunity
from test_decision_architecture_v1081 import make_forecast


def tw_forecast(symbol="9999.TW"):
    return SimpleNamespace(
        ticker=SimpleNamespace(market="TW", resolved_symbol=symbol),
        price_date="2026-07-31",
        decision_card={
            "資料標題": "盤中資料",
            "_price_meta": {"session": "intraday", "price_date": "2026-07-31"},
            "_decision_thesis": {
                "price_truth": {"session": "intraday", "price_date": "2026-07-31"}
            },
        },
    )


class V1081SessionBenchmarkContractTests(unittest.TestCase):
    def test_one_current_taiex_row_is_valid_relative_strength_benchmark(self):
        proxies = {
            "taiex": 6.0,
            "sox": 8.0,
            "nq": 4.0,
            "as_of": {
                "taiex": "2026-07-31 11:10:00+08:00 intraday",
                "sox": "2026-07-30 16:00:00-04:00 official_close",
                "nq": "2026-07-30 16:00:00-04:00 official_close",
            },
        }
        truth = build_cross_asset_session_truth(tw_forecast(), proxies)
        self.assertTrue(truth["benchmark_same_session"])
        self.assertFalse(truth["cross_asset_same_session"])
        self.assertEqual(truth["vote_scope"], "same_session_benchmark")
        self.assertEqual(truth["eligible_keys"], ["taiex"])

    def test_one_tpex_row_can_veto_weak_otc_stock_without_cross_asset_claim(self):
        proxies = {
            "tpex": 5.0,
            "as_of": {"tpex": "2026-07-31 11:12:00+08:00 intraday"},
        }
        truth = build_cross_asset_session_truth(tw_forecast("9999.TWO"), proxies)
        forecast = make_forecast(
            last=96.0,
            day_pct=-2.0,
            vwap=100.0,
            open_price=100.0,
            high=101.0,
            low=95.5,
        )
        forecast.ticker.resolved_symbol = "9999.TWO"
        forecast.decision_card["_session_truth_v1081"] = truth
        forecast.decision_card["_market_proxy_v1081"] = {"tpex": 5.0}
        row = assess_entry_opportunity(forecast)
        self.assertEqual(row["state"], "SELLING_EXPANSION_BLOCK")
        self.assertAlmostEqual(row["market_context"]["relative_gap_pct"], -7.0)
        self.assertTrue(row["market_context"]["severe_relative_weakness"])

    def test_single_benchmark_does_not_confirm_market_command(self):
        truth = {
            "schema": "TINO_CROSS_ASSET_SESSION_TRUTH_V1081",
            "verified": True,
            "same_session": True,
            "benchmark_same_session": True,
            "cross_asset_same_session": False,
            "vote_scope": "same_session_benchmark",
            "eligible_keys": ["taiex"],
            "excluded_keys": ["sox", "nq"],
        }
        row = assess_market_command("TW", {
            "taiex": -8.0,
            "sox": -10.0,
            "nq": -9.0,
            "_session_truth_v1081": truth,
        })
        self.assertEqual(row["code"], "WAIT_CONFIRM")
        self.assertFalse(row["same_session_confirmed"])
        self.assertIn("同時段資料不足", row["label"])

    def test_severity_without_explicit_verification_is_context_only(self):
        news = [NewsItem(
            source="Newswire",
            time="2026-07-31T10:00:00+00:00",
            score=-1.0,
            tag="severity=4",
            title="Major but unverified event",
            link="",
        )]
        row = assess_market_command("US", {
            "sox": -4.0,
            "nq": -3.0,
            "qqq": -2.0,
            "smh": -3.0,
        }, news=news)
        self.assertFalse(row["event_verified"])
        self.assertEqual(row["event_reason"], "")
        self.assertNotIn("Major but unverified event", row["thesis"])

    def test_explicit_verified_event_can_enter_market_context(self):
        news = [NewsItem(
            source="OfficialSource",
            time="2026-07-31T10:00:00+00:00",
            score=-1.0,
            tag="severity=4|event_verified=1",
            title="Verified systemic event",
            link="",
        )]
        row = assess_market_command("US", {
            "sox": -4.0,
            "nq": -3.0,
            "qqq": -2.0,
            "smh": -3.0,
        }, news=news)
        self.assertTrue(row["event_verified"])
        self.assertEqual(row["event_reason"], "Verified systemic event")


if __name__ == "__main__":
    unittest.main()
