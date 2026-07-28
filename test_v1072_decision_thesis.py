# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

import pandas as pd

import prediction_trust_v1072
from data_sources_us import _formal_us_history, _session_rows
from decision_narrative import build_ai_decision_narrative, price_reality
from low_entry_readiness_v1065 import assess_low_entry_readiness
from models import DataTruth, NewsItem, PriceFrame, TickerInfo
from price_truth_v1072 import attach_price_truth, price_truth, scoped_vwap_state
from trend_engine import trend_tag
from orchestrator import _apply_prediction_trust_band


def _direction(label: str = "NEUTRAL", conflict: float = 0.2):
    return SimpleNamespace(
        label=label,
        score=24.0 if label == "UP" else -24.0 if label == "DOWN" else 0.0,
        conflict=conflict,
        p_up=0.62 if label == "UP" else 0.15 if label == "DOWN" else 0.30,
        p_neutral=0.23 if label != "NEUTRAL" else 0.45,
        p_down=0.15 if label == "UP" else 0.62 if label == "DOWN" else 0.25,
        family_contributions={"trend": 4.0 if label == "UP" else -4.0 if label == "DOWN" else 0.0},
        factor_contributions={},
    )


def _frame(
    *,
    market: str = "TW",
    symbol: str = "2408.TW",
    last: float,
    previous: float,
    high: float,
    low: float,
    vwap: float,
    closes: list[float],
    status: str,
    context: dict | None = None,
    atr: float = 4.0,
) -> PriceFrame:
    ticker = TickerInfo(
        symbol,
        symbol,
        symbol,
        market,
        "stock",
        price_limit_pct=0.10 if market == "TW" else None,
    )
    highs = [value + atr * 0.25 for value in closes]
    lows = [max(0.01, value - atr * 0.25) for value in closes]
    return PriceFrame(
        ticker=ticker,
        truth=DataTruth("UNIT_VERIFIED", "2026-07-28", False, True, "unit"),
        open=previous,
        high=high,
        low=low,
        last=last,
        previous_close=previous,
        volume=1_000_000,
        vwap=vwap,
        atr14=atr,
        recent_closes=closes,
        recent_highs=highs,
        recent_lows=lows,
        recent_volumes=[1_000_000] * len(closes),
        price_date="2026-07-28",
        market_status=status,
        context=dict(context or {}),
    )


def _narrative(frame: PriceFrame, label: str = "NEUTRAL", news=None):
    attach_price_truth(frame)
    return build_ai_decision_narrative(
        frame,
        _direction(label),
        list(news or []),
        session_prefix="交易判定",
        low1=95.0,
        low2=92.0,
        attack=103.0,
        stop=89.0,
        no_chase=108.0,
    )


