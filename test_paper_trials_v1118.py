from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import memory_store
from paper_trials_v1118 import build_trial, capture_trial, settle_trial, scorecard, reconcile_trials


def prediction(action="HOLD", **gate):
    return {"id":"real-prediction","ticker":"TEST.TW","market":"TW",
            "target_trade_date":"2026-11-16","run_time_tw":"2026-11-15T18:00:00+08:00",
            "anchor_close":100,"next_high_est":106,"next_close_est":101,
            "price_sample_quality":"verified",
            "public_decision_snapshot":{"session_date":"2026-11-13","action_code":action,
              "entry":{"invalidation_price":96},
              "reasoning":{"cross_module_gate":{"abc_b":50,"abc_c":25,"price_above_vwap":True,**gate}}}}


def audit(**overrides):
    return {"prediction_id":"real-prediction","ticker":"TEST.TW","market":"TW",
            "target":"next","actual_valid":True,"target_trade_date":"2026-11-16",
            "actual_price_date":"2026-11-16","price_sample_quality":"verified",
            "actual_open":100,"actual_low":98,"actual_high":104,"actual_close":103,**overrides}


class TrialTests(TestCase):
    def test_relaxes_hold_but_not_original_control(self):
        row=build_trial(prediction(),{"execution_status":"WAIT_PULLBACK"},False)
        self.assertEqual(row["orders"],["05","06","07","08","09","10"])
        strict=build_trial(prediction("BUY"),{"execution_status":"ACTIONABLE"},True)
        self.assertEqual(len(strict["orders"]),10)

    def test_valid_closed_reference_can_test_wide_policy_only(self):
        p=prediction("BLOCK",code="DATA_BLOCK")
        p["trial_market_status"]="closed_reference"
        p["public_decision_snapshot"]["entry"]["conditional_next_session"]={"eligible":True,"invalidation_price":96}
        self.assertEqual(build_trial(p,{},False)["orders"],["10"])
        p["price_sample_quality"]="reference_limited"
        self.assertEqual(build_trial(p,{},False)["orders"],[])

    def test_preserves_veto_and_no_lookahead(self):
        for gate in ("event_bearish_veto","event_reaction_wait"):
            self.assertEqual(build_trial(prediction(**{gate:True}),{},False)["orders"],[])
        self.assertEqual(build_trial(prediction("SELL"),{},False)["orders"],[])
        p=prediction();p["run_time_tw"]="2026-11-16T09:01:00+08:00"
        self.assertIsNone(build_trial(p,{},False))

    def test_only_verified_exact_target_settles_and_costs_count(self):
        row=build_trial(prediction(),{},False)
        self.assertIsNone(settle_trial(row,audit(actual_price_date="2026-11-17")))
        result=settle_trial(row,audit())
        self.assertEqual(result["status"],"CLOSED")
        self.assertTrue(0<result["net_return_pct"]<3)
        self.assertEqual(settle_trial(row,audit(actual_open=95,actual_low=94))["status"],"CANCELLED")
        txt=scorecard([row],{row["id"]:result})
        self.assertEqual(txt[0]["歷史勝率"],"—")
        self.assertEqual(txt[-1]["歷史勝率"],"100.0%")

    def test_live_capture_deduplicates_and_patch_records_only_paper_files(self):
        with TemporaryDirectory() as folder, patch.object(memory_store,"MEMORY_DIR",Path(folder)), patch.object(memory_store,"read_audit_log",return_value=[audit()]):
            now=datetime(2026,11,15,16,tzinfo=timezone.utc)
            self.assertIsNotNone(capture_trial(prediction(),{},False,now=now))
            self.assertIsNone(capture_trial(prediction(),{},False,now=now))
            self.assertEqual(reconcile_trials(),1)
            self.assertEqual(reconcile_trials(),0)
            self.assertFalse((Path(folder)/"paper_lab/portfolio_events.jsonl").exists())
