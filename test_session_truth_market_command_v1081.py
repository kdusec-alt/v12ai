# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import unittest

from market_command_v1081 import assess_market_command
from models import NewsItem
from session_truth_v1081 import build_cross_asset_session_truth
from decision_architecture_v1081 import assess_entry_opportunity
from test_decision_architecture_v1081 import make_forecast


ROOT = Path(__file__).resolve().parent


def forecast_for_session(market="US", session="intraday", target_date="2026-07-31"):
    if session == "intraday":
        label = "盤中資料"
    elif session == "pre_market":
        label = "盤前資料"
    elif session == "after_hours":
        label = "盤後資料"
    else:
        label = "收盤資料"
    return SimpleNamespace(
        ticker=SimpleNamespace(market=market, resolved_symbol="GENERIC"),
        price_date=target_date,
        decision_card={
            "資料標題": label,
            "_price_meta": {"session": session, "price_date": target_date},
            "_decision_thesis": {"price_truth": {"session": session, "price_date": target_date}},
        },
    )


class SessionTruthAndMarketCommandV1081Tests(unittest.TestCase):
    def test_live_nq_is_not_mixed_with_previous_close_etfs(self):
        proxies = {
            "accepted": True,
            "nq": -3.0,
            "qqq": -2.0,
            "sox": -4.0,
            "smh": -3.5,
            "as_of": {
                "nq": "2026-07-31 08:30:00-04:00 pre_market",
                "qqq": "2026-07-30 16:00:00-04:00 official_close",
                "sox": "2026-07-30 16:00:00-04:00 official_close",
                "smh": "2026-07-30 16:00:00-04:00 official_close",
            },
        }
        truth = build_cross_asset_session_truth(forecast_for_session(), proxies)
        self.assertEqual(set(truth["eligible_keys"]), {"qqq", "sox", "smh"})
        self.assertEqual(truth["excluded_keys"], ["nq"])
        self.assertFalse(truth["same_session"])
        self.assertEqual(truth["vote_scope"], "coherent_context_session")

    def test_current_us_group_can_be_same_session(self):
        proxies = {
            "accepted": True,
            "nq": 2.0,
            "qqq": 2.2,
            "sox": 4.0,
            "smh": 3.5,
            "as_of": {
                key: "2026-07-31 11:00:00-04:00 intraday"
                for key in ("nq", "qqq", "sox", "smh")
            },
        }
        truth = build_cross_asset_session_truth(forecast_for_session(), proxies)
        self.assertTrue(truth["same_session"])
        self.assertEqual(truth["vote_scope"], "same_session")
        self.assertEqual(truth["vote_group_session"], "intraday")
        self.assertEqual(len(truth["eligible_keys"]), 4)

    def test_same_date_premarket_does_not_mix_with_official_close(self):
        proxies = {
            "accepted": True,
            "nq": 3.0,
            "qqq": 1.0,
            "sox": 2.0,
            "smh": 1.5,
            "as_of": {
                "nq": "2026-07-31 08:20:00-04:00 pre_market",
                "qqq": "2026-07-31 16:00:00-04:00 official_close",
                "sox": "2026-07-31 16:00:00-04:00 official_close",
                "smh": "2026-07-31 16:00:00-04:00 official_close",
            },
        }
        truth = build_cross_asset_session_truth(
            forecast_for_session(session="pre_market"), proxies
        )
        self.assertFalse(truth["same_session"])
        self.assertEqual(set(truth["eligible_keys"]), {"qqq", "sox", "smh"})
        self.assertEqual(truth["vote_group_session"], "official_close")
        self.assertIn("nq", truth["excluded_keys"])

    def test_two_current_premarket_rows_form_same_session(self):
        proxies = {
            "accepted": True,
            "nq": 3.0,
            "qqq": 2.5,
            "sox": 1.0,
            "as_of": {
                "nq": "2026-07-31 08:20:00-04:00 pre_market",
                "qqq": "2026-07-31 08:25:00-04:00 pre_market",
                "sox": "2026-07-30 16:00:00-04:00 official_close",
            },
        }
        truth = build_cross_asset_session_truth(
            forecast_for_session(session="pre_market"), proxies
        )
        self.assertTrue(truth["same_session"])
        self.assertEqual(set(truth["eligible_keys"]), {"nq", "qqq"})
        self.assertEqual(truth["vote_group_session"], "pre_market")

    def test_naive_timestamp_never_proves_current_session(self):
        proxies = {
            "accepted": True,
            "nq": 2.0,
            "qqq": 2.2,
            "as_of": {
                "nq": "2026-07-31 11:00:00",
                "qqq": "2026-07-31 11:01:00",
            },
        }
        truth = build_cross_asset_session_truth(forecast_for_session(), proxies)
        self.assertFalse(truth["same_session"])
        self.assertEqual(truth["vote_group_session"], "unknown")

    def test_market_vote_filters_excluded_session_keys(self):
        truth = {
            "schema": "TINO_CROSS_ASSET_SESSION_TRUTH_V1081",
            "verified": True,
            "same_session": False,
            "vote_scope": "coherent_context_session",
            "eligible_keys": ["qqq", "sox", "smh"],
            "excluded_keys": ["nq"],
        }
        row = assess_market_command("US", {
            "nq": -12.0,
            "qqq": -1.0,
            "sox": -2.0,
            "smh": -1.5,
            "vix": 20.0,
            "_session_truth_v1081": truth,
        })
        facts = "｜".join(row["facts"])
        self.assertNotIn("NQ -12.00%", facts)
        self.assertIn("SOX -2.00%", facts)
        self.assertEqual(row["excluded_proxy_keys"], ["nq"])
        self.assertFalse(row["same_session_confirmed"])
        self.assertIn("不宣稱跨資產因果", row["thesis"])

    def test_red_risk_acceleration_outranks_purple_relief(self):
        truth = {
            "schema": "TINO_CROSS_ASSET_SESSION_TRUTH_V1081",
            "verified": True,
            "same_session": True,
            "vote_scope": "same_session",
            "eligible_keys": ["sox", "nq", "qqq", "smh", "vix_change"],
            "excluded_keys": [],
        }
        news = [NewsItem(
            source="OfficialSource",
            time="2026-07-31T10:00:00",
            score=-1.0,
            tag="severity=4|event_verified=1",
            title="Verified systemic event",
            link="",
        )]
        row = assess_market_command("US", {
            "sox": -10.0,
            "nq": -10.0,
            "qqq": 2.0,
            "smh": -10.0,
            "vix": 40.0,
            "vix_change": -5.0,
            "_session_truth_v1081": truth,
        }, news=news)
        self.assertEqual(row["code"], "CRASH")
        self.assertIn("風險加速", row["label"])

    def test_us_purple_uses_no_taiwan_limit_language_and_is_not_entry(self):
        truth = {
            "schema": "TINO_CROSS_ASSET_SESSION_TRUTH_V1081",
            "verified": True,
            "same_session": True,
            "vote_scope": "same_session",
            "eligible_keys": ["sox", "nq", "qqq", "smh", "vix_change"],
            "excluded_keys": [],
        }
        row = assess_market_command("US", {
            "sox": -8.0,
            "nq": -8.0,
            "qqq": 2.0,
            "smh": -8.0,
            "vix": 30.0,
            "vix_change": -5.0,
            "_session_truth_v1081": truth,
        })
        self.assertEqual(row["code"], "CAPITULATION")
        self.assertNotIn("跌停", row["action"])
        self.assertIn("紫燈不是直接進場訊號", row["action"])
        self.assertFalse(row["purple_is_entry_signal"])

    def test_unverified_google_event_cannot_claim_causality(self):
        truth = {
            "schema": "TINO_CROSS_ASSET_SESSION_TRUTH_V1081",
            "verified": True,
            "same_session": True,
            "vote_scope": "same_session",
            "eligible_keys": ["sox", "nq", "qqq", "smh"],
            "excluded_keys": [],
        }
        news = [NewsItem(
            source="GoogleNews",
            time="原始日期待驗證",
            score=-1.0,
            tag="severity=4|timestamp_provenance=v1079|timestamp_status=unverified|timestamp_verified=0",
            title="Unverified headline",
            link="",
        )]
        row = assess_market_command("US", {
            "sox": -4.0,
            "nq": -3.0,
            "qqq": -2.0,
            "smh": -3.0,
            "_session_truth_v1081": truth,
        }, news=news)
        self.assertFalse(row["event_verified"])
        self.assertEqual(row["event_reason"], "")
        self.assertNotIn("Unverified headline", row["thesis"])

    def test_tpex_stock_prefers_tpex_benchmark(self):
        forecast = make_forecast(
            last=101.0,
            day_pct=1.0,
            vwap=100.0,
            evidence="TAIEX +6.0%｜TPEX +2.0%",
            same_session=True,
        )
        forecast.ticker.resolved_symbol = "9999.TWO"
        forecast.decision_card["_market_proxy_v1081"] = {"taiex": 6.0, "tpex": 2.0}
        forecast.decision_card["_session_truth_v1081"] = {
            "same_session": True,
            "eligible_keys": ["taiex", "tpex"],
        }
        row = assess_entry_opportunity(forecast)
        self.assertEqual(row["market_context"]["benchmark_return_pct"], 2.0)
        self.assertAlmostEqual(row["market_context"]["relative_gap_pct"], -1.0)

    def test_app_uses_v1081_market_command_and_session_attachment(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('"market_command_v1081"', source)
        self.assertIn("_attach_session_truth_v1081", source)
        self.assertIn('proxies["_session_truth_v1081"]', source)


if __name__ == "__main__":
    unittest.main()
