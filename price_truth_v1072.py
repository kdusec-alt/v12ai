# -*- coding: utf-8 -*-
"""Session-aware price truth for the V1072 decision layer.

The formal daily close and the current trading-session quote are different
facts.  This module keeps them separate and gives every visible return/VWAP
statement one explicit basis.  It is intentionally read-only with respect to
forecast prices and learning memory.
"""
from __future__ import annotations

from datetime import date
import math
from typing import Any, Dict, Mapping

from models import PriceFrame


_ACTIVE_SESSIONS = {"pre_market", "intraday", "after_hours", "close_confirm"}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _positive(value: Any) -> float | None:
    number = _num(value)
    return number if number is not None and number > 0 else None


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _pct(current: Any, reference: Any) -> float | None:
    now = _positive(current)
    base = _positive(reference)
    if now is None or base is None:
        return None
    return (now / base - 1.0) * 100.0


def _iso(value: Any) -> str:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except Exception:
        return ""


def _session_labels(market: str, status: str, live: bool) -> tuple[str, str, str]:
    if market == "US":
        if status == "pre_market" and live:
            return "目前盤前", "盤前漲跌", "盤前VWAP"
        if status == "intraday" and live:
            return "目前盤中", "盤中漲跌", "正式盤VWAP"
        if status == "after_hours" and live:
            return "目前盤後", "盤後漲跌", "盤後VWAP"
        return "最近正式收盤", "上個交易日漲跌", "正式盤VWAP"
    if status == "intraday":
        return "今日盤中", "今日盤中漲跌", "VWAP"
    if status == "close_confirm":
        return "今日收盤確認", "今日收盤漲跌", "VWAP"
    if status == "after_close":
        return "今日正式收盤", "今日收盤漲跌", "VWAP"
    if status == "pre_market":
        return "盤前最近收盤", "上個交易日漲跌", "VWAP"
    return "最近正式收盤", "上個交易日漲跌", "VWAP"


def _range_label(market: str, status: str, live: bool) -> str:
    if market == "US" and live:
        return {
            "pre_market": "盤前",
            "intraday": "正式盤",
            "after_hours": "盤後",
        }.get(status, "時段")
    return "今日" if market == "TW" else "正式盤"


