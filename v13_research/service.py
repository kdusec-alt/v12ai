# -*- coding: utf-8 -*-
"""Fail-safe public entry point for the V13 Research platform."""
from __future__ import annotations

import os
import threading
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


_RECOVERY_LOCK = threading.RLock()
_RECOVERY_REPORT: Dict[str, Any] | None = None


def recover_research_history_from_prediction_log(limit: int | None = None) -> Dict[str, Any]:
    """Rebuild missing V13 sidecar rows from the canonical Prediction Log.

    Genome is deterministic and derives only from persisted V12 rows.  This
    one-time process guard repairs an empty/partial Research Lab after a
    Streamlit redeploy without fetching market data or touching V12 Decision.
    """
    global _RECOVERY_REPORT
    with _RECOVERY_LOCK:
        if _RECOVERY_REPORT is not None:
            return dict(_RECOVERY_REPORT)
        started = perf_counter()
        report: Dict[str, Any] = {
            "status": "PASS",
            "checked": 0,
            "written": 0,
            "existing": 0,
            "errors": 0,
            "research_only": True,
            "decision_influence": False,
        }
        try:
            from memory_store import PREDICTION_LOG, read_jsonl
            from .compatibility import prediction_row_to_seed
            from .detection_engine import detect_genome_event
            from .genome_engine import build_genome_snapshot
            from .repository import (
                append_detection_event,
                append_genome_snapshot,
                append_research_seed,
                genome_prediction_exists,
                get_recent_genome_snapshots,
            )

            configured = limit if limit is not None else int(os.environ.get("TINO_V13_RECOVERY_LIMIT", "600") or 600)
            bounded = max(1, min(2000, int(configured)))
            rows = read_jsonl(PREDICTION_LOG, limit=bounded)
            for row in rows:
                if not isinstance(row, Mapping) or bool(row.get("skipped")):
                    continue
                prediction_id = str(row.get("id") or "").strip()
                ticker = str(row.get("ticker") or "").strip()
                if not prediction_id or not ticker:
                    continue
                report["checked"] += 1
                if genome_prediction_exists(prediction_id):
                    report["existing"] += 1
                    continue
                try:
                    seed = prediction_row_to_seed(row)
                    previous = get_recent_genome_snapshots(seed.ticker, limit=2)
                    genome = build_genome_snapshot(seed)
                    detection = detect_genome_event(genome, previous)
                    append_research_seed(seed.to_dict())
                    genome_result = append_genome_snapshot(genome.to_dict())
                    append_detection_event(detection.to_dict())
                    if str(genome_result.get("status") or "") == "written":
                        report["written"] += 1
                except Exception:
                    report["errors"] += 1
            if report["errors"]:
                report["status"] = "WARN"
        except Exception as exc:
            report["status"] = "WARN"
            report["errors"] = int(report.get("errors") or 0) + 1
            report["reason"] = f"{type(exc).__name__}: {exc}"
        report["total_ms"] = round((perf_counter() - started) * 1000.0, 3)
        _RECOVERY_REPORT = dict(report)
        return dict(report)
