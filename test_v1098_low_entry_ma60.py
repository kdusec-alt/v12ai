# -*- coding: utf-8 -*-
from types import SimpleNamespace
import unittest

from decision_core_v1096 import build_decision_snapshot
from decision_architecture_v1081 import assess_entry_opportunity
from models import DataTruth, PriceFrame, TickerInfo
from trend_engine import build_trend_snapshot


def _price_frame(*, last=72.70, status="intraday"):
    ticker = TickerInfo("6770", "6770.TW", "力積電", "TW", "stock", "TWSE", "TWD", 0.10)
    closes = [55.0 + i * 0.30 for i in range(60)] + [last]
    return PriceFrame(
        ticker=ticker,
        truth=DataTruth("UNIT", "2026-09-21", False, True, "verified"),
        open=74.40, high=74.50, low=72.50, last=last, previous_close=74.00,
        volume=55_840_000, vwap=73.30, atr14=2.0,
        recent_closes=closes,
        recent_highs=[value + 0.5 for value in closes],
        recent_lows=[value - 0.5 for value in closes],
        recent_volumes=[10_000_000] * len(closes),
        price_date="2026-09-21", market_status=status,
        context={
            "price_meta": {"source": "TWSE_MIS", "decision_blocked": False},
            "inst": {
                "accepted": True, "foreign": 7_791, "foreign_3": 13_194,
                "source": "YahooInstitutional", "date": "2026-09-18", "reason": "verified",
            },
            "margin": {"accepted": False, "margin": None, "source": "WAIT"},
            "market_heat": {"accepted": False, "change": None, "source": "WAIT"},
            "macro": {"accepted": True, "score": 1.0, "source": "MARKET", "date": "2026-09-21", "reason": "verified"},
        },
    )


def _forecast(*, last=72.70):
    price = _price_frame(last=last)
    day_return = (last / price.previous_close - 1.0) * 100.0
    price.context["price_snapshot"] = {
        "last": last, "previous_close": price.previous_close,
        "open": price.open, "high": price.high, "low": price.low,
        "vwap": price.vwap,
    }
    card = {
        "現價": last, "昨收": price.previous_close, "開盤": price.open,
        "最高": price.high, "最低": price.low, "漲跌幅": day_return,
        "VWAP": price.vwap, "VWAP位置": "VWAP 下方", "防守": 69.46,
        "不追": 77.69, "轉強": "站穩 74.60", "攻擊": "站穩 74.60",
        "資料標題": "盤中資料",
        "_price_meta": {"session": "intraday", "decision_blocked": False},
        "_decision_thesis": {
            "state": "deep_stabilization", "entry_permission": "conditional",
            "action_mode": "pullback_or_confirmation",
            "price_truth": {
                "session": "intraday", "current_price": last,
                "current_return_pct": day_return, "open": price.open,
                "high": price.high, "low": price.low, "vwap": price.vwap,
                "vwap_available": True, "live_session_quote": True,
                "decision_blocked": False,
            },
        },
    }
    return SimpleNamespace(
        ticker=price.ticker, price_frame=price,
        raw=SimpleNamespace(raw_abc={"A": 40, "B": 51, "C": 9}),
        final_t0=last, final_t1=last * 1.0048, final_t1_high=75.5, final_t1_low=69.5,
        confidence=74, no_chase=77.69, low_entry=72.50,
        decision_card=card, radar={},
        data_truths=[price.truth], news_items=[], signals=[], tags=[],
        one_liner="", reality_anchor="", trace=None,
    )


def _momentum_forecast():
    last, previous, vwap = 4942.5, 4710.0, 4900.0
    day_return = (last / previous - 1.0) * 100.0
    return SimpleNamespace(
        ticker=SimpleNamespace(market="TW", resolved_symbol="2454.TW", asset_type="stock"),
        radar={}, news_items=[],
        decision_card={
            "現價": last, "漲跌幅": day_return, "開盤": 4800.0,
            "最高": 5035.0, "最低": 4780.0, "VWAP": vwap,
            "防守": 4780.0, "不追": 5095.0, "轉強": "站穩 5095",
            "資料標題": "盤中資料", "_price_meta": {"session": "intraday"},
            "_decision_thesis": {
                "state": "strong_continuation", "entry_permission": "conditional",
                "action_mode": "pullback_or_confirmation",
                "price_truth": {
                    "session": "intraday", "current_price": last,
                    "current_return_pct": day_return, "open": 4800.0,
                    "high": 5035.0, "low": 4780.0, "vwap": vwap,
                    "vwap_available": True, "live_session_quote": True,
                },
            },
        },
    )


class V1098LowEntryMA60Tests(unittest.TestCase):
    def test_intraday_extra_bar_preserves_formal_ma60(self):
        snap = build_trend_snapshot(_price_frame())
        self.assertIsNotNone(snap.ma60)
        self.assertIsNotNone(snap.ma60_gap_pct)

    def test_red_pullback_becomes_left_low_candidate_not_blanket_block(self):
        payload = build_decision_snapshot(_forecast(), prior_snapshot={}).to_dict()
        gate = payload["reasoning"]["cross_module_gate"]
        self.assertTrue(gate["left_low_candidate"])
        self.assertTrue(gate["controlled_low_trade"])
        self.assertNotEqual(gate["code"], "NO_ENTRY_CONFLICT")
        self.assertEqual(payload["action_code"], "HOLD")
        self.assertEqual(payload["entry"]["trigger_status"], "PENDING")
        self.assertIn(payload["entry"]["entry_state_code"], {"IN_PULLBACK", "WAIT_STABILIZE"})

    def test_bounce_from_low_zone_can_trigger_small_left_entry(self):
        payload = build_decision_snapshot(_forecast(last=72.95), prior_snapshot={}).to_dict()
        self.assertTrue(payload["reasoning"]["cross_module_gate"]["left_low_candidate"])
        self.assertEqual(payload["entry"]["trigger_status"], "TRIGGERED")
        self.assertEqual(payload["action_code"], "BUY")
        self.assertEqual(payload["situation_code"], "BUY_LOW_ENTRY")
        self.assertIn("20%～30%試單", payload["instruction"])

    def test_green_momentum_move_is_not_bought_after_three_percent(self):
        entry = assess_entry_opportunity(_momentum_forecast())
        self.assertEqual(entry["state"], "OVERHEATED_NO_CHASE")
        self.assertIn("追價風險", entry["summary"])


if __name__ == "__main__":
    unittest.main()
