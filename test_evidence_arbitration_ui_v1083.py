# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class EvidenceArbitrationUiV1083Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")

    def test_v1096_snapshot_is_primary_and_ui_does_not_arbitrate(self):
        self.assertIn("_decision_snapshot_payload", self.source)
        self.assertNotIn("build_evidence_reasoning(p, entry)", self.source)
        self.assertIn("正式決策快照未完成；UI禁止重新仲裁", self.source)

    def test_recommended_entry_is_visible_and_keeps_v1081_price_tiles(self):
        self.assertIn("public_snapshot", self.source)
        self.assertIn("AI執行價格", self.source)
        self.assertIn("class='reasoning-price'", self.source)
        self.assertIn('entry.get("price_tiles")', self.source)
        self.assertIn("price_tiles_html", self.source)

    def test_abc_quantum_and_acceptance_are_explained(self):
        self.assertIn("abc_context", self.source)
        self.assertIn("quantum_context", self.source)
        self.assertIn("ABC情境", self.source)
        self.assertIn("Quantum", self.source)
        self.assertIn("price_acceptance", self.source)

    def test_v9_height_and_three_layer_structure_remain_unchanged(self):
        self.assertIn("html_block(html, height=642, scrolling=False)", self.source)
        self.assertIn("class='entrylamp", self.source)
        self.assertIn("class='decision'", self.source)
        self.assertIn("class='t1'", self.source)


if __name__ == "__main__":
    unittest.main()
