# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict

from models import FinalForecast


def _direction_sign(label: Any) -> int:
    text = str(label or "").strip().upper()
    return 1 if text == "UP" else -1 if text == "DOWN" else 0


def top_contributions(values: Dict[str, Any], limit: int | None = 16) -> Dict[str, float]:
    rows = []
    for name, value in dict(values or {}).items():
        try:
            number = float(value)
        except Exception:
            continue
        rows.append((str(name), number))
    rows.sort(key=lambda item: abs(item[1]), reverse=True)
    selected = rows if limit is None else rows[: max(1, int(limit))]
    return {name: round(value, 4) for name, value in selected}


def prediction_dna(forecast: FinalForecast, direction: Dict[str, Any], card: Dict[str, Any]) -> Dict[str, Any]:
    # Prediction DNA is the audit SSOT, so it keeps every direction factor.
    # The Learning Center may display a compact subset, but no hidden factor is
    # allowed to disappear from the stored explanation or attribution path.
    factors = top_contributions(dict(direction.get("factor_contributions") or {}), None)
    families = top_contributions(dict(direction.get("family_contributions") or {}), None)
    risks = top_contributions(dict(direction.get("risk_contributions") or {}), None)
    dominant_name = ""
    dominant_value = 0.0
    if factors:
        dominant_name, dominant_value = max(factors.items(), key=lambda item: abs(item[1]))
    direction_score = round(float(direction.get("score") or 0.0), 4)
    direction_total = round(sum(factors.values()), 4)
    reconciliation_error = round(direction_score - direction_total, 4)
    gross = sum(abs(v) for v in factors.values())
    dominant_share = abs(dominant_value) / gross if gross > 1e-9 else 0.0
    price_meta = dict(card.get("_price_meta") or {})
    micro = dict(card.get("_market_microstructure") or {})
    return {
        "schema": "TINO_PREDICTION_DNA_V1",
        "ticker": forecast.ticker.resolved_symbol,
        "market": forecast.ticker.market,
        "exchange": forecast.ticker.exchange,
        "asset_type": forecast.ticker.asset_type,
        "direction": str(direction.get("label") or "NEUTRAL"),
        "direction_score": direction_score,
        "gate_state": direction.get("gate_state"),
        "dominant_force": dominant_name,
        "dominant_contribution": round(dominant_value, 4),
        "dominant_share": round(dominant_share, 4),
        "direction_reconciled_total": direction_total,
        "direction_reconciliation_error": reconciliation_error,
        "direction_reconciled": abs(reconciliation_error) <= 0.01,
        "factor_contributions": factors,
        "family_contributions": families,
        "family_scores": top_contributions(dict(direction.get("family_scores") or {}), 16),
        "family_weights": top_contributions(dict(direction.get("family_weights") or {}), 16),
        "risk_contributions": risks,
        "risk_total": round(sum(max(0.0, value) for value in risks.values()), 4),
        "confidence_components": top_contributions(dict(direction.get("confidence_components") or {}), 12),
        "learning_calibration": dict(direction.get("learning_calibration") or {}),
        "data_quality": direction.get("quality"),
        "conflict": direction.get("conflict"),
        "uncertainty": direction.get("uncertainty"),
        "price_sample": {
            "source": price_meta.get("source"),
            "status": price_meta.get("status"),
            "verified": bool(price_meta.get("price_verified")),
            "limited": bool(price_meta.get("limited_price_mode")),
            "emerging_grace": bool(price_meta.get("emerging_price_grace")),
        },
        "microstructure": micro,
        "event_macro": str((forecast.radar or {}).get("事件/Macro") or ""),
        "policy_geo": str((forecast.radar or {}).get("Policy/Geo") or ""),
    }


def contribution_attribution(
    values: Dict[str, Any],
    actual_direction: str,
    *,
    limit: int = 16,
) -> Dict[str, Dict[str, Any]]:
    sign = _direction_sign(actual_direction)
    if sign == 0:
        return {}
    cleaned = top_contributions(values, limit)
    gross = sum(abs(v) for v in cleaned.values())
    out: Dict[str, Dict[str, Any]] = {}
    for name, contribution in cleaned.items():
        if abs(contribution) < 0.05:
            continue
        contribution_sign = 1 if contribution > 0 else -1
        out[name] = {
            "contribution": round(contribution, 4),
            "aligned": bool(contribution_sign == sign),
            "impact_weight": round(abs(contribution) / gross, 4) if gross > 1e-9 else 0.0,
        }
    return out


def update_attribution_learning(
    current: Dict[str, Any],
    attribution: Dict[str, Any],
    *,
    now_text: str,
) -> Dict[str, Any]:
    out = dict(current or {})
    for name, row in dict(attribution or {}).items():
        if not isinstance(row, dict) or row.get("aligned") is None:
            continue
        old = dict(out.get(name) or {})
        try:
            count = int(float(old.get("count") or 0)) + 1
            hits = int(float(old.get("hits") or 0)) + (1 if bool(row.get("aligned")) else 0)
            impact = max(0.0, min(1.0, float(row.get("impact_weight") or 0.0)))
            old_weight_sum = float(old.get("impact_weight_sum") or 0.0)
            old_score_sum = float(old.get("weighted_alignment_sum") or 0.0)
        except Exception:
            count, hits, impact, old_weight_sum, old_score_sum = 1, int(bool(row.get("aligned"))), 0.0, 0.0, 0.0
        misses = count - hits
        signed = 1.0 if bool(row.get("aligned")) else -1.0
        weight_sum = old_weight_sum + impact
        score_sum = old_score_sum + signed * impact
        bayes_hit = (hits + 2.0) / (count + 4.0)
        weighted_reliability = 0.5 + 0.5 * (score_sum / weight_sum) if weight_sum > 1e-9 else 0.5
        blended = max(0.0, min(1.0, bayes_hit * 0.65 + weighted_reliability * 0.35))
        if count < 8:
            active_multiplier = 1.0
        else:
            span = 0.08 if count < 20 else 0.15
            active_multiplier = 1.0 + max(-span, min(span, (blended - 0.5) * 0.60))
        old.update({
            "count": count,
            "hits": hits,
            "misses": misses,
            "bayes_hit_rate": round(bayes_hit, 4),
            "weighted_reliability": round(weighted_reliability, 4),
            "blended_reliability": round(blended, 4),
            "impact_weight_sum": round(weight_sum, 4),
            "weighted_alignment_sum": round(score_sum, 4),
            "active_multiplier": round(max(0.85, min(1.15, active_multiplier)), 4),
            "gate": "active" if count >= 8 else f"collecting_{count}/8",
            "updated_at_tw": now_text,
        })
        out[str(name)] = old
    return out
