# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class ResponsiveUiV1065Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.battle_source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")
        cls.radar_source = (ROOT / "ui_v9_radar.py").read_text(encoding="utf-8")

    def test_radar_removes_repeated_row_prefixes(self):
        self.assertIn("def _strip_duplicate_label", self.radar_source)
        self.assertIn("Daily Headline｜Daily Headline", self.radar_source)
        self.assertIn('f"{label} |"', self.radar_source)
        self.assertIn("_strip_duplicate_label(key, _clean_main(raw_val))", self.radar_source)

    def test_normal_desktop_no_longer_uses_old_1100_stack_breakpoint(self):
        self.assertIn("@media(max-width:1020px) and (min-width:721px)", self.battle_source)
        self.assertIn("@media(max-width:720px)", self.battle_source)
        self.assertNotIn("@media(max-width:1100px)", self.battle_source)

    def test_left_and_right_iframes_use_aligned_height(self):
        self.assertIn("html_block(html, height=642, scrolling=False)", self.battle_source)
        self.assertIn("html_block(html, height=642, scrolling=False)", self.radar_source)
        self.assertIn("min-height:624px", self.radar_source)

    def test_battle_panel_uses_orchestrator_owned_session_snapshot(self):
        self.assertIn("_decision_snapshot_payload", self.battle_source)
        self.assertNotIn("entry = assess_entry_opportunity(p)", self.battle_source)
        self.assertIn("build_decision_brief", self.battle_source)
        self.assertIn("AI交易決策", self.battle_source)
        self.assertIn("entry_price_strategy", self.battle_source)
        self.assertNotIn("from low_entry_readiness_v1065 import assess_low_entry_readiness", self.battle_source)


if __name__ == "__main__":
    unittest.main()
