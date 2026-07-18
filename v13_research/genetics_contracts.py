# -*- coding: utf-8 -*-
"""Versioned contracts for the V13 shadow genetics sidecar.

These records are intentionally unable to influence V12.  They describe a
shared market environment, a ticker-specific sensitivity genome, the resulting
shadow phenotype, and post-close fitness evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

ENVIRONMENT_SCHEMA_VERSION = "V13_ENVIRONMENT_GENOME_V1"
TICKER_GENOME_SCHEMA_VERSION = "V13_TICKER_GENOME_V1"
PHENOTYPE_SCHEMA_VERSION = "V13_SHADOW_PHENOTYPE_V1"
FITNESS_SCHEMA_VERSION = "V13_SHADOW_FITNESS_V1"
EVOLUTION_SCHEMA_VERSION = "V13_EVOLUTION_GATE_V1"
SHADOW_GENETICS_ENGINE_VERSION = "V13_RC05_SHADOW_GENETICS_1.0.0"


@dataclass(frozen=True)
class EnvironmentGenome:
    environment_id: str
    schema_version: str
    engine_version: str
    bucket_tw: str
    observed_at_tw: str
    genes: Dict[str, float] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0
    source_event_id: str = ""
    research_only: bool = True
    decision_influence: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TickerGenomeSnapshot:
    snapshot_id: str
    lineage_id: str
    schema_version: str
    engine_version: str
    prediction_id: str
    run_time_tw: str
    ticker: str
    market: str
    asset_type: str
    sensitivities: Dict[str, float] = field(default_factory=dict)
    traits: Dict[str, Any] = field(default_factory=dict)
    source_genome_id: str = ""
    confidence: float = 0.0
    research_only: bool = True
    decision_influence: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowPhenotype:
    phenotype_id: str
    schema_version: str
    engine_version: str
    prediction_id: str
    run_time_tw: str
    target_trade_date: str
    ticker: str
    market: str
    environment_id: str
    ticker_genome_snapshot_id: str
    official_direction: str
    official_direction_score: float
    selected_candidate_id: str
    shadow_bias: float
    shadow_adjusted_score: float
    shadow_direction: str
    gene_contributions: Dict[str, float] = field(default_factory=dict)
    candidate_outcomes: List[Dict[str, Any]] = field(default_factory=list)
    explanation: List[str] = field(default_factory=list)
    research_only: bool = True
    decision_influence: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ShadowFitness:
    fitness_id: str
    schema_version: str
    engine_version: str
    audit_id: str
    prediction_id: str
    audit_time_tw: str
    target_trade_date: str
    ticker: str
    market: str
    actual_direction: str
    phenotype_id: str
    environment_id: str
    baseline_hit: bool
    candidate_results: List[Dict[str, Any]] = field(default_factory=list)
    sample_eligible: bool = False
    exclusion_reasons: List[str] = field(default_factory=list)
    research_only: bool = True
    decision_influence: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvolutionGateStatus:
    status_id: str
    schema_version: str
    engine_version: str
    evaluated_at_tw: str
    status: str
    champion_id: str
    leading_challenger_id: str
    eligible_samples: int
    ticker_count: int
    market_count: int
    trade_day_count: int
    baseline_hit_rate: float
    challenger_hit_rate: float
    uplift: float
    requirements: Dict[str, Any] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)
    auto_promote: bool = False
    requires_manual_review: bool = True
    research_only: bool = True
    decision_influence: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
