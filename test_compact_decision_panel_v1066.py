# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class CompactDecisionPanelV1066Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")

    def test_decision_main_evidence_market_and_chip_are_one_card(self):
        self.assertIn("class='evidence-summary'", self.source)
        self.assertIn("class='evidence-details'", self.source)
        self.assertIn("<b>AI 證據：</b>", self.source)
        self.assertIn("<b>市場：</b>", self.source)
        self.assertIn("build_decision_brief", self.source)

    def test_five_large_boxes_are_replaced_by_one_price_strip(self):
        self.assertIn("class='pricebar'", self.source)
        self.assertNotIn("<div class='grid'>", self.source)
        self.assertNotIn("<div class='mini'>", self.source)
        self.assertIn("price_tiles_html", self.source)
        self.assertIn('entry.get("price_tiles")', self.source)

    def test_duplicate_footer_sections_are_not_rendered(self):
        self.assertNotIn("<div class='bottom'>一句話", self.source)
        self.assertNotIn("籌碼摘要：{safe(p.radar.get('左側籌碼摘要'))}", self.source)

    def test_entry_timing_keeps_dynamic_strategy_and_limits_reason_chips(self):
        self.assertIn("entry_summary", self.source)
        self.assertIn('decision_brief.get("reasons")', self.source)
        self.assertIn("entry_price_strategy", self.source)
        self.assertIn("AI交易決策", self.source)
        self.assertIn("_decision_snapshot_payload", self.source)
        self.assertNotIn("AI低接成熟度", self.source)

    def test_price_strip_is_owned_by_entry_state_with_legacy_fallback(self):
        self.assertIn('entry.get("price_tiles")', self.source)
        self.assertIn("legacy_price_tiles", self.source)
        self.assertIn("price_tiles_html", self.source)
        for key in ("攻擊", "轉強", "防守", "不追"):
            self.assertIn(f'd.get("{key}")', self.source)

    def test_v1084_entry_map_has_one_current_action_and_five_unified_columns(self):
        self.assertIn("class='action-now'", self.source)
        self.assertIn("空手｜", self.source)
        self.assertIn("持股｜", self.source)
        self.assertIn("def _entry_map_tiles", self.source)
        for label in ("現在", "低接", "確認", "加碼", "失效"):
            self.assertIn(f'"label": "{label}"', self.source)


if __name__ == "__main__":
    unittest.main()
