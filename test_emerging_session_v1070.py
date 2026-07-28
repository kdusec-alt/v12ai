# -*- coding: utf-8 -*-
from datetime import datetime
from zoneinfo import ZoneInfo
import unittest

import data_sources_tw as tw
from emerging_session_v1070 import emerging_session_phase, install_emerging_session_v1070
from models import TickerInfo


TZ = ZoneInfo("Asia/Taipei")


def at(hour: int, minute: int):
    return datetime(2026, 7, 28, hour, minute, tzinfo=TZ)


class EmergingSessionV1070Tests(unittest.TestCase):
    def ticker(self, symbol: str, exchange: str):
        return TickerInfo(
            raw=symbol.split(".")[0], resolved_symbol=symbol, name=symbol,
            market="TW", asset_type="stock", exchange=exchange,
            currency="TWD", price_limit_pct=None if exchange == "TPEX_EMERGING" else 0.10,
        )

    def test_official_emerging_session_boundaries(self):
        self.assertEqual(emerging_session_phase(at(14, 34)), "intraday")
        self.assertEqual(emerging_session_phase(at(15, 0)), "close_confirm")
        self.assertEqual(emerging_session_phase(at(15, 6)), "after_close")

    def test_installed_router_keeps_tpex_main_board_after_close(self):
        def probe(ticker):
            return tw._tw_session_phase(at(14, 34))

        fetch = install_emerging_session_v1070(probe)
        emerging = fetch(self.ticker("6586.TWO", "TPEX_EMERGING"))
        main_board = fetch(self.ticker("5483.TWO", "TPEX"))
        self.assertEqual(emerging, "intraday")
        self.assertEqual(main_board, "after_close")

    def test_symbol_context_does_not_leak(self):
        def probe(ticker):
            return tw._tw_session_phase(at(14, 34))

        fetch = install_emerging_session_v1070(probe)
        fetch(self.ticker("6586.TWO", "TPEX_EMERGING"))
        self.assertEqual(fetch(self.ticker("5483.TWO", "TPEX")), "after_close")


if __name__ == "__main__":
    unittest.main()
