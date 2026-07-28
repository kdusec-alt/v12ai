# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from decision_narrative import build_ai_decision_narrative
from models import DataTruth, NewsItem, PriceFrame, TickerInfo
from news_causal_intelligence_v1073 import (
    analyze_news_causality,
    select_effective_news_items,
)
from price_truth_v1072 import attach_price_truth


TAIPEI = ZoneInfo("Asia/Taipei")


def _direction(label: str = "NEUTRAL"):
    return SimpleNamespace(
        label=label,
        score=28.0 if label == "UP" else -28.0 if label == "DOWN" else 0.0,
        conflict=0.20,
        p_up=0.58 if label == "UP" else 0.18 if label == "DOWN" else 0.30,
        p_neutral=0.24 if label != "NEUTRAL" else 0.45,
        p_down=0.18 if label == "UP" else 0.58 if label == "DOWN" else 0.25,
        family_contributions={"flow": 0.0, "trend": 4.0 if label == "UP" else -4.0},
        factor_contributions={},
    )


def _tw_frame(
    *,
    symbol: str = "9999.TW",
    name: str = "泛用記憶體",
    price_date: str = "2026-07-28",
    status: str = "after_close",
    last: float = 90.0,
    previous: float = 100.0,
    timestamp: str = "",
) -> PriceFrame:
    closes = [112.0 - i * 0.5 for i in range(22)] + [previous, last]
    context = {
        "macro": {},
        "inst": {},
        "margin": {},
        "price_snapshot": {
            "last": last,
            "open": previous,
            "high": max(previous, last),
            "low": min(previous, last),
            "previous_close": previous,
            "volume": 2_000_000,
            "vwap": (previous + last) / 2,
            "time": timestamp,
            "source": "UNIT",
            "market_mode": status,
        },
        "price_meta": {
            "source": "UNIT",
            "session": status,
            "current_trade_date": price_date,
            "label": timestamp,
        },
    }
    frame = PriceFrame(
        ticker=TickerInfo(
            symbol,
            symbol,
            name,
            "TW",
            "stock",
            exchange="TWSE",
            price_limit_pct=0.10,
        ),
        truth=DataTruth("UNIT", price_date, False, True, "unit"),
        open=previous,
        high=max(previous, last),
        low=min(previous, last),
        last=last,
        previous_close=previous,
        volume=2_000_000,
        vwap=(previous + last) / 2,
        atr14=4.0,
        recent_closes=closes,
        recent_highs=[value + 1.0 for value in closes],
        recent_lows=[value - 1.0 for value in closes],
        recent_volumes=[2_000_000] * len(closes),
        price_date=price_date,
        market_status=status,
        context=context,
    )
    return attach_price_truth(frame)


def _us_frame(
    *,
    symbol: str = "XYZ",
    status: str = "after_hours",
    last: float = 92.0,
    regular_close: float = 100.0,
    timestamp: str = "07/29 04:20 台灣",
) -> PriceFrame:
    closes = [80.0 + i * 0.8 for i in range(22)] + [98.0, regular_close]
    context = {
        "macro": {},
        "short": {},
        "price_meta": {
            "source": "UNIT_1m_PrePost",
            "session": status,
            "label": timestamp,
            "regular_close": regular_close,
            "regular_close_date": "2026-07-28",
            "formal_previous_close": 98.0,
            "formal_previous_close_date": "2026-07-27",
            "session_reference_close": regular_close,
            "current_trade_date": "2026-07-28",
            "extended_accepted": True,
            "vwap_accepted": True,
            "history_scope": "formal_daily_only",
        },
        "us_session": {
            "accepted": True,
            "last": last,
            "high": max(last, regular_close),
            "low": min(last, regular_close),
            "vwap": (last + regular_close) / 2,
            "timestamp": timestamp,
            "trade_date": "2026-07-28",
            "reference_close": regular_close,
        },
    }
    frame = PriceFrame(
        ticker=TickerInfo(symbol, symbol, "Example Corporation", "US", "stock"),
        truth=DataTruth("UNIT_1m_PrePost", "2026-07-28", False, True, "unit"),
        open=regular_close,
        high=max(last, regular_close),
        low=min(last, regular_close),
        last=last,
        previous_close=regular_close,
        volume=500_000,
        vwap=(last + regular_close) / 2,
        atr14=5.0,
        recent_closes=closes,
        recent_highs=[value + 1.0 for value in closes],
        recent_lows=[value - 1.0 for value in closes],
        recent_volumes=[500_000] * len(closes),
        price_date="2026-07-28",
        market_status=status,
        context=context,
    )
    return attach_price_truth(frame)


def _narrative(frame: PriceFrame, news, label: str = "DOWN"):
    return build_ai_decision_narrative(
        frame,
        _direction(label),
        news,
        session_prefix="交易判定",
        low1=88.0,
        low2=84.0,
        attack=96.0,
        stop=82.0,
        no_chase=104.0,
    )


