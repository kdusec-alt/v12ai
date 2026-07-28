# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest

from exchange_rule_engine_v1069 import (
    assess_today_reachability,
    build_exchange_rule_snapshot,
    classify_static_rule,
    tw_daily_price_bounds,
)
from low_entry_readiness_v1065 import assess_low_entry_readiness
from models import TickerInfo
from price_guard import apply_market_bounds
from ticker_resolver import resolve_ticker


def _rule_snapshot(
    ticker: TickerInfo,
    *,
    reference: float,
    current: float,
    historical: float | None = None,
    source: str = "TPEX_MIS_Realtime",
    market_status: str = "intraday",
):
    return build_exchange_rule_snapshot(
        ticker,
        reference_price=reference,
        current_price=current,
        historical_previous_close=historical,
        reference_source=source,
        market_status=market_status,
        price_date="2026-07-28",
        quote_name=ticker.name,
    )


def _forecast_with_rule(*, last=189.0, first=181.34, second=175.53, stop=171.79, confirmation=191.83, rule=None):
    decision = {
        "現價": last,
        "低接第一批": first,
        "低接第二批": second,
        "防守": stop,
        "不追": 199.00,
        "攻擊": "事件前縮小試單",
        "轉強": f"站穩 {confirmation:.2f} 才轉強",
        "VWAP位置": "VWAP 下方",
        "漲跌幅": -7.35,
        "標題": "AI進場決策卡｜事件卡｜公布後確認",
        "主訊息": f"站穩 {confirmation:.2f} 才小單，回測 {first:.2f} 止穩再分批，破 {stop:.2f} 停。",
        "決策分": -10,
        "_direction_engine": {"gate_state": "B回測", "score": -10},
        "_trend_snapshot": {"ma20_gap_pct": -13.0},
        "_price_meta": {"decision_blocked": False, "exchange_rule": dict(rule or {})},
    }
    radar = {
        "Fair Value": "保守 168.25｜中性 189.00｜樂觀 209.75",
        "左側籌碼摘要": "法人分歧｜外資偏空",
        "三大法人": "外資偏空",
        "資券 / 融資融券": "融資觀察",
        "空方成本 / 回補": "等待回補",
    }
    return SimpleNamespace(
        decision_card=decision,
        radar=radar,
        news_items=[],
        ticker=SimpleNamespace(market="TW"),
        no_chase=199.00,
        confidence=51,
    )


