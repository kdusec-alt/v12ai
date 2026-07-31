# -*- coding: utf-8 -*-
from pathlib import Path
import sys
import tempfile
import types
import unittest

from decision_architecture_v1081 import assess_entry_opportunity
from test_decision_architecture_v1081 import make_forecast
import learning_integrity_v1081 as integrity


ROOT = Path(__file__).resolve().parent


def integrity_snapshot(predictions, audits):
    with tempfile.TemporaryDirectory() as tmp:
        fake = types.ModuleType("memory_store")
        fake.DEFAULT_VISIBLE_LOG_ROWS = 900
        fake.PREDICTION_LOG = Path(tmp) / "prediction_log.jsonl"
        fake.AUDIT_LOG = Path(tmp) / "audit_log.jsonl"
        fake.TICKER_PROFILE = Path(tmp) / "ticker_profiles.json"
        fake.read_prediction_log = lambda limit: predictions
        fake.read_audit_log = lambda limit: audits
        previous = sys.modules.get("memory_store")
        sys.modules["memory_store"] = fake
        try:
            return integrity.learning_integrity_snapshot(100)
        finally:
            if previous is None:
                sys.modules.pop("memory_store", None)
            else:
                sys.modules["memory_store"] = previous


class V1081IntegrityHardeningTests(unittest.TestCase):
    def test_unverified_negative_fundamental_does_not_create_formal_veto(self):
        row = assess_entry_opportunity(make_forecast(
            last=98.0,
            day_pct=0.8,
            vwap=100.0,
            open_price=99.0,
            high=101.0,
            low=97.5,
            fundamental_text="市場傳聞財報低於預期",
        ))
        self.assertEqual(row["fundamental"]["state"], "negative")
        self.assertFalse(row["fundamental"]["verified"])
        self.assertEqual(row["state"], "WAIT_VWAP_RECLAIM")
        self.assertIn("未驗證", "｜".join(item["text"] for item in row["conditions"]))

    def test_generic_source_word_does_not_verify_social_relay(self):
        row = assess_entry_opportunity(make_forecast(
            last=98.0,
            day_pct=0.8,
            vwap=100.0,
            open_price=99.0,
            high=101.0,
            low=97.5,
            fundamental_text="財報低於預期｜來源：社群轉述",
        ))
        self.assertEqual(row["fundamental"]["state"], "negative")
        self.assertFalse(row["fundamental"]["verified"])
        self.assertEqual(row["fundamental"]["verification_basis"], "unverified")
        self.assertEqual(row["state"], "WAIT_VWAP_RECLAIM")

    def test_trusted_official_filing_can_verify_fundamental_risk(self):
        row = assess_entry_opportunity(make_forecast(
            last=96.0,
            day_pct=-3.0,
            vwap=100.0,
            open_price=100.0,
            high=101.0,
            low=95.5,
            fundamental_text="財報低於預期｜EPS衰退｜來源：正式財報",
        ))
        self.assertTrue(row["fundamental"]["verified"])
        self.assertEqual(row["fundamental"]["verification_basis"], "trusted_source_marker")
        self.assertEqual(row["state"], "SELLING_EXPANSION_BLOCK")

    def test_verified_cross_asset_rows_still_require_same_session_truth(self):
        forecast = make_forecast(
            market="US",
            last=104.0,
            day_pct=4.0,
            vwap=101.0,
            evidence="QQQ +3.0%｜SOX +5.0%｜NQ +3.5%",
            same_session=False,
        )
        forecast.decision_card["_session_truth_v1081"] = {"verified": True}
        row = assess_entry_opportunity(forecast)
        self.assertTrue(row["market_context"]["supportive"])
        self.assertFalse(row["market_context"]["session_verified"])
        self.assertFalse(row["market_context"]["confirmed"])

    def test_malformed_audit_values_do_not_crash_integrity_snapshot(self):
        predictions = [{
            "id": "p1",
            "official_sample_key": "TW|A|2026-07-31|T1",
            "target_kind": "T1_CLOSE_NEXT_SESSION",
            "next_close_est": 100,
            "valid_price_sample": True,
            "price_sample_quality": "verified",
        }]
        audits = [{
            "audit_id": "p1:next",
            "prediction_id": "p1",
            "official_sample_key": "TW|A|2026-07-31|T1",
            "target": "next",
            "actual_valid": True,
            "predicted_close": "broken",
            "actual_close": "also-broken",
            "anchor_close": None,
        }]
        row = integrity_snapshot(predictions, audits)
        self.assertEqual(row["verified_t1_audits"], 0)
        self.assertEqual(row["pending_official_samples"], 1)

    def test_event_revision_group_is_not_reported_as_corrupt_duplicate(self):
        key = "TW|A|2026-07-31|T1"
        predictions = [
            {
                "id": "p-base",
                "official_sample_key": key,
                "target_kind": "T1_CLOSE_NEXT_SESSION",
                "next_close_est": 100,
                "valid_price_sample": True,
                "price_sample_quality": "verified",
            },
            {
                "id": "p-event",
                "official_sample_key": key,
                "target_kind": "T1_CLOSE_NEXT_SESSION",
                "next_close_est": 102,
                "valid_price_sample": True,
                "price_sample_quality": "verified",
                "event_revision": True,
                "event_bundle_id": "verified-bundle",
                "revision_type": "EVENT_REASSESSMENT",
            },
        ]
        row = integrity_snapshot(predictions, [])
        self.assertEqual(row["event_revision_groups"], 1)
        self.assertEqual(row["duplicate_official_keys"], 0)
        self.assertFalse(any("重複" in warning for warning in row["warnings"]))

    def test_non_event_refresh_is_query_revision_not_corrupt_duplicate(self):
        key = "TW|B|2026-07-31|T1"
        predictions = [
            {
                "id": "p1",
                "official_sample_key": key,
                "target_kind": "T1_CLOSE_NEXT_SESSION",
                "next_close_est": 50,
                "valid_price_sample": True,
                "price_sample_quality": "verified",
            },
            {
                "id": "p2",
                "official_sample_key": key,
                "target_kind": "T1_CLOSE_NEXT_SESSION",
                "next_close_est": 51,
                "valid_price_sample": True,
                "price_sample_quality": "verified",
            },
        ]
        row = integrity_snapshot(predictions, [])
        self.assertEqual(row["event_revision_groups"], 0)
        self.assertEqual(row["query_revision_groups"], 1)
        self.assertEqual(row["duplicate_official_keys"], 0)
        self.assertFalse(any("重複" in warning for warning in row["warnings"]))

    def test_us_same_session_t1_is_quarantined_from_learning(self):
        key = "US|SKHY|2026-07-31|T1"
        predictions = [{
            "id": "us-bad-target",
            "official_sample_key": key,
            "target_kind": "T1_CLOSE_NEXT_SESSION",
            "target_trade_date": "2026-07-31",
            "run_time_tw": "2026-07-31T22:33:00+08:00",
            "market": "US",
            "ticker": "SKHY",
            "next_close_est": 143.33,
            "valid_price_sample": True,
            "price_sample_quality": "verified",
        }]
        audits = [{
            "audit_id": "us-bad-target:next",
            "prediction_id": "us-bad-target",
            "official_sample_key": key,
            "target": "next",
            "actual_valid": True,
            "predicted_close": 143.33,
            "actual_close": 148.19,
            "anchor_close": 146.95,
            "price_sample_quality": "verified",
            "actual_direction": "UP",
        }]
        row = integrity_snapshot(predictions, audits)
        self.assertEqual(row["invalid_target_session_groups"], 1)
        self.assertEqual(row["quarantined_target_session_audits"], 1)
        self.assertEqual(row["learning_eligible_t1_audits"], 0)
        self.assertTrue(any("美股T1目標日錯置" in warning for warning in row["warnings"]))

    def test_admin_ui_surfaces_compact_learning_integrity(self):
        source = (ROOT / "ui_admin.py").read_text(encoding="utf-8")
        self.assertIn('st.session_state.get("learning_integrity_v1081")', source)
        self.assertIn("st.sidebar.warning(text)", source)
        self.assertIn("st.sidebar.caption(text)", source)


if __name__ == "__main__":
    unittest.main()
