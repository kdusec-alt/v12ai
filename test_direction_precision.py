# -*- coding: utf-8 -*-
from __future__ import annotations

from models import DataTruth, PriceFrame, SignalPacket, TickerInfo
from direction_engine import build_direction_forecast
from arbitration import signal_price_adjustment
from orchestrator import orchestrate
import orchestrator
import learning
from models import NewsItem


def _frame(*, market: str, rising: bool, vwap_atr: float = 0.0, context=None) -> PriceFrame:
    start = 100.0 if rising else 170.0
    step = 1.2 if rising else -1.2
    closes = [start + step * i for i in range(60)]
    last = closes[-1]
    atr = 2.0
    symbol = "UNIT.TW" if market == "TW" else "UNIT"
    ticker = TickerInfo(symbol, symbol, "Unit", market, "stock", price_limit_pct=0.10 if market == "TW" else None)
    ctx = {
        "price_meta": {"price_verified": True, "source": "UNIT_REAL"},
        "inst": {}, "margin": {}, "bsi": {}, "macro": {}, "short": {},
    }
    if context:
        ctx.update(context)
    return PriceFrame(
        ticker=ticker,
        truth=DataTruth("UNIT_REAL", "2026-07-10", False, True, "unit"),
        open=last,
        high=last + 1.0,
        low=last - 1.0,
        last=last,
        previous_close=closes[-2],
        volume=2_000_000,
        vwap=last + vwap_atr * atr,
        atr14=atr,
        recent_closes=closes,
        recent_highs=[x + 1.0 for x in closes],
        recent_lows=[x - 1.0 for x in closes],
        recent_volumes=[2_000_000] * len(closes),
        price_date="2026-07-10",
        market_status="after_close",
        context=ctx,
    )


def test_slight_vwap_conflict_does_not_flip_confirmed_trend():
    up = build_direction_forecast(_frame(market="TW", rising=True, vwap_atr=0.30), [])
    down = build_direction_forecast(_frame(market="TW", rising=False, vwap_atr=-0.30), [])
    assert up.label == "UP", up
    assert down.label == "DOWN", down


def test_large_intraday_conflict_becomes_neutral_not_false_reversal():
    # A real conflict is not merely one tick under VWAP: confirmed trend is up,
    # while the live session reverses by 1.5 ATR and closes near the low.
    frame = _frame(market="TW", rising=True)
    prev = frame.recent_closes[-2]
    frame.market_status = "intraday"
    frame.previous_close = prev
    frame.open = prev + 0.5
    frame.last = prev - 3.0
    frame.high = prev + 0.8
    frame.low = frame.last - 0.3
    frame.vwap = prev - 0.3
    frame.recent_closes[-1] = frame.last
    result = build_direction_forecast(frame, [])
    assert result.label == "NEUTRAL", result
    assert result.conflict >= 0.45, result


def test_tw_flow_and_us_macro_are_market_specific():
    tw_ctx = {
        "inst": {
            "accepted": True, "source": "FinMind_Institutional",
            "foreign": -1800, "foreign_3": -6000, "foreign_5": -11000, "foreign_10": -19000,
            "trust": -400, "trust_3": -1200, "trust_5": -2500, "trust_10": -4000,
            "dealer": -200, "dealer_3": -600, "dealer_5": -900, "dealer_10": -1600,
        },
        "margin": {"accepted": True, "source": "FinMind_MARGIN", "margin": 1500, "short": 100},
        "bsi": {"accepted": True, "source": "SBL_OFFICIAL", "cover_rate": 20},
    }
    tw = build_direction_forecast(_frame(market="TW", rising=False, context=tw_ctx), [])
    assert "flow" in tw.family_scores and tw.family_scores["flow"] < 0, tw

    us_ctx = {
        "macro": {"accepted": True, "source": "US_MARKET", "sox": 3.2, "nq": 2.1},
        "short": {"short_float": 22.0},
    }
    us = build_direction_forecast(_frame(market="US", rising=True, context=us_ctx), [])
    assert "overnight" in us.family_scores and us.family_scores["overnight"] > 0, us
    assert "short" in us.family_scores, us


def test_risk_does_not_push_price_down():
    frame = _frame(market="US", rising=True)
    no_risk = SignalPacket("UnitUnique", "x", 10, 0, 0, 0.05, "", "unit", "", True)
    high_risk = SignalPacket("UnitUnique", "x", 10, 0, 100, 0.05, "", "unit", "", True)
    assert signal_price_adjustment(no_risk, frame) == signal_price_adjustment(high_risk, frame)


def test_duplicate_vwap_family_is_price_neutral():
    frame = _frame(market="TW", rising=True)
    for module in ["VWAP", "LCR", "FQC", "Liquidity", "QCRE", "市場風控"]:
        signal = SignalPacket(module, "x", 100, 0, 100, 1.0, "", "unit", "", True)
        assert signal_price_adjustment(signal, frame) == 0.0, module


