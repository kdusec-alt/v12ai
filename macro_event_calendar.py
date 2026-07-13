# -*- coding: utf-8 -*-
"""TINO RC4 official macro event calendar and pre-event risk decay.

This module is intentionally lightweight and network-free.  It centralises the
known Tier-1 US macro releases used by both Taiwan and US routes, converts the
release time from New York to Taipei with ``zoneinfo``, and treats an upcoming
event as *uncertainty/risk* rather than a bullish or bearish prediction.

Future schedule maintenance can be supplied without code changes through
``TINO_MACRO_EVENTS_JSON``.  The value is a JSON list of dictionaries containing
``code``, ``name``, ``datetime`` (ISO-8601), optional ``timezone`` and optional
``tier``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo


_TAIPEI = ZoneInfo("Asia/Taipei")
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MacroEvent:
    code: str
    name: str
    release_at: datetime
    tier: int = 1
    source: str = "OFFICIAL_2026_SCHEDULE"


# 2026 dates published by BLS / Federal Reserve.  Times are stored in the
# originating US Eastern timezone so daylight-saving conversion stays correct.
_BLS_0830_DATES: Dict[str, Sequence[str]] = {
    "CPI": (
        "2026-07-14", "2026-08-12", "2026-09-11",
        "2026-10-14", "2026-11-10", "2026-12-10",
    ),
    "PPI": (
        "2026-07-15", "2026-08-13", "2026-09-10",
        "2026-10-15", "2026-11-13", "2026-12-15",
    ),
    "NFP": (
        "2026-08-07", "2026-09-04", "2026-10-02",
        "2026-11-06", "2026-12-04",
    ),
}

# BEA Personal Income and Outlays release (includes PCE inflation data).
_BEA_0830_DATES: Dict[str, Sequence[str]] = {
    "PCE": (
        "2026-07-30", "2026-08-26", "2026-09-30",
        "2026-10-29", "2026-11-25", "2026-12-23",
    ),
}

# ISM reports are published at 10:00 ET.  These are Tier-2 because they can
# change rate/sector expectations but should not outrank CPI/FOMC/NFP/PCE.
_ISM_1000_DATES: Dict[str, Sequence[str]] = {
    "ISM_MFG": (
        "2026-08-03", "2026-09-01", "2026-10-01",
        "2026-11-02", "2026-12-01",
    ),
    "ISM_SVC": (
        "2026-08-05", "2026-09-03", "2026-10-05",
        "2026-11-04", "2026-12-03",
    ),
}

_FOMC_1400_DATES: Sequence[str] = (
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
)

_EVENT_NAMES = {
    "CPI": "CPI",
    "PPI": "PPI",
    "PCE": "PCE物價",
    "NFP": "NFP",
    "FOMC": "FOMC利率決議",
    "ISM_MFG": "ISM製造業",
    "ISM_SVC": "ISM服務業",
}

# Event sensitivity controls confidence/risk only before publication.
_EVENT_SENSITIVITY = {
    "CPI": 1.00,
    "FOMC": 1.00,
    "PCE": 0.92,
    "NFP": 0.90,
    "PPI": 0.78,
    "ISM_MFG": 0.60,
    "ISM_SVC": 0.55,
}


def _official_events() -> List[MacroEvent]:
    out: List[MacroEvent] = []
    for code, dates in _BLS_0830_DATES.items():
        for day in dates:
            dt = datetime.fromisoformat(f"{day}T08:30:00").replace(tzinfo=_NEW_YORK)
            out.append(MacroEvent(code, _EVENT_NAMES[code], dt, 1, "BLS_2026_SCHEDULE"))
    for code, dates in _BEA_0830_DATES.items():
        for day in dates:
            dt = datetime.fromisoformat(f"{day}T08:30:00").replace(tzinfo=_NEW_YORK)
            out.append(MacroEvent(code, _EVENT_NAMES[code], dt, 1, "BEA_2026_SCHEDULE"))
    for code, dates in _ISM_1000_DATES.items():
        for day in dates:
            dt = datetime.fromisoformat(f"{day}T10:00:00").replace(tzinfo=_NEW_YORK)
            out.append(MacroEvent(code, _EVENT_NAMES[code], dt, 2, "ISM_2026_SCHEDULE"))
    for day in _FOMC_1400_DATES:
        dt = datetime.fromisoformat(f"{day}T14:00:00").replace(tzinfo=_NEW_YORK)
        out.append(MacroEvent("FOMC", _EVENT_NAMES["FOMC"], dt, 1, "FED_2026_SCHEDULE"))
    return out


def _override_events() -> List[MacroEvent]:
    raw = str(os.environ.get("TINO_MACRO_EVENTS_JSON") or "").strip()
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except Exception:
        return []
    if not isinstance(rows, list):
        return []

    out: List[MacroEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "EVENT").upper().strip()
        name = str(row.get("name") or code).strip()
        raw_dt = str(row.get("datetime") or "").strip()
        if not raw_dt:
            continue
        try:
            parsed = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                tz_name = str(row.get("timezone") or "America/New_York")
                parsed = parsed.replace(tzinfo=ZoneInfo(tz_name))
            tier = max(1, int(row.get("tier") or 1))
            out.append(MacroEvent(code, name, parsed, tier, "TINO_MACRO_EVENTS_JSON"))
        except Exception:
            continue
    return out


def all_macro_events() -> List[MacroEvent]:
    """Return de-duplicated official and environment-supplied events."""
    merged: Dict[tuple[str, str], MacroEvent] = {}
    for event in [*_official_events(), *_override_events()]:
        key = (event.code, event.release_at.astimezone(_TAIPEI).isoformat())
        merged[key] = event
    return sorted(merged.values(), key=lambda item: item.release_at)


def _now_taipei(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(_TAIPEI)
    if now.tzinfo is None:
        return now.replace(tzinfo=_TAIPEI)
    return now.astimezone(_TAIPEI)


def _uncertainty_for_hours(hours: float, code: str) -> float:
    if hours < 0:
        return 0.0
    if hours <= 6:
        base = 0.22
    elif hours <= 12:
        base = 0.20
    elif hours <= 24:
        base = 0.16
    elif hours <= 48:
        base = 0.11
    elif hours <= 72:
        base = 0.07
    elif hours <= 120:
        base = 0.03
    else:
        base = 0.0
    return round(base * _EVENT_SENSITIVITY.get(str(code).upper(), 0.75), 4)


def upcoming_macro_events(
    now: datetime | None = None,
    *,
    limit: int = 4,
    horizon_days: int = 240,
) -> List[Dict[str, Any]]:
    reference = _now_taipei(now)
    horizon_h = max(1, int(horizon_days)) * 24
    rows: List[Dict[str, Any]] = []
    for event in all_macro_events():
        local_dt = event.release_at.astimezone(_TAIPEI)
        delta_h = (local_dt - reference).total_seconds() / 3600.0
        if delta_h < 0 or delta_h > horizon_h:
            continue
        rows.append({
            "code": event.code,
            "name": event.name,
            "tier": event.tier,
            "release_at": local_dt.isoformat(),
            "release_taipei": local_dt.strftime("%Y-%m-%d %H:%M"),
            "countdown_hours": round(delta_h, 2),
            "direction_score": 0.0,
            "uncertainty": _uncertainty_for_hours(delta_h, event.code),
            "source": event.source,
        })
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def _calendar_text(events: Sequence[Dict[str, Any]]) -> str:
    if not events:
        return "近期暫無已確認一級宏觀事件｜依官方日曆校準"
    parts: List[str] = []
    for index, row in enumerate(events[:3]):
        dt = str(row.get("release_taipei") or "")[5:16].replace("-", "/")
        hours = max(0, int(float(row.get("countdown_hours") or 0.0)))
        if index == 0:
            prefix = "下一個一級事件：" if int(row.get("tier") or 1) == 1 else "下一個宏觀事件："
        else:
            prefix = ""
        separator = "：" if index > 0 else " "
        parts.append(f"{prefix}{row.get('name')}{separator}{dt} 台灣（倒數{hours}小時）")
    return "｜".join(parts)


def build_macro_context(
    price_date: str = "",
    *,
    now: datetime | None = None,
    event_score: float = 0.0,
    eps: str = "",
    eps_tags: str = "",
) -> Dict[str, Any]:
    """Build one shared macro-calendar block for TW and US routes.

    Before publication, an event carries zero direction and only increases
    uncertainty / lowers confidence and position size.  Directional impact must
    come from an observed surprise and cross-market confirmation after release.
    """
    reference = _now_taipei(now)
    events = upcoming_macro_events(reference, limit=4)
    nearest = dict(events[0]) if events else {}
    uncertainty = float(nearest.get("uncertainty") or 0.0)
    hours = float(nearest.get("countdown_hours") or 99999.0)
    if hours <= 48:
        strength = "高"
    elif hours <= 120:
        strength = "中高"
    else:
        strength = "中"

    risk_score = round(uncertainty * 100.0, 1)
    return {
        "accepted": True,
        "source": "TINO_RC4_OFFICIAL_MACRO_CALENDAR",
        "date": str(price_date or reference.date().isoformat()),
        "calendar_generated_at": reference.isoformat(),
        "event_score": float(event_score or 0.0),
        "strength": strength,
        "eps": eps or "EPS/營收事件看深度分析",
        "eps_tags": eps_tags or "宏觀事件觀察",
        "calendar": _calendar_text(events),
        "events": events,
        "nearest_event": nearest,
        "event_uncertainty": round(uncertainty, 4),
        "event_risk": risk_score,
        "confidence_penalty": round(uncertainty * 28.0, 1),
        "position_scale": round(max(0.45, 1.0 - uncertainty * 1.8), 3),
        "pre_event_direction": 0.0,
        "event_policy": "公布前只調整風險/信心/部位；公布後依預期差與跨市場確認方向",
    }


def macro_calendar_guard_text(now: datetime | None = None) -> str:
    try:
        return str(build_macro_context(now=now).get("calendar") or "宏觀事件以官方日曆確認")
    except Exception:
        return "宏觀事件以官方日曆確認"


def event_risk_from_context(
    macro: Mapping[str, Any] | None,
    *,
    earnings_days: float | None = None,
) -> Tuple[float, List[str]]:
    """Read structured countdowns (or legacy calendar text) into uncertainty."""
    penalty = 0.0
    factors: List[str] = []
    if isinstance(macro, Mapping):
        events = macro.get("events")
        if isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
            for row in events:
                if not isinstance(row, Mapping):
                    continue
                try:
                    hours = float(row.get("countdown_hours"))
                except Exception:
                    continue
                if hours < 0:
                    continue
                try:
                    uncertainty = float(row.get("uncertainty"))
                except Exception:
                    uncertainty = _uncertainty_for_hours(hours, str(row.get("code") or ""))
                penalty = max(penalty, uncertainty)
                if hours <= 120:
                    factors.append(f"{row.get('name') or row.get('code') or '宏觀事件'} T-{hours:.0f}h")
        if not factors:
            text = str(macro.get("calendar") or "")
            matches = re.findall(r"(CPI|PPI|PCE|NFP|FOMC[^｜：]*|ISM(?:製造業|服務業)?)[^｜]*倒數\s*(\d+)\s*小時", text, flags=re.I)
            if not matches:
                matches = [("宏觀事件", value) for value in re.findall(r"倒數\s*(\d+)\s*小時", text)]
            for name, raw_hours in matches:
                hours = float(raw_hours)
                value = _uncertainty_for_hours(hours, str(name).split()[0])
                penalty = max(penalty, value)
                if hours <= 120:
                    factors.append(f"{str(name).strip()} T-{hours:.0f}h")
    if earnings_days is not None:
        if 0 <= earnings_days <= 1:
            penalty = max(penalty, 0.18)
            factors.append(f"財報 T-{earnings_days:.0f}d")
        elif earnings_days <= 3:
            penalty = max(penalty, 0.09)
            factors.append(f"財報 T-{earnings_days:.0f}d")
    unique: List[str] = []
    for item in factors:
        if item not in unique:
            unique.append(item)
    return max(0.0, min(0.30, penalty)), unique[:3]
