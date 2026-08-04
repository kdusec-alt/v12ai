# -*- coding: utf-8 -*-
"""V1097 Conference Intelligence Engine (CIE).

Industry conferences are forward-looking context, not news and not macro
releases.  This module keeps their schedule, lifecycle and ticker exposure
separate so a pre-event appearance can never become a bullish vote by itself.
Verified sessions are refreshed from official sites; deployment JSON remains
an emergency override and additive source.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
import re
from typing import Any, Dict, List, Mapping, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

_TAIPEI = ZoneInfo("Asia/Taipei")

CONFERENCE_ALIASES = {
    "FLASH MEMORY SUMMIT": "FMS",
    "FUTURE OF MEMORY AND STORAGE": "FMS",
    "FUTURE OF MEMORY & STORAGE": "FMS",
    "NVIDIA GTC": "GTC",
    "OCP GLOBAL SUMMIT": "OCP",
    "OCP SUMMIT": "OCP",
    "SUPERCOMPUTING": "SC",
}

CONFERENCE_TIERS = {
    "FMS": 1, "GTC": 1, "COMPUTEX": 1, "OFC": 1,
    "OCP": 1, "HOT CHIPS": 1, "SC": 2,
    "AMD ADVANCING AI": 2, "INTEL INNOVATION": 2,
}

OFFICIAL_HOSTS = {
    "FMS": ("terrapinn.com", "fmsnow.com"),
    "GTC": ("nvidia.com",),
    "COMPUTEX": ("computextaipei.com.tw",),
    "OFC": ("ofcconference.org",),
    "OCP": ("opencompute.org",),
    "HOT CHIPS": ("hotchips.org",),
    "SC": ("supercomputing.org",),
    "AMD ADVANCING AI": ("amd.com",),
    "INTEL INNOVATION": ("intel.com",),
}

COMPANY_DISPLAY_NAMES = {
    "AMD": "AMD", "FADU": "FADU", "FMS": "FMS", "IBM": "IBM",
    "INTEL": "Intel", "MARVELL": "Marvell", "MICRON": "Micron",
    "NVIDIA": "NVIDIA", "OCP": "OCP", "SK HYNIX": "SK hynix",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def canonical_company(value: Any) -> str:
    company = _clean(value) or "Conference"
    return COMPANY_DISPLAY_NAMES.get(company.upper(), company)


def canonical_conference(value: Any) -> str:
    name = _clean(value).upper()
    return CONFERENCE_ALIASES.get(name, name)


def _symbols(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = re.split(r"[,，、|\s]+", value)
    if not isinstance(value, Sequence):
        return ()
    out: List[str] = []
    for item in value:
        symbol = _clean(item).upper()
        if symbol and symbol not in out:
            out.append(symbol)
    return tuple(out)


def _official_source(conference: str, url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == allowed or host.endswith("." + allowed)
               for allowed in OFFICIAL_HOSTS.get(conference, ()))


@dataclass(frozen=True)
class ConferenceSession:
    event_id: str
    session_id: str
    conference: str
    company: str
    title: str
    start_at: datetime
    end_at: datetime | None
    technologies: tuple[str, ...]
    direct_tickers: tuple[str, ...]
    supply_chain_tickers: tuple[str, ...]
    tier: int
    importance: int
    source_url: str
    source_tier: str
    expected_direction: str = "NEUTRAL_PENDING"
    actual_result: str = "PENDING"


def _parse_datetime(value: Any, timezone_name: Any) -> datetime:
    parsed = datetime.fromisoformat(_clean(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(_clean(timezone_name) or "Asia/Taipei"))
    return parsed


def _row_to_session(row: Mapping[str, Any]) -> ConferenceSession | None:
    try:
        conference = canonical_conference(row.get("conference"))
        start = _parse_datetime(row.get("start_at") or row.get("datetime"), row.get("timezone"))
        end_raw = row.get("end_at")
        end = _parse_datetime(end_raw, row.get("timezone")) if end_raw else None
        source_url = _clean(row.get("source_url"))
        official = _official_source(conference, source_url)
        if not official and _clean(row.get("source_tier")).upper() == "OFFICIAL":
            return None
        event_id = _clean(row.get("event_id")) or f"{conference}_{start.year}"
        company = canonical_company(row.get("company"))
        title = _clean(row.get("title")) or "議程待確認"
        session_id = _clean(row.get("session_id")) or re.sub(
            r"[^A-Z0-9]+", "_", f"{event_id}_{company}_{start.isoformat()}".upper()
        ).strip("_")
        return ConferenceSession(
            event_id=event_id,
            session_id=session_id,
            conference=conference,
            company=company,
            title=title,
            start_at=start,
            end_at=end,
            technologies=tuple(_clean(x) for x in (row.get("technologies") or []) if _clean(x)),
            direct_tickers=_symbols(row.get("direct_tickers") or row.get("affected_tickers")),
            supply_chain_tickers=_symbols(row.get("supply_chain_tickers")),
            tier=max(1, int(row.get("tier") or CONFERENCE_TIERS.get(conference, 3))),
            importance=max(1, min(5, int(row.get("importance") or (5 if CONFERENCE_TIERS.get(conference) == 1 else 4)))),
            source_url=source_url,
            source_tier="OFFICIAL" if official else "SECONDARY",
        )
    except Exception:
        return None


def all_conference_sessions() -> List[ConferenceSession]:
    """Load, validate and de-duplicate official and deployment sessions."""
    raw = _clean(os.environ.get("TINO_CIE_EVENTS_JSON"))
    rows: List[Any] = []
    if raw:
        try:
            supplied = json.loads(raw)
            rows.extend(supplied if isinstance(supplied, list) else [])
        except Exception:
            pass
    if _clean(os.environ.get("TINO_CIE_AUTO_FETCH", "1")).lower() not in {"0", "false", "off"}:
        try:
            from conference_sources_v1097 import fetch_official_sessions
            rows.extend(fetch_official_sessions())
        except Exception:
            pass
    if not isinstance(rows, list):
        return []
    merged: Dict[str, ConferenceSession] = {}
    for row in rows:
        session = _row_to_session(row) if isinstance(row, Mapping) else None
        if session:
            old = merged.get(session.session_id)
            if old is None or (old.source_tier != "OFFICIAL" and session.source_tier == "OFFICIAL"):
                merged[session.session_id] = session
    return sorted(merged.values(), key=lambda item: item.start_at)


def _lifecycle(session: ConferenceSession, now: datetime) -> str:
    start = session.start_at.astimezone(_TAIPEI)
    end = (session.end_at or session.start_at).astimezone(_TAIPEI)
    hours = (start - now).total_seconds() / 3600.0
    if now <= end and now >= start:
        return "LIVE"
    if hours > 72:
        return "AGENDA_PUBLISHED"
    if hours > 1:
        return "PRE_EVENT"
    if hours >= 0:
        return "STARTING_SOON"
    if session.actual_result == "PENDING":
        return "AWAITING_RESULT"
    return "POST_EVENT"


def conference_calendar(now: datetime | None = None, *, horizon_days: int = 180, limit: int = 24) -> Dict[str, Any]:
    reference = now or datetime.now(_TAIPEI)
    reference = reference.replace(tzinfo=_TAIPEI) if reference.tzinfo is None else reference.astimezone(_TAIPEI)
    rows: List[Dict[str, Any]] = []
    for session in all_conference_sessions():
        start = session.start_at.astimezone(_TAIPEI)
        hours = (start - reference).total_seconds() / 3600.0
        lifecycle = _lifecycle(session, reference)
        if hours > horizon_days * 24 or lifecycle in {"AWAITING_RESULT", "POST_EVENT"} and hours < -72:
            continue
        row = asdict(session)
        row.update({
            "start_at": start.isoformat(),
            "end_at": session.end_at.astimezone(_TAIPEI).isoformat() if session.end_at else "",
            "start_taipei": start.strftime("%Y-%m-%d %H:%M"),
            "countdown_hours": round(hours, 2),
            "lifecycle": lifecycle,
            "stars": "★" * session.importance + "☆" * (5 - session.importance),
            "direction_score": 0.0 if lifecycle in {"AGENDA_PUBLISHED", "PRE_EVENT", "STARTING_SOON", "LIVE"} else None,
            "event_policy": "會前僅標示題材曝險；會後依官方結果、預期差與價格反應仲裁",
        })
        rows.append(row)
        if len(rows) >= max(1, int(limit)):
            break
    tier1 = sum(1 for row in rows if int(row.get("tier") or 3) == 1)
    return {
        "schema": "TINO_CIE_V1097",
        "generated_at": reference.isoformat(),
        "sessions": rows,
        "tier1_count": tier1,
        "direction_vote": 0.0,
        "policy": "INDUSTRY_CONTEXT_ONLY_BEFORE_VERIFIED_RESULT",
    }


def conference_watch_display(now: datetime | None = None) -> Dict[str, Any]:
    calendar = conference_calendar(now)
    try:
        from conference_sources_v1097 import conference_source_health
        calendar["source_health"] = conference_source_health()
    except Exception:
        calendar["source_health"] = {"status": "UNAVAILABLE", "session_count": 0, "sources": {}}
    sessions = list(calendar.get("sessions") or [])
    if not sessions:
        return {"level": "caption", "text": "🟣 AI產業日曆｜近期無已載入的官方議程", **calendar}
    nearest = sessions[0]
    label = {"LIVE": "進行中", "STARTING_SOON": "即將開始", "PRE_EVENT": "會前預警", "AGENDA_PUBLISHED": "議程已公布", "AWAITING_RESULT": "等待會後確認"}.get(nearest["lifecycle"], nearest["lifecycle"])
    hours = float(nearest.get("countdown_hours") or 0.0)
    if nearest["lifecycle"] == "LIVE":
        timing = "今日進行中"
    elif hours < 24:
        timing = f"{max(0, round(hours))}小時後"
    else:
        timing = f"{max(1, int(hours // 24))}天後"
    text = (
        f"🟣 INDUSTRY TIER-{nearest['tier']}｜{nearest['conference']}｜{label}｜"
        f"下一場 {nearest['company']}｜{timing}｜{nearest['start_taipei'][5:]} 台北｜"
        f"{nearest['stars']}｜會前不投方向票"
    )
    return {"level": "industry", "text": text, **calendar}
