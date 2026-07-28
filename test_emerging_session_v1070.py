# -*- coding: utf-8 -*-
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import unittest

from emerging_session_v1070 import emerging_session_phase


TZ = ZoneInfo("Asia/Taipei")


def at(hour: int, minute: int):
    return datetime(2026, 7, 28, hour, minute, tzinfo=TZ)


class EmergingSessionV1070Tests(unittest.TestCase):
    def test_official_emerging_session_boundaries(self):
        self.assertEqual(emerging_session_phase(at(8, 59)), "pre_market")
        self.assertEqual(emerging_session_phase(at(9, 0)), "intraday")
        self.assertEqual(emerging_session_phase(at(14, 34)), "intraday")
        self.assertEqual(emerging_session_phase(at(14, 59)), "intraday")
        self.assertEqual(emerging_session_phase(at(15, 0)), "close_confirm")
        self.assertEqual(emerging_session_phase(at(15, 5)), "close_confirm")
        self.assertEqual(emerging_session_phase(at(15, 6)), "after_close")

    def test_installation_entry_is_present_in_data_router(self):
        source = Path("data_sources.py").read_text(encoding="utf-8")
        self.assertIn("install_emerging_session_v1070", source)
        self.assertIn("fetch_tw_price = install_emerging_session_v1070(fetch_tw_price)", source)

    def test_main_board_session_contract_remains_1330(self):
        source = Path("data_sources_tw.py").read_text(encoding="utf-8")
        self.assertIn("time(9, 0) <= t < time(13, 30)", source)
        self.assertIn("time(13, 30) <= t <= time(13, 35)", source)


if __name__ == "__main__":
    unittest.main()