class USSessionTruthTests(unittest.TestCase):
    def test_intraday_rows_never_mix_premarket_or_after_hours(self):
        index = pd.DatetimeIndex([
            "2026-07-28 08:00:00-04:00",
            "2026-07-28 09:15:00-04:00",
            "2026-07-28 10:00:00-04:00",
            "2026-07-28 15:55:00-04:00",
            "2026-07-28 17:00:00-04:00",
        ])
        frame = pd.DataFrame(
            {"Open": [1] * 5, "High": [1] * 5, "Low": [1] * 5, "Close": [1] * 5, "Volume": [1] * 5},
            index=index,
        )
        now = datetime(2026, 7, 28, 17, 30, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(len(_session_rows(frame, "pre_market", now)), 2)
        self.assertEqual(len(_session_rows(frame, "intraday", now)), 2)
        self.assertEqual(len(_session_rows(frame, "after_hours", now)), 1)

    def test_intraday_partial_daily_candle_is_not_formal_close(self):
        index = pd.DatetimeIndex(["2026-07-24", "2026-07-27", "2026-07-28"])
        hist = pd.DataFrame(
            {"Open": [98, 100, 92], "High": [101, 102, 95], "Low": [97, 99, 90], "Close": [100, 101, 91]},
            index=index,
        )
        now = datetime(2026, 7, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))
        formal = _formal_us_history(hist, "intraday", now)
        self.assertEqual(len(formal), 2)
        self.assertEqual(float(formal.iloc[-1]["Close"]), 101.0)

    def test_premarket_partial_daily_candle_is_not_formal_close(self):
        index = pd.DatetimeIndex(["2026-07-27", "2026-07-28"])
        hist = pd.DataFrame(
            {"Open": [100, 92], "High": [102, 95], "Low": [99, 90], "Close": [101, 91]},
            index=index,
        )
        now = datetime(2026, 7, 28, 8, 0, tzinfo=ZoneInfo("America/New_York"))
        formal = _formal_us_history(hist, "pre_market", now)
        self.assertEqual(len(formal), 1)
        self.assertEqual(float(formal.iloc[-1]["Close"]), 101.0)

    def test_premarket_return_and_formal_return_have_distinct_bases(self):
        context = {
            "price_meta": {
                "session": "pre_market",
                "regular_close": 100.0,
                "formal_previous_close": 98.0,
                "session_reference_close": 100.0,
                "extended_accepted": True,
                "vwap_accepted": True,
                "current_trade_date": "2026-07-28",
                "regular_close_date": "2026-07-27",
                "history_scope": "formal_daily_only",
            },
            "us_session": {"accepted": True, "last": 92.0, "high": 99.0, "low": 91.0, "vwap": 94.0},
        }
        frame = _frame(
            market="US", symbol="MU", last=92, previous=100, high=99, low=91,
            vwap=94, closes=[90 + i * 0.5 for i in range(20)] + [98, 100],
            status="pre_market", context=context,
        )
        truth = price_truth(frame)
        self.assertEqual(truth["return_label"], "盤前漲跌")
        self.assertAlmostEqual(truth["current_return_pct"], -8.0)
        self.assertAlmostEqual(truth["formal_return_pct"], 2.0408)
        self.assertIn("上個交易日 +2.04%", truth["header_label"])

    def test_unverified_session_vwap_is_never_called_above_or_below(self):
        context = {
            "price_meta": {
                "session": "pre_market",
                "regular_close": 100.0,
                "formal_previous_close": 99.0,
                "session_reference_close": 100.0,
                "extended_accepted": True,
                "vwap_accepted": False,
                "history_scope": "formal_daily_only",
            },
            "us_session": {"accepted": True, "last": 102.0, "high": 103.0, "low": 101.0, "vwap": None},
        }
        frame = _frame(
            market="US", symbol="GOOG", last=102, previous=100, high=103, low=101,
            vwap=102, closes=[120 - i for i in range(22)], status="pre_market", context=context,
        )
        self.assertIn("待確認", scoped_vwap_state(frame))
        self.assertFalse(price_reality(frame).above_vwap)


class TaiwanTruthTests(unittest.TestCase):
    def test_fresh_formal_close_replaces_stale_header_return(self):
        frame = _frame(
            last=123.5,
            previous=133.5,
            high=128.0,
            low=123.0,
            vwap=125.0,
            closes=[110, 114, 118, 121, 126, 130, 133.5],
            status="after_close",
            atr=5.0,
        )
        attach_price_truth(frame)
        self.assertTrue(price_truth(frame)["formal_series_mismatch"])
        self.assertIn("今日 -7.49%", trend_tag(frame))

    def test_live_row_is_not_its_own_breakout_reference(self):
        frame = _frame(
            last=104.0,
            previous=100.0,
            high=104.0,
            low=100.0,
            vwap=102.0,
            closes=[95.0, 97.0, 99.0, 100.0, 104.0],
            status="intraday",
            atr=5.0,
        )
        frame.recent_highs[-2] = 110.0
        self.assertFalse(price_reality(frame).breakout)


