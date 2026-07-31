# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import types
import unittest

import analysis_speed_v1081 as speed
import learning_integrity_v1081 as integrity
from news_reassessment_priority_v1081 import prioritize_reassessment_plan


ROOT = Path(__file__).resolve().parent


class V1081SpeedAndLearningIntegrityTests(unittest.TestCase):
    def test_news_cache_is_bounded_and_force_refresh_bypasses(self):
        calls = []

        def fetcher(symbol, force_refresh=False):
            calls.append((symbol, force_refresh))
            return [SimpleNamespace(title=f"row-{len(calls)}")]

        first = speed.fetch_news_cached(fetcher, "GENERIC-A", ttl_seconds=60)
        second = speed.fetch_news_cached(fetcher, "GENERIC-A", ttl_seconds=60)
        forced = speed.fetch_news_cached(fetcher, "GENERIC-A", force_refresh=True, ttl_seconds=60)
        self.assertEqual(len(calls), 2)
        self.assertEqual(first[0].title, second[0].title)
        self.assertNotEqual(second[0].title, forced[0].title)

    def test_foreground_pipeline_records_timings_without_caching_forecast(self):
        forecast = SimpleNamespace(decision_card={})
        marks = []

        def fetch_price(symbol):
            return SimpleNamespace(symbol=symbol)

        def fetch_news(symbol, force_refresh=False):
            return []

        def build_signals(symbol):
            return []

        def orchestrate(price, macro, news_items, extra_signals):
            return forecast

        row = speed.run_analysis_pipeline(
            "GENERIC-B",
            "neutral",
            True,
            fetch_price=fetch_price,
            fetch_news=fetch_news,
            build_learning_signals=build_signals,
            orchestrate=orchestrate,
            mark_runtime_stage=lambda name, **kwargs: marks.append(name),
        )
        self.assertIs(row, forecast)
        perf = row.decision_card["_analysis_performance_v1081"]
        self.assertFalse(perf["full_forecast_cached"])
        self.assertEqual(perf["worker_limits"], {"official": 3, "foreground": 2})
        self.assertIn("analysis_fetch_price_start", marks)
        self.assertIn("analysis_orchestrate_done", marks)

    def test_source_has_bounded_workers_and_no_background_executor(self):
        source = (ROOT / "analysis_speed_v1081.py").read_text(encoding="utf-8")
        self.assertIn("max_workers=3", source)
        self.assertIn("max_workers=2", source)
        self.assertNotIn("ThreadPoolExecutor()", source)
        self.assertNotIn("daemon=True", source)

    def test_news_priority_rejects_stale_or_unverified_rows(self):
        stale = prioritize_reassessment_plan({
            "needs_reassessment": True,
            "event_fingerprint": "abc",
            "event_severity": 4,
            "stale_reindexed": True,
        })
        self.assertFalse(stale["needs_reassessment"])
        self.assertEqual(stale["reassessment_priority"], "IGNORE_STALE")

        unverified = prioritize_reassessment_plan({
            "needs_reassessment": True,
            "event_severity": 3,
        })
        self.assertFalse(unverified["needs_reassessment"])
        self.assertEqual(unverified["reassessment_priority"], "VERIFY_ONLY")

    def test_verified_major_news_requests_full_priority_recompute(self):
        row = prioritize_reassessment_plan({
            "needs_reassessment": True,
            "event_fingerprint": "verified-event",
            "event_verified": True,
            "event_severity": 3,
        })
        self.assertTrue(row["needs_reassessment"])
        self.assertTrue(row["immediate_recompute"])
        self.assertTrue(row["full_pipeline_required"])
        self.assertEqual(row["reassessment_priority"], "P1_IMMEDIATE")

    def test_sync_result_tuple_false_is_not_recorded_as_success(self):
        self.assertEqual(
            integrity._interpret_sync_result((False, "remote_verify_mismatch")),
            (False, "remote_verify_mismatch"),
        )
        self.assertEqual(integrity._interpret_sync_result((True, None)), (True, ""))
        self.assertEqual(integrity._interpret_sync_result(None), (False, "sync_returned_none"))

    def test_learning_integrity_counts_official_and_learning_eligible_separately(self):
        predictions = [
            {
                "id": "p1", "official_sample_key": "TW|A|2026-07-31|T1",
                "target_kind": "T1_CLOSE_NEXT_SESSION", "next_close_est": 100,
                "valid_price_sample": True, "price_sample_quality": "verified",
                "run_time_tw": "2026-07-30T13:30:00+08:00",
            },
            {
                "id": "p1-revision", "official_sample_key": "TW|A|2026-07-31|T1",
                "target_kind": "T1_CLOSE_NEXT_SESSION", "next_close_est": 102,
                "valid_price_sample": True, "price_sample_quality": "verified",
                "run_time_tw": "2026-07-30T14:00:00+08:00",
            },
            {
                "id": "p2", "official_sample_key": "TW|B|2026-07-31|T1",
                "target_kind": "T1_CLOSE_NEXT_SESSION", "next_close_est": 50,
                "valid_price_sample": True, "price_sample_quality": "verified",
                "run_time_tw": "2026-07-30T14:10:00+08:00",
            },
        ]
        audits = [
            {
                "audit_id": "p1:next", "prediction_id": "p1",
                "official_sample_key": "TW|A|2026-07-31|T1",
                "target": "next", "actual_valid": True,
                "predicted_close": 100, "actual_close": 101, "anchor_close": 98,
                "price_sample_quality": "verified", "actual_direction": "UP",
                "audit_time_tw": "2026-07-31T14:20:00+08:00",
            },
            {
                "audit_id": "limited:next", "prediction_id": "limited",
                "official_sample_key": "TW|C|2026-07-31|T1",
                "target": "next", "actual_valid": True,
                "predicted_close": 80, "actual_close": 79, "anchor_close": 78,
                "price_sample_quality": "reference_limited", "actual_direction": "UP",
                "audit_time_tw": "2026-07-31T14:21:00+08:00",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            fake = types.ModuleType("memory_store")
            fake.PREDICTION_LOG = Path(tmp) / "prediction_log.jsonl"
            fake.AUDIT_LOG = Path(tmp) / "audit_log.jsonl"
            fake.TICKER_PROFILE = Path(tmp) / "ticker_profiles.json"
            fake.read_prediction_log = lambda limit: predictions
            fake.read_audit_log = lambda limit: audits
            previous = sys.modules.get("memory_store")
            sys.modules["memory_store"] = fake
            try:
                row = integrity.learning_integrity_snapshot(100)
            finally:
                if previous is None:
                    sys.modules.pop("memory_store", None)
                else:
                    sys.modules["memory_store"] = previous

        self.assertEqual(row["official_prediction_rows"], 3)
        self.assertEqual(row["formal_t1_audits"], 2)
        self.assertEqual(row["learning_eligible_t1_audits"], 1)
        self.assertEqual(row["reference_limited_audits"], 1)
        self.assertEqual(row["pending_official_samples"], 1)
        self.assertEqual(row["duplicate_official_keys"], 1)
        self.assertFalse(row["decision_influence"])

    def test_audit_batch_remains_cloud_safe(self):
        self.assertEqual(integrity.recommended_audit_batch(0), 1)
        self.assertEqual(integrity.recommended_audit_batch(20), 2)
        self.assertEqual(integrity.recommended_audit_batch(20, hard_cap=8), 2)


if __name__ == "__main__":
    unittest.main()
