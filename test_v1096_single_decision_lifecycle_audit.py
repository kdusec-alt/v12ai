# -*- coding: utf-8 -*-
from pathlib import Path
from types import SimpleNamespace
import json
import unittest

from decision_core_v1096 import (
    build_decision_snapshot, build_recovery_lifecycle, build_typed_evidence,
)
from decision_replay_v1096 import audit_trade_horizons, summarize_trade_decision_replay
from learning import _public_trade_outcome, forecast_snapshot
from memory_policy_v1093 import memory_permission


ROOT = Path(__file__).resolve().parent


def forecast(*, state="BUY_TODAY_CONFIRM", margin_5=-9000, foreign=-1000, foreign_3=-12000, market="TW"):
    ticker = SimpleNamespace(
        resolved_symbol="TEST.TW" if market == "TW" else "TEST", name="TEST",
        market=market, asset_type="stock", exchange="TWSE" if market == "TW" else "NASDAQ",
        currency="TWD" if market == "TW" else "USD", price_limit_pct=0.10 if market == "TW" else None,
    )
    context = {
        "margin": {"accepted": True, "margin": -3000, "margin_5": margin_5, "margin_balance": 500000, "source": "YahooMargin", "date": "2026-08-03", "reason": "verified"},
        "inst": {"accepted": True, "foreign": foreign, "foreign_3": foreign_3, "source": "YahooInstitutional", "date": "2026-08-03", "reason": "verified"},
        "market_heat": {"accepted": True, "change": -100, "source": "TWSE", "date": "2026-08-03", "reason": "verified"},
        "macro": {"accepted": True, "score": 1.2, "source": "MARKET", "date": "2026-08-03", "reason": "verified"},
    }
    price = SimpleNamespace(context=context, last=102.0, previous_close=100.0, vwap=101.0, volume=10000000)
    entry = {
        "state": state, "operative_price": 102.0, "operative_return_pct": 2.0,
        "vwap": 101.0, "vwap_position": "above", "stop_price": 98.0,
        "invalidation_price": 98.0, "low_entry_zone": {"lower": 99.0, "upper": 101.0},
    }
    return SimpleNamespace(
        ticker=ticker, price_frame=price, raw=SimpleNamespace(raw_abc={"A": 20, "B": 60, "C": 20}),
        final_t0=102.0, final_t1=101.5, final_t1_high=104.0, final_t1_low=98.5,
        confidence=75, no_chase=104.0, low_entry=99.0,
        decision_card={"現價": 102.0, "昨收": 100.0, "VWAP": 101.0, "資料標題": "收盤資料", "防守": 98.0, "不追": 104.0},
        radar={}, data_truths=[SimpleNamespace(source="PRICE", date="2026-08-04", fallback=False, accepted=True, reason="ok", freshness="latest")],
        news_items=[SimpleNamespace(score=0.5)], signals=[], tags=[], one_liner="", reality_anchor="", trace=None,
        _test_entry=entry,
    )