class DecisionThesisScenarioTests(unittest.TestCase):
    def test_limit_down_plus_severe_miss_enters_forecast_cooldown(self):
        frame = _frame(
            last=90, previous=100, high=100, low=90, vwap=96,
            closes=[115 - i * 0.6 for i in range(24)] + [100, 90],
            status="after_close",
        )
        frame.context["prediction_trust_v1072"] = {
            "accepted": True,
            "severity": "severe",
            "entry_block": True,
            "reason": "最近正式昨測誤差 -10.90%",
        }
        result = _narrative(frame, "DOWN")
        self.assertEqual(result["state"], "forecast_cooldown")
        self.assertEqual(result["entry_permission"], "blocked")
        self.assertIn("只列觀察支撐，不是買點", result["message"])

    def test_limit_up_inside_long_downtrend_is_countertrend_breakout(self):
        closes = [180 - i for i in range(40)] + [100, 109.8]
        frame = _frame(
            market="TW", symbol="4577.TW", last=109.8, previous=100,
            high=109.8, low=100.5, vwap=106, closes=closes,
            status="after_close", atr=2.0,
            context={"macro": {"accepted": True, "source": "UNIT", "sox": -3.0, "nq": -1.0}},
        )
        macro_news = [NewsItem("Official", "2026-07-28", -0.2, "policy_geo macro_event", "全球關稅風險", "")]
        result = _narrative(frame, "UP", macro_news)
        self.assertEqual(result["state"], "countertrend_breakout")
        self.assertIn("下降趨勢中的強勢反攻", result["title"])
        self.assertNotEqual(result["state"], "bad_news_absorbed")

    def test_us_premarket_shock_is_repricing_not_generic_risk(self):
        context = {
            "price_meta": {
                "session": "pre_market", "regular_close": 100.0,
                "formal_previous_close": 102.0, "session_reference_close": 100.0,
                "extended_accepted": True, "vwap_accepted": True,
                "history_scope": "formal_daily_only",
            },
            "us_session": {"accepted": True, "last": 91.0, "high": 98.0, "low": 90.5, "vwap": 94.0},
        }
        frame = _frame(
            market="US", symbol="MU", last=91, previous=100, high=98, low=90.5,
            vwap=94, closes=[80 + i for i in range(21)], status="pre_market",
            context=context, atr=4.0,
        )
        result = _narrative(frame, "DOWN")
        self.assertEqual(result["state"], "session_repricing")
        self.assertIn("15–30 分鐘", result["message"])
        self.assertEqual(result["entry_permission"], "blocked")

    def test_us_intraday_repricing_never_says_wait_for_market_open(self):
        context = {
            "price_meta": {
                "session": "intraday", "regular_close": 35.92,
                "formal_previous_close": 35.92, "session_reference_close": 35.92,
                "extended_accepted": True, "vwap_accepted": True,
                "history_scope": "formal_daily_only",
            },
            "us_session": {
                "accepted": True, "last": 33.46, "high": 34.65,
                "low": 32.12, "vwap": 33.10,
            },
        }
        frame = _frame(
            market="US", symbol="IONQ", last=33.46, previous=35.92,
            high=34.65, low=32.12, vwap=33.10,
            closes=[44 - i * 0.4 for i in range(22)], status="intraday",
            context=context, atr=2.0,
        )
        result = _narrative(frame, "DOWN")
        self.assertEqual(result["state"], "session_repricing")
        self.assertEqual(result["entry_permission"], "blocked")
        self.assertIn("盤中先確認後續不再破低", result["message"])
        self.assertNotIn("正式開盤後", result["message"])
        self.assertIn("正式盤中重定價", result["title"])

    def test_glw_backward_beat_and_high_bar_reset_outrank_fomc_template(self):
        context = {
            "price_meta": {
                "session": "pre_market", "regular_close": 143.36,
                "formal_previous_close": 146.65, "session_reference_close": 143.36,
                "extended_accepted": True, "vwap_accepted": True,
                "history_scope": "formal_daily_only",
            },
            "us_session": {
                "accepted": True, "last": 120.0, "high": 123.0,
                "low": 119.0, "vwap": 121.8,
            },
        }
        frame = _frame(
            market="US", symbol="GLW", last=120.0, previous=143.36,
            high=123.0, low=119.0, vwap=121.8,
            closes=[110 + i * 1.6 for i in range(20)] + [146.65, 143.36],
            status="pre_market", context=context, atr=8.0,
        )
        news = [
            NewsItem(
                "GoogleNewsUS", "2026-07-28 19:00", 0.12,
                "bullish_us_company_earnings",
                "Corning earnings beat estimates with strong AI optical sales", "",
            ),
            NewsItem(
                "GoogleNewsUS", "2026-07-28 19:01", -0.12,
                "bearish_us_company_earnings_forward_risk",
                "Corning guidance is in line but not enough as stock sinks", "",
            ),
            NewsItem(
                "Official", "2026-07-28 19:02", -0.08,
                "daily_headline_macro", "FOMC decision ahead", "",
            ),
        ]
        result = _narrative(frame, "DOWN", news)
        self.assertEqual(result["state"], "earnings_expectation_reset")
        self.assertEqual(
            result["earnings_evidence"]["state"],
            "backward_beat_high_bar_reset",
        )
        self.assertIn("本季佳績不抵銷前瞻落差", result["title"])
        self.assertIn("市場隱含高標", result["message"])
        self.assertIn("15–30 分鐘", result["message"])
        self.assertAlmostEqual(result["price_truth"]["current_return_pct"], -16.2946)
        self.assertAlmostEqual(result["price_truth"]["formal_return_pct"], -2.2434)
        self.assertIn("上個交易日 -2.24%", result["price_truth"]["header_label"])

    def test_us_premarket_vwap_reclaim_below_ma20_is_countertrend_rebound(self):
        context = {
            "price_meta": {
                "session": "pre_market", "regular_close": 100.0,
                "formal_previous_close": 101.0, "session_reference_close": 100.0,
                "extended_accepted": True, "vwap_accepted": True,
                "history_scope": "formal_daily_only",
            },
            "us_session": {"accepted": True, "last": 102.0, "high": 103.0, "low": 100.5, "vwap": 101.0},
        }
        frame = _frame(
            market="US", symbol="GOOG", last=102, previous=100, high=103, low=100.5,
            vwap=101, closes=[140 - i * 1.5 for i in range(22)], status="pre_market",
            context=context, atr=3.0,
        )
        result = _narrative(frame, "NEUTRAL")
        self.assertEqual(result["state"], "session_countertrend_rebound")
        self.assertIn("已收復VWAP", result["title"])
        self.assertIn("仍低於中期均線", result["message"])

    def test_evidence_families_are_unique(self):
        frame = _frame(
            last=104.5, previous=100, high=105, low=100, vwap=103,
            closes=[80 + i for i in range(21)] + [104.5], status="after_close",
            context={"macro": {"accepted": True, "source": "UNIT", "sox": -1.0, "nq": -0.8}},
        )
        news = [
            NewsItem("Official", "2026-07-28", -0.2, "policy_geo macro_event", "關稅事件", ""),
            NewsItem("Official", "2026-07-28", -0.1, "policy_geo macro_event", "同一事件更新", ""),
        ]
        result = _narrative(frame, "UP", news)
        families = result["evidence_families_counted"]
        self.assertEqual(len(families), len(set(families)))
        self.assertEqual(families.count("macro_event"), 1)


