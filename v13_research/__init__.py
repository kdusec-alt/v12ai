# -*- coding: utf-8 -*-
"""TINO AI V13 Research sidecar.

RC02 adds the fast Bubble Genome engine while preserving the RC01 bootstrap
contract: research is isolated from V12 Decision and formal forecasting.
"""
from .contracts import (
    GENOME_ENGINE_VERSION,
    GENOME_SCHEMA_VERSION,
    RESEARCH_SCHEMA_VERSION,
    GenomeSnapshot,
    ResearchSeed,
)
from .genome_engine import GENE_LABELS, GENE_ORDER, build_genome_snapshot
from .service import capture_prediction_seed, research_enabled

__all__ = [
    "RESEARCH_SCHEMA_VERSION",
    "GENOME_SCHEMA_VERSION",
    "GENOME_ENGINE_VERSION",
    "ResearchSeed",
    "GenomeSnapshot",
    "GENE_ORDER",
    "GENE_LABELS",
    "build_genome_snapshot",
    "capture_prediction_seed",
    "research_enabled",
]
