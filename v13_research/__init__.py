# -*- coding: utf-8 -*-
"""TINO AI V13 Research platform.

RC03 adds the isolated AI Research Lab, Scheduler, mutation detection, quality
guards, and bounded research storage while preserving the V12 Decision kernel.
"""
from .contracts import (
    DETECTION_ENGINE_VERSION,
    DETECTION_SCHEMA_VERSION,
    GENOME_ENGINE_VERSION,
    GENOME_SCHEMA_VERSION,
    RESEARCH_SCHEMA_VERSION,
    SCHEDULER_VERSION,
    DetectionEvent,
    GenomeSnapshot,
    ResearchSeed,
)
from .detection_engine import detect_genome_event
from .genome_engine import GENE_LABELS, GENE_ORDER, build_genome_snapshot
from .service import capture_prediction_seed, research_enabled
from .macro_event_engine import (
    MACRO_EVENT_ENGINE_VERSION,
    MACRO_EVENT_SCHEMA_VERSION,
    assess_macro_event_result,
    compact_macro_event_line,
)

__all__ = [
    "RESEARCH_SCHEMA_VERSION",
    "GENOME_SCHEMA_VERSION",
    "GENOME_ENGINE_VERSION",
    "DETECTION_SCHEMA_VERSION",
    "DETECTION_ENGINE_VERSION",
    "SCHEDULER_VERSION",
    "ResearchSeed",
    "GenomeSnapshot",
    "DetectionEvent",
    "GENE_ORDER",
    "GENE_LABELS",
    "build_genome_snapshot",
    "detect_genome_event",
    "capture_prediction_seed",
    "research_enabled",
    "MACRO_EVENT_SCHEMA_VERSION",
    "MACRO_EVENT_ENGINE_VERSION",
    "assess_macro_event_result",
    "compact_macro_event_line",
]
