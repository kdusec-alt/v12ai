# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from models import NewsItem
from market_shock_levels_v1062 import install_market_shock_levels_v1062

install_market_shock_levels_v1062()

from decision_narrative_v1062 import dominant_market_shock


class DecisionNarrativeV1062Tests(unittest.TestCase):
    def test_dominant_shock_prefers_hormuz_war_over_tariff(self):
        tariff = NewsItem(
            "Reuters",
            "2026-07-24T20:00:00+08:00",
            -0.16,
            "global_event_core|family=trade_tariff|severity=3|tariff|ticker_profile=memory",
            "Taiwan tariff pressure rises",
            "https://example.test/tariff",
        )
        war = NewsItem(
            "Reuters",
            "2026-07-24T21:00:00+08:00",
            -0.18,
            "global_event_core|family=energy|severity=3|oil_price_up|ticker_profile=memory",
            "Iran war escalates and Hormuz blockade pushes Brent higher",
            "https://example.test/war",
        )
        shock = dominant_market_shock([tariff, war])
        self.assertEqual(shock["level"], 5)
        self.assertIn("荷姆茲航道", shock["drivers"])
        self.assertIn("伊朗/中東戰爭", shock["drivers"])


if __name__ == "__main__":
    unittest.main()
