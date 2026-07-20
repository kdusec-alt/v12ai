# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from event_lifecycle import (
    MAX_EVENT_ROWS,
    acknowledge_global_event,
    get_global_event_view,
    global_event_display,
    update_global_event_state,
)


def _event(
    fingerprint: str,
    title: str,
    *,
    severity: int = 3,
    category: str = "geo_policy_escalation",
    risk_sign: int = -1,
):
    return {
        "fingerprint": fingerprint,
        "title": title,
        "source": "test",
        "severity": severity,
        "category": category,
        "risk_sign": risk_sign,
        "reason": "test material event",
    }


def _plan(*events):
    return {"events": list(events), "needs_reassessment": bool(events)}


def test_only_yellow_and_red_events_are_recorded(tmp_path):
    path = tmp_path / "events.json"
    view = update_global_event_state(
        _plan(_event("info-1", "一般新聞", severity=1, category="material_headline")),
        ticker="MRVL",
        market="US",
        now=1000,
        path=path,
    )
    assert view["dominant"] is None
    assert view["recent"] == []
    assert not path.exists()


def test_global_red_alert_survives_ticker_switch(tmp_path):
    path = tmp_path / "events.json"
    first = update_global_event_state(
        _plan(_event("iran-1", "美國攻擊伊朗，油價跳升")),
        ticker="6770.TW",
        market="TW",
        now=1000,
        path=path,
    )
    second = update_global_event_state(
        {}, ticker="2454.TW", market="TW", now=1100, path=path
    )
    assert first["dominant"]["event_id"] == second["dominant"]["event_id"]
    assert second["dominant"]["scope"] == "global"
    assert "2454.TW" not in second["dominant"]["source_tickers"]
    assert global_event_display(second["dominant"])["level"] == "error"


def test_same_story_is_merged_and_reinforcement_reopens_ack(tmp_path):
    path = tmp_path / "events.json"
    first = update_global_event_state(
        _plan(_event("iran-1", "伊朗戰爭風險升高")), ticker="MU", now=1000, path=path
    )
    event_id = first["dominant"]["event_id"]
    assert acknowledge_global_event(event_id, now=1010, path=path) is True
    assert get_global_event_view(now=1020, path=path)["dominant"] is None

    repeated = update_global_event_state(
        _plan(_event("iran-1", "伊朗戰爭風險升高")), ticker="SMR", now=1030, path=path
    )
    assert repeated["dominant"] is None
    assert len(repeated["recent"]) == 1

    reinforced = update_global_event_state(
        _plan(_event("iran-2", "伊朗飛彈攻擊造成油價再升")), ticker="SMR", now=1040, path=path
    )
    assert reinforced["dominant"]["event_id"] == event_id
    assert reinforced["dominant"]["reinforcement_count"] == 2
    assert reinforced["dominant"]["acknowledged"] is False
    assert len(reinforced["recent"]) == 1


def test_counter_news_downgrades_same_event_instead_of_replacing_history(tmp_path):
    path = tmp_path / "events.json"
    red = update_global_event_state(
        _plan(_event("iran-war", "美伊戰爭升級")), ticker="MU", now=1000, path=path
    )
    event_id = red["dominant"]["event_id"]
    yellow = update_global_event_state(
        _plan(
            _event(
                "iran-peace",
                "美伊達成停火協議",
                severity=2,
                category="geo_deescalation",
                risk_sign=1,
            )
        ),
        ticker="2454.TW",
        now=1200,
        path=path,
    )
    assert yellow["dominant"]["event_id"] == event_id
    assert yellow["dominant"]["status"] == "cooling"
    assert yellow["dominant"]["severity"] == 2
    assert len(yellow["recent"]) == 1
    assert global_event_display(yellow["dominant"])["level"] == "warning"


def test_unrelated_global_events_can_coexist(tmp_path):
    path = tmp_path / "events.json"
    update_global_event_state(
        _plan(_event("iran-1", "伊朗戰爭升級")), ticker="MU", now=1000, path=path
    )
    view = update_global_event_state(
        _plan(_event("taiwan-1", "台海軍演與封鎖風險升高")),
        ticker="2454.TW",
        now=1100,
        path=path,
    )
    assert view["active_count"] == 2
    assert len(view["recent"]) == 2
    assert {row["event_key"] for row in view["recent"]} == {
        "global:geopolitical:iran_middle_east",
        "global:geopolitical:taiwan_strait",
    }


def test_company_event_remains_ticker_scoped(tmp_path):
    path = tmp_path / "events.json"
    view = update_global_event_state(
        _plan(
            _event(
                "mrvl-guide",
                "Marvell cuts guidance",
                category="company_hard_negative",
            )
        ),
        ticker="MRVL",
        market="US",
        now=1000,
        path=path,
    )
    assert view["dominant"]["scope"] == "ticker"
    assert view["dominant"]["event_key"].startswith("ticker:MRVL:")


def test_quiet_time_causes_conservative_downgrade_then_resolution(tmp_path):
    path = tmp_path / "events.json"
    update_global_event_state(
        _plan(_event("shock-1", "流動性危機引發市場恐慌", severity=4, category="systemic_market_shock")),
        ticker="SPY",
        now=1000,
        path=path,
    )
    yellow = get_global_event_view(now=1000 + 13 * 3600, path=path)
    assert yellow["dominant"]["status"] == "cooling"
    assert yellow["dominant"]["severity"] == 2
    resolved = get_global_event_view(now=1000 + 49 * 3600, path=path)
    assert resolved["dominant"] is None
    assert resolved["recent"][0]["status"] == "resolved"


def test_state_is_bounded_and_resolved_rows_expire(tmp_path):
    path = tmp_path / "events.json"
    for index in range(MAX_EVENT_ROWS + 7):
        update_global_event_state(
            _plan(
                _event(
                    f"company-{index}",
                    f"Company {index} cuts guidance",
                    severity=2,
                    category="company_forward_update",
                )
            ),
            ticker=f"T{index}",
            now=1000 + index,
            path=path,
        )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored["events"]) == MAX_EVENT_ROWS

    # All company yellow rows resolve after 36h, and resolved rows are
    # removed three days later instead of growing an operational archive.
    get_global_event_view(now=1000 + 37 * 3600, path=path)
    final = get_global_event_view(now=1000 + 37 * 3600 + 3 * 24 * 3600 + 1, path=path)
    assert final["recent"] == []
