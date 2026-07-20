# -*- coding: utf-8 -*-
"""Deterministic, network-free V13 shadow genetics engine.

The engine models one shared environment and lets different ticker genomes
express different phenotypes.  It produces counterfactual research scores
only; the persisted V12 prediction remains the champion and is never mutated.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Dict, Mapping, Sequence, Tuple

from .genetics_contracts import (
    ENVIRONMENT_SCHEMA_VERSION,
    PHENOTYPE_SCHEMA_VERSION,
    SHADOW_GENETICS_ENGINE_VERSION,
    TICKER_GENOME_SCHEMA_VERSION,
    EnvironmentGenome,
    ShadowPhenotype,
    TickerGenomeSnapshot,
)

ENVIRONMENT_GENE_ORDER: Tuple[str, ...] = (
    "geopolitical_stability",
    "energy_affordability",
    "liquidity",
    "inflation_relief",
    "ai_cycle",
    "risk_appetite",
)

ENVIRONMENT_GENE_LABELS = {
    "geopolitical_stability": "地緣穩定",
    "energy_affordability": "能源可負擔性",
    "liquidity": "流動性",
    "inflation_relief": "通膨降溫",
    "ai_cycle": "AI 景氣循環",
    "risk_appetite": "風險偏好",
}

OFFICIAL_CHAMPION_ID = "V12_OFFICIAL_BASELINE"
SELECTED_CHALLENGER_ID = "V13_G1_BALANCED"

# Each candidate is a small, versioned policy genome.  G1 candidates are
# deterministic children of the neutral V12 baseline and cannot self-promote.
POLICY_POPULATION: Tuple[Dict[str, Any], ...] = (
    {
        "candidate_id": OFFICIAL_CHAMPION_ID,
        "generation": 0,
        "lineage": "V12",
        "mutation": "none",
        "weights": {name: 0.0 for name in ENVIRONMENT_GENE_ORDER},
    },
    {
        "candidate_id": SELECTED_CHALLENGER_ID,
        "generation": 1,
        "lineage": "V12×ENV",
        "mutation": "balanced_overlay",
        "weights": {
            "geopolitical_stability": 0.95,
            "energy_affordability": 0.65,
            "liquidity": 1.00,
            "inflation_relief": 0.80,
            "ai_cycle": 0.85,
            "risk_appetite": 1.00,
        },
    },
    {
        "candidate_id": "V13_G1_DEFENSIVE",
        "generation": 1,
        "lineage": "V12×ENV",
        "mutation": "risk_gene_amplification",
        "weights": {
            "geopolitical_stability": 1.20,
            "energy_affordability": 0.80,
            "liquidity": 1.05,
            "inflation_relief": 0.90,
            "ai_cycle": 0.45,
            "risk_appetite": 1.25,
        },
    },
    {
        "candidate_id": "V13_G1_THEME",
        "generation": 1,
        "lineage": "V12×ENV",
        "mutation": "theme_gene_amplification",
        "weights": {
            "geopolitical_stability": 0.65,
            "energy_affordability": 0.75,
            "liquidity": 0.90,
            "inflation_relief": 0.65,
            "ai_cycle": 1.30,
            "risk_appetite": 0.90,
        },
    },
)


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _stable_id(prefix: str, payload: Mapping[str, Any], length: int = 18) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length].upper()


def _time_bucket(value: Any, minutes: int = 30) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        bucket_minute = (parsed.minute // minutes) * minutes
        return parsed.replace(minute=bucket_minute, second=0, microsecond=0).isoformat()
    except Exception:
        match = re.search(r"(\d{4}-\d{2}-\d{2})[^0-9]+(\d{2}):(\d{2})", text)
        if match:
            minute = (int(match.group(3)) // minutes) * minutes
            return f"{match.group(1)}T{match.group(2)}:{minute:02d}:00"
    return text[:16] or "unknown"


def _contains(text: str, words: Sequence[str]) -> bool:
    return any(word in text for word in words)


def _environment_vector(text: str, macro_event: Mapping[str, Any]) -> Tuple[Dict[str, float], list[str]]:
    genes = {name: 0.0 for name in ENVIRONMENT_GENE_ORDER}
    evidence: list[str] = []
    lower = text.lower()

    if _contains(lower, ("戰爭", "攻擊", "開戰", "伊朗", "中東", "war", "missile", "invasion", "conflict")):
        genes["geopolitical_stability"] -= 0.85
        genes["risk_appetite"] -= 0.45
        evidence.append("geopolitical_shock")
    if _contains(lower, ("和平", "停火", "ceasefire", "peace", "de-escal")):
        genes["geopolitical_stability"] += 0.70
        genes["risk_appetite"] += 0.25
        evidence.append("geopolitical_relief")
    if _contains(lower, ("石油", "原油", "油價", "oil", "opec", "能源危機")):
        shock = _contains(lower, ("暴漲", "飆升", "斷供", "制裁", "surge", "spike", "embargo"))
        genes["energy_affordability"] -= 0.75 if shock else 0.40
        evidence.append("energy_shock")
    if _contains(lower, ("降息", "寬鬆", "dovish", "rate cut", "liquidity injection")):
        genes["liquidity"] += 0.70
        genes["risk_appetite"] += 0.30
        evidence.append("liquidity_easing")
    if _contains(lower, ("升息", "緊縮", "hawkish", "rate hike", "殖利率上升", "yield spike")):
        genes["liquidity"] -= 0.75
        genes["risk_appetite"] -= 0.25
        evidence.append("liquidity_tightening")
    if _contains(lower, ("通膨降溫", "低於預期", "cooling inflation", "disinflation")):
        genes["inflation_relief"] += 0.65
        genes["liquidity"] += 0.20
        evidence.append("inflation_relief")
    if _contains(lower, ("通膨升溫", "高於預期", "hot inflation", "inflation spike")):
        genes["inflation_relief"] -= 0.70
        genes["liquidity"] -= 0.20
        evidence.append("inflation_pressure")
    if _contains(lower, ("ai 泡沫", "ai泡沫", "估值泡沫", "ai bubble", "bubble burst")):
        genes["ai_cycle"] -= 0.80
        genes["risk_appetite"] -= 0.30
        evidence.append("ai_bubble_risk")
    elif _contains(lower, ("ai需求", "ai 需求", "人工智慧需求", "ai boom", "ai spending", "ai capex")):
        genes["ai_cycle"] += 0.65
        evidence.append("ai_cycle_expansion")
    if _contains(lower, ("崩盤", "金融危機", "流動性危機", "crash", "systemic", "credit crisis")):
        genes["risk_appetite"] -= 0.90
        genes["liquidity"] -= 0.45
        evidence.append("systemic_risk")

    event_score = _finite(macro_event.get("event_score"), 0.0)
    event_confidence = _clamp(_finite(macro_event.get("event_confidence"), 0.0), 0.0, 1.0)
    event_code = str(macro_event.get("event_code") or "").upper()
    if event_code in {"CPI", "PPI"} and event_confidence > 0.0:
        genes["inflation_relief"] += event_score * event_confidence
        genes["liquidity"] += event_score * event_confidence * 0.45
        evidence.append(f"official_{event_code.lower()}_signal")
    elif event_code == "FOMC" and event_confidence > 0.0:
        genes["liquidity"] += event_score * event_confidence
        genes["risk_appetite"] += event_score * event_confidence * 0.35
        evidence.append("official_fomc_signal")

    return ({name: round(_clamp(value, -1.0, 1.0), 4) for name, value in genes.items()}, sorted(set(evidence)))


def build_environment_genome(
    prediction_row: Mapping[str, Any],
    macro_event: Mapping[str, Any] | None = None,
) -> EnvironmentGenome:
    event = _mapping(macro_event)
    category = str(prediction_row.get("event_category") or "").lower()
    event_text = ""
    if any(token in category for token in ("macro", "geo", "policy", "commodity", "energy", "war")):
        event_text = " ".join([
            str(prediction_row.get("event_title") or ""),
            str(prediction_row.get("reassessment_reason") or ""),
        ])
    text = " ".join([
        str(prediction_row.get("macro_bias") or ""),
        category,
        event_text,
        str(event.get("semantic_verdict") or ""),
        str(event.get("risk_verdict") or ""),
        str(event.get("summary_line") or ""),
    ])
    genes, evidence = _environment_vector(text, event)
    bucket = _time_bucket(prediction_row.get("run_time_tw"), 30)
    source_event_id = str(event.get("event_id") or "")
    payload = {
        "schema": ENVIRONMENT_SCHEMA_VERSION,
        "bucket_tw": bucket,
        "genes": genes,
        "source_event_id": source_event_id,
    }
    confidence = min(0.95, 0.25 + len(evidence) * 0.12 + (0.12 if source_event_id else 0.0))
    return EnvironmentGenome(
        environment_id=_stable_id("ENV-", payload),
        schema_version=ENVIRONMENT_SCHEMA_VERSION,
        engine_version=SHADOW_GENETICS_ENGINE_VERSION,
        bucket_tw=bucket,
        observed_at_tw=str(prediction_row.get("run_time_tw") or ""),
        genes=genes,
        evidence=evidence,
        confidence=round(confidence, 4),
        source_event_id=source_event_id,
    )


def _bubble_score(genome: Mapping[str, Any], name: str, default: float = 45.0) -> float:
    genes = _mapping(genome.get("genes"))
    row = _mapping(genes.get(name))
    return _clamp(_finite(row.get("score"), default), 0.0, 100.0)


def build_ticker_genome(
    prediction_row: Mapping[str, Any],
    bubble_genome: Mapping[str, Any],
) -> TickerGenomeSnapshot:
    ticker = str(prediction_row.get("ticker") or "").strip().upper()
    market = str(prediction_row.get("market") or "").strip().upper()
    asset_type = str(prediction_row.get("asset_type") or "").strip().lower()
    identity_text = " ".join([
        ticker,
        str(prediction_row.get("name") or ""),
        " ".join(str(v) for v in (prediction_row.get("tags") or []) if v),
    ]).lower()
    semiconductor = _contains(identity_text, (
        "semiconductor", "半導體", "晶圓", "記憶體", "聯發科", "台積電",
        "nvda", "mrvl", "amd", "avgo", "arm", "tsm", "2330", "2454", "mu",
    ))
    ai_exposed = semiconductor or _contains(identity_text, ("人工智慧", "ai ", "ai供應鏈", "cloud", "data center"))
    energy_producer = _contains(identity_text, ("energy", "oil", "petroleum", "石油", "能源", "exxon", "chevron"))
    defensive = _contains(identity_text, ("utility", "公用事業", "consumer staples", "必需消費", "healthcare", "醫療"))

    valuation = _bubble_score(bubble_genome, "valuation_heat") / 100.0
    expectation = _bubble_score(bubble_genome, "expectation_heat") / 100.0
    narrative = _bubble_score(bubble_genome, "narrative_heat") / 100.0
    liquidity = _bubble_score(bubble_genome, "liquidity") / 100.0
    divergence = _bubble_score(bubble_genome, "divergence") / 100.0
    macro = _bubble_score(bubble_genome, "macro_sensitivity") / 100.0
    growth_cooling = _bubble_score(bubble_genome, "growth_cooling") / 100.0
    defense_discount = 0.25 if defensive else 0.0

    sensitivities = {
        "geopolitical_stability": _clamp(0.30 + 0.55 * macro + (0.12 if semiconductor else 0.0) - defense_discount, 0.05, 1.0),
        "energy_affordability": -0.85 if energy_producer else _clamp(0.22 + 0.25 * macro, 0.05, 0.75),
        "liquidity": _clamp(0.30 + 0.38 * valuation + 0.22 * liquidity - defense_discount, 0.05, 1.0),
        "inflation_relief": _clamp(0.22 + 0.34 * valuation + 0.18 * macro - defense_discount, 0.05, 1.0),
        "ai_cycle": _clamp((0.62 if ai_exposed else 0.12) + 0.22 * expectation + 0.18 * narrative, 0.05, 1.0),
        "risk_appetite": _clamp(0.28 + 0.26 * divergence + 0.22 * liquidity + 0.18 * growth_cooling - defense_discount, 0.05, 1.0),
    }
    sensitivities = {name: round(value, 4) for name, value in sensitivities.items()}
    traits = {
        "semiconductor": semiconductor,
        "ai_exposed": ai_exposed,
        "energy_producer": energy_producer,
        "defensive": defensive,
        "valuation_duration": round((valuation + expectation) / 2.0, 4),
        "narrative_reflexivity": round((narrative + divergence) / 2.0, 4),
    }
    lineage_id = _stable_id("TG-", {"ticker": ticker, "market": market, "asset_type": asset_type}, 14)
    snapshot_payload = {
        "schema": TICKER_GENOME_SCHEMA_VERSION,
        "prediction_id": prediction_row.get("id"),
        "lineage_id": lineage_id,
        "sensitivities": sensitivities,
    }
    return TickerGenomeSnapshot(
        snapshot_id=_stable_id("TGS-", snapshot_payload),
        lineage_id=lineage_id,
        schema_version=TICKER_GENOME_SCHEMA_VERSION,
        engine_version=SHADOW_GENETICS_ENGINE_VERSION,
        prediction_id=str(prediction_row.get("id") or ""),
        run_time_tw=str(prediction_row.get("run_time_tw") or ""),
        ticker=ticker,
        market=market,
        asset_type=asset_type,
        sensitivities=sensitivities,
        traits=traits,
        source_genome_id=str(bubble_genome.get("genome_id") or ""),
        confidence=round(_clamp(_finite(bubble_genome.get("genome_confidence"), 0.0), 0.0, 1.0), 4),
    )


def _direction(score: float) -> str:
    return "UP" if score >= 8.0 else "DOWN" if score <= -8.0 else "NEUTRAL"


def _candidate_outcome(
    policy: Mapping[str, Any],
    environment: EnvironmentGenome,
    ticker_genome: TickerGenomeSnapshot,
    official_score: float,
) -> Dict[str, Any]:
    weights = _mapping(policy.get("weights"))
    contributions: Dict[str, float] = {}
    weight_total = 0.0
    for name in ENVIRONMENT_GENE_ORDER:
        weight = max(0.0, _finite(weights.get(name), 0.0))
        weight_total += weight
        contributions[name] = (
            _finite(environment.genes.get(name), 0.0)
            * _finite(ticker_genome.sensitivities.get(name), 0.0)
            * weight
        )
    raw = sum(contributions.values()) / weight_total if weight_total > 0 else 0.0
    # A bounded overlay of +/-30 score points prevents a research candidate
    # from manufacturing extreme outcomes from sparse environment evidence.
    shadow_bias = _clamp(raw * 30.0 * environment.confidence, -30.0, 30.0)
    adjusted = _clamp(official_score + shadow_bias, -100.0, 100.0)
    return {
        "candidate_id": str(policy.get("candidate_id") or ""),
        "generation": int(policy.get("generation") or 0),
        "lineage": str(policy.get("lineage") or ""),
        "mutation": str(policy.get("mutation") or ""),
        "shadow_bias": round(shadow_bias, 4),
        "adjusted_score": round(adjusted, 4),
        "direction": _direction(adjusted),
        "gene_contributions": {name: round(value, 5) for name, value in contributions.items()},
    }


def build_shadow_phenotype(
    prediction_row: Mapping[str, Any],
    environment: EnvironmentGenome,
    ticker_genome: TickerGenomeSnapshot,
) -> ShadowPhenotype:
    official_direction = str(prediction_row.get("predicted_direction") or "NEUTRAL").upper()
    official_score = _finite(prediction_row.get("direction_score"), 0.0)
    outcomes = [
        _candidate_outcome(policy, environment, ticker_genome, official_score)
        for policy in POLICY_POPULATION
    ]
    for outcome in outcomes:
        if outcome.get("candidate_id") == OFFICIAL_CHAMPION_ID:
            outcome["shadow_bias"] = 0.0
            outcome["adjusted_score"] = round(official_score, 4)
            outcome["direction"] = official_direction
            outcome["gene_contributions"] = {name: 0.0 for name in ENVIRONMENT_GENE_ORDER}
        elif abs(_finite(outcome.get("shadow_bias"), 0.0)) < 1e-9:
            # With no common-environment contribution there is no genetic
            # evidence for a counterfactual direction.  Re-thresholding the
            # raw official score could otherwise manufacture DOWN/UP while the
            # UI correctly shows Shadow Bias = 0.
            outcome["direction"] = official_direction
    selected = next(row for row in outcomes if row["candidate_id"] == SELECTED_CHALLENGER_ID)
    contributions = dict(selected.get("gene_contributions") or {})
    ranked = sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
    explanation = [
        f"{ENVIRONMENT_GENE_LABELS.get(name, name)} {value:+.3f}"
        for name, value in ranked[:3] if abs(value) >= 0.01
    ]
    payload = {
        "schema": PHENOTYPE_SCHEMA_VERSION,
        "prediction_id": prediction_row.get("id"),
        "environment_id": environment.environment_id,
        "ticker_genome_snapshot_id": ticker_genome.snapshot_id,
        "candidate": SELECTED_CHALLENGER_ID,
    }
    return ShadowPhenotype(
        phenotype_id=_stable_id("PH-", payload),
        schema_version=PHENOTYPE_SCHEMA_VERSION,
        engine_version=SHADOW_GENETICS_ENGINE_VERSION,
        prediction_id=str(prediction_row.get("id") or ""),
        run_time_tw=str(prediction_row.get("run_time_tw") or ""),
        target_trade_date=str(prediction_row.get("target_trade_date") or ""),
        ticker=ticker_genome.ticker,
        market=ticker_genome.market,
        environment_id=environment.environment_id,
        ticker_genome_snapshot_id=ticker_genome.snapshot_id,
        official_direction=official_direction,
        official_direction_score=round(official_score, 4),
        selected_candidate_id=SELECTED_CHALLENGER_ID,
        shadow_bias=float(selected["shadow_bias"]),
        shadow_adjusted_score=float(selected["adjusted_score"]),
        shadow_direction=str(selected["direction"]),
        gene_contributions=contributions,
        candidate_outcomes=outcomes,
        explanation=explanation,
    )


def build_shadow_bundle(
    prediction_row: Mapping[str, Any],
    bubble_genome: Mapping[str, Any],
    macro_event: Mapping[str, Any] | None = None,
) -> Tuple[EnvironmentGenome, TickerGenomeSnapshot, ShadowPhenotype]:
    environment = build_environment_genome(prediction_row, macro_event)
    ticker_genome = build_ticker_genome(prediction_row, bubble_genome)
    phenotype = build_shadow_phenotype(prediction_row, environment, ticker_genome)
    return environment, ticker_genome, phenotype
