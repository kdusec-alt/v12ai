# -*- coding: utf-8 -*-
"""Fail-safe V13 Research service entry point.

The public function never raises into app.py.  V12 formal prediction remains
available even when V13 is disabled, incomplete, or its storage is unavailable.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Mapping

from .compatibility import prediction_row_to_seed
from .repository import append_research_seed

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


def research_enabled() -> bool:
    return str(os.environ.get("TINO_V13_RESEARCH", "0") or "0").strip().lower() in _TRUE_VALUES


def capture_prediction_seed(prediction_row: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not research_enabled():
        return {"status": "disabled", "reason": "TINO_V13_RESEARCH=0"}
    try:
        if not isinstance(prediction_row, Mapping):
            return {"status": "skipped", "reason": "formal_prediction_row_missing"}
        if bool(prediction_row.get("skipped")):
            return {"status": "skipped", "reason": str(prediction_row.get("reason") or "prediction_skipped")}
        seed = prediction_row_to_seed(prediction_row)
        result = append_research_seed(seed.to_dict())
        result["schema_version"] = seed.schema_version
        result["ticker"] = seed.ticker
        return result
    except Exception as exc:
        return {
            "status": "degraded",
            "reason": f"{type(exc).__name__}: {exc}",
        }
