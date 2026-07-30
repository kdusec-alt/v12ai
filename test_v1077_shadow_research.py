# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import learning
from final_arbiter_v1077 import run_final_arbiter
from market_regime_v1077 import build_market_regime_shadow
from models import DataTruth, PriceFrame, TickerInfo


def _direction(
    label: str = "NEUTRAL",
    *,
    conflict: float = 0.20,
    quality: float = 0.85,
    flow: float = 0.0,
    intraday: float = 0.0,
    price_action: float = 0.0,
):
    p_up, p_neutral, p_down = {
        "UP": (0.62, 0.23, 0.15),
        "DOWN": (0.15, 0.23, 0.62),
        "NEUTRAL": (0.28, 0.46, 0.26),
    }[label]
    return SimpleNamespace(
        label=label,
        score=30.0 if label == "UP" else -30.0 if label == "DOWN" else 0.0,
        p_up=p_up,
        p_neutral=p_neutral,
        p_down=p_down,
        confidence=68.0,
        quality=quality,
        conflict=conflict,
        family_scores={
            "flow": flow,
            "intraday": intraday,
            "price_action": price_action,
        },
        to_dict=lambda: {
            "label": label,
            "score": 30.0 if label == "UP" else -30.0 if label == "DOWN" else 0.0,
            "p_up": p_up,
            "p_neutral": p_neutral,
            "p_down": p_down,
            "quality": quality,
            "conflict": conflict,
            "family_scores": {
                "flow": flow,
                "intraday": intraday,
                "price_action": price_action,
            },
        },
    )


def _frame(
    closes,
    *,
    last: float,
    previous: float,
    open_: float,
    high: float,
    low: float,
    vwap: float,
    volume_ratio: float = 1.0,
    context=None,
    symbol: str = "9999.TW",
    name: str = "測試公司",
):
    series = list(closes)
    if not series or series[-1] != last:
        series.append(last)
    prior_volumes = [1_000_000.0] * max(len(series) - 1, 1)
    volumes = prior_volumes + [1_000_000.0 * volume_ratio]
    volumes = volumes[-len(series):]
    return PriceFrame(
        ticker=TickerInfo(
            symbol,
            symbol,
            name,
            "TW",
            "stock",
            price_limit_pct=0.10,
        ),
        truth=DataTruth("UNIT_REAL", "2026-07-30", False, True, "unit"),
        open=open_,
        high=high,
        low=low,
        last=last,
        previous_close=previous,
        volume=volumes[-1],
        vwap=vwap,
        atr14=3.0,
        recent_closes=series,
        recent_highs=[value + 1.0 for value in series[:-1]] + [high],
        recent_lows=[value - 1.0 for value in series[:-1]] + [low],
        recent_volumes=volumes,
        price_date="2026-07-30",
        market_status="after_close",
        context=context or {},
    )


