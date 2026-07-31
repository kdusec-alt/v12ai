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


def _first_truth_date(forecast: FinalForecast) -> date | None:
    for truth in getattr(forecast, "data_truths", None) or []:
        parsed = _parse_date(getattr(truth, "date", ""))
        if parsed:
            return parsed
    return None


def target_trade_date_for_forecast(forecast: FinalForecast) -> str:
    """Return the exact official market session targeted by T1.

    T1 is always ``next session close``.  A US pre-market or intraday forecast
    therefore targets the following US trading day, not the current session's
    close.  The current/verified session date remains the T0/Audit anchor.
    """
    market = str(getattr(getattr(forecast, "ticker", None), "market", "") or "").upper()
    if market != "US":
        return _next_weekday(_first_truth_date(forecast) or datetime.now(_TW).date()).isoformat()

    session_date = _first_truth_date(forecast) or datetime.now(_NY).date()
    return _next_weekday(session_date).isoformat()


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
