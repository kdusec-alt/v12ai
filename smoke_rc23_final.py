# -*- coding: utf-8 -*-
from __future__ import annotations

from models import DataTruth, PriceFrame, TickerInfo
from trend_engine import build_trend_snapshot, trend_tag, trend_radar_line
from orchestrator import _institution_flow_momentum


def _pf(closes, last=None, status="after_close"):
    t = TickerInfo("UNIT", "UNIT.TW", "Unit", "TW", "stock")
    last = float(last if last is not None else closes[-1])
    return PriceFrame(
        ticker=t,
        truth=DataTruth("unit", "2026-07-09", False, True, "unit"),
        open=last, high=last, low=last, last=last, previous_close=float(closes[-2]),
        volume=1000, vwap=last, atr14=1.0,
        recent_closes=list(map(float, closes)), recent_highs=list(map(float, closes)), recent_lows=list(map(float, closes)), recent_volumes=[1000]*len(closes),
        price_date="2026-07-09", market_status=status, context={"price_meta": {}}
    )


def test_20d_return_close_to_close():
    closes = list(range(100, 160))
    pf = _pf(closes)
    snap = build_trend_snapshot(pf)
    expected = (closes[-1] / closes[-20] - 1.0) * 100.0
    assert abs(snap.ret_20d - expected) < 1e-9, (snap.ret_20d, expected)


def test_intraday_streak_excludes_live_last():
    closes = [100, 101, 102, 103, 95]
    pf = _pf(closes, last=95, status="intraday")
    snap = build_trend_snapshot(pf)
    assert snap.direction == "連漲", snap
    assert snap.streak_days == 3, snap
    assert "盤中參考" in trend_tag(pf)


def test_ma_alerts_present():
    closes = [100 + i * 0.1 for i in range(60)]
    pf = _pf(closes)
    line = trend_radar_line(pf)
    assert "20D" in line and "月線MA20" in line and "季線MA60" in line, line


def test_institution_momentum():
    inst = {
        "accepted": True, "source": "FinMind_Institutional",
        "foreign": -100, "foreign_10": -12093, "foreign_streak": "連賣7天",
        "trust": 1000, "trust_10": 15632, "trust_streak": "連買10天",
        "dealer": -5, "dealer_10": -134, "dealer_streak": "連賣7天",
    }
    txt = _institution_flow_momentum(inst)
    assert "Institution Flow Momentum" in txt
    assert "投信" in txt and "持續布局" in txt
    assert "外資" in txt and "持續調節" in txt


if __name__ == "__main__":
    for t in [test_20d_return_close_to_close, test_intraday_streak_excludes_live_last, test_ma_alerts_present, test_institution_momentum]:
        t()
        print("OK", t.__name__)
    print("RC23 FINAL TARGETED TESTS OK")
