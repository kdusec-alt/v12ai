# -*- coding: utf-8 -*-
"""Immediate, read-only trust response to the latest official forecast audit.

Long-term learning can update model weights later.  The front-stage decision
still needs an immediate safety response when yesterday's forecast missed
badly.  This module reads existing official audit rows and returns bounded
confidence/maturity controls; it never writes memory or alters forecast prices.
"""
from __future__ import annotations

from datetime import date
import math
from typing import Any, Dict

from memory_store import read_audit_log


def _num(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def _ticker(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _neutral() -> Dict[str, Any]:
    return {
        "accepted": False,
        "severity": "none",
        "confidence_cut": 0.0,
        "maturity_cap": 100,
        "entry_block": False,
        "range_width_multiplier": 1.0,
        "reason": "尚無最近一筆正式昨測今收可供短期校準",
        "readonly": True,
    }


def assess_prediction_trust(
    ticker: str,
    market: str,
    current_trade_date: str = "",
    *,
    limit: int = 1600,
) -> Dict[str, Any]:
    """Assess the most recent valid ``target=next`` audit for one ticker."""
    target_ticker = _ticker(ticker)
    current = _day(current_trade_date)
    candidates: list[Dict[str, Any]] = []
    try:
        rows = read_audit_log(limit)
    except Exception:
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _ticker(row.get("ticker")) != target_ticker:
            continue
        if str(row.get("market") or market or "").upper() != str(market or "").upper():
            continue
        if str(row.get("target") or "next").lower() != "next":
            continue
        if row.get("actual_valid") is not True:
            continue
        predicted = _num(row.get("predicted_close"))
        actual = _num(row.get("actual_close"))
        target_date = _day(row.get("target_trade_date"))
        if predicted is None or actual is None or predicted <= 0 or actual <= 0 or target_date is None:
            continue
        if current is not None:
            age = (current - target_date).days
            if age < 0 or age > 8:
                continue
        candidates.append(row)
    if not candidates:
        return _neutral()

    row = max(
        candidates,
        key=lambda item: (
            str(item.get("target_trade_date") or ""),
            str(item.get("audit_time_tw") or item.get("audit_date_tw") or ""),
        ),
    )
    predicted = float(row["predicted_close"])
    actual = float(row["actual_close"])
    error_pct = _num(row.get("error_pct"))
    if error_pct is None:
        error_pct = (actual / predicted - 1.0) * 100.0
    abs_error = abs(error_pct)

    predicted_low = _num(row.get("predicted_low"))
    predicted_high = _num(row.get("predicted_high"))
    half_band_pct = 0.0
    if predicted_low and predicted_high and predicted_high > predicted_low:
        half_band_pct = (predicted_high - predicted_low) / (2.0 * predicted) * 100.0
    floor = 4.0 if str(market or "").upper() == "TW" else 5.0
    tolerance = max(floor, half_band_pct, 0.01)
    normalized_miss = abs_error / tolerance
    direction_hit = row.get("direction_hit")

    if abs_error >= 8.0 or normalized_miss >= 1.8:
        severity, cut, cap, block, width = "severe", 14.0, 39, True, 1.60
    elif abs_error >= 5.0 or normalized_miss >= 1.2:
        severity, cut, cap, block, width = "high", 9.0, 49, direction_hit is False, 1.35
    elif abs_error >= 3.0 or normalized_miss >= 0.8:
        severity, cut, cap, block, width = "moderate", 5.0, 59, False, 1.18
    else:
        severity, cut, cap, block, width = "normal", 0.0, 100, False, 1.0

    direction_note = "且方向判錯" if direction_hit is False else ""
    reason = (
        f"最近正式昨測誤差 {error_pct:+.2f}%{direction_note}；"
        f"相對容許帶 {tolerance:.2f}% 為 {normalized_miss:.2f} 倍"
    )
    return {
        "accepted": True,
        "severity": severity,
        "confidence_cut": cut,
        "maturity_cap": cap,
        "entry_block": block,
        "range_width_multiplier": width,
        "reason": reason,
        "error_pct": round(error_pct, 4),
        "absolute_error_pct": round(abs_error, 4),
        "tolerance_pct": round(tolerance, 4),
        "normalized_miss": round(normalized_miss, 4),
        "direction_hit": direction_hit,
        "target_trade_date": str(row.get("target_trade_date") or "")[:10],
        "prediction_id": str(row.get("prediction_id") or ""),
        "audit_id": str(row.get("audit_id") or ""),
        "readonly": True,
    }
