# -*- coding: utf-8 -*-
"""Fast, deterministic Bubble Genome engine.

Performance contract
--------------------
* No network I/O.
* No pandas / DataFrame construction.
* No historical-log scan.
* O(1) arithmetic over the already-persisted V12 ResearchSeed.
* Never mutates the seed or any V12 object.
"""
from __future__ import annotations

import hashlib
import json
import math
from time import perf_counter
from typing import Any, Dict, Iterable, Mapping, Sequence, Tuple

from .contracts import GENOME_ENGINE_VERSION, GENOME_SCHEMA_VERSION, GenomeSnapshot, ResearchSeed

GENE_ORDER: Tuple[str, ...] = (
    "price_heat",
    "valuation_heat",
    "expectation_heat",
    "narrative_heat",
    "divergence",
    "growth_cooling",
    "liquidity",
    "institution_divergence",
    "short_squeeze",
    "macro_sensitivity",
)

GENE_LABELS: Dict[str, str] = {
    "price_heat": "Price Heat",
    "valuation_heat": "Valuation Heat",
    "expectation_heat": "Expectation Heat",
    "narrative_heat": "Narrative Heat",
    "divergence": "Divergence",
    "growth_cooling": "Growth Cooling",
    "liquidity": "Liquidity",
    "institution_divergence": "Institution Divergence",
    "short_squeeze": "Short Squeeze",
    "macro_sensitivity": "Macro Sensitivity",
}

# Weights express research importance, not trading influence.  Missing genes are
# excluded and the remaining weights are renormalised.
GENE_WEIGHTS: Dict[str, float] = {
    "price_heat": 0.13,
    "valuation_heat": 0.13,
    "expectation_heat": 0.09,
    "narrative_heat": 0.10,
    "divergence": 0.13,
    "growth_cooling": 0.11,
    "liquidity": 0.08,
    "institution_divergence": 0.08,
    "short_squeeze": 0.06,
    "macro_sensitivity": 0.09,
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _norm(value: Any, maximum: float) -> float | None:
    number = _finite(value)
    if number is None or maximum <= 0:
        return None
    return round(_clamp(number / maximum * 100.0), 2)


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(clean) / len(clean) if clean else None


def _abs_family(families: Mapping[str, Any], name: str) -> float | None:
    value = _finite(families.get(name))
    return _clamp(abs(value)) if value is not None else None


def _positive_family(families: Mapping[str, Any], name: str) -> float | None:
    value = _finite(families.get(name))
    return _clamp(max(0.0, value)) if value is not None else None


def _gene(score: float | None, confidence: float, source: str, observed: bool = True) -> Dict[str, Any]:
    available = bool(score is not None and observed)
    return {
        "score": round(_clamp(score), 2) if available else None,
        "confidence": round(_clamp(confidence, 0.0, 1.0), 3) if available else 0.0,
        "source": str(source or ""),
        "status": "observed" if available else "insufficient_data",
    }


def _stable_id(prefix: str, payload: Mapping[str, Any], length: int) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:length].upper()}"


