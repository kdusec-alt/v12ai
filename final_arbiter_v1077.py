# -*- coding: utf-8 -*-
"""Single final shadow arbitration contract for V1077.

All existing engines finish first.  This arbiter then records one immutable
decision contract and any disagreement between model direction, decision
thesis, event gates, prediction trust, and the crash-state engine.

V1077 is observation-only: the contract cannot rewrite formal prices,
probabilities, confidence, entry levels, or learning weights.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Mapping


SCHEMA = "TINO_FINAL_ARBITER_V1077_SHADOW_V1"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value is None:
        return {}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict() or {})
        except Exception:
            pass
    return dict(getattr(value, "__dict__", {}) or {})


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def run_final_arbiter(
    *,
    direction: Any,
    decision_thesis: Mapping[str, Any] | None,
    prediction_trust: Mapping[str, Any] | None,
    market_regime: Mapping[str, Any] | None,
    news_causal: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return the one final shadow action after every upstream layer."""
    model = _as_dict(direction)
    thesis = dict(decision_thesis or {})
    trust = dict(prediction_trust or {})
    regime = dict(market_regime or {})
    causal = dict(news_causal or {})

    label = str(model.get("label") or "NEUTRAL").upper()
    p_up = _num(model.get("p_up"))
    p_neutral = _num(model.get("p_neutral"))
    p_down = _num(model.get("p_down"))
    quality = _num(model.get("quality"))
    conflict = _num(model.get("conflict"))
    directional_gap = abs(p_up - p_down)
    thesis_permission = str(thesis.get("entry_permission") or "conditional")
    thesis_action = str(thesis.get("action_mode") or "wait")
    thesis_state = str(thesis.get("state") or "")
    regime_state = str(regime.get("state") or "normal_correction")
    confirmation_score = _num(regime.get("confirmation_score"))
    causal_state = str(causal.get("causal_state") or "")

    vetoes: list[str] = []
    confirmations: list[str] = []
    if thesis_state == "price_truth_blocked":
        vetoes.append("price_truth_blocked")
    if causal_state in {
        "event_awaiting_market_reaction",
        "scheduled_event_pending",
        "event_reaction_in_progress",
    }:
        vetoes.append(causal_state)
    if bool(trust.get("entry_block")):
        vetoes.append("prediction_trust_entry_block")
    if quality < 0.55:
        vetoes.append("direction_quality_below_0.55")
    if conflict >= 0.48:
        vetoes.append("evidence_conflict")
    if regime_state in {"selling_expansion", "panic_acceleration", "deleveraging"}:
        vetoes.append(regime_state)

    abstain = bool(
        label == "NEUTRAL"
        or max(p_up, p_down) < 0.50
        or directional_gap < 0.15
        or conflict >= 0.48
        or quality < 0.55
    )
    if not abstain:
        confirmations.append(f"direction_{label.lower()}")
    if regime_state in {"rebound_confirmed", "trend_repair"}:
        confirmations.append(regime_state)
    if confirmation_score >= 64.0:
        confirmations.append("price_confirmation_score")

    hard_block = bool(
        thesis_permission == "blocked"
        or thesis_state == "price_truth_blocked"
        or causal_state in {"event_awaiting_market_reaction", "event_reaction_in_progress"}
        or bool(trust.get("entry_block"))
    )
    if hard_block:
        action = "BLOCKED"
        permission = "blocked"
    elif regime_state in {"selling_expansion", "panic_acceleration", "deleveraging"}:
        action = "DEFEND"
        permission = "observe"
    elif regime_state == "selling_exhaustion":
        action = "EXHAUSTION_WATCH"
        permission = "observe"
    elif regime_state == "bottom_probe":
        action = "TEST_ONLY" if not abstain and label != "DOWN" else "WAIT_CONFIRMATION"
        permission = "test" if action == "TEST_ONLY" else "observe"
    elif regime_state == "rebound_confirmed":
        action = "CONFIRMED_REBOUND" if label != "DOWN" and not abstain else "WAIT_CONFIRMATION"
        permission = "conditional" if action == "CONFIRMED_REBOUND" else "observe"
    elif regime_state == "trend_repair":
        action = "TREND_FOLLOW" if label == "UP" and not abstain else "CONDITIONAL"
        permission = "conditional"
    else:
        action = "WAIT_CONFIRMATION"
        permission = "observe"

    formal_blocked = thesis_permission == "blocked"
    shadow_blocked = permission == "blocked"
    material_divergence = bool(
        formal_blocked != shadow_blocked
        or (
            thesis_action in {"pullback", "pullback_or_confirmation"}
            and action in {"DEFEND", "BLOCKED"}
        )
        or (
            thesis_action in {"reclaim_only", "cooldown", "session_wait"}
            and action in {"CONFIRMED_REBOUND", "TREND_FOLLOW"}
        )
    )

    fingerprint_payload = {
        "formal_direction": label,
        "probabilities": [p_up, p_neutral, p_down],
        "quality": quality,
        "conflict": conflict,
        "thesis_state": thesis_state,
        "thesis_action": thesis_action,
        "thesis_permission": thesis_permission,
        "trust_block": bool(trust.get("entry_block")),
        "causal_state": causal_state,
        "regime_state": regime_state,
        "regime_transition": regime.get("transition"),
        "shadow_action": action,
    }
    return {
        "schema": SCHEMA,
        "shadow": True,
        "decision_influence": False,
        "execution_order": "after_direction_after_thesis_after_trust_after_regime",
        "arbiter_passes": 1,
        "input_fingerprint": _fingerprint(fingerprint_payload),
        "formal_direction": label,
        "formal_direction_score": model.get("score"),
        "formal_probabilities": {
            "up": round(p_up, 6),
            "neutral": round(p_neutral, 6),
            "down": round(p_down, 6),
        },
        "direction_call": "ABSTAIN" if abstain else label,
        "refused_direction_prediction": abstain,
        "shadow_action": action,
        "shadow_entry_permission": permission,
        "market_state": regime_state,
        "market_state_label": regime.get("state_label"),
        "market_transition": regime.get("transition"),
        "stress_score": regime.get("stress_score"),
        "exhaustion_score": regime.get("exhaustion_score"),
        "confirmation_score": regime.get("confirmation_score"),
        "formal_thesis_state": thesis_state,
        "formal_thesis_action": thesis_action,
        "formal_entry_permission": thesis_permission,
        "vetoes": list(dict.fromkeys(vetoes)),
        "confirmations": list(dict.fromkeys(confirmations)),
        "material_display_divergence": material_divergence,
        "divergence_reason": (
            f"formal={thesis_permission}/{thesis_action}; shadow={permission}/{action}"
            if material_divergence else ""
        ),
        "formal_fields_immutable": [
            "T0",
            "T1",
            "T1_HIGH",
            "T1_LOW",
            "ABC",
            "direction_probability",
            "confidence",
            "entry_levels",
            "learning_weights",
        ],
        "formal_weights_changed": False,
    }
