# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest

import learning
from models import TickerInfo


class LearningReadonlyCompareTests(unittest.TestCase):
    def setUp(self):
        self.old_audits = learning.read_audit_log
        self.old_predictions = learning.read_prediction_log
        self.old_append = learning.append_jsonl
        self.old_audit_date = learning._audit_trade_date

    def tearDown(self):
        learning.read_audit_log = self.old_audits
        learning.read_prediction_log = self.old_predictions
        learning.append_jsonl = self.old_append
        learning._audit_trade_date = self.old_audit_date

    @staticmethod
    def _forecast():
        ticker = TickerInfo("2337.TW", "2337.TW", "旺宏", "TW", "stock", price_limit_pct=0.10)
        return SimpleNamespace(ticker=ticker, decision_card={"現價": 109.8})

    def test_confirmed_audit_is_found_by_ticker_and_session_not_latest_prediction_id(self):
        learning._audit_trade_date = lambda market=None: "2026-07-22"
        learning.read_audit_log = lambda limit=500: [{
            "audit_id": "older-official:next",
            "prediction_id": "older-official",
            "ticker": "2337.TW",
            "target": "next",
            "target_trade_date": "2026-07-22",
            "prediction_run_date_tw": "2026-07-21",
            "predicted_close": 102.0,
            "actual_close": 109.8,
            "error": 7.8,
            "error_pct": 7.6471,
            "anchor_close": 100.0,
            "actual_valid": True,
            "audit_time_tw": "2026-07-22T14:01:00+08:00",
        }]
        # A newer event-revision prediction exists, but it was not the row Auto
        # Audit confirmed.  The front stage must still show the official audit.
        learning.read_prediction_log = lambda limit=500: [{
            "id": "new-event-revision",
            "ticker": "2337.TW",
            "target_trade_date": "2026-07-22",
            "run_date_tw": "2026-07-21",
            "run_time_tw": "2026-07-21T23:00:00+08:00",
            "next_close_est": 105.0,
        }]
        learning.append_jsonl = lambda *args, **kwargs: self.fail("read-only compare attempted a write")
        result = learning.t1_prediction_vs_actual(self._forecast(), 109.8, write=False)
        self.assertEqual(result["status"], "audited")
        self.assertTrue(result["readonly"])
        self.assertEqual(result["prediction_id"], "older-official")
        self.assertIn("2026-07-22 收盤", result["display"])

    def test_unconfirmed_actual_is_never_rendered_as_official_audit(self):
        learning._audit_trade_date = lambda market=None: "2026-07-22"
        learning.read_audit_log = lambda limit=500: [{
            "ticker": "2337.TW",
            "target": "next",
            "target_trade_date": "2026-07-22",
            "predicted_close": 102.0,
            "actual_close": 109.8,
            "anchor_close": 100.0,
            "actual_valid": False,
        }]
        learning.read_prediction_log = lambda limit=500: []
        result = learning.t1_prediction_vs_actual(self._forecast(), 109.8, write=False)
        self.assertEqual(result["status"], "no_t1_prediction")


if __name__ == "__main__":
    unittest.main()
