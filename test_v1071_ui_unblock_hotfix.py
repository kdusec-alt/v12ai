import ast
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import learning
from auto_audit_scheduler import _market_window
from v13_research.close_recheck import _cutoff_time


ROOT = Path(__file__).resolve().parent


def _function_source(path: str, function_name: str) -> str:
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"{function_name} not found in {path}")


class V1071UiUnblockHotfixTests(unittest.TestCase):
    def test_admin_render_is_status_only(self):
        body = _function_source("ui_admin.py", "render_admin")
        self.assertIn("_render_admin_auto_audit_status(st)", body)
        self.assertNotIn("run_admin_auto_audit_cycle(", body)
        self.assertNotIn("execute_due_auto_audit_once(", body)

    def test_full_main_never_runs_close_recheck(self):
        body = _function_source("app.py", "main")
        self.assertNotIn("run_login_close_recheck(", body)
        self.assertIn("_admin_maintenance_fragment()", body)

    def test_fragment_uses_one_ticker_and_never_full_rerun(self):
        body = _function_source("app.py", "_admin_maintenance_fragment_body")
        detector = _function_source("app.py", "_is_fragment_rerun")
        self.assertIn("max_tickers_per_market=2", body)
        self.assertIn("request_rerun=False", body)
        self.assertIn("batch_size_override=1", body)
        self.assertIn("if not _is_fragment_rerun()", body)
        self.assertIn("fragment_ids_this_run", detector)

    def test_due_calibration_is_not_postponed_by_active_forecast_or_view(self):
        body = _function_source("app.py", "_admin_maintenance_fragment_body")
        self.assertNotIn('get("forecast")', body)
        self.assertNotIn('get("main_view")', body)

    def test_tw_auto_audit_cutoff_remains_1410_taipei(self):
        before = datetime(2026, 7, 28, 14, 9, tzinfo=ZoneInfo("Asia/Taipei"))
        at_cutoff = datetime(2026, 7, 28, 14, 10, tzinfo=ZoneInfo("Asia/Taipei"))
        self.assertFalse(_market_window("TW", before)["ready"])
        self.assertTrue(_market_window("TW", at_cutoff)["ready"])

    def test_us_auto_audit_cutoff_remains_1615_new_york(self):
        before_ny = datetime(2026, 7, 28, 16, 14, tzinfo=ZoneInfo("America/New_York"))
        at_cutoff_ny = datetime(2026, 7, 28, 16, 15, tzinfo=ZoneInfo("America/New_York"))
        self.assertFalse(_market_window("US", before_ny.astimezone(ZoneInfo("Asia/Taipei")))["ready"])
        self.assertTrue(_market_window("US", at_cutoff_ny.astimezone(ZoneInfo("Asia/Taipei")))["ready"])

    def test_tw_close_recheck_cutoff_remains_1700_taipei(self):
        self.assertEqual(_cutoff_time().strftime("%H:%M"), "17:00")

    def test_deferred_tickers_are_not_false_errors(self):
        rows = [
            {
                "id": "A:next",
                "ticker": "1111.TW",
                "market": "TW",
                "target_trade_date": "2026-07-28",
                "next_close_est": 100.0,
                "session_mode": "closed",
            },
            {
                "id": "B:next",
                "ticker": "2222.TW",
                "market": "TW",
                "target_trade_date": "2026-07-28",
                "next_close_est": 200.0,
                "session_mode": "closed",
            },
        ]
        snapshot = {
            "actual_close": 101.0,
            "price_date": "2026-07-28",
            "market_status": "closed_reference",
        }
        with (
            patch.object(learning, "read_prediction_log", return_value=rows),
            patch.object(learning, "_audit_id_set", return_value=set()),
            patch.object(learning, "fetch_actual_daily_snapshot", return_value=snapshot) as fetch,
            patch.object(learning, "actual_matches_target", return_value=True),
            patch.object(learning, "audit_prediction_row", return_value={"audit_id": "A:next"}),
            patch.object(learning, "refresh_learning_weights", return_value={"status": "OK"}),
            patch.object(learning, "prediction_audit_dashboard", return_value={}),
        ):
            result = learning.auto_audit_queried_predictions(
                limit=20,
                max_tickers=1,
                apply_safe_learning=False,
                market_filter="TW",
                trade_date="2026-07-28",
            )

        fetch.assert_called_once_with("1111.TW")
        self.assertEqual(result["audited_t1_count"], 1)
        self.assertEqual(result["fetched_ticker_count"], 1)
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
