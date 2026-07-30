# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from event_impact_lexicon_v1078 import assess_major_event
from event_intelligence_v1062 import _dominant_shock
from event_reassessment import classify_event
from market_shock_levels_v1062 import (
    assess_market_shock_v1062 as assess_market_shock,
    install_market_shock_levels_v1062,
)
from models import NewsItem
from news_causal_intelligence_v1073 import analyze_news_causality
from test_news_causal_intelligence_v1073 import _narrative, _tw_frame


TAIPEI = ZoneInfo("Asia/Taipei")
install_market_shock_levels_v1062()


class EventAttributionV1078Tests(unittest.TestCase):
    def _quanta_frame(self):
        return _tw_frame(
            symbol="2382.TW",
            name="廣達",
            price_date="2026-07-30",
            status="intraday",
            last=281.5,
            previous=309.5,
            timestamp="2026-07-30T11:00:00+08:00",
        )

    def _quanta_news(self):
        return [
            NewsItem(
                "Reuters",
                "2026-07-30 08:00",
                -0.22,
                "tw_company_2382_capital_action_capital_financing",
                "廣達發行4,900萬單位GDS募資22億美元，每單位代表5股，折價約7.8%",
                "",
            ),
            NewsItem(
                "Wire",
                "2026-07-30 07:30",
                0.18,
                "tw_company_2382_earnings_bullish_event",
                "廣達受惠微軟財報優於預期，AI伺服器需求強勁",
                "",
            ),
        ]

    def test_self_gds_outranks_customer_earnings(self):
        result = analyze_news_causality(
            self._quanta_frame(),
            self._quanta_news(),
            now=datetime(2026, 7, 30, 11, 5, tzinfo=TAIPEI),
        )
        self.assertEqual(result["dominant_family"], "capital_financing")
        self.assertEqual(result["dominant_priority_tier"], 4)
        self.assertEqual(result["dominant_subject_role"], "self")
        self.assertEqual(result["cause_priority"], "company")
        self.assertEqual(result["causal_state"], "event_price_confirming")
        self.assertLess(result["company_score"], 0.0)
        self.assertIn("公司級籌資利空已獲價格確認", result["company_text"])
        customer = [
            row for row in result["selected_company_events"]
            if row["family"] == "counterparty_event"
        ][0]
        self.assertEqual(customer["event_owner"], "microsoft")
        self.assertLess(customer["priority_tier"], result["dominant_priority_tier"])

    def test_customer_earnings_cannot_impersonate_target_earnings(self):
        result = analyze_news_causality(
            self._quanta_frame(),
            [self._quanta_news()[1]],
            now=datetime(2026, 7, 30, 11, 5, tzinfo=TAIPEI),
        )
        self.assertEqual(result["dominant_family"], "counterparty_event")
        self.assertEqual(result["dominant_scope"], "industry")
        self.assertEqual(result["dominant_subject_role"], "counterparty")
        self.assertEqual(result["dominant_event_owner"], "microsoft")
        self.assertEqual(result["cause_priority"], "industry_narrative")

    def test_gds_price_confirmation_reaches_specialized_ai_card(self):
        frame = self._quanta_frame()
        frame.context["news_causal_v1073"] = analyze_news_causality(
            frame,
            self._quanta_news(),
            now=datetime(2026, 7, 30, 11, 5, tzinfo=TAIPEI),
        )
        result = _narrative(frame, self._quanta_news())
        self.assertEqual(result["state"], "capital_raise_repricing")
        self.assertIn("增資折價重估", result["title"])
        self.assertIn("籌資利空獲價格確認", result["title"])
        self.assertIn("不應由客戶財報", result["message"])

    def test_oil_move_uses_numeric_magnitude_buckets(self):
        medium = assess_major_event(
            "WTI +4.20%／Brent +3.80%",
            "global_event_core|family=energy|oil_price_up|magnitude_pct=+4.2000",
        )
        severe = assess_major_event(
            "WTI +6.07%／Brent +6.05%",
            "global_event_core|family=energy|oil_price_up|magnitude_pct=+6.0700",
        )
        self.assertEqual(medium["priority_tier"], 3)
        self.assertEqual(severe["priority_tier"], 4)
        self.assertGreater(severe["impact_score"], medium["impact_score"])
        self.assertEqual(severe["magnitude_basis"], "market_move_pct")

    def test_war_language_has_escalation_ladder(self):
        tension = assess_major_event("台海緊張升高並展開軍演")
        attack = assess_major_event("伊朗遭飛彈攻擊，衝突升級")
        blockade = assess_major_event("正式開戰並封鎖荷姆茲海峽")
        self.assertEqual(tension["priority_tier"], 3)
        self.assertEqual(attack["priority_tier"], 4)
        self.assertEqual(blockade["priority_tier"], 5)
        self.assertGreater(blockade["impact_score"], attack["impact_score"])

    def test_market_shock_level_changes_with_measured_oil_move(self):
        medium = NewsItem(
            "Core", "2026-07-30T10:00:00+08:00", -0.18,
            "global_event_core|family=energy|severity=3|oil_price_up|"
            "magnitude_pct=+4.2000|ticker_profile=broad",
            "WTI +4.20%／Brent +3.80%", "",
        )
        severe = NewsItem(
            "Core", "2026-07-30T10:00:00+08:00", -0.24,
            "global_event_core|family=energy|severity=4|oil_price_up|"
            "magnitude_pct=+6.0700|ticker_profile=broad",
            "WTI +6.07%／Brent +6.05%", "",
        )
        self.assertEqual(assess_market_shock(medium)["level"], 3)
        self.assertEqual(assess_market_shock(severe)["level"], 4)
        self.assertGreater(
            assess_market_shock(severe)["score"],
            assess_market_shock(medium)["score"],
        )

    def test_event_watcher_classifies_gds_as_structural_repricing(self):
        event = classify_event(self._quanta_news()[0])
        self.assertEqual(event["category"], "company_capital_raise")
        self.assertEqual(event["priority_tier"], 4)
        self.assertGreaterEqual(event["impact_score"], 80.0)
        self.assertIn("EPS稀釋", event["transmission"])

    def test_war_plus_verified_oil_move_is_one_escalated_chain(self):
        war = NewsItem(
            "Reuters", "2026-07-30T09:55:00+08:00", -0.18,
            "global_event_core|family=geopolitical|severity=3|"
            "eventid=iran_war|ticker_profile=broad",
            "Iran war escalates after missile strike", "",
        )
        oil = NewsItem(
            "Core", "2026-07-30T10:00:00+08:00", -0.18,
            "global_event_core|family=energy|severity=3|oil_price_up|"
            "magnitude_pct=+4.2000|ticker_profile=broad",
            "WTI +4.20%／Brent +3.80%", "",
        )
        shock = _dominant_shock([war, oil])
        self.assertEqual(shock["level"], 5)
        self.assertEqual(shock["magnitude_pct"], 4.2)
        self.assertEqual(shock["magnitude_basis"], "war_plus_verified_oil_move")
        self.assertIn("伊朗/中東戰爭", shock["drivers"])
        self.assertIn("油價急升", shock["drivers"])


if __name__ == "__main__":
    unittest.main()
