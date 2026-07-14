# -*- coding: utf-8 -*-
"""Fail-safe V13 Research service entry point.

The public function never raises into app.py.  V12 formal prediction remains
available even when V13 is disabled, incomplete, or its storage is unavailable.
"""
from __future__ import annotations

import os
from time import perf_counter
from typing import Any, Dict, Mapping

from .compatibility import prediction_row_to_seed
from .genome_engine import build_genome_snapshot
from .repository import append_genome_snapshot, append_research_seed

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}


def research_enabled() -> bool:
    """V13 is on by default from RC02; set TINO_V13_RESEARCH=0 to disable."""
    raw = str(os.environ.get("TINO_V13_RESEARCH", "1") or "1").strip().lower()
    if raw in _FALSE_VALUES:
        return False
    return raw in _TRUE_VALUES or raw == ""


def capture_prediction_seed(prediction_row: Mapping[str, Any] | None) -> Dict[str, Any]:
    started = perf_counter()
    if not research_enabled():
        return {"status": "disabled", "reason": "TINO_V13_RESEARCH=0"}
    try:
        if not isinstance(prediction_row, Mapping):
            return {"status": "skipped", "reason": "formal_prediction_row_missing"}
        if bool(prediction_row.get("skipped")):
            return {
                "status": "skipped",
                "reason": str(prediction_row.get("reason") or "prediction_skipped"),
            }

        seed = prediction_row_to_seed(prediction_row)

        # Build first, then persist. A calculation failure therefore cannot
        # leave a seed without its corresponding Genome snapshot.
        # No network, DataFrame, live market fetch, or historical scan is
        # allowed in this path.
        genome = build_genome_snapshot(seed)
        seed_result = append_research_seed(seed.to_dict())
        genome_result = append_genome_snapshot(genome.to_dict())

        status = "written" if "written" in {
            str(seed_result.get("status")), str(genome_result.get("status"))
        } else "duplicate"
        return {
            "status": status,
            "schema_version": seed.schema_version,
            "genome_schema_version": genome.schema_version,
            "ticker": seed.ticker,
            "seed": seed_result,
            "genome": genome_result,
            "genome_id": genome.genome_id,
            "genome_score": genome.genome_score,
            "genome_confidence": genome.genome_confidence,
            "fingerprint": genome.fingerprint,
            "calc_ms": genome.calc_ms,
            "total_ms": round((perf_counter() - started) * 1000.0, 3),
            "research_only": True,
            "decision_influence": False,
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "reason": f"{type(exc).__name__}: {exc}",
            "total_ms": round((perf_counter() - started) * 1000.0, 3),
            "research_only": True,
            "decision_influence": False,
        }
