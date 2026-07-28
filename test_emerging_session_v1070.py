# -*- coding: utf-8 -*-
from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

import data_sources_tw as tw
from emerging_session_v1070 import install_emerging_session_v1070
from models import TickerInfo


TZ = ZoneInfo("Asia/Taipei")


def at(hour: int, minute: int):
    return datetime(2026, 7, 28, hour, minute, tzinfo=TZ)


class EmergingSessionV1070Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def probe(ticker):
            return {
                "phase_1434": tw._tw_session_phase(at(14, 34)),
                "phase_1500": tw._tw_session_phase(at(15, 0)),
                "phase_1506": tw._tw_session_phase(at(15, 6)),
            }
        cls.fetch = install_emerging_session_v1070(probe)

    def ticker(self, symbol: str, exchange: str):
        return TickerInfo(
            raw=symbol.split(".")[0], resolved_symbol=symbol, name=symbol,
            market="TW", asset_type="stock", exchange=exchange,
            currency="TWD", price_limit_pct=None if exchange == "TPEX_EMERGING" else 0.10,
        )

    def test_emerging_is_intraday_until_1500(self):
        result = self.fetch(self.ticker("6586.TWO", "TPEX_EMERGING"))
        self.assertEqual(result["phase_1434"], "intraday")
        self.assertEqual(result["phase_1500"], "close_confirm")
        self.assertEqual(result["phase_1506"], "after_close")

    def test_tpex_main_board_still_closes_at_1330(self):
        result = self.fetch(self.ticker("5483.TWO", "TPEX"))
        self.assertEqual(result["phase_1434"], "after_close")
        self.assertEqual(result["phase_1500"], "after_close")

    def test_symbol_context_does_not_leak_between_calls(self):
        self.fetch(self.ticker("6586.TWO", "TPEX_EMERGING"))
        result = self.fetch(self.ticker("5483.TWO", "TPEX"))
        self.assertEqual(result["phase_1434"], "after_close")


if __name__ == "__main__":
    unittest.main()
