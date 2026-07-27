# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class DecisionEvidenceSpacingV1067Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")

    def test_evidence_uses_fixed_two_line_viewport_without_webkit_clamp(self):
        self.assertIn(".decision-evidence", self.source)
        self.assertIn("display:block", self.source)
        self.assertIn("height:31px", self.source)
        self.assertIn("line-height:1.22", self.source)
        self.assertNotIn("-webkit-line-clamp:2", self.source)

    def test_price_bar_is_separated_from_evidence(self):
        self.assertIn("margin-bottom:4px", self.source)
        self.assertIn(".pricebar{margin-top:0", self.source)
        self.assertIn("clear:both", self.source)

    def test_compact_desktop_preserves_two_complete_lines(self):
        self.assertIn("height:27px", self.source)
        self.assertIn("line-height:1.18", self.source)
        self.assertIn("padding:2px 0 2px 6px", self.source)


if __name__ == "__main__":
    unittest.main()