class ExchangeRuleV1069Tests(unittest.TestCase):
    def test_twse_and_tpex_common_stock_share_ten_percent_rule(self):
        twse = resolve_ticker("2330")
        tpex = resolve_ticker("3163")
        self.assertEqual(twse.market, "TW")
        self.assertEqual(twse.price_limit_pct, 0.10)
        self.assertEqual(tpex.resolved_symbol, "3163.TWO")
        self.assertEqual(tpex.exchange, "TPEX")
        self.assertEqual(tpex.price_limit_pct, 0.10)

    def test_two_suffix_does_not_mean_unlimited(self):
        ticker = resolve_ticker("5483.TWO")
        self.assertEqual(ticker.exchange, "TPEX")
        self.assertEqual(ticker.price_limit_pct, 0.10)

    def test_emerging_and_us_have_no_taiwan_static_limit(self):
        emerging = resolve_ticker("6586")
        us = resolve_ticker("AAPL")
        self.assertEqual(emerging.exchange, "TPEX_EMERGING")
        self.assertIsNone(emerging.price_limit_pct)
        self.assertIsNone(us.price_limit_pct)

    def test_domestic_leveraged_etf_uses_twenty_percent_and_etf_tick(self):
        ticker = resolve_ticker("00631L")
        self.assertEqual(ticker.asset_type, "etf")
        self.assertEqual(ticker.price_limit_pct, 0.20)
        lower, upper = tw_daily_price_bounds(31.12, 0.20, "DOMESTIC_LEVERAGED_ETF")
        self.assertEqual(lower, 24.90)
        self.assertEqual(upper, 37.34)

    def test_foreign_underlying_etf_has_no_static_limit(self):
        rule = classify_static_rule(
            market="TW", exchange="TWSE", asset_type="etf",
            symbol="00662.TW", name="富邦NASDAQ",
        )
        self.assertFalse(rule["fixed_daily_limit"])
        self.assertIsNone(rule["price_limit_pct"])

    def test_common_stock_bounds_use_directional_tick_rounding(self):
        self.assertEqual(tw_daily_price_bounds(204.0, 0.10), (184.0, 224.0))
        self.assertEqual(tw_daily_price_bounds(631.0, 0.10), (568.0, 694.0))
        self.assertEqual(apply_market_bounds(180.0, 204.0, "TW", 0.10), 184.0)
        self.assertEqual(apply_market_bounds(230.0, 204.0, "TW", 0.10), 224.0)

    def test_ex_right_reference_prevents_fake_sixteen_percent_drop(self):
        ticker = resolve_ticker("3163")
        snapshot = _rule_snapshot(ticker, reference=631.0, current=622.0, historical=743.0)
        self.assertTrue(snapshot["reference_verified"])
        self.assertTrue(snapshot["reference_adjusted"])
        self.assertAlmostEqual(snapshot["official_change_pct"], -1.4263, places=4)
        self.assertEqual(snapshot["daily_lower"], 568.0)
        self.assertEqual(snapshot["daily_upper"], 694.0)

    def test_first_batch_below_official_lower_is_not_rewritten(self):
        ticker = resolve_ticker("5483")
        snapshot = _rule_snapshot(ticker, reference=204.0, current=189.0, historical=204.0)
        reach = assess_today_reachability(181.34, snapshot)
        self.assertFalse(reach["reachable"])
        self.assertEqual(reach["status"], "below_daily_lower")
        self.assertEqual(reach["lower"], 184.0)
        self.assertEqual(reach["entry"], 181.34)

        result = assess_low_entry_readiness(_forecast_with_rule(rule=snapshot))
        self.assertEqual(result["reachability"]["status"], "below_daily_lower")
        self.assertEqual(result["wait_plan"]["pullback"], 181.34)
        self.assertIn("181.34", result["summary"])
        self.assertIn("184.00", result["summary"])
        self.assertIn("今日不可達", result["summary"])
        self.assertIn("不為了成交", result["summary"])
        self.assertNotEqual(result["color"], "green")

    def test_unverified_reference_never_hard_changes_entry(self):
        ticker = resolve_ticker("5483")
        snapshot = _rule_snapshot(
            ticker, reference=204.0, current=189.0, historical=204.0,
            source="YahooChart_1m",
        )
        reach = assess_today_reachability(181.34, snapshot)
        self.assertEqual(reach["status"], "reference_pending")
        result = assess_low_entry_readiness(_forecast_with_rule(rule=snapshot))
        self.assertEqual(result["wait_plan"]["pullback"], 181.34)
        self.assertIn("官方交易基準尚未驗證", result["summary"])

    def test_official_new_listing_override_can_disable_static_limit(self):
        ticker = TickerInfo(
            raw="9999", resolved_symbol="9999.TW", name="測試新股",
            market="TW", asset_type="stock", exchange="TWSE", currency="TWD",
            price_limit_pct=0.10,
        )
        snapshot = build_exchange_rule_snapshot(
            ticker,
            reference_price=100.0,
            current_price=150.0,
            reference_source="TWSE_MIS_Realtime",
            market_status="intraday",
            official_no_static_limit=True,
            official_rule_code="TW_INITIAL_LISTING_FIRST_FIVE_DAYS",
        )
        self.assertFalse(snapshot["fixed_daily_limit"])
        self.assertIsNone(snapshot["daily_lower"])
        self.assertIsNone(snapshot["daily_upper"])
        self.assertEqual(snapshot["rule_confidence"], "official")


if __name__ == "__main__":
    unittest.main()
