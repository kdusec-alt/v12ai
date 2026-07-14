# -*- coding: utf-8 -*-
"""Compatibility adapter from a persisted V12 Prediction Log row to V13.

The adapter reads the already-written formal snapshot.  It never reads live
market data and never mutates the forecast object or the V12 log row.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Mapping

from .contracts import RESEARCH_SCHEMA_VERSION, ResearchSeed


def _plain_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _stable_seed_id(row: Mapping[str, Any]) -> str:
    prediction_id = str(row.get("id") or "").strip()
    base = {
        "schema": RESEARCH_SCHEMA_VERSION,
        "prediction_id": prediction_id,
        "ticker": str(row.get("ticker") or "").strip().upper(),
        "run_time_tw": str(row.get("run_time_tw") or ""),
        "target_trade_date": str(row.get("target_trade_date") or ""),
    }
    payload = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def prediction_row_to_seed(row: Mapping[str, Any]) -> ResearchSeed:
    if not isinstance(row, Mapping):
        raise TypeError("prediction row must be a mapping")

    prediction_id = str(row.get("id") or "").strip()
    ticker = str(row.get("ticker") or "").strip().upper()
    if not prediction_id or not ticker:
        raise ValueError("formal prediction id and ticker are required")

    bubble = _plain_mapping(row.get("bubble_radar"))
    metrics = _plain_mapping(bubble.get("metrics"))
    bubble_snapshot = {
        "accepted": bool(bubble.get("accepted")),
        "bubble_conclusion_eligible": bool(
            bubble.get("bubble_conclusion_eligible", bubble.get("accepted", False))
        ),
        "research_only": True,
        "decision_influence": False,
        "temperature": _finite_or_none(bubble.get("temperature", bubble.get("score"))),
        "score": _finite_or_none(bubble.get("score")),
        "level": str(bubble.get("level") or ""),
        "quality": _finite_or_none(bubble.get("quality")),
        "reason": str(bubble.get("reason") or ""),
        "metrics": metrics,
    }

    direction_snapshot = {
        "label": str(row.get("predicted_direction") or ""),
        "score": _finite_or_none(row.get("direction_score")),
        "confidence": _finite_or_none(row.get("direction_confidence")),
        "quality": _finite_or_none(row.get("direction_quality")),
        "conflict": _finite_or_none(row.get("direction_conflict")),
        "regime": str(row.get("direction_regime") or ""),
        "family_scores": _plain_mapping(row.get("direction_family_scores")),
        "family_contributions": _plain_mapping(row.get("direction_family_contributions")),
        "factor_contributions": _plain_mapping(row.get("direction_factor_contributions")),
        "risk_contributions": _plain_mapping(row.get("direction_risk_contributions")),
    }

    truths = row.get("truths") if isinstance(row.get("truths"), list) else []
    accepted_truths = sum(1 for item in truths if isinstance(item, Mapping) and item.get("accepted"))
    fallback_truths = sum(1 for item in truths if isinstance(item, Mapping) and item.get("fallback"))
    truth_snapshot = {
        "count": len(truths),
        "accepted_count": accepted_truths,
        "fallback_count": fallback_truths,
        "sources": [
            str(item.get("source") or "")
            for item in truths
            if isinstance(item, Mapping) and item.get("source")
        ][:20],
    }

    data_quality = {
        "valid_price_sample": bool(row.get("valid_price_sample", False)),
        "price_sample_quality": str(row.get("price_sample_quality") or ""),
        "price_source": str(row.get("price_source") or ""),
        "price_status": str(row.get("price_status") or ""),
        "live_data": bool(row.get("live_data", False)),
    }

    return ResearchSeed(
        seed_id=_stable_seed_id(row),
        schema_version=RESEARCH_SCHEMA_VERSION,
        prediction_id=prediction_id,
        run_time_tw=str(row.get("run_time_tw") or ""),
        run_date_tw=str(row.get("run_date_tw") or ""),
        target_trade_date=str(row.get("target_trade_date") or ""),
        ticker=ticker,
        market=str(row.get("market") or "").upper(),
        asset_type=str(row.get("asset_type") or "").lower(),
        model_version=str(row.get("model_version") or ""),
        data_quality=data_quality,
        bubble_snapshot=bubble_snapshot,
        direction_snapshot=direction_snapshot,
        truth_snapshot=truth_snapshot,
    )
