# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from models import NewsItem, TickerInfo
from ticker_event_exposure import (
    annotate_global_event_news,
    build_ticker_event_exposure,
    classify_ticker_profile,
)


class TickerEventExposureTests(unittest.TestCase):
    def _ticker(self, code: str, name: str, market: str = "TW", asset_type: str = "stock") -> TickerInfo:
        symbol = code if market == "US" else f"{code}.TW"
        return TickerInfo(code, symbol, name, market, asset_type)

    def test_memory_and_semiconductor_profiles_are_distinct(self):
        memory = self._ticker("2337", "旺宏")
        semi = self._ticker("2330", "台積電")
        self.assertEqual(classify_ticker_profile(memory), "memory")
        self.assertEqual(classify_ticker_profile(semi), "semiconductor")
        self.assertGreater(
            build_ticker_event_exposure(memory)["rule_multipliers"]["chip_controls"],
            1.0,
        )

    def test_airline_and_energy_have_opposite_oil_direction(self):
        airline = build_ticker_event_exposure(self._ticker("2618", "長榮航"))
        energy = build_ticker_event_exposure(self._ticker("6505", "台塑化"))
        self.assertLess(airline["oil_direction_scale"], 0)
        self.assertGreater(energy["oil_direction_scale"], 0)

    def test_biotech_direct_tariff_sensitivity_is_lower_than_ai_supply_chain(self):
        biotech = build_ticker_event_exposure(self._ticker("6586", "醣基"))
        ai_power = build_ticker_event_exposure(self._ticker("2308", "台達電"))
        self.assertLess(
            biotech["rule_multipliers"]["tariff"],
            ai_power["rule_multipliers"]["tariff"],
        )

    def test_global_event_rows_receive_ticker_profile_metadata(self):
        ticker = self._ticker("2337", "旺宏")
        row = NewsItem(
            "TINO_GlobalEventCore",
            "2026-07-24T22:00:00+08:00",
            -0.18,
            "global_event_core|family=energy|severity=3|eventid=oil_1",
            "Brent rises on Hormuz risk",
            "https://example.test",
        )
        annotated = annotate_global_event_news(ticker, [row])
        self.assertEqual(len(annotated), 1)
        self.assertIn("ticker_profile=memory", annotated[0].tag)
        self.assertIn("ticker_code=2337", annotated[0].tag)


if __name__ == "__main__":
    unittest.main()