def _build_gene_vector(seed: ResearchSeed) -> Dict[str, Dict[str, Any]]:
    bubble = _mapping(seed.bubble_snapshot)
    metrics = _mapping(bubble.get("metrics"))
    direction = _mapping(seed.direction_snapshot)
    families = _mapping(direction.get("family_scores"))

    bubble_quality = _finite(bubble.get("quality"))
    if bubble_quality is None:
        bubble_quality = 0.35
    bubble_quality = _clamp(bubble_quality, 0.0, 1.0)
    direction_quality = _finite(direction.get("quality"))
    if direction_quality is None:
        direction_quality = 0.35
    direction_quality = _clamp(direction_quality, 0.0, 1.0)

    price_observed = any(
        _finite(metrics.get(key)) is not None for key in ("ret20", "ret60", "ma20_gap")
    )
    price = _norm(metrics.get("price_heat"), 30.0) if price_observed else None

    valuation_available = bool(metrics.get("valuation_available")) or any(
        _finite(metrics.get(key)) not in (None, 0.0) for key in ("pe", "forward_pe", "ps")
    )
    valuation = _norm(metrics.get("valuation_heat"), 22.0) if valuation_available else None

    expectation = _norm(metrics.get("expectation_heat"), 10.0)

    narrative_parts = [
        expectation,
        _abs_family(families, "news"),
        _abs_family(families, "analyst_event"),
        _abs_family(families, "fundamental_event"),
    ]
    narrative = _mean(narrative_parts)
    narrative_observed = any(value is not None for value in narrative_parts)

    bubble_accepted = bool(bubble.get("accepted"))
    divergence = _norm(metrics.get("divergence"), 35.0) if bubble_accepted else None

    growth_values = [
        _finite(metrics.get("qoq")),
        _finite(metrics.get("revenue_yoy")),
        _finite(metrics.get("earnings_yoy_for_decision")),
        _finite(metrics.get("monthly_mom")),
    ]
    growth_observed = any(value is not None for value in growth_values)
    negative_growth = max(
        [
            _clamp(-value * 1.8)
            for value in growth_values
            if value is not None and value < 0.0
        ]
        or [0.0]
    )
    deceleration = _norm(metrics.get("deceleration"), 22.0)
    growth_cooling = max(negative_growth, deceleration or 0.0) if growth_observed else None

    liquidity_parts = [
        _abs_family(families, "flow"),
        _abs_family(families, "intraday"),
        _abs_family(families, "price_action"),
        _abs_family(families, "leverage"),
        _abs_family(families, "market_heat"),
    ]
    liquidity = _mean(liquidity_parts)

    institutional_parts = [
        _finite(families.get("flow")),
        _finite(families.get("analyst_event")),
        _finite(families.get("futures")),
    ]
    institution_values = [value for value in institutional_parts if value is not None]
    market_reference = _mean(
        [
            _finite(families.get("trend")),
            _finite(families.get("intraday")),
            _finite(families.get("price_action")),
            _finite(families.get("fundamental_event")),
        ]
    )
    institution_divergence = None
    if institution_values and market_reference is not None:
        institutional_consensus = sum(institution_values) / len(institution_values)
        institution_divergence = _clamp(abs(institutional_consensus - market_reference) / 2.0)

    short_score = _finite(families.get("short"))
    short_squeeze = None
    if short_score is not None:
        squeeze_parts = [
            _clamp(max(0.0, short_score) * 1.45),
            _clamp(max(0.0, _finite(families.get("intraday")) or 0.0)),
            _clamp(max(0.0, _finite(families.get("trend")) or 0.0)),
            price,
        ]
        short_squeeze = _mean(squeeze_parts)

    macro_parts = [
        _abs_family(families, "geo_policy"),
        _abs_family(families, "overnight"),
        _abs_family(families, "market_heat"),
        _abs_family(families, "futures"),
    ]
    macro_sensitivity = _mean(macro_parts)

    return {
        "price_heat": _gene(
            price,
            0.55 + 0.45 * bubble_quality,
            "bubble.metrics.price_heat",
            observed=price_observed,
        ),
        "valuation_heat": _gene(
            valuation,
            0.45 + 0.55 * bubble_quality,
            "bubble.metrics.valuation_heat",
            observed=valuation_available,
        ),
        "expectation_heat": _gene(
            expectation,
            0.35 + 0.45 * bubble_quality,
            "bubble.metrics.expectation_heat",
        ),
        "narrative_heat": _gene(
            narrative,
            0.30 + 0.35 * max(bubble_quality, direction_quality),
            "expectation+direction.news/analyst/fundamental",
            observed=narrative_observed,
        ),
        "divergence": _gene(
            divergence,
            0.45 + 0.50 * bubble_quality,
            "bubble.metrics.divergence",
            observed=bubble_accepted,
        ),
        "growth_cooling": _gene(
            growth_cooling,
            0.40 + 0.50 * bubble_quality,
            "bubble.metrics.deceleration+verified_growth",
            observed=growth_observed,
        ),
        "liquidity": _gene(
            liquidity,
            0.35 + 0.45 * direction_quality,
            "direction.flow/intraday/price_action/leverage/market_heat",
        ),
        "institution_divergence": _gene(
            institution_divergence,
            0.35 + 0.45 * direction_quality,
            "direction.institution_vs_market",
        ),
        "short_squeeze": _gene(
            short_squeeze,
            0.35 + 0.45 * direction_quality,
            "direction.short+price_confirmation",
            observed=short_score is not None,
        ),
        "macro_sensitivity": _gene(
            macro_sensitivity,
            0.35 + 0.45 * direction_quality,
            "direction.geo_policy/overnight/market_heat/futures",
        ),
    }


