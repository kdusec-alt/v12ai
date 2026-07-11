# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy

from direction_engine import build_direction_forecast
from models import DataTruth, NewsItem, PriceFrame, SignalPacket, TickerInfo
from orchestrator import orchestrate
from quantum_entanglement import build_quantum_evidence


REF = "2026-07-10"


def _frame(*, symbol="2330.TW", name="台積電", market="TW", rising=True, context=None):
    start = 100.0 if rising else 150.0
    step = 0.45 if rising else -0.45
    closes = [start + step * i for i in range(60)]
    last = closes[-1]
    ticker = TickerInfo(
        symbol, symbol, name, market, "stock",
        price_limit_pct=0.10 if market == "TW" else None,
    )
    ctx = {
        "price_meta": {"price_verified": True, "source": "UNIT_REAL"},
        "inst": {}, "margin": {}, "bsi": {}, "macro": {}, "short": {},
        "market_heat": {"accepted": False, "source": "UNIT"},
    }
    if context:
        ctx.update(deepcopy(context))
    return PriceFrame(
        ticker=ticker,
        truth=DataTruth("UNIT_REAL", REF, False, True, "unit"),
        open=last - step * 0.2,
        high=last + 0.8,
        low=last - 0.8,
        last=last,
        previous_close=closes[-2],
        volume=2_000_000,
        vwap=last - 0.10 if rising else last + 0.10,
        atr14=2.0,
        recent_closes=closes,
        recent_highs=[x + 0.8 for x in closes],
        recent_lows=[x - 0.8 for x in closes],
        recent_volumes=[2_000_000] * 60,
        price_date=REF,
        market_status="after_close",
        context=ctx,
    )


def _quantum(frame, signals=None, news=None):
    direction = build_direction_forecast(frame, signals or [], news or [])
    return direction


def test_tw_monthly_revenue_only_two_trading_sessions():
    base = {
        "accepted": True,
        "source": "MOPS_MonthRevenue",
        "revenue_model_usable": True,
        "revenue_verified": True,
        "revenue_quality": "official",
        "announcement_date": REF,
        "yoy": 32.0,
        "mom": 12.0,
        "accum_yoy": 22.0,
    }
    fresh = _quantum(_frame(context={"fundamental": base}))
    day2 = dict(base, announcement_date="2026-07-09")
    next_session = _quantum(_frame(context={"fundamental": day2}))
    stale = dict(base, announcement_date="2026-07-08")
    expired = _quantum(_frame(context={"fundamental": stale}))

    assert fresh.family_scores.get("fundamental_event", 0) > 20, fresh
    assert next_session.family_scores.get("fundamental_event", 0) > 0, next_session
    assert "fundamental_event" not in expired.family_scores, expired
    assert fresh.score > expired.score, (fresh, expired)


def test_us_earnings_guidance_only_48_hours():
    frame = _frame(symbol="MU", name="Micron", market="US")
    fresh_news = [NewsItem("unit", "2026-07-10T12:00:00", 0.22, "earnings", "Micron beats earnings and raises guidance", "")]
    stale_news = [NewsItem("unit", "2026-07-07T12:00:00", 0.22, "earnings", "Micron beats earnings and raises guidance", "")]
    fresh = _quantum(frame, news=fresh_news)
    stale = _quantum(frame, news=stale_news)
    assert fresh.family_scores.get("fundamental_event", 0) > 20, fresh
    assert "fundamental_event" not in stale.family_scores, stale


def test_margin_streak_is_context_aware():
    margin = {
        "accepted": True,
        "source": "FinMind_MARGIN",
        "date": REF,
        "margin": 3500,
        "margin_3": 9000,
        "margin_5": 15000,
        "margin_10": 26000,
        "margin_streak": "連增4天",
    }
    weak = _quantum(_frame(symbol="2408.TW", name="南亞科", rising=False, context={"margin": margin}))
    strong = _quantum(_frame(symbol="2408.TW", name="南亞科", rising=True, context={"margin": margin}))
    assert weak.family_scores.get("leverage", 0) < -20, weak
    assert weak.family_scores["leverage"] < strong.family_scores["leverage"], (weak, strong)


def _geo_signal(score=-30.0):
    return SignalPacket(
        "Quantum Macro", "政策/地緣", score, 0.0, 0.0, 0.0,
        "unit geo", "unit", REF, True,
    )


def _geo_news():
    return [NewsItem("unit", "2026-07-10T08:00:00", -0.2, "policy_geo", "New export controls increase geopolitical risk for memory chips", "")]


