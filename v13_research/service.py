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


def capture_audit_fitness(
    audit_row: Mapping[str, Any] | None,
    *,
    evaluate_gate: bool = True,
) -> Dict[str, Any]:
    """Consume an already-persisted audit as shadow-only fitness evidence."""
    if not research_enabled():
        return {"status": "disabled", "reason": "TINO_V13_RESEARCH=0"}
    try:
        if not isinstance(audit_row, Mapping):
            return {"status": "skipped", "reason": "audit_row_missing"}
        from .fitness_engine import build_shadow_fitness, evaluate_evolution_gate
        from .repository import (
            append_evolution_gate,
            append_shadow_fitness,
            get_shadow_phenotype_for_prediction,
            load_recent_shadow_fitness,
        )

        prediction_id = str(audit_row.get("prediction_id") or "")
        phenotype = get_shadow_phenotype_for_prediction(prediction_id)
        if not phenotype:
            return {
                "status": "skipped",
                "reason": "shadow_phenotype_missing",
                "prediction_id": prediction_id,
                "research_only": True,
                "decision_influence": False,
            }
        fitness = build_shadow_fitness(audit_row, phenotype)
        fitness_result = append_shadow_fitness(fitness.to_dict())
        if not evaluate_gate:
            return {
                "status": "written" if fitness_result.get("status") == "written" else "cache_hit",
                "fitness": fitness_result,
                "research_only": True,
                "decision_influence": False,
            }
        rows = load_recent_shadow_fitness(2000)
        gate = evaluate_evolution_gate(rows, evaluated_at_tw=str(audit_row.get("audit_time_tw") or ""))
        gate_result = append_evolution_gate(gate.to_dict())
        return {
            "status": "written" if fitness_result.get("status") == "written" else "cache_hit",
            "fitness": fitness_result,
            "gate": gate_result,
            "gate_status": gate.status,
            "eligible_samples": gate.eligible_samples,
            "research_only": True,
            "decision_influence": False,
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "reason": f"{type(exc).__name__}: {exc}",
            "research_only": True,
            "decision_influence": False,
        }


_RECOVERY_LOCK = threading.RLock()
_RECOVERY_REPORT: Dict[str, Any] | None = None
_FITNESS_RECOVERY_REPORT: Dict[str, Any] | None = None


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
                try:
                    result = schedule_prediction_research(row)
                    if str(result.get("status") or "") == "cache_hit":
                        report["existing"] += 1
                    else:
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


def recover_shadow_fitness_from_audit_log(limit: int = 1200) -> Dict[str, Any]:
    """Backfill fitness for historical audits after phenotype recovery."""
    global _FITNESS_RECOVERY_REPORT
    with _RECOVERY_LOCK:
        if _FITNESS_RECOVERY_REPORT is not None:
            return dict(_FITNESS_RECOVERY_REPORT)
        report: Dict[str, Any] = {
            "status": "PASS",
            "checked": 0,
            "written": 0,
            "existing": 0,
            "skipped": 0,
            "errors": 0,
            "research_only": True,
            "decision_influence": False,
        }
        try:
            from memory_store import read_audit_log
            for row in read_audit_log(max(1, min(2500, int(limit)))):
                if not isinstance(row, Mapping):
                    continue
                report["checked"] += 1
                result = capture_audit_fitness(row, evaluate_gate=False)
                status = str(result.get("status") or "")
                if status == "written":
                    report["written"] += 1
                elif status == "cache_hit":
                    report["existing"] += 1
                elif status == "degraded":
                    report["errors"] += 1
                else:
                    report["skipped"] += 1
            if report["errors"]:
                report["status"] = "WARN"
            from .fitness_engine import evaluate_evolution_gate
            from .repository import append_evolution_gate, load_recent_shadow_fitness
            rows = load_recent_shadow_fitness(2000)
            gate = evaluate_evolution_gate(
                rows,
                evaluated_at_tw=str(rows[-1].get("audit_time_tw") or "") if rows else "",
            )
            append_evolution_gate(gate.to_dict())
            report["gate_status"] = gate.status
            report["eligible_samples"] = gate.eligible_samples
        except Exception as exc:
            report["status"] = "WARN"
            report["errors"] += 1
            report["reason"] = f"{type(exc).__name__}: {exc}"
        _FITNESS_RECOVERY_REPORT = dict(report)
        return dict(report)
