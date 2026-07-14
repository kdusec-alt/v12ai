# -*- coding: utf-8 -*-
"""Fail-safe public entry point for the V13 Research platform."""
from __future__ import annotations

import os
from time import perf_counter
from typing import Any, Dict, Mapping

from .scheduler import schedule_prediction_research

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def research_enabled() -> bool:
    """V13 is enabled by default; set TINO_V13_RESEARCH=0 for emergency isolation."""
    raw = str(os.environ.get("TINO_V13_RESEARCH", "1") or "1").strip().lower()
    if raw in _FALSE_VALUES:
        return False
    return raw in _TRUE_VALUES or raw == ""


def capture_prediction_seed(prediction_row: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Consume one already-persisted formal V12 Prediction Log row.

    This function never raises into app.py and never mutates the row.
    """
    started = perf_counter()
    if not research_enabled():
        return {"status": "disabled", "reason": "TINO_V13_RESEARCH=0"}
    try:
        if not isinstance(prediction_row, Mapping):
            return {"status": "skipped", "reason": "formal_prediction_row_missing"}
        if bool(prediction_row.get("skipped")):
            return {"status": "skipped", "reason": str(prediction_row.get("reason") or "prediction_skipped")}
        return schedule_prediction_research(prediction_row)
    except Exception as exc:
        return {
            "status": "degraded",
            "reason": f"{type(exc).__name__}: {exc}",
            "total_ms": round((perf_counter() - started) * 1000.0, 3),
            "research_only": True,
            "decision_influence": False,
        }
