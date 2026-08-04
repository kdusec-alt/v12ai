# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


class EvidenceReasoningUiV1082Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")

    def test_v1096_snapshot_reasoning_is_rendered_inside_existing_decision_card(self):
        self.assertIn("_decision_snapshot_payload", self.source)
        self.assertNotIn("build_evidence_reasoning(p, entry)", self.source)
        self.assertIn("class='reasoning-line'", self.source)
        self.assertIn("AI推理", self.source)
        self.assertIn("前三大主因", self.source)
        self.assertIn("short_term_bias", self.source)
        self.assertIn("medium_term_bias", self.source)
        self.assertIn("price_acceptance", self.source)

    def test_reasoning_replaces_main_narrative_but_keeps_entry_price_state(self):
        self.assertIn('reasoning.get("decision_message")', self.source)
        self.assertIn('entry.get("price_tiles")', self.source)
        self.assertIn("entry_price_strategy", self.source)
        self.assertIn("canonical_main_message", self.source)

    def test_v9_panel_height_and_three_layer_contract_remain_unchanged(self):
        self.assertIn("html_block(html, height=642, scrolling=False)", self.source)
        self.assertIn("class='entrylamp", self.source)
        self.assertIn("class='decision'", self.source)
        self.assertIn("class='t1'", self.source)

    def test_full_original_evidence_remains_expandable(self):
        self.assertIn("展開完整 AI 證據", self.source)
        self.assertIn("<b>AI 證據：</b>", self.source)
        self.assertIn("<b>市場：</b>", self.source)
        self.assertIn("<b>推理仲裁：</b>", self.source)


if __name__ == "__main__":
    unittest.main()
