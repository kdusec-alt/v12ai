# -*- coding: utf-8 -*-
"""Fast V13 Genome mutation and quality detector.

The detector is deterministic and O(number_of_genes).  It performs no network
I/O, no pandas work, and never writes into V12 objects.
"""
from __future__ import annotations

import hashlib
import json
import math
from time import perf_counter
from typing import Any, Dict, Iterable, Mapping, Sequence

from .contracts import (
    DETECTION_ENGINE_VERSION,
    DETECTION_SCHEMA_VERSION,
    DetectionEvent,
    GenomeSnapshot,
)
from .genome_engine import GENE_ORDER

_MINOR_DELTA = 15.0
_MAJOR_DELTA = 25.0
_STRUCTURAL_DELTA = 40.0


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _snapshot_dict(value: GenomeSnapshot | Mapping[str, Any] | None) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, GenomeSnapshot):
        return value.to_dict()
    return dict(value) if isinstance(value, Mapping) else {}


def _stable_event_id(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "DE-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18].upper()


def _gene_score(snapshot: Mapping[str, Any], name: str) -> float | None:
    genes = _mapping(snapshot.get("genes"))
    gene = _mapping(genes.get(name))
    return _finite(gene.get("score"))


def _quality_flags(current: Mapping[str, Any], previous: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    coverage = _finite(current.get("coverage"))
    confidence = _finite(current.get("genome_confidence"))
    if coverage is None or coverage < 0.50:
        flags.append("low_coverage")
    if confidence is None or confidence < 0.45:
        flags.append("low_confidence")

    if previous:
        prev_coverage = _finite(previous.get("coverage"))
        if coverage is not None and prev_coverage is not None and prev_coverage - coverage >= 0.25:
            flags.append("coverage_drop")
        if str(previous.get("market") or "") != str(current.get("market") or ""):
            flags.append("market_identity_changed")
        if str(previous.get("asset_type") or "") != str(current.get("asset_type") or ""):
            flags.append("asset_type_changed")
        if str(current.get("run_time_tw") or "") < str(previous.get("run_time_tw") or ""):
            flags.append("time_order_reversed")

        missing_now = 0
        observed_before = 0
        for name in GENE_ORDER:
            old = _gene_score(previous, name)
            new = _gene_score(current, name)
            if old is not None:
                observed_before += 1
                if new is None:
                    missing_now += 1
        if observed_before >= 4 and missing_now >= 2:
            flags.append("multiple_genes_missing")
    return flags


def _is_persistent(
    current_delta: float,
    name: str,
    previous: Mapping[str, Any],
    previous_previous: Mapping[str, Any],
) -> bool:
    if not previous or not previous_previous:
        return False
    previous_score = _gene_score(previous, name)
    older_score = _gene_score(previous_previous, name)
    if previous_score is None or older_score is None:
        return False
    prior_delta = previous_score - older_score
    if current_delta == 0.0 or prior_delta == 0.0:
        return False
    same_direction = (current_delta > 0.0 and prior_delta > 0.0) or (current_delta < 0.0 and prior_delta < 0.0)
    return bool(same_direction and abs(prior_delta) >= max(5.0, abs(current_delta) * 0.35))


def detect_genome_event(
    current: GenomeSnapshot | Mapping[str, Any],
    history: Sequence[GenomeSnapshot | Mapping[str, Any]] | None = None,
) -> DetectionEvent:
    """Compare one Genome snapshot with at most two previous snapshots."""
    started = perf_counter()
    current_row = _snapshot_dict(current)
    history_rows = [_snapshot_dict(item) for item in (history or []) if _snapshot_dict(item)]
    previous = history_rows[-1] if history_rows else {}
    previous_previous = history_rows[-2] if len(history_rows) >= 2 else {}

    deltas: Dict[str, float] = {}
    changed_genes: list[str] = []
    persistent_genes: list[str] = []
    if previous:
        for name in GENE_ORDER:
            new = _gene_score(current_row, name)
            old = _gene_score(previous, name)
            if new is None or old is None:
                continue
            delta = round(new - old, 2)
            deltas[name] = delta
            if abs(delta) >= _MINOR_DELTA:
                changed_genes.append(name)
                if _is_persistent(delta, name, previous, previous_previous):
                    persistent_genes.append(name)

    absolute = sorted((abs(value) for value in deltas.values()), reverse=True)
    max_delta = absolute[0] if absolute else 0.0
    major_count = sum(1 for value in deltas.values() if abs(value) >= _MAJOR_DELTA)
    moderate_count = sum(1 for value in deltas.values() if abs(value) >= 18.0)

    mutation_level = "baseline" if not previous else "stable"
    severity = "info"
    if previous:
        if max_delta >= _STRUCTURAL_DELTA or major_count >= 3:
            mutation_level, severity = "structural", "critical"
        elif max_delta >= _MAJOR_DELTA or moderate_count >= 2:
            mutation_level, severity = "major", "high"
        elif max_delta >= _MINOR_DELTA:
            mutation_level, severity = "minor", "medium"

    quality_flags = _quality_flags(current_row, previous)
    confirmed = bool(
        mutation_level == "baseline"
        or (mutation_level == "structural" and major_count >= 2)
        or persistent_genes
    )
    if quality_flags and mutation_level == "stable":
        severity = "data_quality"

    if mutation_level == "baseline":
        status = "baseline"
    elif quality_flags and mutation_level == "stable":
        status = "degraded"
    elif mutation_level == "stable":
        status = "stable"
    elif confirmed:
        status = "confirmed"
    else:
        status = "watch"

    alerts: list[str] = []
    if changed_genes:
        alerts.append("gene_shift:" + ",".join(changed_genes[:5]))
    if persistent_genes:
        alerts.append("persistent_shift:" + ",".join(persistent_genes[:5]))
    alerts.extend("quality:" + flag for flag in quality_flags[:5])

    previous_score = _finite(previous.get("genome_score")) if previous else None
    current_score = _finite(current_row.get("genome_score"))
    score_delta = None
    if previous_score is not None and current_score is not None:
        score_delta = round(current_score - previous_score, 2)

    event_id = _stable_event_id({
        "schema": DETECTION_SCHEMA_VERSION,
        "snapshot_id": str(current_row.get("snapshot_id") or ""),
        "previous_snapshot_id": str(previous.get("snapshot_id") or ""),
    })
    return DetectionEvent(
        event_id=event_id,
        schema_version=DETECTION_SCHEMA_VERSION,
        engine_version=DETECTION_ENGINE_VERSION,
        snapshot_id=str(current_row.get("snapshot_id") or ""),
        previous_snapshot_id=str(previous.get("snapshot_id") or ""),
        ticker=str(current_row.get("ticker") or "").upper(),
        market=str(current_row.get("market") or "").upper(),
        asset_type=str(current_row.get("asset_type") or "").lower(),
        run_time_tw=str(current_row.get("run_time_tw") or ""),
        severity=severity,
        mutation_level=mutation_level,
        confirmed=confirmed,
        status=status,
        gene_deltas=deltas,
        changed_genes=changed_genes,
        quality_flags=quality_flags,
        alerts=alerts,
        previous_genome_score=previous_score,
        current_genome_score=current_score,
        score_delta=score_delta,
        research_only=True,
        decision_influence=False,
        calc_ms=round((perf_counter() - started) * 1000.0, 3),
    )
