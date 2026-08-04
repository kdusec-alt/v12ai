# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import unittest

from entry_opportunity_v1080 import assess_entry_opportunity


ROOT = Path(__file__).resolve().parent


def make_forecast(
    *, market="TW", session="intraday", last=54.5, day_pct=9.99,
    first=50.8, second=49.31, stop=48.35, no_chase=57.1,
    confirmation=55.46, vwap=53.8, thesis_state="surge_divergence",
    permission="conditional", action_mode="pullback", gate="A突破",
    evidence="TSM_ADR +7.64%｜SOX +8.19%｜NQ +4.48%｜VIX 17.09",
    regime_state="", decision_blocked=False, truth_last=None,
    truth_day_pct=None, live_session_quote=False,
):
    truth_price = last if truth_last is None else truth_last
    truth_return = day_pct if truth_day_pct is None else truth_day_pct
    decision = {
        "現價": last,
        "漲跌幅": day_pct,
        "低接第一批": first,
        "低接第二批": second,
        "防守": stop,
        "不追": no_chase,
        "轉強": f"站穩 {confirmation:.2f}",
        "攻擊": f"站穩 {confirmation:.2f} 小量",
        "VWAP位置": "VWAP 上方" if vwap and truth_price >= vwap else "VWAP 下方",
        "資料標題": "盤中資料" if session == "intraday" else "盤後資料",
        "_price_meta": {"decision_blocked": decision_blocked, "session": session},
        "_direction_engine": {"gate_state": gate},
        "_decision_thesis": {
            "state": thesis_state,
            "entry_permission": permission,
            "action_mode": action_mode,
            "price_truth": {
                "session": session,
                "current_price": truth_price,
                "current_return_pct": truth_return,
                "vwap": vwap,
                "vwap_available": vwap is not None,
                "live_session_quote": live_session_quote,
                "decision_blocked": decision_blocked,
            },
        },
    }
    if regime_state:
        decision["_market_regime"] = {"state": regime_state}
    return SimpleNamespace(
        decision_card=decision,
        radar={"事件/Macro": evidence},
        news_items=[],
        ticker=SimpleNamespace(market=market),
    )


