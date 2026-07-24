# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from models import NewsItem
from market_shock_indicator import assess_market_shock, annotate_market_shock_news
from event_reassessment_v1062 import (
    classify_event_v1062,
    assess_event_delta_v1062,
    event_watch_display_v1062,
    install_event_reassessment_v1062,
)


class MarketShockIndicatorTests(unittest.TestCase):
    def _row(self, title: str, tag: str, score: float = -0.18) -> NewsItem:
        return NewsItem(
            "Reuters",
            "2026-07-24T21:30:00+08:00",
            score,
            tag,
            title,
            "https://example.test",
        )

    def test_hormuz_and_iran_war_reach_extreme_shock(self):
        row = self._row(
            "Iran war escalates and Hormuz blockade pushes Brent higher",
            "global_event_core|family=energy|severity=3|oil_price_up|ticker_profile=memory",
        )
        shock = assess_market_shock(row)
        self.assertEqual(shock["level"], 5)
        self.assertGreaterEqual(shock["depth"], 5)
        self.assertIn("荷姆茲航道", shock["drivers"])
        self.assertIn("伊朗/中東戰爭", shock["drivers"])

    def test_oil_spike_is_stronger_for_airline_than_biotech(self):
        airline = self._row(
            "Brent tops 100 as oil prices jump",
            "global_event_core|family=energy|severity=3|oil_price_up|ticker_profile=airline",
        )
        biotech = self._row(
            "Brent tops 100 as oil prices jump",
            "global_event_core|family=energy|severity=3|oil_price_up|ticker_profile=biotech",
        )
        self.assertGreater(
            assess_market_shock(airline)["score"],
            assess_market_shock(biotech)["score"],
        )

    def test_falling_oil_is_not_mislabeled_as_spike(self):
        row = self._row(
            "Oil prices fall sharply",
            "global_event_core|family=energy|severity=3|oil_price_down|ticker_profile=broad",
            score=0.10,
        )
        shock = assess_market_shock(row)
        self.assertLessEqual(shock["level"], 2)
        self.assertNotIn("油價急升", shock["drivers"])

    def test_annotation_adds_visible_shock_metadata(self):
        row = self._row(
            "Brent tops 100 as Hormuz risk rises",
            "global_event_core|family=energy|severity=3|oil_price_up|ticker_profile=semiconductor",
        )
        tagged = annotate_market_shock_news([row])[0]
        self.assertIn("shock_level=", tagged.tag)
        self.assertIn("shock_score=", tagged.tag)
        self.assertIn("shock_depth=", tagged.tag)

    def test_five_minute_watcher_elevates_l5_to_red_alert(self):
        install_event_reassessment_v1062()
        now = datetime(2026, 7, 24, 22, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        row = self._row(
            "Iran war escalates and Hormuz blockade pushes Brent higher",
            "global_event_core|family=energy|severity=3|eventid=iran_hormuz_1|oil_price_up|ticker_profile=memory",
        )
        classified = classify_event_v1062(row)
        self.assertEqual(classified["market_shock_level"], 5)
        self.assertGreaterEqual(classified["severity"], 4)

        plan = assess_event_delta_v1062(
            [],
            [row],
            ticker="2337.TW",
            market="TW",
            not_before_epoch=now.timestamp() - 3600,
            now=now,
        )
        self.assertEqual(plan["market_shock_level"], 5)
        self.assertTrue(plan["needs_reassessment"])
        payload = event_watch_display_v1062(
            plan,
            notice=str(plan.get("event_title") or ""),
            ticker="2337.TW",
        )
        self.assertEqual(payload["level"], "error")
        self.assertIn("市場衝擊 L5", payload["text"])


if __name__ == "__main__":
    unittest.main()