class V1096SingleDecisionLifecycleAuditTests(unittest.TestCase):
    def test_v9_golden_master_contract_is_preserved(self):
        contract = json.loads((ROOT / "V9_GOLDEN_MASTER_CONTRACT_V1093.json").read_text(encoding="utf-8"))
        source = (ROOT / contract["frontend_file"]).read_text(encoding="utf-8")
        for layer in contract["required_layers"]:
            self.assertIn(f"class='{layer}", source)
        for label in contract["required_price_tiles"]:
            self.assertIn(f'"label": "{label}"', source)
        self.assertIn(f"height={contract['required_height']}", source)
        for forbidden in contract["forbidden_public_terms"]:
            self.assertNotIn(forbidden, source)

    def test_core_has_no_ticker_specific_hardcode(self):
        source = (ROOT / "decision_core_v1096.py").read_text(encoding="utf-8")
        for symbol in ("2337", "6770", "2330", "MRVL", "MU", "NVDA"):
            self.assertNotIn(symbol, source)

    def test_ui_reads_snapshot_and_never_calls_arbitration(self):
        source = (ROOT / "ui_v9_battle_panel.py").read_text(encoding="utf-8")
        self.assertIn("_decision_snapshot_payload", source)
        self.assertNotIn("entry = assess_entry_opportunity(p)", source)
        self.assertNotIn("reasoning = build_evidence_reasoning(p, entry)", source)
        self.assertIn("UI禁止重新仲裁", source)

    def test_typed_margin_evidence_uses_numeric_fields(self):
        facts = build_typed_evidence(forecast())
        leverage = next(item for item in facts if item.family == "leverage")
        self.assertTrue(leverage.accepted)
        self.assertEqual(leverage.direction, 1)
        self.assertEqual(leverage.unit, "pct_of_margin_balance")
        self.assertNotIn("融資減少", leverage.reason)

    def test_missing_margin_is_unknown_not_neutral_zero(self):
        row = forecast()
        row.price_frame.context["margin"] = {"accepted": False, "margin": None, "source": "WAIT"}
        leverage = next(item for item in build_typed_evidence(row) if item.family == "leverage")
        self.assertEqual(leverage.state, "UNKNOWN")
        self.assertIsNone(leverage.value)

    def test_lifecycle_does_not_invent_sequence_from_one_snapshot(self):
        row = forecast()
        facts = build_typed_evidence(row)
        lifecycle = build_recovery_lifecycle(row, row._test_entry, facts, None)
        self.assertEqual(lifecycle["state"], "REBOUNDING")
        self.assertFalse(lifecycle["sequence_verified"])

    def test_pullback_then_reclaim_verifies_sequence(self):
        row = forecast()
        facts = build_typed_evidence(row)
        lifecycle = build_recovery_lifecycle(
            row, row._test_entry, facts,
            {"lifecycle": {"state": "PULLBACK"}},
        )
        self.assertEqual(lifecycle["state"], "RECLAIMED")
        self.assertTrue(lifecycle["sequence_verified"])

    def test_public_memory_cannot_write(self):
        self.assertFalse(memory_permission("public", "write", "prediction_log")["allowed"])
        self.assertTrue(memory_permission("admin", "write", "prediction_log")["allowed"])

    def test_snapshot_is_frozen_single_action(self):
        # Patch the entry detector at its ownership boundary for a deterministic contract.
        import decision_core_v1096
        old = decision_core_v1096.assess_entry_opportunity
        decision_core_v1096.assess_entry_opportunity = lambda item: dict(item._test_entry)
        try:
            snap = build_decision_snapshot(forecast(), prior_snapshot={"lifecycle": {"state": "PULLBACK"}})
            payload = snap.to_dict()
            self.assertEqual(payload["position_status"], "UNKNOWN")
            self.assertIsNone(payload["average_cost"])
            self.assertIsNone(payload["position_size"])

        finally:
            decision_core_v1096.assess_entry_opportunity = old
        self.assertEqual(snap.schema, "TINO_DECISION_SNAPSHOT_V1096")
        self.assertIn(snap.action_code, {"BUY", "HOLD", "BLOCK", "REDUCE", "SELL"})
        with self.assertRaises(Exception):
            snap.action_code = "BUY"
        with self.assertRaises(TypeError):
            snap.lifecycle["state"] = "ENTRY_TRIGGERED"

    def test_unknown_position_never_publishes_immediate_reduce(self):
        import decision_core_v1096
        row = forecast(state="SELLING_EXPANSION_BLOCK")
        old = decision_core_v1096.assess_entry_opportunity
        decision_core_v1096.assess_entry_opportunity = lambda item: dict(item._test_entry)
        try:
            snap = build_decision_snapshot(row, prior_snapshot={"lifecycle": {"state": "PULLBACK"}})
        finally:
            decision_core_v1096.assess_entry_opportunity = old
        payload = snap.to_dict()
        self.assertEqual(payload["position_status"], "UNKNOWN")
        self.assertNotEqual(payload["action_code"], "REDUCE")
        self.assertEqual(payload["situation_code"], "HOLD_POSITION_UNKNOWN")
        self.assertIn("部位資料未知", payload["instruction"])

    def test_gate_uses_all_evidence_while_ui_only_displays_top_three(self):
        import decision_core_v1096
        row = forecast()
        old = decision_core_v1096.assess_entry_opportunity
        decision_core_v1096.assess_entry_opportunity = lambda item: dict(item._test_entry)
        try:
            snap = build_decision_snapshot(row, prior_snapshot={"lifecycle": {"state": "PULLBACK"}})
        finally:
            decision_core_v1096.assess_entry_opportunity = old
        payload = snap.to_dict()
        self.assertGreater(len(payload["evidence"]), 3)
        self.assertLessEqual(len(payload["reasoning"]["top_drivers"]), 3)
        self.assertIn("tw_margin", payload["reasoning"]["cross_module_gate"]["recovery_confirmation_groups"])

    def test_public_action_is_logged(self):
        row = forecast()
        row.decision_snapshot = SimpleNamespace(to_dict=lambda: {
            "schema": "TINO_DECISION_SNAPSHOT_V1096", "action_code": "BUY",
            "situation_code": "BUY_RECOVERY_ENTRY", "label": "買進", "reason": "ok",
            "entry": {"current_price": 100, "confirmation_price": 101, "invalidation_price": 97},
            "lifecycle": {"state": "ENTRY_TRIGGERED"}, "funnel": [],
        })
        snap = forecast_snapshot(row)
        self.assertEqual(snap["public_action"], "BUY")
        self.assertEqual(snap["public_lifecycle_state"], "ENTRY_TRIGGERED")

    def test_trade_outcome_has_mfe_mae_and_stop(self):
        outcome = _public_trade_outcome(
            {"public_action": "BUY", "public_entry_price": 100, "public_invalidation_price": 97},
            {"actual_high": 106, "actual_low": 96}, 103,
        )
        self.assertEqual(outcome["mfe_pct"], 6.0)
        self.assertEqual(outcome["mae_pct"], 4.0)
        self.assertTrue(outcome["stop_touched"])

    def test_replay_scores_quality_not_buy_count(self):
        report = summarize_trade_decision_replay(
            [{"public_action": "BUY", "public_blocking_stage": None}],
            [{"public_trade_audit": {"public_action": "BUY", "risk_adjusted_quality": 2.5, "stop_touched": False, "missed_rebound": False}}],
        )
        self.assertEqual(report["success_objective"], "risk_adjusted_quality_not_buy_frequency")
        self.assertEqual(report["quality_by_action"]["BUY"]["mean_risk_adjusted_quality"], 2.5)

    def test_horizon_audit_reports_t1_t3_t5(self):
        sessions = [
            {"close": 101 + i, "high": 102 + i, "low": 99 - i * 0.2}
            for i in range(5)
        ]
        audit = audit_trade_horizons(
            {"public_action": "BUY", "public_entry_price": 100, "public_invalidation_price": 97},
            sessions,
        )
        self.assertTrue(audit["horizons"]["t_plus_1"]["available"])
        self.assertTrue(audit["horizons"]["t_plus_3"]["available"])
        self.assertTrue(audit["horizons"]["t_plus_5"]["available"])


if __name__ == "__main__":
    unittest.main()
