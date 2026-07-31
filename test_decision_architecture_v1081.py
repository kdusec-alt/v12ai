# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import unittest

from decision_architecture_v1081 import assess_entry_opportunity


ROOT = Path(__file__).resolve().parent


def make_forecast(
    *,
    market="TW",
    session="intraday",
    open_price=100.0,
    high=104.0,
    low=98.0,
    last=102.0,
    day_pct=2.0,
    vwap=100.0,
    thesis_state="neutral",
    permission="conditional",
    action_mode="pullback",
    regime_state="",
    evidence="",
    fundamental_text="",
    event_verified=False,
    event_elapsed=None,
    event_severity=0,
    same_session=False,
    limit_locked=None,
    stop=94.0,
    no_chase=110.0,
    first=96.0,
    second=92.0,
    confirmation=104.0,
):
    price_truth = {
        "session": session,
        "current_price": last,
        "current_return_pct": day_pct,
        "vwap": vwap,
        "vwap_available": vwap is not None,
        "live_session_quote": True,
        "decision_blocked": False,
    }
    thesis = {
        "state": thesis_state,
        "entry_permission": permission,
        "action_mode": action_mode,
        "price_truth": price_truth,
        "event_verified": event_verified,
        "event_severity": event_severity,
    }
    if event_elapsed is not None:
        thesis["reaction_elapsed_minutes"] = event_elapsed
    decision = {
        "開盤": open_price,
        "最高": high,
        "最低": low,
        "現價": last,
        "漲跌幅": day_pct,
        "低接第一批": first,
        "低接第二批": second,
        "防守": stop,
        "不追": no_chase,
        "轉強": f"站穩 {confirmation}",
        "攻擊": f"站穩 {confirmation} 小量",
        "VWAP位置": "VWAP 上方" if vwap is not None and last > vwap else "VWAP 下方",
        "資料標題": "盤中資料" if session == "intraday" else "盤後資料",
        "_price_meta": {"session": session, "decision_blocked": False},
        "_direction_engine": {"gate_state": "B回測"},
        "_decision_thesis": thesis,
        "_session_truth_v1081": {"same_session": same_session},
    }
    if regime_state:
        decision["_market_regime_v1077"] = {"state": regime_state}
    if limit_locked is not None:
        decision["_price_meta"]["exchange_rule"] = {"is_limit_up_locked": limit_locked}
    radar = {"事件/Macro": evidence, "基本面": fundamental_text}
    return SimpleNamespace(
        decision_card=decision,
        radar=radar,
        news_items=[],
        ticker=SimpleNamespace(market=market, resolved_symbol="GENERIC"),
    )