def build_price_truth(price: PriceFrame) -> Dict[str, Any]:
    """Return one auditable price snapshot without changing ``PriceFrame``."""
    context = _mapping(getattr(price, "context", {}))
    meta = _mapping(context.get("price_meta"))
    snap = _mapping(context.get("price_snapshot"))
    us_session = _mapping(context.get("us_session"))

    market = str(getattr(getattr(price, "ticker", None), "market", "") or "").upper()
    status = str(getattr(price, "market_status", "") or meta.get("session") or "closed_reference")
    active = status in _ACTIVE_SESSIONS

    current = (
        _positive(snap.get("last"))
        or _positive(us_session.get("last"))
        or _positive(getattr(price, "last", None))
    )
    reference = (
        _positive(meta.get("session_reference_close"))
        or _positive(snap.get("previous_close"))
        or _positive(us_session.get("reference_close"))
        or _positive(getattr(price, "previous_close", None))
    )
    current_return = _pct(current, reference)

    closes = [
        value
        for value in (_positive(row) for row in list(getattr(price, "recent_closes", []) or []))
        if value is not None
    ]
    formal_close = _positive(meta.get("regular_close"))
    formal_previous = _positive(meta.get("formal_previous_close"))
    if market == "US":
        if formal_close is None:
            formal_close = closes[-1] if closes else (
                current if not active else reference
            )
        if formal_previous is None:
            if len(closes) >= 2:
                formal_previous = closes[-2]
            elif not active:
                formal_previous = reference
    else:
        if status in {"after_close", "closed_reference"}:
            formal_close = current or (closes[-1] if closes else None)
            formal_previous = reference or (closes[-2] if len(closes) >= 2 else None)
        else:
            formal_close = _positive(meta.get("regular_close")) or reference or (
                closes[-1] if closes else None
            )
            formal_previous = _positive(meta.get("formal_previous_close")) or (
                closes[-2] if len(closes) >= 2 else None
            )
    formal_return = _pct(formal_close, formal_previous)

    live = bool(
        active
        and (
            market != "US"
            or meta.get("extended_accepted")
            or us_session.get("accepted")
            or snap.get("market_mode") in _ACTIVE_SESSIONS
        )
    )
    scope_label, return_label, vwap_label = _session_labels(market, status, live)

    vwap = _positive(snap.get("vwap")) or _positive(us_session.get("vwap")) or _positive(
        getattr(price, "vwap", None)
    )
    if market == "US" and status in {"pre_market", "intraday", "after_hours"}:
        if live:
            vwap_available = bool(
                meta.get("vwap_accepted")
                if meta.get("vwap_accepted") is not None
                else us_session.get("vwap_accepted", vwap is not None)
            )
        else:
            vwap_available = False
    else:
        vwap_available = vwap is not None
    vwap_state = (
        f"{vwap_label} {'上方' if current is not None and vwap is not None and current >= vwap else '下方'}"
        if vwap_available and current is not None
        else f"{vwap_label}待確認"
    )

    high = _positive(snap.get("high")) or _positive(us_session.get("high")) or _positive(
        getattr(price, "high", None)
    )
    low = _positive(snap.get("low")) or _positive(us_session.get("low")) or _positive(
        getattr(price, "low", None)
    )
    source = str(
        meta.get("source")
        or snap.get("source")
        or us_session.get("source")
        or getattr(getattr(price, "truth", None), "source", "")
        or ""
    )
    timestamp = str(
        meta.get("label")
        or snap.get("time")
        or us_session.get("timestamp")
        or ""
    )

    series_last = closes[-1] if closes else None
    formal_series_mismatch = bool(
        formal_close is not None
        and series_last is not None
        and abs(formal_close - series_last) > max(0.005, abs(formal_close) * 0.0005)
    )
    range_consistent = bool(
        current is not None
        and high is not None
        and low is not None
        and high + max(0.005, high * 0.0001) >= current
        and low - max(0.005, low * 0.0001) <= current
        and high >= low
    )
    basis_consistent = bool(current is not None and reference is not None and range_consistent)

    current_trade_date = (
        _iso(meta.get("current_trade_date"))
        or _iso(us_session.get("trade_date"))
        or _iso(getattr(price, "price_date", ""))
    )
    formal_date = (
        _iso(meta.get("regular_close_date"))
        or _iso(getattr(price, "price_date", ""))
    )

    if market == "US" and status in {"pre_market", "intraday", "after_hours"} and live:
        header_label = (
            f"上個交易日 {formal_return:+.2f}%"
            if formal_return is not None
            else "上個交易日漲跌待確認"
        )
    elif current_return is not None:
        header_label = (
            f"今日 {current_return:+.2f}%"
            if market == "TW"
            else f"上個交易日 {current_return:+.2f}%"
        )
    else:
        header_label = ""

    return {
        "schema": "TINO_PRICE_TRUTH_V1072",
        "market": market,
        "session": status,
        "session_scope": scope_label,
        "return_label": return_label,
        "range_label": _range_label(market, status, live),
        "current_price": round(current, 6) if current is not None else None,
        "current_reference_close": round(reference, 6) if reference is not None else None,
        "current_return_pct": round(current_return, 4) if current_return is not None else None,
        "formal_close": round(formal_close, 6) if formal_close is not None else None,
        "formal_previous_close": round(formal_previous, 6) if formal_previous is not None else None,
        "formal_return_pct": round(formal_return, 4) if formal_return is not None else None,
        "formal_date": formal_date,
        "current_trade_date": current_trade_date,
        "live_session_quote": live,
        "high": round(high, 6) if high is not None else None,
        "low": round(low, 6) if low is not None else None,
        "vwap": round(vwap, 6) if vwap is not None else None,
        "vwap_available": vwap_available,
        "vwap_scope": vwap_label,
        "vwap_state": vwap_state,
        "source": source,
        "timestamp": timestamp,
        "formal_series_mismatch": formal_series_mismatch,
        "basis_consistent": basis_consistent,
        "header_label": header_label,
        "decision_blocked": bool(meta.get("decision_blocked")) or not basis_consistent,
    }


def attach_price_truth(price: PriceFrame) -> PriceFrame:
    """Attach a read-only truth payload to the existing context."""
    context = dict(getattr(price, "context", {}) or {})
    price.context = context
    context["price_truth"] = build_price_truth(price)
    return price


def price_truth(price: PriceFrame) -> Dict[str, Any]:
    context = _mapping(getattr(price, "context", {}))
    existing = _mapping(context.get("price_truth"))
    return existing or build_price_truth(price)


def scoped_vwap_state(price: PriceFrame) -> str:
    return str(price_truth(price).get("vwap_state") or "VWAP待確認")
