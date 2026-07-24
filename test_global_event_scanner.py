# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import global_event_scanner as scanner
from event_reassessment import assess_event_delta, classify_event, event_fingerprint
from models import NewsItem

_TW = ZoneInfo("Asia/Taipei")


def test_oil_quote_builds_market_wide_transmission(monkeypatch):
    monkeypatch.setattr(scanner, "_quote_change", lambda symbol: {
        "symbol": symbol,
        "price": 100.0,
        "previous": 96.0,
        "pct": 4.1667 if symbol == "CL=F" else 3.25,
    })
    item = scanner._oil_event(datetime(2026, 7, 24, 21, 20, tzinfo=_TW))
    assert item is not None
    assert "family=energy" in item.tag
    assert "severity=3" in item.tag
    assert "通膨預期" in item.title and "半導體" in item.title
    event = classify_event(item)
    assert event["category"] == "geo_energy_escalation"
    assert event["severity"] == 3
    assert "US10Y" in event["affected_assets"]


def test_pmi_seed_is_visible_before_release():
    item = scanner._pmi_seed(datetime(2026, 7, 24, 21, 23, tzinfo=_TW))
    assert item is not None
    assert "21:45" in item.title
    assert "54.5" in item.title
    event = classify_event(item)
    assert event["category"] == "macro_release_pending"
    assert event["risk_sign"] == 0
    assert event["severity"] == 2


def test_tariff_seed_uses_precise_total_tariff_wording():
    item = scanner._tariff_seed(datetime(2026, 7, 24, 12, 0, tzinfo=_TW))
    assert item is not None
    assert "合計至10%" in item.title
    assert "並非所有商品一律額外加10%" in item.title
    event = classify_event(item)
    assert event["category"] == "geo_trade_controls"
    assert event["severity"] == 3
    assert "毛利率" in event["transmission"]


def test_global_fingerprint_is_stable_inside_same_severity_bucket():
    left = NewsItem(
        "core", "2026-07-24T21:20:00+08:00", -0.18,
        "global_event_core|family=energy|severity=3|eventid=oil_supply_shock_20260724",
        "WTI +3.1%", "",
    )
    right = NewsItem(
        "core", "2026-07-24T21:25:00+08:00", -0.18,
        "global_event_core|family=energy|severity=3|eventid=oil_supply_shock_20260724",
        "WTI +4.0%", "",
    )
    escalated = NewsItem(
        "core", "2026-07-24T21:30:00+08:00", -0.24,
        "global_event_core|family=energy|severity=4|eventid=oil_supply_shock_20260724",
        "WTI +6.2%", "",
    )
    assert event_fingerprint(left) == event_fingerprint(right)
    assert event_fingerprint(left) != event_fingerprint(escalated)


def test_five_minute_delta_reassesses_on_ticker_independent_oil_event():
    item = NewsItem(
        "core", "2026-07-24T21:20:00+08:00", -0.18,
        "global_event_core|family=energy|severity=3|eventid=oil_supply_shock_20260724|daily_headline|policy_geo",
        "Global Event Core｜中東/美伊供應風險推升油價｜WTI +4.0%", "",
    )
    result = assess_event_delta(
        [], [item], ticker="SKHY", market="US",
        not_before_epoch=datetime(2026, 7, 24, 21, 0, tzinfo=_TW).timestamp(),
        now=datetime(2026, 7, 24, 21, 25, tzinfo=_TW),
    )
    assert result["needs_reassessment"] is True
    assert result["event_category"] == "geo_energy_escalation"
    assert result["event_family"] == "energy"
    assert "油價↑" in result["event_transmission"]