class PredictionTrustTests(unittest.TestCase):
    def setUp(self):
        self.old_reader = prediction_trust_v1072.read_audit_log

    def tearDown(self):
        prediction_trust_v1072.read_audit_log = self.old_reader

    def test_official_eight_percent_miss_blocks_entry_and_caps_maturity(self):
        prediction_trust_v1072.read_audit_log = lambda limit=1600: [{
            "audit_id": "audit-1",
            "prediction_id": "prediction-1",
            "ticker": "6586.TW",
            "market": "TW",
            "target": "next",
            "target_trade_date": "2026-07-28",
            "predicted_close": 135.5,
            "actual_close": 123.5,
            "error_pct": -8.8561,
            "direction_hit": False,
            "actual_valid": True,
            "audit_time_tw": "2026-07-28T17:01:00+08:00",
        }]
        result = prediction_trust_v1072.assess_prediction_trust("6586.TW", "TW", "2026-07-28")
        self.assertEqual(result["severity"], "severe")
        self.assertTrue(result["entry_block"])
        self.assertEqual(result["maturity_cap"], 39)
        self.assertEqual(result["confidence_cut"], 14.0)

    def test_readiness_uses_trust_cap_without_rewriting_prices(self):
        decision = {
            "現價": 123.5, "低接第一批": 119.5, "低接第二批": 116.0,
            "防守": 113.8, "不追": 130.0, "攻擊": "站穩 126.7 小量",
            "轉強": "站穩 126.7 才轉強", "VWAP位置": "VWAP 下方",
            "漲跌幅": -7.49, "標題": "AI進場決策卡｜價格結構破壞＋模型失準冷卻",
            "主訊息": "119.50 只列觀察支撐，不是買點；站回 126.70 後重評。",
            "決策分": -20, "_direction_engine": {"gate_state": "C防守", "score": -20},
            "_trend_snapshot": {"ma20_gap_pct": 1.9},
            "_price_meta": {"decision_blocked": False},
            "_prediction_trust": {
                "accepted": True, "severity": "severe", "entry_block": True,
                "maturity_cap": 39, "reason": "最近正式昨測誤差 -8.86%",
            },
            "_decision_thesis": {
                "state": "forecast_cooldown", "entry_permission": "blocked",
                "message": "交易判定：119.50 只列觀察支撐，不是買點；站回 126.70 後重評。",
            },
        }
        forecast = SimpleNamespace(
            decision_card=decision,
            radar={"Fair Value": "下緣情境 118.5｜現價基準 123.5｜上緣情境 128.5"},
            news_items=[],
            ticker=SimpleNamespace(market="TW"),
            no_chase=130.0,
        )
        result = assess_low_entry_readiness(forecast)
        self.assertLessEqual(result["score"], 39)
        self.assertEqual(result["color"], "red")
        self.assertIn("不是買點", result["summary"])
        self.assertEqual(result["wait_plan"]["pullback"], 119.5)

    def test_miss_widens_range_but_does_not_move_t1(self):
        frame = _frame(
            last=100, previous=99, high=102, low=98, vwap=100,
            closes=[80 + i for i in range(21)], status="after_close", atr=4.0,
        )
        high, low = _apply_prediction_trust_band(
            frame, 101.0, 105.0, 97.0, {"range_width_multiplier": 1.6}
        )
        self.assertGreater(high - 101.0, 105.0 - 101.0)
        self.assertGreater(101.0 - low, 101.0 - 97.0)
        self.assertLess(low, 101.0)
        self.assertGreater(high, 101.0)


if __name__ == "__main__":
    unittest.main()
