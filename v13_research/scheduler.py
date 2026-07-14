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
    get_recent_genome_snapshots,
    research_seed_exists,
)


def schedule_prediction_research(prediction_row: Mapping[str, Any]) -> Dict[str, Any]:
    started = perf_counter()
    seed = prediction_row_to_seed(prediction_row)

    # The same formal Prediction Log row can be seen again during Streamlit
    # reruns.  Stop before Genome calculation and all disk writes.
    if research_seed_exists(seed.seed_id):
        return {
            "status": "cache_hit",
            "scheduler_version": SCHEDULER_VERSION,
            "ticker": seed.ticker,
            "seed_id": seed.seed_id,
            "research_only": True,
            "decision_influence": False,
            "total_ms": round((perf_counter() - started) * 1000.0, 3),
        }

    previous = get_recent_genome_snapshots(seed.ticker, limit=2)
    genome = build_genome_snapshot(seed)
    detection = detect_genome_event(genome, previous)

    seed_result = append_research_seed(seed.to_dict())
    genome_result = append_genome_snapshot(genome.to_dict())
    detection_result = append_detection_event(detection.to_dict())

    return {
        "status": "written",
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