class EntryOpportunityV1080Tests(unittest.TestCase):
    def test_tw_limit_up_waits_next_session_not_old_low_entry(self):
        row = assess_entry_opportunity(make_forecast())
        self.assertEqual(row["state"], "WAIT_NEXT_SESSION")
        self.assertIn("今日不追", row["canonical_main_message"])
        self.assertIn("下一交易時段", row["price_strategy_text"])
        self.assertNotIn("首選買點", row["canonical_main_message"])

    def test_event_repricing_can_allow_small_confirmation_today(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", last=141.5, day_pct=11.6, first=129.0,
            second=126.0, stop=124.0, no_chase=139.0,
            confirmation=143.0, vwap=139.8,
            thesis_state="strong_continuation", session="intraday",
            evidence="SOX +8.19%｜NQ +4.22%｜QQQ +3.30%｜SMH +6.88%",
        ))
        self.assertEqual(row["state"], "BUY_TODAY_CONFIRM")
        self.assertTrue(row["anchor_invalidated"])
        self.assertIn("小量參與", row["canonical_main_message"])
        self.assertIn("原低接", row["summary"])

    def test_after_hours_extreme_move_waits_for_regular_session(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", session="after_hours", last=149.0, day_pct=17.52,
            truth_last=160.41, truth_day_pct=26.52, live_session_quote=True,
            first=141.21, second=130.84, stop=127.24, no_chase=152.60,
            confirmation=155.79, vwap=157.5,
            thesis_state="strong_continuation",
            evidence="SOX +8.19%｜NQ +4.22%｜QQQ +3.30%｜SMH +6.88%",
        ))
        self.assertEqual(row["state"], "WAIT_NEXT_SESSION")
        self.assertEqual(row["operative_price"], 160.41)
        self.assertEqual(row["operative_return_pct"], 26.52)
        self.assertIn("盤後", row["summary"])
        self.assertIn("缺口", row["canonical_main_message"])

    def test_formal_close_after_market_cannot_say_buy_today(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", session="after_close", last=149.0, day_pct=17.52,
            first=141.21, second=130.84, stop=127.24, no_chase=152.60,
            confirmation=155.79, vwap=146.0,
            thesis_state="strong_continuation",
            evidence="SOX +8.19%｜NQ +4.22%｜QQQ +3.30%｜SMH +6.88%",
        ))
        self.assertEqual(row["state"], "WAIT_NEXT_SESSION")
        self.assertNotIn("今日可小量參與", row["canonical_main_message"])

    def test_generic_limit_up_headline_does_not_lock_a_us_ticker(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", session="intraday", last=141.5, day_pct=11.6,
            first=129.0, second=126.0, stop=124.0, no_chase=139.0,
            confirmation=143.0, vwap=139.8,
            thesis_state="strong_continuation",
            evidence="SOX +8.19%｜NQ +4.22%｜舊新聞提到其他股票漲停",
        ))
        self.assertEqual(row["state"], "BUY_TODAY_CONFIRM")

    def test_event_first_reaction_never_becomes_buy_now(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", session="intraday", last=141.5, day_pct=11.6,
            first=129.0, second=126.0, stop=124.0, no_chase=139.0,
            confirmation=143.0, vwap=139.8,
            thesis_state="event_reaction_in_progress", permission="blocked",
            evidence="SOX +8.19%｜NQ +4.22%｜QQQ +3.30%",
        ))
        self.assertEqual(row["state"], "WAIT_INTRADAY_PULLBACK")
        self.assertIn("15–30分鐘", row["summary"])

    def test_selling_expansion_blocks_low_price_catching(self):
        row = assess_entry_opportunity(make_forecast(
            market="US", last=90.0, day_pct=-7.0, first=91.0,
            second=88.0, stop=85.0, no_chase=100.0,
            confirmation=96.0, vwap=94.0,
            thesis_state="macro_beta_selloff", permission="blocked",
            action_mode="reclaim_only", gate="C防守",
            evidence="SOX -4.0%｜NQ -3.0%｜QQQ -2.7%",
            regime_state="selling_expansion",
        ))
        self.assertEqual(row["state"], "SELLING_EXPANSION_BLOCK")
        self.assertIn("禁止接刀", row["canonical_main_message"])

    def test_exhaustion_plus_vwap_reclaim_allows_confirmation(self):
        row = assess_entry_opportunity(make_forecast(
            market="TW", last=48.8, day_pct=1.2, first=48.2,
            second=46.8, stop=45.9, no_chase=52.0,
            confirmation=49.2, vwap=48.5,
            thesis_state="deep_stabilization", gate="B回測",
            evidence="SOX +1.0%｜NQ +0.8%",
            regime_state="selling_exhaustion",
        ))
        self.assertEqual(row["state"], "BUY_TODAY_CONFIRM")
        self.assertIn("賣壓已由擴張轉為衰竭", row["canonical_main_message"])

    def test_formal_forecast_is_explicitly_unchanged(self):
        row = assess_entry_opportunity(make_forecast())
        self.assertTrue(row["formal_forecast_unchanged"])
        self.assertTrue(row["narrative_only"])
        self.assertFalse(row["show_score"])

    def test_battle_panel_reads_single_snapshot_not_low_entry_maturity(self):
        source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")
        self.assertIn("_decision_snapshot_payload", source)
        self.assertNotIn("entry = assess_entry_opportunity(p)", source)
        self.assertIn("AI交易決策", source)
        self.assertIn("price_tiles_html", source)
        self.assertIn('entry.get("price_tiles")', source)
        self.assertNotIn("AI低接成熟度", source)
        self.assertNotIn("from low_entry_readiness_v1065 import assess_low_entry_readiness", source)


if __name__ == "__main__":
    unittest.main()
