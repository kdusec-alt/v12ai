# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from models import NewsItem
from event_intelligence_v1062 import assess_policy_geo_v1062


class EventIntelligenceV1062Tests(unittest.TestCase):
    def _row(self, profile: str, title: str, tag: str, score: float = -0.18) -> NewsItem:
        return NewsItem(
            "Reuters",
            "2026-07-24T21:30:00+08:00",
            score,
            f"global_event_core|{tag}|ticker_profile={profile}|ticker_label={profile}",
            title,
            "https://example.test",
        )

    def test_tariff_hits_memory_more_than_biotech(self):
        now = datetime(2026, 7, 24, 22, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        memory = assess_policy_geo_v1062(
            [self._row("memory", "Taiwan tariff pressure rises", "family=trade_tariff|tariff")],
            market="TW",
            now=now,
        )
        biotech = assess_policy_geo_v1062(
            [self._row("biotech", "Taiwan tariff pressure rises", "family=trade_tariff|tariff")],
            market="TW",
            now=now,
        )
        self.assertLess(memory["score"], biotech["score"])
        self.assertGreater(memory["risk"], biotech["risk"])
        self.assertIn("個股曝險", memory["line"])

    def test_airline_oil_risk_is_stronger_than_biotech(self):
        now = datetime(2026, 7, 24, 22, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        title = "Brent rises as Hormuz disruption lifts oil prices"
        airline = assess_policy_geo_v1062(
            [self._row("airline", title, "family=energy|oil_price_up|policy_geo")],
            market="TW",
            now=now,
        )
        biotech = assess_policy_geo_v1062(
            [self._row("biotech", title, "family=energy|oil_price_up|policy_geo")],
            market="TW",
            now=now,
        )
        self.assertLess(airline["score"], biotech["score"])
        self.assertGreater(airline["risk"], biotech["risk"])

    def test_energy_can_receive_positive_oil_direction_but_price_keeps_veto(self):
        now = datetime(2026, 7, 24, 22, 0, tzinfo=ZoneInfo("Asia/Taipei"))
        result = assess_policy_geo_v1062(
            [self._row("energy", "Brent rises as Hormuz disruption lifts oil prices", "family=energy|oil_price_up|policy_geo")],
            market="TW",
            now=now,
        )
        self.assertGreater(result["score"], 0)
        self.assertTrue(result["price_veto"])
        self.assertEqual(result["ticker_profile"], "energy")


if __name__ == "__main__":
    unittest.main()
