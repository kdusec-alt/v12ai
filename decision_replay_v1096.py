# -*- coding: utf-8 -*-
"""Read-only V1096 replay and trade-quality aggregation."""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Dict, Iterable, Mapping


def _num(value: Any):
    try:
        return float(value) if value is not None else None
    except Exception:
        return None


def summarize_trade_decision_replay(
    predictions: Iterable[Mapping[str, Any]], audits: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Compare formal public actions without rewarding a higher BUY count."""
    prediction_rows = [dict(row) for row in predictions if isinstance(row, Mapping)]
    audit_rows = [dict(row) for row in audits if isinstance(row, Mapping)]
    actions = Counter(str(row.get("public_action") or "UNKNOWN") for row in prediction_rows)
    blockers = Counter(str(row.get("public_blocking_stage") or "NONE") for row in prediction_rows)
    by_action = defaultdict(list)
    stopped = Counter()
    missed = Counter()
    for row in audit_rows:
        trade = row.get("public_trade_audit") if isinstance(row.get("public_trade_audit"), Mapping) else {}
        action = str(trade.get("public_action") or row.get("public_action") or "UNKNOWN")
        quality = _num(trade.get("risk_adjusted_quality"))
        if quality is not None:
            by_action[action].append(quality)
        if trade.get("stop_touched") is True:
            stopped[action] += 1
        if trade.get("missed_rebound") is True:
            missed[action] += 1
    quality = {
        action: {
            "samples": len(values),
            "mean_risk_adjusted_quality": round(mean(values), 4) if values else None,
            "stop_count": stopped[action],
            "missed_rebound_count": missed[action],
        }
        for action, values in sorted(by_action.items())
    }
    return {
        "schema": "TINO_DECISION_REPLAY_V1096",
        "prediction_samples": len(prediction_rows),
        "audit_samples": len(audit_rows),
        "action_distribution": dict(actions),
        "blocking_funnel": dict(blockers),
        "quality_by_action": quality,
        "success_objective": "risk_adjusted_quality_not_buy_frequency",
        "formal_weights_changed": False,
    }


def audit_trade_horizons(
    prediction: Mapping[str, Any], session_snapshots: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Calculate public BUY quality at T+1/T+3/T+5 from ordered sessions."""
    action = str(prediction.get("public_action") or "")
    entry = _num(prediction.get("public_entry_price")) or 0.0
    invalid = _num(prediction.get("public_invalidation_price")) or 0.0
    sessions = [dict(row) for row in session_snapshots if isinstance(row, Mapping)][:5]
    horizons = {}
    for horizon in (1, 3, 5):
        used = sessions[:horizon]
        if action != "BUY" or entry <= 0 or len(used) < horizon:
            horizons[f"t_plus_{horizon}"] = {"available": False}
            continue
        closes = [_num(row.get("actual_close") or row.get("close")) for row in used]
        highs = [_num(row.get("actual_high") or row.get("high")) for row in used]
        lows = [_num(row.get("actual_low") or row.get("low")) for row in used]
        close = closes[-1]
        valid_highs = [value for value in highs if value is not None]
        valid_lows = [value for value in lows if value is not None]
        max_high = max(valid_highs) if valid_highs else None
        min_low = min(valid_lows) if valid_lows else None
        horizons[f"t_plus_{horizon}"] = {
            "available": close is not None,
            "return_pct": round((close - entry) / entry * 100.0, 4) if close is not None else None,
            "mfe_pct": round((max_high - entry) / entry * 100.0, 4) if max_high is not None else None,
            "mae_pct": round((entry - min_low) / entry * 100.0, 4) if min_low is not None else None,
            "stop_touched": bool(invalid > 0 and min_low is not None and min_low < invalid),
        }
    return {
        "schema": "TINO_PUBLIC_TRADE_HORIZON_AUDIT_V1096",
        "public_action": action or None,
        "entry_price": entry or None,
        "horizons": horizons,
        "formal_weights_changed": False,
    }


def replay_from_memory(limit: int = 2000) -> Dict[str, Any]:
    from memory_store import read_audit_log, read_prediction_log
    return summarize_trade_decision_replay(
        read_prediction_log(limit), read_audit_log(limit)
    )
