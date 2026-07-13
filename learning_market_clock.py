# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Any, Dict
from zoneinfo import ZoneInfo

from models import FinalForecast

_TW = ZoneInfo("Asia/Taipei")
_NY = ZoneInfo("America/New_York")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_date(value: Any) -> date | None:
    try:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def _next_weekday(base: date) -> date:
    day = base + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _session_mode(forecast: FinalForecast) -> str:
    label = str((getattr(forecast, "decision_card", {}) or {}).get("資料標題") or "")
    if "盤中" in label:
        return "intraday"
    if "盤前" in label:
        return "pre_market"
    if "盤後" in label:
        return "after_hours"
    if "收盤" in label or "休市" in label:
        return "closed"
    return "unknown"


def target_trade_date_for_forecast(forecast: FinalForecast) -> str:
    """Return the exact market session targeted by T1."""
    market = str(getattr(getattr(forecast, "ticker", None), "market", "") or "").upper()
    if market != "US":
        return _next_weekday(datetime.now(_TW).date()).isoformat()

    now_ny = datetime.now(_NY)
    active = now_ny.date()
    if _session_mode(forecast) in {"pre_market", "intraday"}:
        while active.weekday() >= 5:
            active = _next_weekday(active)
        return active.isoformat()

    truth_date = None
    for truth in getattr(forecast, "data_truths", None) or []:
        truth_date = _parse_date(getattr(truth, "date", ""))
        if truth_date:
            break
    return _next_weekday(truth_date or active).isoformat()


def fetch_actual_daily_snapshot(ticker: str) -> Dict[str, Any]:
    """Fetch a verified official daily OHLC snapshot for learning audit."""
    from data_sources import fetch_price

    frame = fetch_price(ticker)
    closes = [_safe_float(v) for v in (getattr(frame, "recent_closes", None) or []) if _safe_float(v) > 0]
    highs = [_safe_float(v) for v in (getattr(frame, "recent_highs", None) or []) if _safe_float(v) > 0]
    lows = [_safe_float(v) for v in (getattr(frame, "recent_lows", None) or []) if _safe_float(v) > 0]
    truth = getattr(frame, "truth", None)
    status = str(getattr(frame, "market_status", "") or "")
    ready_status = status in {"closed_reference", "after_close", "close_confirm", "after_hours"}
    close = closes[-1] if closes else _safe_float(getattr(frame, "last", 0.0))
    return {
        "actual_close": close,
        "actual_open": _safe_float(getattr(frame, "open", 0.0)),
        "actual_high": highs[-1] if highs else _safe_float(getattr(frame, "high", 0.0)),
        "actual_low": lows[-1] if lows else _safe_float(getattr(frame, "low", 0.0)),
        "price_date": str(getattr(frame, "price_date", "") or ""),
        "market_status": status,
        "source": str(getattr(truth, "source", "fetch_price") or "fetch_price"),
        "actual_valid": bool(
            close > 0 and bool(getattr(truth, "accepted", False))
            and not bool(getattr(truth, "fallback", False)) and ready_status
        ),
    }


def actual_matches_target(actual: Dict[str, Any], target_date: str) -> bool:
    return bool(
        actual.get("actual_valid")
        and str(actual.get("price_date") or "")[:10] == str(target_date or "")[:10]
    )
