# -*- coding: utf-8 -*-
"""V13 Macro Result + Market Reaction orchestration.

Pipeline
--------
calendar -> official result -> consensus gap -> semantic verdict -> post-event
market reaction -> append-only research log.

The engine is research-only and cannot mutate V12 Direction, T1, Confidence,
or the AI decision card.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

try:
    from macro_event_calendar import all_macro_events
except Exception:  # pragma: no cover
    def all_macro_events():
        return []

from .macro_official_sources import (
    _extract_expectation_words,
    _extract_forecast_from_news,
    _latest_fomc_statement,
    _news_texts,
    _official_bls_result,
    _override_for,
    _number,
    parse_bls_cpi_text,
    parse_bls_ppi_text,
)
from .macro_reaction_engine import (
    _build_summary,
    _expectation_state,
    _inflation_event_score,
    _market_confirmation,
    _semantic_inflation,
    _surprise_map,
    classify_market_reaction,
)

_TAIPEI = ZoneInfo("Asia/Taipei")
_SUPPORTED_CODES = {"CPI", "PPI", "FOMC"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
MACRO_EVENT_SCHEMA_VERSION = "V13_MACRO_EVENT_V1"
MACRO_EVENT_ENGINE_VERSION = "V13_RC04_MACRO_1.1.0"
_ASSESS_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.RLock()


@dataclass(frozen=True)
class MacroEventResult:
    event_id: str
    schema_version: str
    engine_version: str
    event_code: str
    event_name: str
    release_at_tw: str
    observed_at_tw: str
    official_confirmed: bool
    source: str
    source_url: str
    period: str = ""
    actual: Dict[str, Any] = field(default_factory=dict)
    forecast: Dict[str, Any] = field(default_factory=dict)
    previous: Dict[str, Any] = field(default_factory=dict)
    surprise: Dict[str, Any] = field(default_factory=dict)
    expectation_state: str = "unknown"
    semantic_verdict: str = ""
    risk_verdict: str = ""
    event_score: float = 0.0
    score_basis: str = "none"
    event_confidence: float = 0.0
    reaction_state: str = "pending"
    reaction_score: float = 0.0
    reaction_summary: str = ""
    market_confirmation: Dict[str, Any] = field(default_factory=dict)
    summary_line: str = ""
    quality_flags: List[str] = field(default_factory=list)
    research_only: bool = True
    decision_influence: bool = False
    calc_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)



def _macro_enabled() -> bool:
    research = str(os.environ.get("TINO_V13_RESEARCH", "1") or "1").strip().lower()
    macro = str(os.environ.get("TINO_V13_MACRO_EVENT", "1") or "1").strip().lower()
    return research not in _FALSE_VALUES and macro not in _FALSE_VALUES


def _now_tw(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(_TAIPEI)
    if now.tzinfo is None:
        return now.replace(tzinfo=_TAIPEI)
    return now.astimezone(_TAIPEI)


def _recent_supported_event(now: datetime, macro: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    if isinstance(macro, Mapping):
        explicit = macro.get("latest_released_event")
        if isinstance(explicit, Mapping):
            code = str(explicit.get("code") or "").upper()
            if code in _SUPPORTED_CODES:
                return dict(explicit)

    best: Tuple[float, Dict[str, Any]] | None = None
    for event in all_macro_events():
        code = str(getattr(event, "code", "") or "").upper()
        release = getattr(event, "release_at", None)
        if code not in _SUPPORTED_CODES or not isinstance(release, datetime):
            continue
        local = release.astimezone(_TAIPEI)
        age_h = (now - local).total_seconds() / 3600.0
        if age_h < -0.25 or age_h > 48.0:
            continue
        candidate = {
            "code": code,
            "name": str(getattr(event, "name", code) or code),
            "release_at": local.isoformat(),
            "release_date": local.date().isoformat(),
            "age_hours": round(max(0.0, age_h), 2),
        }
        if best is None or age_h < best[0]:
            best = (age_h, candidate)
    return best[1] if best else {}


def _merge_result(base: Mapping[str, Any], override: Mapping[str, Any], news_forecast: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(base or {})
    for section in ("actual", "forecast", "previous"):
        merged = dict(out.get(section) or {})
        if section == "forecast":
            for key, value in dict(news_forecast or {}).items():
                if value is not None:
                    merged[key] = value
        override_section = override.get(section) if isinstance(override.get(section), Mapping) else {}
        for key, value in dict(override_section or {}).items():
            if value is not None:
                merged[str(key)] = value
        out[section] = merged
    for key in ("period", "source", "source_url", "official_confirmed"):
        if override.get(key) not in (None, ""):
            out[key] = override.get(key)
    return out


def _event_id(code: str, release_date: str, result: Mapping[str, Any]) -> str:
    # A new log row is created when the official/consensus payload or the
    # reaction class changes, not on every small price tick.
    stable = json.dumps(
        {
            "code": code,
            "release_date": release_date,
            "actual": result.get("actual"),
            "forecast": result.get("forecast"),
            "reaction_state": result.get("reaction_state"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]


def _fomc_result(
    actual: Mapping[str, Any],
    override: Mapping[str, Any],
    texts: Sequence[str],
    official_confirmed: bool,
) -> Tuple[float, str, str, str, float]:
    action = str(actual.get("action") or "unknown")
    expected_action = str(override.get("expected_action") or "unknown")
    if action == "cut":
        score, semantic = 0.35, "Fed降息"
        risk = "降息未必等於利多，需辨識經濟惡化與利多出盡"
    elif action == "hike":
        score, semantic = -0.55, "Fed升息"
        risk = "資金成本上升，成長股與高估值資產承壓"
    elif action == "hold":
        score, semantic = 0.0, "Fed維持利率"
        risk = "利率未變，重點轉向聲明、點陣圖與殖利率反應"
    else:
        score, semantic = 0.0, "利率決議語意待確認"
        risk = "等待官方聲明與市場反應確認"

    if expected_action != "unknown":
        expectation = "meet" if action == expected_action else "cooler" if action == "cut" else "hotter"
    else:
        expectation = _extract_expectation_words(texts)
        if expectation == "unknown":
            expectation = "near_consensus"
    confidence = 0.78 if official_confirmed else 0.48
    return score, semantic, risk, expectation, confidence


def assess_macro_event_result(
    macro: Mapping[str, Any] | None,
    news_items: Sequence[Any] | None = None,
    *,
    now: datetime | None = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """Return the latest official macro result and behaviour verdict."""
    started = time.perf_counter()
    if not _macro_enabled():
        return {"status": "disabled", "research_only": True, "decision_influence": False}
    reference = _now_tw(now)
    macro_map = dict(macro or {})
    event = _recent_supported_event(reference, macro_map)
    if not event:
        return {"status": "no_recent_event", "research_only": True, "decision_influence": False}

    code = str(event.get("code") or "").upper()
    release_date = str(event.get("release_date") or str(event.get("release_at") or "")[:10])
    cache_key = f"{code}:{release_date}:{int(reference.timestamp() // 300)}"
    with _CACHE_LOCK:
        cached = _ASSESS_CACHE.get(cache_key)
        if cached:
            cached_ttl = 30 if str(cached[1].get("status") or "") == "result_pending" else 300
            if time.time() - cached[0] < cached_ttl:
                return dict(cached[1])

    texts = _news_texts(news_items)
    override = _override_for(code, release_date)
    official = (
        _official_bls_result(code, release_date)
        if code in {"CPI", "PPI"}
        else _latest_fomc_statement(reference, release_date)
    )
    if not official and not override:
        result = {
            "status": "result_pending",
            "event_code": code,
            "release_at_tw": event.get("release_at"),
            "summary_line": f"Macro Event｜{code}｜官方結果待同步｜事件時間已確認，尚不建立方向",
            "research_only": True,
            "decision_influence": False,
            "calc_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
        with _CACHE_LOCK:
            _ASSESS_CACHE[cache_key] = (time.time(), dict(result))
        return result

    news_forecast = _extract_forecast_from_news(code, texts) if code in {"CPI", "PPI"} else {}
    merged = _merge_result(official, override, news_forecast)
    actual = dict(merged.get("actual") or {})
    previous = dict(merged.get("previous") or {})
    forecast = dict(merged.get("forecast") or {})
    quality_flags = list(merged.get("quality_flags") or [])
    official_confirmed = bool(merged.get("official_confirmed"))

    if code in {"CPI", "PPI"}:
        surprise = _surprise_map(actual, forecast)
        event_score, score_basis = _inflation_event_score(code, surprise, actual, previous)
        semantic, risk = _semantic_inflation(code, actual, previous, event_score)
        word_hint = _extract_expectation_words(texts)
        available_forecasts = sum(1 for value in forecast.values() if value is not None)
        if available_forecasts > 0:
            expectation = _expectation_state(event_score, surprise, word_hint)
        elif word_hint != "unknown":
            # Explicit news wording is acceptable, but missing consensus numbers
            # must never be converted from a trend score into "符合/高低於預期".
            expectation = word_hint
        else:
            expectation = "unknown"
            quality_flags.append("forecast_missing")
        confidence = 0.58 + (0.22 if official_confirmed else 0.0) + min(0.12, available_forecasts * 0.03)
        if score_basis == "trend":
            confidence -= 0.08
    else:
        surprise = {}
        score_basis = "fomc_action"
        event_score, semantic, risk, expectation, confidence = _fomc_result(
            actual, override, texts, official_confirmed
        )

    confirmation = _market_confirmation(
        macro_map,
        texts,
        release_at_tw=str(event.get("release_at") or ""),
        event_code=code,
        now=reference,
    )
    reaction_state, reaction_score, reaction_summary = classify_market_reaction(
        event_score, expectation, confirmation
    )
    if confirmation.get("sign") != "neutral":
        confidence += 0.08
    confidence = max(0.0, min(0.98, confidence))
    summary = _build_summary(code, event_score, expectation, semantic, risk, reaction_summary)

    provisional: Dict[str, Any] = {
        "event_code": code,
        "event_name": str(event.get("name") or code),
        "release_at_tw": str(event.get("release_at") or ""),
        "observed_at_tw": reference.isoformat(),
        "official_confirmed": official_confirmed,
        "source": str(merged.get("source") or ("STRUCTURED_OVERRIDE" if override else "NEWS_DERIVED")),
        "source_url": str(merged.get("source_url") or ""),
        "period": str(merged.get("period") or ""),
        "actual": actual,
        "forecast": forecast,
        "previous": previous,
        "surprise": surprise,
        "expectation_state": expectation,
        "semantic_verdict": semantic,
        "risk_verdict": risk,
        "event_score": round(event_score, 4),
        "score_basis": score_basis,
        "event_confidence": round(confidence, 4),
        "reaction_state": reaction_state,
        "reaction_score": round(reaction_score, 4),
        "reaction_summary": reaction_summary,
        "market_confirmation": confirmation,
        "summary_line": summary,
        "quality_flags": sorted(set(quality_flags)),
    }
    row = MacroEventResult(
        event_id=_event_id(code, release_date, provisional),
        schema_version=MACRO_EVENT_SCHEMA_VERSION,
        engine_version=MACRO_EVENT_ENGINE_VERSION,
        **provisional,
        calc_ms=round((time.perf_counter() - started) * 1000.0, 3),
    ).to_dict()
    row["status"] = "confirmed" if official_confirmed else "provisional"

    if persist:
        try:
            from .repository import append_macro_event
            row["storage"] = append_macro_event(row)
        except Exception as exc:
            row["storage"] = {"status": "degraded", "reason": f"{type(exc).__name__}: {exc}"}
    with _CACHE_LOCK:
        _ASSESS_CACHE[cache_key] = (time.time(), dict(row))
    return row


def compact_macro_event_line(
    macro: Mapping[str, Any] | None,
    news_items: Sequence[Any] | None = None,
    *,
    now: datetime | None = None,
) -> str:
    try:
        result = assess_macro_event_result(macro, news_items, now=now, persist=True)
        return str(result.get("summary_line") or "")
    except Exception:
        return ""


__all__ = [
    "MACRO_EVENT_SCHEMA_VERSION",
    "MACRO_EVENT_ENGINE_VERSION",
    "MacroEventResult",
    "parse_bls_cpi_text",
    "parse_bls_ppi_text",
    "classify_market_reaction",
    "assess_macro_event_result",
    "compact_macro_event_line",
]
