# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from v13_research.fitness_engine import build_shadow_fitness, evaluate_evolution_gate
from v13_research.shadow_genetics import (
    OFFICIAL_CHAMPION_ID,
    SELECTED_CHALLENGER_ID,
    build_shadow_bundle,
)


def _bubble(genome_id: str, *, valuation: float = 70.0, macro: float = 60.0):
    scores = {
        "price_heat": 62.0,
        "valuation_heat": valuation,
        "expectation_heat": 68.0,
        "narrative_heat": 74.0,
        "divergence": 55.0,
        "growth_cooling": 42.0,
        "liquidity": 58.0,
        "institution_divergence": 45.0,
        "short_squeeze": 28.0,
        "macro_sensitivity": macro,
    }
    return {
        "genome_id": genome_id,
        "genome_confidence": 0.84,
        "genes": {name: {"score": value} for name, value in scores.items()},
    }


def _row(ticker: str, name: str, prediction_id: str):
    return {
        "id": prediction_id,
        "ticker": ticker,
        "name": name,
        "market": "US",
        "asset_type": "stock",
        "run_time_tw": "2026-07-18T08:07:30+08:00",
        "run_date_tw": "2026-07-18",
        "target_trade_date": "2026-07-20",
        "model_version": "V12-test",
        "macro_bias": "戰爭風險升高、油價暴漲、AI 泡沫修正",
        "predicted_direction": "UP",
        "direction_score": 12.0,
        "bubble_radar": {},
        "truths": [],
        "valid_price_sample": True,
    }


def test_shared_environment_but_ticker_specific_phenotype():
    mrvl = _row("MRVL", "Marvell Technology", "p-mrvl")
    xom = _row("XOM", "Exxon Mobil Energy", "p-xom")
    before_mrvl = deepcopy(mrvl)
    before_xom = deepcopy(xom)

    mrvl_env, mrvl_genome, mrvl_pheno = build_shadow_bundle(mrvl, _bubble("G-MRVL"))
    xom_env, xom_genome, xom_pheno = build_shadow_bundle(xom, _bubble("G-XOM", valuation=35.0))

    assert mrvl_env.environment_id == xom_env.environment_id
    assert mrvl_env.genes == xom_env.genes
    assert mrvl_genome.lineage_id != xom_genome.lineage_id
    assert mrvl_genome.sensitivities != xom_genome.sensitivities
    assert mrvl_pheno.shadow_bias != xom_pheno.shadow_bias
    assert mrvl == before_mrvl and xom == before_xom
    assert mrvl_pheno.research_only is True
    assert mrvl_pheno.decision_influence is False


def test_user_identity_never_changes_genetics():
    first = _row("MRVL", "Marvell Technology", "p-1")
    second = dict(first)
    first["user_id"] = "admin"
    second["user_id"] = "visitor-999"
    one = build_shadow_bundle(first, _bubble("G-MRVL"))
    two = build_shadow_bundle(second, _bubble("G-MRVL"))
    assert one[0].environment_id == two[0].environment_id
    assert one[1].snapshot_id == two[1].snapshot_id
    assert one[2].phenotype_id == two[2].phenotype_id


def test_audit_creates_counterfactual_fitness_without_promotion():
    row = _row("MRVL", "Marvell Technology", "p-fit")
    _, _, phenotype = build_shadow_bundle(row, _bubble("G-MRVL"))
    audit = {
        "audit_id": "p-fit:next",
        "prediction_id": "p-fit",
        "audit_time_tw": "2026-07-20T14:00:00+08:00",
        "target_trade_date": "2026-07-20",
        "ticker": "MRVL",
        "market": "US",
        "target": "next",
        "actual_direction": "DOWN",
        "actual_valid": True,
    }
    fitness = build_shadow_fitness(audit, phenotype.to_dict())
    assert fitness.sample_eligible is True
    assert any(row["candidate_id"] == OFFICIAL_CHAMPION_ID for row in fitness.candidate_results)
    gate = evaluate_evolution_gate([fitness.to_dict()], evaluated_at_tw=audit["audit_time_tw"])
    assert gate.status == "collecting"
    assert gate.auto_promote is False
    assert gate.requires_manual_review is True
    assert gate.decision_influence is False