def test_orchestrator_abc_comes_from_direction_engine_and_trace_rebuilds():
    frame = _frame(market="US", rising=True, vwap_atr=0.25, context={
        "macro": {"accepted": True, "source": "US_MARKET", "sox": 2.0, "nq": 1.5},
        "short": {"short_float": 18.0},
    })
    forecast = orchestrate(frame)
    direction = forecast.decision_card["_direction_engine"]
    assert forecast.raw.raw_abc["A"] == round(direction["p_up"] * 100, 1)
    assert abs(sum(forecast.raw.raw_abc.values()) - 100.0) <= 0.2
    assert any(step.name == "Direction Ensemble" for step in forecast.trace.steps)
    assert abs(forecast.trace.reconstruct_final_t1() - forecast.final_t1) < 0.011


def test_learning_audits_direction_separately_from_price_error():
    frame = _frame(market="US", rising=True, vwap_atr=0.20, context={
        "macro": {"accepted": True, "source": "US_MARKET", "sox": 2.5, "nq": 1.8},
        "short": {"short_float": 16.0},
    })
    forecast = orchestrate(frame)
    row = learning.forecast_snapshot(forecast)
    stored = []
    profiles = {}
    old_read = learning.read_audit_log
    old_append = learning.append_jsonl
    old_load = learning.load_profiles
    old_save = learning.save_profiles
    try:
        learning.read_audit_log = lambda limit=500: list(stored)
        learning.append_jsonl = lambda path, data: stored.append(dict(data))
        learning.load_profiles = lambda: dict(profiles)
        learning.save_profiles = lambda data: profiles.update(data)
        actual = float(row["anchor_close"]) * 1.02
        audit = learning.audit_prediction_row(row, actual, source="unit", target="next")
        assert audit["predicted_direction"] == "UP", audit
        assert audit["actual_direction"] == "UP", audit
        assert audit["direction_hit"] is True, audit
        assert audit["direction_brier"] is not None, audit
        assert "error_pct" in audit and "direction_hit" in audit
    finally:
        learning.read_audit_log = old_read
        learning.append_jsonl = old_append
        learning.load_profiles = old_load
        learning.save_profiles = old_save


def test_narrative_layer_cannot_change_formal_forecast_or_trace():
    frame = _frame(market="TW", rising=True, vwap_atr=-0.20, context={
        "macro": {"accepted": True, "source": "US_MARKET", "sox": -1.8, "nq": -1.1},
        "inst": {
            "accepted": True, "source": "OFFICIAL", "foreign": -1200,
            "foreign_3": -3000, "foreign_5": -5000, "foreign_10": -9000,
            "trust": -200, "dealer": -100,
        },
    })
    news = [NewsItem("GoogleNewsTW", "2026-07-10 12:00", -0.12, "tw_company_bearish_event", "Unit 下修展望", "")]
    enriched = orchestrate(frame, news_items=news)
    original_builder = orchestrator.build_ai_decision_narrative
    try:
        orchestrator.build_ai_decision_narrative = None
        legacy = orchestrate(frame, news_items=news)
    finally:
        orchestrator.build_ai_decision_narrative = original_builder
    assert enriched.final_t0 == legacy.final_t0
    assert enriched.final_t1 == legacy.final_t1
    assert enriched.final_t1_high == legacy.final_t1_high
    assert enriched.final_t1_low == legacy.final_t1_low
    assert enriched.confidence == legacy.confidence
    assert enriched.decision_card["_direction_engine"] == legacy.decision_card["_direction_engine"]
    assert enriched.trace.to_rows() == legacy.trace.to_rows()
    assert enriched.decision_card["_decision_narrative"]["narrative_only"] is True


def test_tw_radar_exposes_same_news_evidence_rows_as_us():
    frame = _frame(market="TW", rising=True, context={
        "macro": {"accepted": True, "source": "US_MARKET", "sox": 1.2, "nq": 0.8},
    })
    news = [
        NewsItem("GoogleNewsTW", "2026-07-10 08:00", -0.08, "tw_daily_policy_geo", "中東油價風險升溫", ""),
        NewsItem("GoogleNewsTW", "2026-07-10 09:00", 0.12, "tw_company_bullish_event", "Unit 上調展望", ""),
    ]
    forecast = orchestrate(frame, news_items=news)
    for key in ("Daily Headline", "Policy/Geo", "Company News"):
        assert key in forecast.radar and forecast.radar[key], (key, forecast.radar)


if __name__ == "__main__":
    tests = [
        test_slight_vwap_conflict_does_not_flip_confirmed_trend,
        test_large_intraday_conflict_becomes_neutral_not_false_reversal,
        test_tw_flow_and_us_macro_are_market_specific,
        test_risk_does_not_push_price_down,
        test_duplicate_vwap_family_is_price_neutral,
        test_orchestrator_abc_comes_from_direction_engine_and_trace_rebuilds,
        test_learning_audits_direction_separately_from_price_error,
        test_narrative_layer_cannot_change_formal_forecast_or_trace,
        test_tw_radar_exposes_same_news_evidence_rows_as_us,
    ]
    for test in tests:
        test()
        print("OK", test.__name__)
    print("DIRECTION PRECISION TESTS OK")
