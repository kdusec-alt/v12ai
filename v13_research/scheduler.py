# -*- coding: utf-8 -*-
"""V13 Research Scheduler.

One formal V12 Prediction Log row enters here exactly once.  The scheduler
skips duplicates before Genome calculation, shares one Genome object with all
research detectors, and never raises into the V12 foreground path.
"""
from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any, Dict, Mapping
from zoneinfo import ZoneInfo

from .compatibility import prediction_row_to_seed
from .contracts import SCHEDULER_VERSION
from .detection_engine import detect_genome_event
from .genome_engine import build_genome_snapshot
from .repository import (
    append_detection_event,
    append_environment_genome,
    append_genome_snapshot,
    append_research_seed,
    append_shadow_phenotype,
    append_ticker_genome,
    detection_snapshot_exists,
    genome_prediction_exists,
    get_recent_genome_snapshots,
    load_recent_macro_events,
    research_seed_exists,
)
from .shadow_genetics import build_shadow_bundle

_TAIPEI = ZoneInfo("Asia/Taipei")


def _timestamp(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_TAIPEI)
        return parsed.timestamp()
    except Exception:
        return None


def _macro_event_at_prediction(prediction_row: Mapping[str, Any]) -> Dict[str, Any]:
    """Select only macro evidence already observable at prediction time."""
    prediction_ts = _timestamp(prediction_row.get("run_time_tw"))
    if prediction_ts is None:
        return {}
    selected: tuple[float, Dict[str, Any]] | None = None
    for row in load_recent_macro_events(300):
        observed_ts = _timestamp(row.get("observed_at_tw") or row.get("release_at_tw"))
        if observed_ts is None or observed_ts > prediction_ts + 300.0:
            continue
        age_hours = (prediction_ts - observed_ts) / 3600.0
        if age_hours < -0.1 or age_hours > 72.0:
            continue
        if selected is None or observed_ts > selected[0]:
            selected = (observed_ts, row)
    return dict(selected[1]) if selected else {}


def _schedule_shadow_genetics(
    prediction_row: Mapping[str, Any],
    genome_row: Mapping[str, Any],
) -> Dict[str, Any]:
    """Write one complete shadow bundle; never changes the formal row."""
    latest_macro = _macro_event_at_prediction(prediction_row)
    environment, ticker_genome, phenotype = build_shadow_bundle(
        prediction_row,
        genome_row,
        latest_macro,
    )
    environment_result = append_environment_genome(environment.to_dict())
    ticker_result = append_ticker_genome(ticker_genome.to_dict())
    phenotype_result = append_shadow_phenotype(phenotype.to_dict())
    statuses = {
        str(environment_result.get("status") or ""),
        str(ticker_result.get("status") or ""),
        str(phenotype_result.get("status") or ""),
    }
    return {
        "status": "written" if "written" in statuses else "cache_hit",
        "environment": environment_result,
        "ticker_genome": ticker_result,
        "phenotype": phenotype_result,
        "environment_id": environment.environment_id,
        "ticker_genome_snapshot_id": ticker_genome.snapshot_id,
        "phenotype_id": phenotype.phenotype_id,
        "shadow_bias": phenotype.shadow_bias,
        "shadow_direction": phenotype.shadow_direction,
        "research_only": True,
        "decision_influence": False,
    }


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
        shadow = _schedule_shadow_genetics(prediction_row, genome.to_dict())
        return {
            "status": "shadow_repaired" if shadow.get("status") == "written" else "cache_hit",
            "scheduler_version": SCHEDULER_VERSION,
            "ticker": seed.ticker,
            "seed_id": seed.seed_id,
            "genome_id": genome.genome_id,
            "shadow_genetics": shadow,
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
    # The formal V13 Seed/Genome/Detection bundle is durable before the newer
    # shadow genetics sidecar is appended.  This preserves recovery ordering.
    shadow = _schedule_shadow_genetics(prediction_row, genome.to_dict())

    repaired = bool(seed_exists or genome_exists or detection_exists)
    return {
        "status": "repaired" if repaired else "written",
        "scheduler_version": SCHEDULER_VERSION,
        "ticker": seed.ticker,
        "seed": seed_result,
        "genome": genome_result,
        "detection": detection_result,
        "shadow_genetics": shadow,
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