def test_official_baseline_uses_persisted_direction_label():
    row = _row("MRVL", "Marvell Technology", "p-label")
    row["predicted_direction"] = "UP"
    row["direction_score"] = 1.0
    _, _, phenotype = build_shadow_bundle(row, _bubble("G-MRVL"))
    baseline = next(
        outcome for outcome in phenotype.candidate_outcomes
        if outcome["candidate_id"] == OFFICIAL_CHAMPION_ID
    )
    assert baseline["direction"] == "UP"
    assert baseline["shadow_bias"] == 0.0


def test_historical_recovery_rejects_future_macro_evidence(monkeypatch):
    from v13_research import scheduler

    future = {
        "event_id": "future-cpi",
        "event_code": "CPI",
        "observed_at_tw": "2026-07-19T08:00:00+08:00",
        "event_score": -0.8,
    }
    prior = {
        "event_id": "prior-ppi",
        "event_code": "PPI",
        "observed_at_tw": "2026-07-18T07:30:00+08:00",
        "event_score": 0.3,
    }
    monkeypatch.setattr(scheduler, "load_recent_macro_events", lambda limit=300: [prior, future])
    selected = scheduler._macro_event_at_prediction(_row("MRVL", "Marvell", "p-time"))
    assert selected["event_id"] == "prior-ppi"


def test_gate_requires_cross_ticker_cross_market_out_of_sample_evidence():
    rows = []
    start = date(2026, 1, 1)
    for index in range(80):
        ticker_index = index % 10
        day = start + timedelta(days=index % 24)
        rows.append({
            "fitness_id": f"F-{index}",
            "sample_eligible": True,
            "ticker": f"T{ticker_index}",
            "market": "TW" if ticker_index % 2 == 0 else "US",
            "target_trade_date": day.isoformat(),
            "baseline_hit": index % 5 in {0, 1},
            "candidate_results": [
                {"candidate_id": SELECTED_CHALLENGER_ID, "hit": index % 5 in {0, 1, 2, 3}},
                {"candidate_id": "V13_G1_DEFENSIVE", "hit": index % 5 in {0, 1, 2}},
            ],
        })
    gate = evaluate_evolution_gate(rows, evaluated_at_tw="2026-07-18T12:00:00+08:00")
    assert gate.status == "eligible_manual_review"
    assert gate.leading_challenger_id == SELECTED_CHALLENGER_ID
    assert gate.uplift >= 0.04
    assert gate.auto_promote is False


def test_scheduler_writes_and_repairs_complete_shadow_bundle(tmp_path, monkeypatch):
    from v13_research import repository
    from v13_research.scheduler import schedule_prediction_research

    paths = {
        "RESEARCH_DIR": tmp_path,
        "RESEARCH_SEED_LOG": tmp_path / "research_seed.jsonl",
        "GENOME_SNAPSHOT_LOG": tmp_path / "genome_snapshot.jsonl",
        "DETECTION_EVENT_LOG": tmp_path / "detection_event.jsonl",
        "MACRO_EVENT_LOG": tmp_path / "macro_event.jsonl",
        "ENVIRONMENT_GENOME_LOG": tmp_path / "environment_genome.jsonl",
        "TICKER_GENOME_LOG": tmp_path / "ticker_genome.jsonl",
        "SHADOW_PHENOTYPE_LOG": tmp_path / "shadow_phenotype.jsonl",
        "SHADOW_FITNESS_LOG": tmp_path / "shadow_fitness.jsonl",
        "EVOLUTION_GATE_LOG": tmp_path / "evolution_gate.jsonl",
    }
    for name, value in paths.items():
        monkeypatch.setattr(repository, name, value)
    repository._ID_CACHE.clear()
    repository._ROWS_CACHE.clear()
    repository._RECENT_BY_TICKER.clear()
    repository._RECENT_CACHE_SIGNATURE = None

    row = _row("MRVL", "Marvell Technology", "p-scheduler")
    row["direction_family_scores"] = {"geo_policy": -25.0, "market_heat": -15.0}
    row["direction_family_contributions"] = {"geo_policy": -8.0, "market_heat": -5.0}
    first = schedule_prediction_research(row)
    second = schedule_prediction_research(row)

    assert first["status"] == "written"
    assert first["shadow_genetics"]["status"] == "written"
    assert second["status"] == "cache_hit"
    assert second["shadow_genetics"]["status"] == "cache_hit"
    assert paths["ENVIRONMENT_GENOME_LOG"].exists()
    assert paths["TICKER_GENOME_LOG"].exists()
    assert paths["SHADOW_PHENOTYPE_LOG"].exists()