def _macro(sign: float):
    keys = {"tx_night": 1.2 * sign, "sox": 2.0 * sign, "smh": 1.8 * sign, "nq": 1.0 * sign, "qqq": 0.9 * sign, "mu": 2.5 * sign, "tsm_adr": 1.1 * sign}
    return {
        "accepted": True,
        "source": "UNIT_OBSERVED",
        **keys,
        "as_of": {key: REF for key in keys},
    }


def test_memory_geo_risk_uses_night_sox_nasdaq_transmission():
    memory = _quantum(
        _frame(symbol="2408.TW", name="南亞科", context={"macro": _macro(-1)}),
        [_geo_signal(-30)], _geo_news(),
    )
    broad = _quantum(
        _frame(symbol="2882.TW", name="國泰金", context={"macro": _macro(-1)}),
        [_geo_signal(-30)], _geo_news(),
    )
    assert memory.family_scores.get("geo_policy", 0) < broad.family_scores.get("geo_policy", 0), (memory, broad)
    assert memory.family_scores.get("overnight", 0) < -20, memory
    assert memory.score < broad.score, (memory, broad)


def test_geo_conflict_is_downweighted_not_blindly_applied():
    confirmed = _quantum(
        _frame(symbol="2408.TW", name="南亞科", context={"macro": _macro(-1)}),
        [_geo_signal(-30)], _geo_news(),
    )
    contradicted = _quantum(
        _frame(symbol="2408.TW", name="南亞科", context={"macro": _macro(+1)}),
        [_geo_signal(-30)], _geo_news(),
    )
    assert abs(contradicted.family_scores["geo_policy"]) < abs(confirmed.family_scores["geo_policy"]), (confirmed, contradicted)
    assert any("海外反向" in reason for reason in contradicted.reasons), contradicted
    # The model may stay directional when trend and overseas markets agree, but
    # the geopolitical input itself must be sharply downweighted.


def test_correlated_proxies_are_one_family_not_double_counted():
    one = {"accepted": True, "source": "UNIT", "sox": 2.0, "nq": 1.0, "as_of": {"sox": REF, "nq": REF}}
    duplicates = {"accepted": True, "source": "UNIT", "sox": 2.0, "smh": 2.0, "nq": 1.0, "qqq": 1.0, "as_of": {"sox": REF, "smh": REF, "nq": REF, "qqq": REF}}
    a = _quantum(_frame(symbol="NVDA", name="NVIDIA Semiconductor", market="US", context={"macro": one}))
    b = _quantum(_frame(symbol="NVDA", name="NVIDIA Semiconductor", market="US", context={"macro": duplicates}))
    assert abs(a.family_scores["overnight"] - b.family_scores["overnight"]) < 0.01, (a, b)


def test_stale_proxy_is_ignored():
    stale_macro = {
        "accepted": True, "source": "UNIT", "sox": 3.0, "nq": 2.0,
        "as_of": {"sox": "2026-07-01", "nq": "2026-07-01"},
    }
    result = _quantum(_frame(symbol="NVDA", name="NVIDIA Semiconductor", market="US", context={"macro": stale_macro}))
    assert "overnight" not in result.family_scores, result


def test_right_evidence_changes_left_tactical_card():
    margin = {
        "accepted": True, "source": "FinMind_MARGIN", "date": REF,
        "margin": 5000, "margin_3": 13000, "margin_5": 22000,
        "margin_10": 39000, "margin_streak": "連增5天",
    }
    frame = _frame(symbol="2408.TW", name="南亞科", rising=False, context={"margin": margin})
    forecast = orchestrate(frame)
    assert forecast.decision_card["低接第二批"] == "暫停", forecast.decision_card
    assert "融資" in forecast.decision_card["主訊息"], forecast.decision_card
    assert forecast.decision_card.get("_quantum_overlay", {}).get("pause_second") is True


if __name__ == "__main__":
    tests = [
        test_tw_monthly_revenue_only_two_trading_sessions,
        test_us_earnings_guidance_only_48_hours,
        test_margin_streak_is_context_aware,
        test_memory_geo_risk_uses_night_sox_nasdaq_transmission,
        test_geo_conflict_is_downweighted_not_blindly_applied,
        test_correlated_proxies_are_one_family_not_double_counted,
        test_stale_proxy_is_ignored,
        test_right_evidence_changes_left_tactical_card,
    ]
    for test in tests:
        test()
        print("OK", test.__name__)
    print("QUANTUM ENTANGLEMENT TESTS OK")
