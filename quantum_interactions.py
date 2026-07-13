# -*- coding: utf-8 -*-
"""Bounded family weighting and interaction rules for TINO V12.

Separated from the evidence builder to keep each module small and auditable.
"""
from __future__ import annotations

import math
from typing import List, Mapping, Tuple


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _num(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def dynamic_family_multiplier(
    family: str,
    *,
    market_status: str,
    profile: str,
    fundamental_event_available: bool,
    geo_available: bool,
) -> float:
    status = str(market_status or "")
    multiplier = 1.0
    if status in {"pre_market", "after_hours", "closed_reference", "after_close"}:
        if family == "overnight":
            multiplier *= 1.35
        if family == "intraday":
            multiplier *= 0.70
    elif status in {"intraday", "close_confirm"}:
        if family == "intraday":
            multiplier *= 1.20
        if family == "overnight":
            multiplier *= 0.82
    if profile in {"memory", "semiconductor"} and family == "overnight":
        multiplier *= 1.18
    if fundamental_event_available:
        if family == "fundamental_event":
            multiplier *= 1.28
        if family == "news":
            multiplier *= 0.55
    if geo_available and family == "news":
        multiplier *= 0.65
    return multiplier


def entanglement_adjustment(scores: Mapping[str, float], market: str) -> Tuple[float, List[str]]:
    """Add bounded same-direction confirmations; never clone a single family."""
    pairs = (
        (
            ("fundamental_event", "overnight", 7.0),
            ("flow", "trend", 5.0),
            ("leverage", "intraday", 4.0),
            ("geo_policy", "overnight", 4.5),
            ("foreign_pressure", "flow", 2.5),
        )
        if str(market).upper() == "TW"
        else (
            ("fundamental_event", "overnight", 7.5),
            ("short", "trend", 3.0),
            ("geo_policy", "overnight", 4.5),
        )
    )
    total = 0.0
    reasons: List[str] = []
    for left, right, cap in pairs:
        a = _num(scores.get(left), 0.0)
        b = _num(scores.get(right), 0.0)
        if abs(a) < 10.0 or abs(b) < 10.0 or a * b <= 0:
            continue
        strength = min(abs(a), abs(b)) / 100.0
        total += math.copysign(cap * strength, a)
        reasons.append(f"{left}×{right}")
    return _clamp(total, -12.0, 12.0), reasons
