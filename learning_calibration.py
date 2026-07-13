# -*- coding: utf-8 -*-
"""Lightweight bounded calibration bridge for TINO RC4.5.

The module intentionally has no Streamlit/Pandas/network dependency.  It reads
only the compact ticker profile JSON and converts audited family reliability
into a very small direction correction.  Core model weights are never rewritten
at runtime; calibration is gated by sample count and strictly capped.
"""
from __future__ import annotations

from pathlib import Path
import math
from typing import Any, Dict, Mapping

from memory_store import TICKER_PROFILE, load_profiles

_CACHE_MTIME_NS: int | None = None
_CACHE_PROFILES: Dict[str, Any] = {}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _profiles_cached() -> Dict[str, Any]:
    """Reload only when ticker_profiles.json changes."""
    global _CACHE_MTIME_NS, _CACHE_PROFILES
    try:
        path = Path(TICKER_PROFILE)
        mtime_ns = path.stat().st_mtime_ns if path.exists() else -1
    except Exception:
        mtime_ns = -1
    if _CACHE_MTIME_NS == mtime_ns:
        return _CACHE_PROFILES
    try:
        loaded = load_profiles()
        _CACHE_PROFILES = loaded if isinstance(loaded, dict) else {}
    except Exception:
        _CACHE_PROFILES = {}
    _CACHE_MTIME_NS = mtime_ns
    return _CACHE_PROFILES


def bounded_learning_calibration(
    ticker: str,
    family_contributions: Mapping[str, float] | None,
) -> Dict[str, Any]:
    """Return a bounded out-of-sample calibration delta.

    Safety contract:
    - no effect before 8 verified T1 direction audits;
    - each family multiplier is constrained by learning.py;
    - total direction adjustment is capped at +/-6 points and no more than 22%
      of current visible evidence magnitude;
    - confidence adjustment is capped at +/-2 points.
    """
    key = str(ticker or "").strip().upper()
    profile = (_profiles_cached().get(key) or {}) if key else {}
    family_learning = profile.get("family_learning") if isinstance(profile, dict) else {}
    if not isinstance(family_learning, dict):
        family_learning = {}

    direction_count = int(_num(profile.get("direction_audit_count"), 0.0)) if isinstance(profile, dict) else 0
    hit_rate = _num(profile.get("direction_hit_rate"), 0.5) if isinstance(profile, dict) else 0.5
    maturity = _clamp(direction_count / 40.0, 0.0, 1.0)
    contributions = {
        str(name): _num(value)
        for name, value in dict(family_contributions or {}).items()
        if math.isfinite(_num(value))
    }

    applied: Dict[str, Dict[str, float]] = {}
    raw_delta = 0.0
    for family, contribution in contributions.items():
        row = family_learning.get(family)
        if not isinstance(row, dict):
            continue
        count = int(_num(row.get("count"), 0.0))
        if count < 8:
            continue
        multiplier = _clamp(_num(row.get("active_multiplier"), 1.0), 0.85, 1.15)
        delta = contribution * (multiplier - 1.0)
        if abs(delta) < 0.005:
            continue
        raw_delta += delta
        applied[family] = {
            "count": float(count),
            "hit_rate": round(_num(row.get("bayes_hit_rate"), 0.5), 4),
            "multiplier": round(multiplier, 4),
            "delta": round(delta, 4),
        }

    evidence_magnitude = sum(abs(v) for v in contributions.values())
    dynamic_cap = min(6.0, max(0.0, evidence_magnitude * 0.22))
    delta = _clamp(raw_delta, -dynamic_cap, dynamic_cap) if dynamic_cap > 0 else 0.0

    confidence_delta = 0.0
    if direction_count >= 8:
        confidence_delta = _clamp((hit_rate - 0.50) * 6.0 * max(maturity, 0.20), -2.0, 2.0)

    return {
        "eligible": bool(applied),
        "ticker": key,
        "direction_audit_count": direction_count,
        "direction_hit_rate": round(hit_rate, 4) if direction_count else None,
        "maturity": round(maturity, 4),
        "raw_delta": round(raw_delta, 4),
        "delta": round(delta, 4),
        "confidence_delta": round(confidence_delta, 4),
        "applied_families": applied,
        "gate": "verified_t1>=8_bounded" if applied else "collecting_verified_t1",
    }
