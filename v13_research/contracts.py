# -*- coding: utf-8 -*-
"""Stable contracts for the V13 Research sidecar.

Phase 0 intentionally stores only immutable research seeds.  It must never
change V12 forecast fields, model weights, Decision score, or UI output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict

RESEARCH_SCHEMA_VERSION = "V13_RESEARCH_SEED_V1"


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