class NewsCausalIntelligenceV1073Tests(unittest.TestCase):
    def test_tw_after_close_earnings_waits_for_first_market_reaction(self):
        frame = _tw_frame()
        news = [
            NewsItem(
                "Official",
                "2026-07-28 16:00",
                0.18,
                "tw_company_bullish_earnings",
                "泛用記憶體 Q2 earnings beat estimates and raises guidance",
                "",
            )
        ]
        assessment = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 28, 17, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(assessment["causal_state"], "event_awaiting_market_reaction")
        self.assertEqual(assessment["entry_gate"], "block_until_first_reaction")
        self.assertFalse(assessment["can_compare_to_price"])

        frame.context["news_causal_v1073"] = assessment
        result = _narrative(frame, news)
        self.assertEqual(result["state"], "event_awaiting_market_reaction")
        self.assertIn("現有價格尚未驗證", result["title"])
        self.assertNotIn("價格未買單", result["message"])
        self.assertNotIn("利多出現但", result["message"])

    def test_tw_next_intraday_is_first_reaction_not_completed_session(self):
        frame = _tw_frame(
            price_date="2026-07-29",
            status="intraday",
            last=94.0,
            previous=90.0,
            timestamp="2026-07-29T10:00:00+08:00",
        )
        news = [
            NewsItem(
                "Official", "2026-07-28 16:00", 0.18,
                "tw_company_bullish_earnings",
                "泛用記憶體 Q2 earnings beat estimates and raises guidance", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 29, 10, 5, tzinfo=TAIPEI),
        )
        self.assertEqual(result["reaction_state"], "reaction_in_progress")
        self.assertTrue(result["can_compare_to_price"])
        self.assertNotEqual(result["causal_state"], "event_awaiting_market_reaction")

    def test_tw_next_formal_close_completes_event_verification(self):
        frame = _tw_frame(
            price_date="2026-07-29",
            status="after_close",
            last=94.0,
            previous=90.0,
        )
        news = [
            NewsItem(
                "Official", "2026-07-28 16:00", 0.18,
                "tw_company_bullish_earnings",
                "泛用記憶體 Q2 earnings beat estimates and raises guidance", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 29, 16, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(result["reaction_state"], "verified_session_complete")
        self.assertEqual(result["causal_state"], "event_price_confirming")

    def test_preclose_positive_event_can_be_rejected_by_close(self):
        frame = _tw_frame(last=90.0, previous=100.0)
        news = [
            NewsItem(
                "Official", "2026-07-28 12:00", 0.18,
                "tw_company_bullish_event",
                "泛用記憶體取得大型訂單且上調展望", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 28, 16, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(result["reaction_state"], "verified_session_complete")
        self.assertEqual(result["causal_state"], "positive_event_rejected")

    def test_us_after_hours_quote_can_only_mark_reaction_in_progress(self):
        frame = _us_frame()
        news = [
            NewsItem(
                "Official", "2026-07-29 04:05", -0.18,
                "bearish_us_company_earnings_forward_risk",
                "Example Corporation earnings beat but cuts guidance", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 29, 4, 25, tzinfo=TAIPEI),
        )
        self.assertEqual(result["reaction_state"], "reaction_in_progress")
        self.assertTrue(result["price_has_seen_event"])
        self.assertTrue(result["can_compare_to_price"])
        self.assertEqual(result["entry_gate"], "wait_15_30m")

    def test_same_earnings_story_is_one_family_even_with_many_publishers(self):
        frame = _us_frame(status="pre_market", timestamp="07/29 20:00 台灣")
        news = [
            NewsItem(
                "WireA", "2026-07-29 19:00", 0.16,
                "bullish_us_company_earnings",
                "Example Corporation earnings beat estimates", "",
            ),
            NewsItem(
                "WireB", "2026-07-29 19:02", 0.14,
                "bullish_us_company_earnings",
                "Example Corporation posts record quarterly revenue", "",
            ),
            NewsItem(
                "WireC", "2026-07-29 19:03", -0.18,
                "bearish_us_company_earnings_forward_risk",
                "Example Corporation cuts guidance after earnings beat", "",
            ),
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 29, 20, 5, tzinfo=TAIPEI),
        )
        self.assertEqual(result["company_family_count"], 1)
        self.assertEqual(result["earnings"]["state"], "backward_beat_forward_miss")
        self.assertLess(result["company_score"], 0)
        effective = select_effective_news_items(news, result)
        self.assertLessEqual(len(effective), 3)

    def test_company_event_outranks_shared_macro_background(self):
        frame = _tw_frame()
        news = [
            NewsItem(
                "Official", "2026-07-28 16:00", 0.18,
                "tw_company_bullish_earnings",
                "泛用記憶體 earnings beat and raises guidance", "",
            ),
            NewsItem(
                "Wire", "2026-07-28 15:50", -0.10,
                "tw_daily_macro_event",
                "FOMC uncertainty pressures global markets", "",
            ),
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 28, 17, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(result["cause_priority"], "company")
        self.assertEqual(result["causal_state"], "event_awaiting_market_reaction")
        self.assertIn("共同背景", result["global_text"])

    def test_scheduled_earnings_preview_never_becomes_directional_result(self):
        frame = _us_frame(status="pre_market", timestamp="07/29 19:00 台灣")
        news = [
            NewsItem(
                "Wire", "2026-07-29 18:30", 0.12,
                "bullish_us_company_earnings",
                "Example Corporation will report earnings after the bell", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 29, 19, 5, tzinfo=TAIPEI),
        )
        self.assertEqual(result["causal_state"], "scheduled_event_pending")
        self.assertEqual(result["company_sign"], 0)
        self.assertEqual(result["entry_gate"], "event_caution")

    def test_future_source_timestamp_is_rejected(self):
        frame = _tw_frame()
        news = [
            NewsItem(
                "Wire", "2026-07-30 16:00", 0.20,
                "tw_company_bullish_event",
                "泛用記憶體取得大型訂單", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 28, 17, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(result["classified_count"], 0)
        self.assertEqual(result["causal_state"], "no_fresh_company_event")

    def test_engine_is_generic_for_unlisted_tw_and_us_symbols(self):
        tw = _tw_frame(symbol="9999.TW", name="Example Taiwan")
        us = _us_frame(symbol="XYZ")
        tw_news = [
            NewsItem(
                "Wire", "2026-07-28 12:00", -0.16,
                "tw_company_bearish_event",
                "Example Taiwan cuts guidance as demand slows", "",
            )
        ]
        us_news = [
            NewsItem(
                "Wire", "2026-07-29 04:05", -0.16,
                "bearish_us_company_earnings_forward_risk",
                "Example Corporation cuts guidance as demand slows", "",
            )
        ]
        tw_result = analyze_news_causality(
            tw,
            tw_news,
            now=datetime(2026, 7, 28, 16, 0, tzinfo=TAIPEI),
        )
        us_result = analyze_news_causality(
            us,
            us_news,
            now=datetime(2026, 7, 29, 4, 25, tzinfo=TAIPEI),
        )
        self.assertEqual(tw_result["dominant_family"], "earnings_package")
        self.assertEqual(us_result["dominant_family"], "earnings_package")
        self.assertLess(tw_result["company_score"], 0)
        self.assertLess(us_result["company_score"], 0)

    def test_generator_input_is_consumed_once_and_counted_correctly(self):
        frame = _tw_frame()
        rows = (
            item for item in [
                NewsItem(
                    "Wire", "2026-07-28 12:00", 0.14,
                    "tw_company_bullish_event",
                    "Example Taiwan secures new order", "",
                ),
                NewsItem(
                    "Wire", "2026-07-28 11:00", -0.08,
                    "tw_daily_macro_event",
                    "FOMC uncertainty pressures global markets", "",
                ),
            ]
        )
        result = analyze_news_causality(
            frame,
            rows,
            now=datetime(2026, 7, 28, 16, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(result["raw_count"], 2)
        self.assertEqual(result["classified_count"], 2)

    def test_intraday_missing_price_timestamp_never_uses_future_close(self):
        frame = _tw_frame(
            price_date="2026-07-29",
            status="intraday",
            last=91.0,
            previous=90.0,
            timestamp="",
        )
        news = [
            NewsItem(
                "Wire", "2026-07-29 09:30", 0.14,
                "tw_company_bullish_event",
                "Example Taiwan secures new order", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 29, 10, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(result["causal_state"], "event_time_unverified")
        self.assertFalse(result["can_compare_to_price"])
        self.assertEqual(result["price_observed_at"], "")

    def test_company_specific_export_control_stays_company_evidence(self):
        frame = _us_frame()
        news = [
            NewsItem(
                "Wire", "2026-07-29 04:05", -0.16,
                "bearish_us_company_policy_geo",
                "Example Corporation faces new export control restriction", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 29, 4, 25, tzinfo=TAIPEI),
        )
        self.assertEqual(result["company_family_count"], 1)
        self.assertEqual(result["global_family_count"], 0)
        self.assertEqual(result["dominant_family"], "regulatory_legal")
        self.assertEqual(result["cause_priority"], "company")

    def test_old_company_event_is_context_not_today_cause(self):
        frame = _tw_frame(
            price_date="2026-07-28",
            status="after_close",
            last=90.0,
            previous=100.0,
        )
        news = [
            NewsItem(
                "Official", "2026-07-01 16:00", 0.18,
                "tw_company_bullish_earnings",
                "Example Taiwan earnings beat estimates and raises guidance", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 28, 16, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(result["causal_state"], "event_context_only")
        self.assertEqual(result["cause_priority"], "price")
        self.assertFalse(result["can_compare_to_price"])
        self.assertIn("舊新聞", result["causal_text"])

    def test_stale_scheduled_preview_does_not_remain_pending(self):
        frame = _us_frame(status="pre_market", timestamp="07/29 19:00 台灣")
        news = [
            NewsItem(
                "Wire", "2026-07-20 18:30", 0.12,
                "bullish_us_company_earnings",
                "Example Corporation will report earnings after the bell", "",
            )
        ]
        result = analyze_news_causality(
            frame,
            news,
            now=datetime(2026, 7, 29, 19, 5, tzinfo=TAIPEI),
        )
        self.assertEqual(result["classified_count"], 0)
        self.assertEqual(result["causal_state"], "no_fresh_company_event")


if __name__ == "__main__":
    unittest.main()
