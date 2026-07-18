# -*- coding: utf-8 -*-
"""Post-close fitness and guarded Champion/Challenger evaluation."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from typing import Any, Dict, Mapping, Sequence

from .genetics_contracts import (
    EVOLUTION_SCHEMA_VERSION,
    FITNESS_SCHEMA_VERSION,
    SHADOW_GENETICS_ENGINE_VERSION,
    EvolutionGateStatus,
    ShadowFitness,
)
from .shadow_genetics import OFFICIAL_CHAMPION_ID

MIN_ELIGIBLE_SAMPLES = 60
MIN_TICKERS = 8
MIN_MARKETS = 2
MIN_TRADE_DAYS = 20
MIN_UPLIFT = 0.04
MIN_CHALLENGER_HIT_RATE = 0.55
MAX_TICKER_CONCENTRATION = 0.35


def _stable_id(prefix: str, payload: Mapping[str, Any], length: int = 20) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length].upper()


def build_shadow_fitness(audit: Mapping[str, Any], phenotype: Mapping[str, Any]) -> ShadowFitness:
    audit_id = str(audit.get("audit_id") or "").strip()
    prediction_id = str(audit.get("prediction_id") or "").strip()
    if not audit_id or not prediction_id:
        raise ValueError("audit_id and prediction_id are required")
    actual = str(audit.get("actual_direction") or "").upper()
    target = str(audit.get("target") or "").lower()
    exclusion_reasons = []
    if target != "next":
        exclusion_reasons.append("not_t1_out_of_sample")
    if actual not in {"UP", "DOWN", "NEUTRAL"}:
        exclusion_reasons.append("actual_direction_missing")
    if audit.get("actual_valid") is False:
        exclusion_reasons.append("actual_price_invalid")
    if not phenotype:
        exclusion_reasons.append("phenotype_missing")

    outcomes = phenotype.get("candidate_outcomes") if isinstance(phenotype.get("candidate_outcomes"), list) else []
    candidate_results = []
    for row in outcomes:
        if not isinstance(row, Mapping):
            continue
        direction = str(row.get("direction") or "NEUTRAL").upper()
        candidate_results.append({
            "candidate_id": str(row.get("candidate_id") or ""),
            "generation": int(row.get("generation") or 0),
            "predicted_direction": direction,
            "hit": bool(actual in {"UP", "DOWN", "NEUTRAL"} and direction == actual),
            "shadow_bias": row.get("shadow_bias"),
            "adjusted_score": row.get("adjusted_score"),
        })
    baseline = next(
        (row for row in candidate_results if row.get("candidate_id") == OFFICIAL_CHAMPION_ID),
        {},
    )
    eligible = not exclusion_reasons and bool(candidate_results)
    return ShadowFitness(
        fitness_id=_stable_id("FIT-", {"schema": FITNESS_SCHEMA_VERSION, "audit_id": audit_id}),
        schema_version=FITNESS_SCHEMA_VERSION,
        engine_version=SHADOW_GENETICS_ENGINE_VERSION,
        audit_id=audit_id,
        prediction_id=prediction_id,
        audit_time_tw=str(audit.get("audit_time_tw") or ""),
        target_trade_date=str(audit.get("target_trade_date") or ""),
        ticker=str(audit.get("ticker") or phenotype.get("ticker") or "").upper(),
        market=str(audit.get("market") or phenotype.get("market") or "").upper(),
        actual_direction=actual,
        phenotype_id=str(phenotype.get("phenotype_id") or ""),
        environment_id=str(phenotype.get("environment_id") or ""),
        baseline_hit=bool(baseline.get("hit")),
        candidate_results=candidate_results,
        sample_eligible=eligible,
        exclusion_reasons=exclusion_reasons,
    )


def evaluate_evolution_gate(
    fitness_rows: Sequence[Mapping[str, Any]],
    *,
    evaluated_at_tw: str = "",
) -> EvolutionGateStatus:
    eligible = [row for row in fitness_rows if isinstance(row, Mapping) and bool(row.get("sample_eligible"))]
    candidate_counts: Dict[str, list[int]] = defaultdict(list)
    tickers = set()
    markets = set()
    trade_days = set()
    ticker_counter: Counter[str] = Counter()
    baseline_hits = 0
    for row in eligible:
        ticker = str(row.get("ticker") or "").upper()
        market = str(row.get("market") or "").upper()
        day = str(row.get("target_trade_date") or "")
        if ticker:
            tickers.add(ticker)
            ticker_counter[ticker] += 1
        if market:
            markets.add(market)
        if day:
            trade_days.add(day)
        baseline_hits += int(bool(row.get("baseline_hit")))
        results = row.get("candidate_results") if isinstance(row.get("candidate_results"), list) else []
        for result in results:
            if not isinstance(result, Mapping):
                continue
            candidate_id = str(result.get("candidate_id") or "")
            if candidate_id and candidate_id != OFFICIAL_CHAMPION_ID:
                candidate_counts[candidate_id].append(int(bool(result.get("hit"))))

    sample_count = len(eligible)
    baseline_rate = baseline_hits / sample_count if sample_count else 0.0
    leading_id = ""
    challenger_rate = 0.0
    if candidate_counts:
        leading_id, values = max(
            candidate_counts.items(),
            key=lambda item: (sum(item[1]) / len(item[1]) if item[1] else 0.0, len(item[1]), item[0]),
        )
        challenger_rate = sum(values) / len(values) if values else 0.0
    uplift = challenger_rate - baseline_rate
    concentration = max(ticker_counter.values(), default=0) / sample_count if sample_count else 1.0

    blockers = []
    checks = (
        (sample_count >= MIN_ELIGIBLE_SAMPLES, f"eligible_samples {sample_count}/{MIN_ELIGIBLE_SAMPLES}"),
        (len(tickers) >= MIN_TICKERS, f"tickers {len(tickers)}/{MIN_TICKERS}"),
        (len(markets) >= MIN_MARKETS, f"markets {len(markets)}/{MIN_MARKETS}"),
        (len(trade_days) >= MIN_TRADE_DAYS, f"trade_days {len(trade_days)}/{MIN_TRADE_DAYS}"),
        (challenger_rate >= MIN_CHALLENGER_HIT_RATE, f"challenger_hit_rate {challenger_rate:.3f}/{MIN_CHALLENGER_HIT_RATE:.3f}"),
        (uplift >= MIN_UPLIFT, f"uplift {uplift:+.3f}/{MIN_UPLIFT:+.3f}"),
        (concentration <= MAX_TICKER_CONCENTRATION, f"ticker_concentration {concentration:.3f}/{MAX_TICKER_CONCENTRATION:.3f}"),
    )
    blockers.extend(message for passed, message in checks if not passed)
    if sample_count < MIN_ELIGIBLE_SAMPLES or len(trade_days) < MIN_TRADE_DAYS:
        status = "collecting"
    elif blockers:
        status = "blocked"
    else:
        status = "eligible_manual_review"

    requirements = {
        "min_eligible_samples": MIN_ELIGIBLE_SAMPLES,
        "min_tickers": MIN_TICKERS,
        "min_markets": MIN_MARKETS,
        "min_trade_days": MIN_TRADE_DAYS,
        "min_uplift": MIN_UPLIFT,
        "min_challenger_hit_rate": MIN_CHALLENGER_HIT_RATE,
        "max_ticker_concentration": MAX_TICKER_CONCENTRATION,
        "observed_ticker_concentration": round(concentration, 4),
    }
    payload = {
        "schema": EVOLUTION_SCHEMA_VERSION,
        "latest_fitness_id": str(eligible[-1].get("fitness_id") or "") if eligible else "empty",
        "samples": sample_count,
        "leading": leading_id,
        "status": status,
    }
    return EvolutionGateStatus(
        status_id=_stable_id("EVO-", payload),
        schema_version=EVOLUTION_SCHEMA_VERSION,
        engine_version=SHADOW_GENETICS_ENGINE_VERSION,
        evaluated_at_tw=evaluated_at_tw,
        status=status,
        champion_id=OFFICIAL_CHAMPION_ID,
        leading_challenger_id=leading_id,
        eligible_samples=sample_count,
        ticker_count=len(tickers),
        market_count=len(markets),
        trade_day_count=len(trade_days),
        baseline_hit_rate=round(baseline_rate, 4),
        challenger_hit_rate=round(challenger_rate, 4),
        uplift=round(uplift, 4),
        requirements=requirements,
        blockers=blockers,
        auto_promote=False,
        requires_manual_review=True,
    )
