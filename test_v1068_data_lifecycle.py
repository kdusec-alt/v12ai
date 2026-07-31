# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import unittest

from learning_center_core import _latest_formal_samples
from learning_market_clock import target_trade_date_for_forecast
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

    def test_us_intraday_t1_is_always_next_official_session(self):
        friday = SimpleNamespace(
            ticker=SimpleNamespace(market="US"),
            decision_card={"資料標題": "盤中資料"},
            data_truths=[SimpleNamespace(date="2026-07-31")],
        )
        self.assertEqual(target_trade_date_for_forecast(friday), "2026-08-03")

        premarket = SimpleNamespace(
            ticker=SimpleNamespace(market="US"),
            decision_card={"資料標題": "盤前資料"},
            data_truths=[SimpleNamespace(date="2026-07-30")],
        )
        self.assertEqual(target_trade_date_for_forecast(premarket), "2026-07-31")

    def test_research_status_explains_waiting_and_idle(self):
        waiting = research_status_text({"status": "waiting", "waiting_institution": 3}, {})
        self.assertEqual(waiting["level"], "waiting")
        self.assertIn("不是系統故障", waiting["detail"])
        idle = research_status_text({"status": "waiting", "waiting_institution": 0}, {})
        self.assertEqual(idle["level"], "idle")
        self.assertIn("正常待命", idle["detail"])

    def test_admin_ack_patch_avoids_rerun_before_sessioninfo_initializes(self):
        source = (ROOT / "v1068_runtime_patches.py").read_text(encoding="utf-8")
        start = source.index("def acknowledge_global_event_v1068")
        end = source.index("lifecycle.acknowledge_global_event =", start)
        callback = source[start:end]
        self.assertNotIn("st.rerun", callback)
        self.assertIn("get_global_event_view()", callback)
        self.assertIn("return True", callback)
        self.assertIn("_install_fragment_safe_admin_ack", source)

    def test_research_health_uses_high_contrast_metrics(self):
        source = (ROOT / "v1068_runtime_patches.py").read_text(encoding="utf-8")
        self.assertIn("v1068-research-health", source)
        self.assertIn('color:#f2f8ff!important', source)
        self.assertIn("Decision Influence 維持 FALSE", source)


if __name__ == "__main__":
    unittest.main()
