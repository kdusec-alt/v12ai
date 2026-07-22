# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest

from decision_narrative import build_ai_decision_narrative, price_reality
from models import DataTruth, NewsItem, PriceFrame, TickerInfo


def _direction(label="NEUTRAL", *, flow=0.0, conflict=0.20):
    return SimpleNamespace(
        label=label,
        score=28.0 if label == "UP" else -28.0 if label == "DOWN" else 0.0,
        conflict=conflict,
        p_up=0.58 if label == "UP" else 0.18 if label == "DOWN" else 0.30,
        p_neutral=0.24 if label != "NEUTRAL" else 0.45,
        p_down=0.18 if label == "UP" else 0.58 if label == "DOWN" else 0.25,
        family_contributions={"flow": flow, "trend": 4.0 if label == "UP" else -4.0 if label == "DOWN" else 0.0},
        factor_contributions={"法人": flow},
    )


def _frame(*, last=109.8, previous=100.0, vwap=108.0, high=109.8, low=105.0, macro=None, inst=None):
    closes = [82.0 + i * 0.9 for i in range(20)] + [previous, last]
    highs = [value + 1.0 for value in closes[:-1]] + [high]
    lows = [value - 1.0 for value in closes[:-1]] + [low]
    return PriceFrame(
        ticker=TickerInfo("2337.TW", "2337.TW", "旺宏", "TW", "stock", price_limit_pct=0.10),
        truth=DataTruth("UNIT_REAL", "2026-07-22", False, True, "unit"),
        open=previous,
        high=high,
        low=low,
        last=last,
        previous_close=previous,
        volume=10_000_000,
        vwap=vwap,
        atr14=2.0,
        recent_closes=closes,
        recent_highs=highs,
        recent_lows=lows,
        recent_volumes=[10_000_000] * len(closes),
        price_date="2026-07-22",
        market_status="after_close",
        context={
            "macro": macro or {},
            "inst": inst or {},
            "margin": {},
            "bsi": {},
        },
    )


def _narrative(frame, direction, news=None):
    return build_ai_decision_narrative(
        frame,
        direction,
        news or [],
        session_prefix="明日操盤",
        low1=107.0,
        low2=104.0,
        attack=110.0,
        stop=103.0,
        no_chase=112.0,
    )


class DecisionNarrativeTests(unittest.TestCase):
    def test_limit_up_with_negative_chips_is_divergence_not_generic_bearish(self):
        frame = _frame(
            macro={"accepted": True, "source": "UNIT", "sox": -2.2, "nq": -1.4, "mu": -3.0},
            inst={"accepted": True, "source": "OFFICIAL", "foreign": -1800, "trust": -400, "dealer": -200},
        )
        result = _narrative(frame, _direction("DOWN", flow=-5.0))
        self.assertEqual(result["state"], "surge_divergence")
        self.assertIn("不直接判空", result["title"])
        self.assertNotIn("方向偏空先防守", result["message"])
        self.assertIn("費半", result["evidence_line"])
        self.assertIn("法人", result["evidence_line"])

    def test_limit_up_with_aligned_evidence_is_extreme_breakout(self):
        frame = _frame(
            macro={"accepted": True, "source": "UNIT", "sox": 2.2, "nq": 1.4, "mu": 3.0},
            inst={"accepted": True, "source": "OFFICIAL", "foreign": 1800, "trust": 400, "dealer": 200},
        )
        news = [NewsItem("GoogleNewsTW", "2026-07-22 12:00", 0.16, "tw_company_bullish_event", "旺宏上調展望", "")]
        result = _narrative(frame, _direction("UP", flow=5.0), news)
        self.assertEqual(result["state"], "limit_breakout")
        self.assertIn("漲停／極強突破", result["title"])

    def test_negative_news_rejected_by_strong_price(self):
        frame = _frame()
        news = [NewsItem("GoogleNewsTW", "2026-07-22 12:00", -0.18, "tw_company_bearish_event", "旺宏下修財測", "")]
        result = _narrative(frame, _direction("UP", flow=2.0), news)
        self.assertEqual(result["state"], "bad_news_absorbed")
        self.assertIn("負面新聞未能壓低價格", result["message"])

    def test_positive_news_without_price_confirmation_is_rejected(self):
        frame = _frame(last=91.0, previous=100.0, vwap=95.0, high=100.0, low=90.5)
        news = [NewsItem("GoogleNewsTW", "2026-07-22 12:00", 0.18, "tw_company_bullish_event", "旺宏上調財測", "")]
        result = _narrative(frame, _direction("DOWN", flow=-2.0), news)
        self.assertEqual(result["state"], "good_news_rejected")
        self.assertIn("價格未買單", result["message"])

    def test_price_reality_detects_limit_like_close(self):
        reality = price_reality(_frame())
        self.assertTrue(reality.limit_like)
        self.assertTrue(reality.strong_up)

    def test_tw_four_percent_breakout_is_not_collapsed_into_generic_template(self):
        frame = _frame(last=104.3, previous=100.0, vwap=102.8, high=104.4, low=100.8)
        # Make the pre-session high lower so the +4.3% move is a real breakout.
        frame.recent_highs = [min(value, 102.0) for value in frame.recent_highs[:-1]] + [104.4]
        result = _narrative(frame, _direction("UP", flow=2.0))
        self.assertIn(result["state"], {"strong_continuation", "surge_divergence"})
        self.assertNotIn("range_wait", result["state"])


if __name__ == "__main__":
    unittest.main()