class V1077ShadowResearchTests(unittest.TestCase):
    def test_panic_acceleration_is_not_mislabeled_as_rebound(self):
        frame = _frame(
            [130, 128, 126, 124, 122, 119, 116, 112, 108, 104, 100, 94],
            last=90,
            previous=94,
            open_=95,
            high=96,
            low=89,
            vwap=94,
            volume_ratio=2.0,
        )
        result = build_market_regime_shadow(
            frame,
            _direction("DOWN", flow=-25, intraday=-55, price_action=-48),
        )
        self.assertEqual(result["state"], "panic_acceleration")
        self.assertGreaterEqual(result["stress_score"], 72)
        self.assertLess(result["confirmation_score"], 64)

    def test_deep_lower_shadow_with_wash_is_exhaustion_not_automatic_buy(self):
        frame = _frame(
            [130, 128, 126, 124, 122, 119, 116, 112, 108, 104, 100],
            last=99,
            previous=100,
            open_=100,
            high=101,
            low=92,
            vwap=98.5,
            volume_ratio=2.2,
            context={
                "market_heat": {
                    "accepted": True,
                    "change_yi": -385.22,
                    "balance_yi": 5070.11,
                },
                "margin": {
                    "accepted": True,
                    "margin": -500,
                    "margin_3": -1200,
                    "margin_streak": "連減3日",
                },
                "market_breadth": {
                    "accepted": True,
                    "new_low_count": 80,
                    "previous_new_low_count": 140,
                    "limit_down_count": 12,
                    "previous_limit_down_count": 35,
                    "decliner_count": 900,
                    "previous_decliner_count": 1400,
                },
            },
        )
        regime = build_market_regime_shadow(
            frame,
            _direction("NEUTRAL", flow=5, intraday=18, price_action=20),
        )
        self.assertEqual(regime["state"], "selling_exhaustion")
        self.assertGreaterEqual(regime["exhaustion_score"], 55)
        self.assertTrue(regime["features"]["breadth_stabilizing"])
        arbiter = run_final_arbiter(
            direction=_direction("NEUTRAL"),
            decision_thesis={"entry_permission": "conditional", "action_mode": "confirmation_only"},
            prediction_trust={},
            market_regime=regime,
        )
        self.assertEqual(arbiter["shadow_action"], "EXHAUSTION_WATCH")
        self.assertEqual(arbiter["shadow_entry_permission"], "observe")

    def test_rebound_requires_positive_price_confirmation(self):
        frame = _frame(
            [130, 128, 126, 124, 122, 119, 116, 112, 108, 104, 100, 95],
            last=99,
            previous=95,
            open_=95,
            high=100,
            low=94,
            vwap=97,
            volume_ratio=1.5,
        )
        result = build_market_regime_shadow(
            frame,
            _direction("UP", flow=12, intraday=35, price_action=28),
        )
        self.assertEqual(result["state"], "rebound_confirmed")
        self.assertGreaterEqual(result["confirmation_score"], 64)

    def test_small_green_below_vwap_is_not_confirmed_rebound(self):
        frame = _frame(
            [130, 128, 126, 124, 122, 119, 116, 112, 108, 104, 100, 95],
            last=95.4,
            previous=95,
            open_=95,
            high=97,
            low=94,
            vwap=96.5,
            volume_ratio=0.9,
        )
        result = build_market_regime_shadow(
            frame,
            _direction("NEUTRAL", conflict=0.42, intraday=-5, price_action=-8),
        )
        self.assertNotEqual(result["state"], "rebound_confirmed")
        self.assertTrue(result["requires_price_confirmation"])

    def test_regime_core_is_ticker_and_industry_agnostic(self):
        kwargs = dict(
            closes=[130, 128, 126, 124, 122, 119, 116, 112, 108, 104, 100, 94],
            last=90,
            previous=94,
            open_=95,
            high=96,
            low=89,
            vwap=94,
            volume_ratio=2.0,
        )
        airline = _frame(**kwargs, symbol="2618.TW", name="長榮航")
        retailer = _frame(**kwargs, symbol="9998.TW", name="測試零售")
        direction = _direction("DOWN", flow=-20, intraday=-50, price_action=-45)
        self.assertEqual(
            build_market_regime_shadow(airline, direction)["state"],
            build_market_regime_shadow(retailer, direction)["state"],
        )

    def test_conflicting_direction_is_explicit_abstention(self):
        result = run_final_arbiter(
            direction=_direction("UP", conflict=0.55),
            decision_thesis={"entry_permission": "conditional", "action_mode": "pullback"},
            prediction_trust={},
            market_regime={
                "state": "normal_correction",
                "state_label": "正常修正",
                "transition": "normal_correction->normal_correction",
            },
        )
        self.assertEqual(result["direction_call"], "ABSTAIN")
        self.assertIn("evidence_conflict", result["vetoes"])
        self.assertFalse(result["decision_influence"])

    def test_event_time_veto_outranks_rebound_candidate(self):
        result = run_final_arbiter(
            direction=_direction("UP"),
            decision_thesis={"entry_permission": "conditional", "action_mode": "pullback"},
            prediction_trust={},
            market_regime={
                "state": "rebound_confirmed",
                "state_label": "反彈確認",
                "transition": "bottom_probe->rebound_confirmed",
                "confirmation_score": 80,
            },
            news_causal={"causal_state": "event_awaiting_market_reaction"},
        )
        self.assertEqual(result["shadow_action"], "BLOCKED")
        self.assertIn("event_awaiting_market_reaction", result["vetoes"])

    def test_multi_target_audit_records_path_and_avoidance_without_weight_effect(self):
        row = {
            "id": "prediction-1",
            "official_sample_key": "sample-1",
            "ticker": "9999.TW",
            "market": "TW",
            "model_version": "UNIT",
            "target_trade_date": "2026-07-30",
            "target_kind": "T1_CLOSE_NEXT_SESSION",
            "run_date_tw": "2026-07-29",
            "anchor_close": 100.0,
            "next_close_est": 101.0,
            "next_low_est": 96.0,
            "next_high_est": 104.0,
            "predicted_direction": "UP",
            "direction_neutral_band_pct": 0.30,
            "p_up": 0.60,
            "p_neutral": 0.25,
            "p_down": 0.15,
            "price_sample_quality": "verified",
            "actual_valid": True,
            "predicted_market_state": "selling_exhaustion",
            "predicted_market_transition": "deleveraging->selling_exhaustion",
            "shadow_action": "EXHAUSTION_WATCH",
            "market_regime_v1077": {"features": {"atr": 3.0}},
        }
        snap = {
            "actual_valid": True,
            "actual_open": 98.0,
            "actual_high": 104.0,
            "actual_low": 95.0,
            "actual_close": 103.0,
            "actual_vwap": 100.5,
            "price_date": "2026-07-30",
            "market_status": "closed",
            "source": "UNIT_OFFICIAL",
        }
        stored = []
        profiles = {}
        with (
            patch.object(learning, "read_audit_log", return_value=[]),
            patch.object(learning, "append_jsonl", side_effect=lambda path, data: stored.append(dict(data))),
            patch.object(learning, "load_profiles", side_effect=lambda: dict(profiles)),
            patch.object(learning, "save_profiles", side_effect=lambda data: profiles.update(data)),
        ):
            audit = learning.audit_prediction_row(
                row,
                103.0,
                source="unit",
                target="next",
                actual_snapshot=snap,
            )
        shadow = audit["multi_target_shadow"]
        self.assertEqual(shadow["ohlc_path_proxy"], "lower_low_recovered")
        self.assertEqual(shadow["mfe_pct"], 4.0)
        self.assertEqual(shadow["mae_pct"], 5.0)
        self.assertTrue(shadow["regime_next_session_hit"])
        self.assertTrue(shadow["missed_rebound"])
        self.assertFalse(shadow["decision_influence"])
        self.assertFalse(audit["shadow_decision_influence"])
        self.assertEqual(
            profiles["9999.TW"]["shadow_multi_target"]["audit_count"],
            1,
        )

    def test_official_sample_key_dedupes_reruns_but_not_raw_prediction_ids(self):
        first = learning._official_sample_key(
            market="TW",
            ticker="9999.TW",
            target_trade_date="2026-07-30",
        )
        second = learning._official_sample_key(
            market="TW",
            ticker="9999.TW",
            target_trade_date="2026-07-30",
        )
        next_day = learning._official_sample_key(
            market="TW",
            ticker="9999.TW",
            target_trade_date="2026-07-31",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, next_day)
        rows = [
            {
                "id": "older-rerun",
                "official_sample_key": first,
                "ticker": "9999.TW",
                "market": "TW",
                "target_trade_date": "2026-07-30",
                "target_kind": "T1_CLOSE_NEXT_SESSION",
                "next_close_est": 98.0,
            },
            {
                "id": "latest-rerun",
                "official_sample_key": first,
                "ticker": "9999.TW",
                "market": "TW",
                "target_trade_date": "2026-07-30",
                "target_kind": "T1_CLOSE_NEXT_SESSION",
                "next_close_est": 101.0,
            },
        ]
        with (
            patch.object(learning, "read_prediction_log", return_value=rows),
            patch.object(learning, "_audit_id_set", return_value=set()),
        ):
            pending = learning.pending_auto_audit_summary(
                market_filter="TW",
                trade_date="2026-07-30",
            )
        self.assertEqual(pending["pending_t1_count"], 1)
        with (
            patch.object(learning, "read_prediction_log", return_value=rows),
            patch.object(
                learning,
                "_audit_id_set",
                return_value={"latest-rerun:next"},
            ),
        ):
            completed = learning.pending_auto_audit_summary(
                market_filter="TW",
                trade_date="2026-07-30",
            )
        self.assertEqual(completed["pending_t1_count"], 0)

    def test_orchestrator_runs_final_arbiter_once_without_mutating_formal_outputs(self):
        import orchestrator

        frame = _frame(
            [90 + index for index in range(24)],
            last=114,
            previous=113,
            open_=113,
            high=115,
            low=112.5,
            vwap=113.5,
            context={
                "price_meta": {"price_verified": True, "source": "UNIT_REAL"},
                "market_heat": {"accepted": False, "source": "UNIT"},
                "inst": {},
                "margin": {},
                "bsi": {},
                "macro": {},
                "short": {},
            },
        )
        original = orchestrator.run_final_arbiter
        with patch.object(orchestrator, "run_final_arbiter", wraps=original) as wrapped:
            baseline = orchestrator.orchestrate(frame)
        self.assertEqual(wrapped.call_count, 1)
        with patch.object(
            orchestrator,
            "run_final_arbiter",
            return_value={
                "schema": "UNIT",
                "shadow": True,
                "decision_influence": False,
                "shadow_action": "BLOCKED",
            },
        ):
            challenged = orchestrator.orchestrate(frame)
        self.assertEqual(baseline.final_t0, challenged.final_t0)
        self.assertEqual(baseline.final_t1, challenged.final_t1)
        self.assertEqual(baseline.final_t1_high, challenged.final_t1_high)
        self.assertEqual(baseline.final_t1_low, challenged.final_t1_low)
        self.assertEqual(baseline.confidence, challenged.confidence)
        self.assertEqual(
            baseline.decision_card["_direction_engine"],
            challenged.decision_card["_direction_engine"],
        )
        self.assertEqual(baseline.trace.to_rows(), challenged.trace.to_rows())
        snapshot = learning.forecast_snapshot(baseline)
        self.assertTrue(snapshot["official_sample_key"])
        self.assertEqual(
            snapshot["market_regime_v1077"]["schema"],
            "TINO_MARKET_REGIME_V1077_SHADOW_V1",
        )
        self.assertEqual(
            snapshot["final_arbiter_v1077"]["arbiter_passes"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