class DecisionArchitectureV1081Tests(unittest.TestCase):
    def test_below_vwap_is_reclaim_not_pullback(self):
        row = assess_entry_opportunity(make_forecast(
            open_price=167.5, high=167.5, low=155.0,
            last=156.75, day_pct=2.79, vwap=159.75,
        ))
        self.assertEqual(row["state"], "WAIT_VWAP_RECLAIM")
        self.assertIn("收復", row["canonical_main_message"])
        self.assertNotIn("今日等回測", row["canonical_main_message"])
        self.assertEqual(row["price_tiles"][0]["label"], "狀態")
        self.assertTrue(row["price_shape"]["opening_selloff"])

    def test_alternate_today_ohlc_keys_feed_price_shape(self):
        forecast = make_forecast(last=156.75, day_pct=2.79, vwap=159.75)
        forecast.decision_card.pop("開盤")
        forecast.decision_card.pop("最高")
        forecast.decision_card.pop("最低")
        forecast.decision_card.update({"今日開盤": 167.5, "今日高": 167.5, "今日低": 155.0})
        row = assess_entry_opportunity(forecast)
        self.assertEqual(row["state"], "WAIT_VWAP_RECLAIM")
        self.assertTrue(row["price_shape"]["opening_selloff"])
        self.assertIn("開高後", row["summary"])

    def test_above_vwap_can_wait_for_real_pullback(self):
        row = assess_entry_opportunity(make_forecast(
            last=102.0, day_pct=1.0, vwap=100.0, thesis_state="neutral",
        ))
        self.assertEqual(row["state"], "WAIT_VWAP_PULLBACK")
        self.assertIn("回測", row["canonical_main_message"])

    def test_at_vwap_requires_hold_confirmation(self):
        row = assess_entry_opportunity(make_forecast(last=100.02, vwap=100.0, day_pct=1.2))
        self.assertEqual(row["state"], "WAIT_RECLAIM_HOLD")
        self.assertIn("維持", row["canonical_main_message"])

    def test_selling_exhaustion_above_vwap_reaches_small_confirmation(self):
        row = assess_entry_opportunity(make_forecast(
            last=102.0, day_pct=1.8, vwap=100.0,
            open_price=99.0, high=103.0, low=97.5,
            regime_state="selling_exhaustion",
            thesis_state="neutral",
        ))
        self.assertEqual(row["state"], "BUY_TODAY_CONFIRM")
        self.assertIn("賣壓已由擴張轉為衰竭", row["summary"])
        self.assertIn("小量", row["canonical_main_message"])

    def test_selling_exhaustion_below_vwap_still_requires_reclaim(self):
        row = assess_entry_opportunity(make_forecast(
            last=98.0, day_pct=-0.8, vwap=100.0,
            regime_state="selling_exhaustion",
        ))
        self.assertEqual(row["state"], "WAIT_VWAP_RECLAIM")

    def test_event_state_without_clock_does_not_create_permanent_15_30_template(self):
        row = assess_entry_opportunity(make_forecast(
            last=98.0, vwap=100.0, day_pct=-1.0,
            thesis_state="event_reaction_in_progress",
            event_verified=True,
            event_elapsed=None,
        ))
        self.assertEqual(row["state"], "WAIT_VWAP_RECLAIM")
        self.assertNotIn("15–30分鐘", row["summary"])
        self.assertTrue(row["event_context"]["clock_missing"])

    def test_verified_event_under_30_minutes_only_caps_green_state(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", last=108.0, day_pct=8.0, vwap=104.0,
            thesis_state="event_reaction_in_progress",
            event_verified=True, event_elapsed=12, event_severity=3,
            evidence="即時 QQQ +3.0%｜SOX +5.0%",
            same_session=True,
        ))
        self.assertEqual(row["state"], "WAIT_VWAP_PULLBACK")
        self.assertIn("12", row["summary"])
        self.assertEqual(row["reassessment_priority"], "P1_IMMEDIATE_RECALC")

    def test_event_over_30_minutes_does_not_keep_wait_template(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", last=108.0, day_pct=8.0, vwap=104.0,
            thesis_state="event_reaction_in_progress",
            event_verified=True, event_elapsed=45, event_severity=3,
            evidence="即時 QQQ +3.0%｜SOX +5.0%",
            same_session=True,
        ))
        self.assertEqual(row["state"], "BUY_TODAY_CONFIRM")
        self.assertFalse(row["event_context"]["wait_active"])

    def test_event_wait_never_overrides_selling_red(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", last=90.0, day_pct=-7.0, vwap=96.0,
            thesis_state="event_reaction_in_progress",
            event_verified=True, event_elapsed=8, event_severity=3,
            regime_state="selling_expansion",
        ))
        self.assertEqual(row["state"], "SELLING_EXPANSION_BLOCK")
        self.assertIn("禁止接刀", row["canonical_main_message"])

    def test_negative_fundamental_plus_weak_price_is_veto(self):
        row = assess_entry_opportunity(make_forecast(
            last=96.6, day_pct=-4.83, vwap=100.57,
            open_price=101.0, high=102.0, low=96.6,
            fundamental_text="財報低於預期｜EPS衰退｜來源：正式財報",
        ))
        self.assertEqual(row["state"], "SELLING_EXPANSION_BLOCK")
        self.assertEqual(row["fundamental"]["state"], "negative")
        self.assertIn("基本面", row["summary"])

    def test_negative_fundamental_but_strong_absorption_still_requires_pullback(self):
        row = assess_entry_opportunity(make_forecast(
            last=106.0, day_pct=6.0, vwap=102.0,
            open_price=101.0, high=107.0, low=100.5,
            fundamental_text="財報低於預期｜來源：正式財報",
            thesis_state="bad_news_absorbed",
        ))
        self.assertEqual(row["state"], "WAIT_VWAP_PULLBACK")
        self.assertIn("吸收", row["summary"])

    def test_relative_weakness_can_veto_market_proxy_optimism(self):
        row = assess_entry_opportunity(make_forecast(
            last=96.0, day_pct=-2.0, vwap=100.0,
            open_price=100.0, high=101.0, low=95.5,
            evidence="盤中即時 TAIEX +6.00%｜SOX +8.00%｜NQ +4.00%",
            same_session=True,
        ))
        self.assertEqual(row["state"], "SELLING_EXPANSION_BLOCK")
        self.assertTrue(row["market_context"]["severe_relative_weakness"])
        condition_text = "｜".join(item["text"] for item in row["conditions"])
        self.assertIn("落後市場", condition_text)

    def test_cross_market_support_without_session_truth_is_not_confirmation(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", last=104.0, day_pct=4.0, vwap=101.0,
            evidence="SOX +5.00%｜QQQ +3.00%｜NQ +3.20%",
            same_session=False,
            thesis_state="neutral",
        ))
        self.assertTrue(row["market_context"]["supportive"])
        self.assertFalse(row["market_context"]["confirmed"])

    def test_limit_price_is_liquidity_state_not_automatic_next_day_block(self):
        row = assess_entry_opportunity(make_forecast(
            last=109.99, day_pct=9.99, vwap=107.0,
            open_price=109.99, high=109.99, low=109.99,
            limit_locked=None,
        ))
        self.assertEqual(row["state"], "LIMIT_LIQUIDITY_WAIT")
        self.assertIn("限價", row["canonical_main_message"])
        self.assertNotEqual(row["state"], "WAIT_NEXT_SESSION")

    def test_opened_limit_can_fall_through_to_live_price_logic(self):
        row = assess_entry_opportunity(make_forecast(
            last=108.0, day_pct=8.0, vwap=106.0,
            open_price=109.99, high=109.99, low=105.5,
            limit_locked=False,
        ))
        self.assertNotEqual(row["state"], "LIMIT_LIQUIDITY_WAIT")
        self.assertIn(row["state"], {"WAIT_VWAP_PULLBACK", "BUY_TODAY_CONFIRM", "OVERHEATED_NO_CHASE"})

    def test_after_hours_waits_for_regular_session(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", session="after_hours", last=160.0,
            day_pct=25.0, vwap=157.0,
        ))
        self.assertEqual(row["state"], "WAIT_NEXT_SESSION")

    def test_every_state_owns_five_price_tiles(self):
        scenarios = [
            make_forecast(last=98.0, vwap=100.0),
            make_forecast(last=102.0, vwap=100.0),
            make_forecast(last=90.0, day_pct=-7.0, vwap=96.0, regime_state="selling_expansion"),
            make_forecast(last=109.99, day_pct=9.99, vwap=107.0, open_price=109.99, high=109.99, low=109.99),
            make_forecast(last=102.0, vwap=100.0, regime_state="selling_exhaustion"),
        ]
        for forecast in scenarios:
            with self.subTest(state=assess_entry_opportunity(forecast)["state"]):
                self.assertEqual(len(assess_entry_opportunity(forecast)["price_tiles"]), 5)

    def test_module_contains_no_ticker_or_company_special_case(self):
        source = (ROOT / "decision_architecture_v1081.py").read_text(encoding="utf-8")
        self.assertNotIn('ticker ==', source)
        self.assertNotIn('resolved_symbol ==', source)
        for code in ("2308", "5483", "6217", "6770"):
            self.assertNotIn(code, source)


if __name__ == "__main__":
    unittest.main()
