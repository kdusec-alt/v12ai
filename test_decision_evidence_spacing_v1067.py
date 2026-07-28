# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class DecisionEvidenceSpacingV1067Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")

    def test_evidence_is_a_short_summary_with_expandable_full_payload(self):
        self.assertIn("def _compact_evidence_summary", self.source)
        self.assertIn("class='evidence-summary'", self.source)
        self.assertIn("<details class='evidence-details'>", self.source)
        self.assertIn("展開完整 AI 證據", self.source)
        self.assertIn("max-height:150px", self.source)

    def test_old_fixed_height_clipping_is_removed(self):
        self.assertNotIn("height:31px", self.source)
        self.assertNotIn("height:27px", self.source)
        self.assertNotIn("class='decision-evidence'", self.source)
        self.assertNotIn("-webkit-line-clamp", self.source)

    def test_price_bar_remains_a_separate_row(self):
        self.assertIn(".pricebar{{margin-top:0", self.source)
        self.assertIn("clear:both", self.source)
        self.assertIn("</details>\n        <div class='pricebar'>", self.source)


if __name__ == "__main__":
    unittest.main()
