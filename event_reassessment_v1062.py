# -*- coding: utf-8 -*-
"""Attach Market Shock L0-L5 to the established five-minute event watcher."""
from __future__ import annotations

from typing import Any, Dict, Mapping

import event_reassessment as _legacy
from market_shock_indicator import assess_market_shock

_LEGACY_CLASSIFY = _legacy.classify_event
_LEGACY_ASSESS_DELTA = _legacy.assess_event_delta
_LEGACY_DISPLAY = _legacy.event_watch_display


def classify_event_v1062(item: Any) -> Dict[str, Any]:
    row = dict(_LEGACY_CLASSIFY(item) or {})
    shock = assess_market_shock(item)
    shock_level = int(shock.get("level") or 0)
    severity_floor = 4 if shock_level >= 4 else 3 if shock_level == 3 else 2 if shock_level == 2 else 0
    if severity_floor:
        row["severity"] = max(int(row.get("severity") or 0), severity_floor)

    shock_path = "→".join(shock.get("transmission") or [])
    shock_drivers = "+".join(shock.get("drivers") or [])
    if shock_level >= 2:
        row["reason"] = (
            f"市場衝擊 L{shock_level} {shock.get('label')} {float(shock.get('score') or 0):.0f}/100"
            f"｜{shock_drivers or '跨市場事件'}｜{row.get('reason') or ''}"
        ).strip("｜")
        if shock_path:
            original_path = str(row.get("transmission") or "")
            row["transmission"] = shock_path if not original_path else f"{shock_path}｜{original_path}"
        row["confidence"] = max(float(row.get("confidence") or 0.0), 0.90 if shock_level >= 4 else 0.82)

    row.update({
        "market_shock_level": shock_level,
        "market_shock_label": str(shock.get("label") or "觀察"),
        "market_shock_score": float(shock.get("score") or 0.0),
        "market_shock_depth": int(shock.get("depth") or 1),
        "market_shock_color": str(shock.get("color") or "green"),
        "market_shock_drivers": list(shock.get("drivers") or []),
        "market_shock_transmission": list(shock.get("transmission") or []),
        "price_veto": True,
    })
    return row


def assess_event_delta_v1062(*args, **kwargs) -> Dict[str, Any]:
    plan = dict(_LEGACY_ASSESS_DELTA(*args, **kwargs) or {})
    events = list(plan.get("events") or [])
    lead = events[0] if events else {}
    shock_level = int(lead.get("market_shock_level") or 0)
    shock_score = float(lead.get("market_shock_score") or 0.0)
    shock_label = str(lead.get("market_shock_label") or "觀察")
    shock_depth = int(lead.get("market_shock_depth") or 1)
    shock_drivers = list(lead.get("market_shock_drivers") or [])

    plan.update({
        "market_shock_level": shock_level,
        "market_shock_label": shock_label,
        "market_shock_score": shock_score,
        "market_shock_depth": shock_depth,
        "market_shock_drivers": shock_drivers,
        "market_shock_color": str(lead.get("market_shock_color") or "green"),
    })
    if shock_level >= 2 and plan.get("event_title"):
        plan["event_title"] = (
            f"市場衝擊 L{shock_level} {shock_label} {shock_score:.0f}/100｜"
            f"{str(plan.get('event_title') or '')}"
        )
    return plan


def event_watch_display_v1062(
    report: Mapping[str, Any] | None,
    *,
    notice: str = "",
    ticker: str = "",
    interval_label: str = "5m",
) -> Dict[str, str]:
    row = dict(report or {})
    shock_level = int(row.get("market_shock_level") or 0)
    shock_score = float(row.get("market_shock_score") or 0.0)
    shock_label = str(row.get("market_shock_label") or "觀察")
    base = dict(_LEGACY_DISPLAY(
        row,
        notice=notice,
        ticker=ticker,
        interval_label=interval_label,
    ) or {})
    if notice and shock_level >= 4:
        base["level"] = "error"
        base["text"] = (
            f"🚨 市場衝擊 L{shock_level}｜{shock_label} {shock_score:.0f}/100｜"
            f"{notice or row.get('event_title') or ''}"
        )
    elif notice and shock_level >= 2:
        base["level"] = "warning"
        base["text"] = (
            f"⚠️ 市場衝擊 L{shock_level}｜{shock_label} {shock_score:.0f}/100｜"
            f"{notice or row.get('event_title') or ''}"
        )
    return base


def install_event_reassessment_v1062() -> None:
    _legacy.classify_event = classify_event_v1062
    _legacy.assess_event_delta = assess_event_delta_v1062
    _legacy.event_watch_display = event_watch_display_v1062
