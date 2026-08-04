# -*- coding: utf-8 -*-
from pathlib import Path
import unittest

from evidence_arbitration_v1083 import _decisive_action


ROOT = Path(__file__).resolve().parent


def plan():
    return {
        "current_price": 55.75,
        "confirmation_price": 55.42,
        "invalidation_price": 53.90,
    }


class V1085DecisiveEntryActionTests(unittest.TestCase):
    def test_conflicted_wait_pullback_becomes_block_not_wait(self):
        action = _decisive_action(
            {"state": "WAIT_VWAP_PULLBACK"},
            plan(),
            {"score": 67},
            [
                {"stance_value": 1, "strength": 90, "label": "價格結構"},
                {"stance_value": -1, "strength": 88, "label": "ABC情境"},
                {"stance_value": -1, "strength": 80, "label": "籌碼法人"},
            ],
        )
        self.assertEqual(action["code"], "BLOCK")
        self.assertEqual(action["label"], "禁止進場")
        self.assertNotIn("等回測", action["instruction"])

    def test_confirmed_structure_returns_buy(self):
        action = _decisive_action(
            {"state": "BUY_TODAY_CONFIRM"},
            plan(),
            {"score": 74},
            [{"stance_value": 1, "strength": 86, "label": "價格結構"}],
        )
        self.assertEqual(action["code"], "BUY")
        self.assertEqual(action["label"], "買進")
        self.assertIn("停損", action["instruction"])

    def test_selling_expansion_returns_sell(self):
        action = _decisive_action(
            {"state": "SELLING_EXPANSION_BLOCK"},
            plan(),
            {"score": 31},
            [{"stance_value": -1, "strength": 91, "label": "賣壓"}],
        )
        self.assertEqual(action["code"], "SELL")
        self.assertEqual(action["label"], "賣出")

    def test_overheated_returns_reduce(self):
        action = _decisive_action(
            {"state": "OVERHEATED_NO_CHASE"},
            plan(),
            {"score": 78},
            [{"stance_value": 1, "strength": 88, "label": "價格結構"}],
        )
        self.assertEqual(action["code"], "REDUCE")
        self.assertEqual(action["label"], "減碼")

    def test_public_panel_uses_single_decisive_action(self):
        ui = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")
        self.assertIn("AI交易決策", ui)
        self.assertIn("action_decision", ui)
        self.assertIn("_decision_snapshot_payload", ui)
        self.assertIn("最終決策｜", ui)
        self.assertIn("AI執行價格", ui)
        self.assertIn("html_block(html, height=642, scrolling=False)", ui)

    def test_public_state_meta_has_no_generic_wait_label(self):
        source = (ROOT / "decision_architecture_v1081.py").read_text(encoding="utf-8")
        public_meta = source[source.index("_STATE_META = {"):source.index("_SELLING_STATES")]
        self.assertNotIn("今日等回測", public_meta)
        for label in ("買進", "續抱", "減碼", "賣出", "禁止進場"):
            self.assertIn(label, public_meta)


if __name__ == "__main__":
    unittest.main()
