# -*- coding: utf-8 -*-
"""Stable contracts for the isolated V13 Research platform.

Every record is research-only.  Nothing in this module may modify V12 forecast
fields, model weights, Direction, Decision score, confidence, or front-stage
battle-panel output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

RESEARCH_SCHEMA_VERSION = "V13_RESEARCH_SEED_V1"
GENOME_SCHEMA_VERSION = "V13_BUBBLE_GENOME_V1"
GENOME_ENGINE_VERSION = "V13_RC02_GENOME_1.0.0"
DETECTION_SCHEMA_VERSION = "V13_RESEARCH_DETECTION_V1"
DETECTION_ENGINE_VERSION = "V13_RC03_DETECTION_1.0.0"
SCHEDULER_VERSION = "V13_RC05_SCHEDULER_1.1.0"


@dataclass(frozen=True)
class ResearchSeed:
    seed_id: str
    schema_version: str
    prediction_id: str
    run_time_tw: str
    run_date_tw: str
    target_trade_date: str
    ticker: str
    market: str
    asset_type: str
    model_version: str
    data_quality: Dict[str, Any] = field(default_factory=dict)
    bubble_snapshot: Dict[str, Any] = field(default_factory=dict)
    direction_snapshot: Dict[str, Any] = field(default_factory=dict)
    truth_snapshot: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenomeSnapshot:
    snapshot_id: str
    genome_id: str
    schema_version: str
    engine_version: str
    seed_id: str
    prediction_id: str
    run_time_tw: str
    run_date_tw: str
    target_trade_date: str
    ticker: str
    market: str
    asset_type: str
    genes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fingerprint: str = ""
    genome_score: float = 0.0
    genome_confidence: float = 0.0
    coverage: float = 0.0
    dominant_genes: List[str] = field(default_factory=list)
    data_quality: Dict[str, Any] = field(default_factory=dict)
    research_only: bool = True
    decision_influence: bool = False
    calc_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DetectionEvent:
    event_id: str
    schema_version: str
    engine_version: str
    snapshot_id: str
    previous_snapshot_id: str
    ticker: str
    market: str
    asset_type: str
    run_time_tw: str
    severity: str
    mutation_level: str
    confirmed: bool
    status: str
    gene_deltas: Dict[str, float] = field(default_factory=dict)
    changed_genes: List[str] = field(default_factory=list)
    quality_flags: List[str] = field(default_factory=list)
    alerts: List[str] = field(default_factory=list)
    previous_genome_score: float | None = None
    current_genome_score: float | None = None
    score_delta: float | None = None
    research_only: bool = True
    decision_influence: bool = False
    calc_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
