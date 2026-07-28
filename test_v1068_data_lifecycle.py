# -*- coding: utf-8 -*-
from pathlib import Path
import unittest

from learning_center_core import _latest_formal_samples
from v1068_runtime_patches import latest_t1_audit_records, research_status_text


ROOT = Path(__file__).resolve().parent


class V1068DataLifecycleTests(unittest.TestCase):
    def test_recent_t1_view_keeps_latest_same_ticker_same_target_date(self):
        rows = [
            {
                "target": "next", "ticker": "MU", "market": "US",
                "target_trade_date": "2026-07-27", "audit_time_tw": "2026-07-28T08:01:00+08:00",
                "prediction_run_time_tw": "2026-07-27T22:01:00+08:00", "predicted_close": 898.06,
            },
            {
                "target": "next", "ticker": "MU", "market": "US",
                "target_trade_date": "2026-07-27", "audit_time_tw": "2026-07-28T08:03:00+08:00",
                "prediction_run_time_tw": "2026-07-27T22:02:00+08:00", "predicted_close": 895.23,
            },
        ]
        visible = latest_t1_audit_records(rows)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["predicted_close"], 895.23)
        self.assertEqual(len(rows), 2, "raw audit history must remain append-only")

    def test_recent_t1_view_keeps_different_target_dates(self):
        rows = [
            {"target": "next", "ticker": "TSLL", "market": "US", "target_trade_date": "2026-07-27", "audit_time_tw": "2026-07-28T08:00:00+08:00"},
            {"target": "next", "ticker": "TSLL", "market": "US", "target_trade_date": "2026-07-28", "audit_time_tw": "2026-07-29T08:00:00+08:00"},
        ]
        self.assertEqual(len(latest_t1_audit_records(rows)), 2)

    def test_official_sample_is_already_latest_by_ticker_date_and_kind(self):
        rows = [
            {"ticker": "AMD", "target_trade_date": "2026-07-28", "target_kind": "T1_CLOSE_NEXT_SESSION", "run_time_tw": "2026-07-27T22:00:00+08:00", "next_close_est": 480.0},
            {"ticker": "AMD", "target_trade_date": "2026-07-28", "target_kind": "T1_CLOSE_NEXT_SESSION", "run_time_tw": "2026-07-27T22:30:00+08:00", "next_close_est": 482.6},
        ]
        visible = _latest_formal_samples(rows)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["next_close_est"], 482.6)
        self.assertEqual(len(rows), 2, "raw prediction history must not be overwritten")

    def test_research_status_explains_waiting_and_idle(self):
        waiting = research_status_text({"status": "waiting", "waiting_institution": 3}, {})
        self.assertEqual(waiting["level"], "waiting")
        self.assertIn("不是系統故障", waiting["detail"])
        idle = research_status_text({"status": "waiting", "waiting_institution": 0}, {})
        self.assertEqual(idle["level"], "idle")
        self.assertIn("正常待命", idle["detail"])

    def test_admin_ack_patch_uses_fragment_scope_and_skips_legacy_full_rerun(self):
        source = (ROOT / "v1068_runtime_patches.py").read_text(encoding="utf-8")
        self.assertIn('st.rerun(scope="fragment")', source)
        self.assertIn("return False", source)
        self.assertIn("_install_fragment_safe_admin_ack", source)

    def test_research_health_uses_high_contrast_metrics(self):
        source = (ROOT / "v1068_runtime_patches.py").read_text(encoding="utf-8")
        self.assertIn("v1068-research-health", source)
        self.assertIn('color:#f2f8ff!important', source)
        self.assertIn("Decision Influence 維持 FALSE", source)


if __name__ == "__main__":
    unittest.main()
