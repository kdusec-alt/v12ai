# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from decision_architecture_v1081 import assess_entry_opportunity
from evidence_arbitration_v1083 import build_evidence_arbitration
from test_decision_architecture_v1081 import make_forecast


class EvidenceArbitrationV1083Tests(unittest.TestCase):
    def test_abc_quantum_and_recommended_entry_are_joined(self):
        forecast = make_forecast(
            market="US", last=159.75, day_pct=7.32, vwap=159.75,
            low=154.38, high=161.99, confirmation=163.35,
        )
        forecast.radar.update({
            "ABC 多空情境": "A突破 54%｜B回測 42%｜C防守 3%",
            "Quantum 貢獻": "趨勢 +13.0｜價格結構 -1.0｜方向總分 +28.8",
        })
        entry = assess_entry_opportunity(forecast)
        row = build_evidence_arbitration(forecast, entry)
        self.assertEqual(row["schema"], "TINO_EVIDENCE_ARBITRATION_V1083")
        self.assertTrue(row["abc_context"])
        self.assertTrue(row["quantum_context"])
        self.assertIn("價格結構確認度", row["price_acceptance"]["label"])
        self.assertEqual(row["recommended_entry"]["primary_price"], 159.75)
        self.assertIn("159.75", row["recommended_entry"]["headline"])
        self.assertIn("159.75", row["decision_message"])

    def test_pullback_state_keeps_v1081_vwap_as_entry_zone(self):
        forecast = make_forecast(last=104.0, day_pct=2.0, vwap=100.0, confirmation=106.0)
        entry = assess_entry_opportunity(forecast)
        self.assertEqual(entry["state"], "WAIT_VWAP_PULLBACK")
        row = build_evidence_arbitration(forecast, entry)
        plan = row["recommended_entry"]
        self.assertEqual(plan["label"], "建議承接區")
        self.assertEqual(plan["primary_price"], 100.0)
        self.assertIn("VWAP 100", plan["headline"])
        self.assertTrue(plan["formal_price_model_unchanged"])

    def test_below_vwap_is_confirmation_not_buying_higher_blindly(self):
        forecast = make_forecast(last=98.0, day_pct=-1.0, vwap=100.0, low=96.5)
        entry = assess_entry_opportunity(forecast)
        self.assertEqual(entry["state"], "WAIT_VWAP_RECLAIM")
        plan = build_evidence_arbitration(forecast, entry)["recommended_entry"]
        self.assertEqual(plan["label"], "確認型進場")
        self.assertFalse(plan["actionable"])
        self.assertIn("收復VWAP 100", plan["headline"])
        self.assertIn("不是直接追價", plan["condition"])

    def test_selling_expansion_never_generates_low_entry_price(self):
        forecast = make_forecast(
            last=94.0, day_pct=-6.0, vwap=100.0, low=93.5,
            regime_state="selling_expansion",
        )
        entry = assess_entry_opportunity(forecast)
        self.assertEqual(entry["state"], "SELLING_EXPANSION_BLOCK")
        plan = build_evidence_arbitration(forecast, entry)["recommended_entry"]
        self.assertEqual(plan["label"], "禁止進場")
        self.assertIn("不提供低接價", plan["headline"])

    def test_after_hours_does_not_reuse_old_entry_anchor(self):
        forecast = make_forecast(
            market="US", session="after_hours", last=160.0,
            day_pct=8.0, vwap=149.0, first=131.0,
        )
        entry = assess_entry_opportunity(forecast)
        self.assertEqual(entry["state"], "WAIT_NEXT_SESSION")
        plan = build_evidence_arbitration(forecast, entry)["recommended_entry"]
        self.assertEqual(plan["label"], "下一時段重算")
        self.assertIn("不沿用舊進場價", plan["headline"])
        self.assertFalse(plan["actionable"])

    def test_reasoning_cannot_change_formal_forecast_or_price_model(self):
        forecast = make_forecast(last=102.0, vwap=100.0)
        entry = assess_entry_opportunity(forecast)
        row = build_evidence_arbitration(forecast, entry)
        self.assertTrue(row["narrative_only"])
        self.assertFalse(row["decision_influence"])
        self.assertTrue(row["formal_forecast_unchanged"])
        self.assertTrue(row["formal_price_model_unchanged"])
        self.assertTrue(row["learning_sample_unchanged"])


if __name__ == "__main__":
    unittest.main()
