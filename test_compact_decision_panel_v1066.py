# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class CompactDecisionPanelV1066Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")

    def test_decision_main_evidence_market_and_chip_are_one_card(self):
        self.assertIn("class='decision-evidence'", self.source)
        self.assertIn("<b>證據</b>", self.source)
        self.assertIn("<b>市場</b>", self.source)
        self.assertIn("canonical_main_message", self.source)

    def test_five_large_boxes_are_replaced_by_one_price_strip(self):
        self.assertIn("class='pricebar'", self.source)
        self.assertNotIn("<div class='grid'>", self.source)
        self.assertNotIn("<div class='mini'>", self.source)
        for label in ("低接", "攻擊", "轉強", "停手", "不追"):
            self.assertIn(f"<b>{label}</b>", self.source)

    def test_duplicate_footer_sections_are_not_rendered(self):
        self.assertNotIn("<div class='bottom'>一句話", self.source)
        self.assertNotIn("籌碼摘要：{safe(p.radar.get('左側籌碼摘要'))}", self.source)

    def test_readiness_keeps_price_path_but_limits_reason_chips(self):
        self.assertIn("readiness_summary", self.source)
        self.assertIn("list(readiness.get(\"conditions\") or [])[:2]", self.source)
        self.assertIn("✓ 操作價格已同步", self.source)

    def test_price_strip_uses_existing_decision_card_prices(self):
        for key in ("低接第一批", "低接第二批", "攻擊", "轉強", "防守", "不追"):
            self.assertIn(f"d.get('{key}')", self.source)


if __name__ == "__main__":
    unittest.main()
