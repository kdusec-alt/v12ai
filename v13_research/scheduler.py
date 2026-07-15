# -*- coding: utf-8 -*-
"""V13 Research Scheduler.

One formal V12 Prediction Log row enters here exactly once.  The scheduler
skips duplicates before Genome calculation, shares one Genome object with all
research detectors, and never raises into the V12 foreground path.
"""
from __future__ import annotations

from time import perf_counter
from typing import Any, Dict, Mapping

from .compatibility import prediction_row_to_seed
from .contracts import SCHEDULER_VERSION
from .detection_engine import detect_genome_event
from .genome_engine import build_genome_snapshot
from .repository import (
    append_detection_event,
    append_genome_snapshot,
    append_research_seed,
    detection_snapshot_exists,
    genome_prediction_exists,
    get_recent_genome_snapshots,
    research_seed_exists,
)


def schedule_prediction_research(prediction_row: Mapping[str, Any]) -> Dict[str, Any]:
    started = perf_counter()
    seed = prediction_row_to_seed(prediction_row)

    # Build is deterministic and sub-millisecond in normal operation.  Doing it
    # before the completion check lets the scheduler repair a partial bundle
    # (seed written but Genome/Detection missing after an interrupted process).
    genome = build_genome_snapshot(seed)
    seed_exists = research_seed_exists(seed.seed_id)
    genome_exists = genome_prediction_exists(seed.prediction_id)
    detection_exists = detection_snapshot_exists(genome.snapshot_id)

    if seed_exists and genome_exists and detection_exists:
        return {
            "status": "cache_hit",
            "scheduler_version": SCHEDULER_VERSION,
            "ticker": seed.ticker,
            "seed_id": seed.seed_id,
            "genome_id": genome.genome_id,
            "research_only": True,
            "decision_influence": False,
            "total_ms": round((perf_counter() - started) * 1000.0, 3),
        }

    # During a repair the current snapshot may already be present in the recent
    # ticker tail.  Exclude it so mutation is compared with the true prior two.
    previous = [
        row for row in get_recent_genome_snapshots(seed.ticker, limit=3)
        if str(row.get("snapshot_id") or "") != genome.snapshot_id
    ][-2:]
    detection = detect_genome_event(genome, previous)

    seed_result = (
        {"status": "duplicate", "seed_id": seed.seed_id}
        if seed_exists else append_research_seed(seed.to_dict())
    )
    genome_result = (
        {"status": "duplicate", "snapshot_id": genome.snapshot_id}
        if genome_exists else append_genome_snapshot(genome.to_dict())
    )
    detection_result = (
        {"status": "duplicate", "event_id": detection.event_id}
        if detection_exists else append_detection_event(detection.to_dict())
    )

    repaired = bool(seed_exists or genome_exists or detection_exists)
    return {
        "status": "repaired" if repaired else "written",
        "scheduler_version": SCHEDULER_VERSION,
        "ticker": seed.ticker,
        "seed": seed_result,
        "genome": genome_result,
        "detection": detection_result,
        "genome_id": genome.genome_id,
        "genome_score": genome.genome_score,
        "genome_confidence": genome.genome_confidence,
        "coverage": genome.coverage,
        "fingerprint": genome.fingerprint,
        "mutation_level": detection.mutation_level,
        "mutation_status": detection.status,
        "mutation_confirmed": detection.confirmed,
        "quality_flags": detection.quality_flags,
        "genome_calc_ms": genome.calc_ms,
        "detection_calc_ms": detection.calc_ms,
        "total_ms": round((perf_counter() - started) * 1000.0, 3),
        "research_only": True,
        "decision_influence": False,
    }

