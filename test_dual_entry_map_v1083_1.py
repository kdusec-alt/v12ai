# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import unittest

from evidence_arbitration_v1083 import _entry_plan


ROOT = Path(__file__).resolve().parent


def make_forecast(*, last=198.12, low=195.60, high=201.35, confirm=201.35):
    return SimpleNamespace(decision_card={
        "現價": last,
        "最低": low,
        "最高": high,
        "轉強": f"突破 {confirm}",
        "攻擊": f"站穩 {confirm}",
        "防守": low,
        "不追": 212.49,
    })


class DualEntryMapV10831Tests(unittest.TestCase):
    def test_reclaim_state_exposes_low_entry_and_right_confirmation(self):
        plan = _entry_plan(make_forecast(), {
            "state": "WAIT_VWAP_RECLAIM",
            "operative_price": 198.12,
            "vwap": 199.03,
        })
        self.assertAlmostEqual(plan["low_entry_zone"]["lower"], 195.60, places=2)
        self.assertAlmostEqual(plan["low_entry_zone"]["upper"], 196.972, places=3)
        self.assertEqual(plan["confirmation_price"], 199.03)
        self.assertEqual(plan["add_price"], 201.35)
        self.assertEqual(plan["invalidation_price"], 195.60)
        self.assertEqual(plan["current_location"], "低接與確認價中間")
        self.assertIn("先等", plan["current_action"])
        self.assertIn("低接 195.6～196.97", plan["display_line"])
        self.assertIn("確認 199.03", plan["display_line"])
        self.assertIn("加碼 201.35", plan["display_line"])

    def test_price_inside_low_zone_is_not_automatic_buy(self):
        plan = _entry_plan(make_forecast(last=196.50), {
            "state": "WAIT_VWAP_RECLAIM",
            "operative_price": 196.50,
            "vwap": 199.03,
        })
        self.assertEqual(plan["current_location"], "低接觀察區")
        self.assertIn("止跌量縮", plan["current_action"])
        self.assertIn("低點不再下移", plan["low_entry_condition"])

    def test_price_above_vwap_moves_to_confirmation_path(self):
        plan = _entry_plan(make_forecast(last=199.20), {
            "state": "WAIT_VWAP_RECLAIM",
            "operative_price": 199.20,
            "vwap": 199.03,
        })
        self.assertEqual(plan["current_location"], "右側確認區")
        self.assertIn("回踩不破", plan["current_action"])

    def test_selling_block_never_outputs_low_entry_zone(self):
        plan = _entry_plan(make_forecast(last=190.00), {
            "state": "SELLING_EXPANSION_BLOCK",
            "operative_price": 190.00,
            "vwap": 199.03,
        })
        self.assertIsNone(plan["low_entry_zone"]["lower"])
        self.assertIsNone(plan["low_entry_zone"]["upper"])
        self.assertIn("禁止接刀", plan["low_entry_condition"])
        self.assertEqual(plan["current_action"], "現在不買")

    def test_shared_engine_contains_no_ticker_special_case(self):
        source = (ROOT / "evidence_arbitration_v1083.py").read_text(encoding="utf-8")
        for token in ("MRVL", "MU", "INTC", "SKHY", "2308", "2330", "6217"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