def _score_and_confidence(
    genes: Mapping[str, Mapping[str, Any]],
    seed: ResearchSeed,
) -> Tuple[float, float, float]:
    weighted_score = 0.0
    available_weight = 0.0
    weighted_confidence = 0.0
    available_count = 0

    for name in GENE_ORDER:
        gene = genes.get(name) if isinstance(genes.get(name), Mapping) else {}
        score = _finite(gene.get("score"))
        confidence = _finite(gene.get("confidence"))
        if score is None:
            continue
        weight = GENE_WEIGHTS[name]
        weighted_score += score * weight
        weighted_confidence += _clamp(confidence or 0.0, 0.0, 1.0) * weight
        available_weight += weight
        available_count += 1

    coverage = available_count / len(GENE_ORDER)
    if available_weight <= 0.0:
        return 0.0, 0.0, 0.0

    genome_score = weighted_score / available_weight
    evidence_confidence = weighted_confidence / available_weight

    data_quality = _mapping(seed.data_quality)
    price_quality = 1.0 if bool(data_quality.get("valid_price_sample")) else 0.55
    truth = _mapping(seed.truth_snapshot)
    truth_count = int(_finite(truth.get("count")) or 0)
    accepted_count = int(_finite(truth.get("accepted_count")) or 0)
    truth_quality = accepted_count / truth_count if truth_count > 0 else 0.45

    genome_confidence = (
        evidence_confidence * 0.62
        + coverage * 0.20
        + price_quality * 0.10
        + _clamp(truth_quality, 0.0, 1.0) * 0.08
    )
    return round(_clamp(genome_score), 2), round(_clamp(genome_confidence, 0.0, 1.0), 3), round(coverage, 3)


def _fingerprint(genes: Mapping[str, Mapping[str, Any]]) -> str:
    codes = {
        "price_heat": "P",
        "valuation_heat": "V",
        "expectation_heat": "E",
        "narrative_heat": "N",
        "divergence": "D",
        "growth_cooling": "G",
        "liquidity": "L",
        "institution_divergence": "I",
        "short_squeeze": "S",
        "macro_sensitivity": "M",
    }
    parts = []
    for name in GENE_ORDER:
        gene = genes.get(name) if isinstance(genes.get(name), Mapping) else {}
        value = _finite(gene.get("score"))
        parts.append(f"{codes[name]}{'NA' if value is None else f'{int(round(value)):02d}'}")
    return "-".join(parts)


def build_genome_snapshot(seed: ResearchSeed) -> GenomeSnapshot:
    started = perf_counter()
    genes = _build_gene_vector(seed)
    genome_score, genome_confidence, coverage = _score_and_confidence(genes, seed)

    ranked = sorted(
        (
            (name, float(gene["score"]))
            for name, gene in genes.items()
            if isinstance(gene, Mapping) and _finite(gene.get("score")) is not None
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    dominant_genes = [name for name, score in ranked[:3] if score >= 25.0]

    genome_id = _stable_id(
        "G-",
        {
            "ticker": seed.ticker,
            "market": seed.market,
            "asset_type": seed.asset_type,
        },
        12,
    )
    snapshot_id = _stable_id(
        "GS-",
        {
            "schema": GENOME_SCHEMA_VERSION,
            "seed_id": seed.seed_id,
            "genome_id": genome_id,
        },
        16,
    )

    elapsed_ms = (perf_counter() - started) * 1000.0
    return GenomeSnapshot(
        snapshot_id=snapshot_id,
        genome_id=genome_id,
        schema_version=GENOME_SCHEMA_VERSION,
        engine_version=GENOME_ENGINE_VERSION,
        seed_id=seed.seed_id,
        prediction_id=seed.prediction_id,
        run_time_tw=seed.run_time_tw,
        run_date_tw=seed.run_date_tw,
        target_trade_date=seed.target_trade_date,
        ticker=seed.ticker,
        market=seed.market,
        asset_type=seed.asset_type,
        genes=genes,
        fingerprint=_fingerprint(genes),
        genome_score=genome_score,
        genome_confidence=genome_confidence,
        coverage=coverage,
        dominant_genes=dominant_genes,
        data_quality=dict(seed.data_quality),
        research_only=True,
        decision_influence=False,
        calc_ms=round(elapsed_ms, 3),
    )
