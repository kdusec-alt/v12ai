# -*- coding: utf-8 -*-
"""Truth-guarded US quarterly growth semantics for TINO V12.

Yahoo quoteSummary fields ``revenueGrowth`` and
``earningsQuarterlyGrowth`` are year-over-year measures.  This module obtains
actual sequential-quarter revenue only from the fundamentals timeseries route,
with a hard timeout and a compact process cache.  Failure never blocks price.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple


_QUARTERLY_CACHE: Dict[str, Tuple[float, Dict[str, object]]] = {}
_CACHE_TTL_SEC = 6 * 60 * 60


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "NA"):
            return default
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _raw_reported_value(row: object) -> float | None:
    if not isinstance(row, dict):
        return None
    value = row.get("reportedValue")
    if isinstance(value, dict):
        value = value.get("raw")
    return _num(value)


def _quarterly_rows(payload: Dict[str, object], field: str) -> List[Tuple[str, float]]:
    try:
        result = ((payload.get("timeseries") or {}).get("result") or [])
    except Exception:
        result = []
    out: List[Tuple[str, float]] = []
    for block in result if isinstance(result, list) else []:
        if not isinstance(block, dict):
            continue
        rows = block.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = _raw_reported_value(row)
            stamp = str(row.get("asOfDate") or row.get("date") or "")[:10]
            if value is not None and stamp:
                out.append((stamp, float(value)))
    dedup = {stamp: value for stamp, value in out}
    return sorted(dedup.items(), key=lambda item: item[0])


def _growth_pct(current: float | None, anchor: float | None) -> float | None:
    if current is None or anchor in (None, 0):
        return None
    try:
        return (float(current) - float(anchor)) / abs(float(anchor)) * 100.0
    except Exception:
        return None


def fetch_us_quarterly_metrics(symbol: str) -> Dict[str, object]:
    """Return actual quarterly revenue/EPS growth with a four-second timeout."""
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {}
    key = str(symbol or "").upper().strip()
    now = time.time()
    cached = _QUARTERLY_CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return dict(cached[1])

    out: Dict[str, object] = {}
    try:
        period2 = int(now + 86400)
        period1 = int(now - 3 * 366 * 86400)
        encoded = urllib.parse.quote(key)
        url = (
            "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
            f"{encoded}?symbol={encoded}&type=quarterlyTotalRevenue,quarterlyDilutedEPS"
            f"&period1={period1}&period2={period2}"
        )
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 TINO-V12-Fundamental"},
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))

        revenues = _quarterly_rows(payload, "quarterlyTotalRevenue")
        eps_rows = _quarterly_rows(payload, "quarterlyDilutedEPS")
        if revenues:
            latest_date, latest_revenue = revenues[-1]
            out["latest_revenue"] = latest_revenue
            out["latest_revenue_date"] = latest_date
            if len(revenues) >= 2:
                out["revenue_qoq"] = _growth_pct(latest_revenue, revenues[-2][1])
            if len(revenues) >= 5:
                out["revenue_yoy"] = _growth_pct(latest_revenue, revenues[-5][1])
        if eps_rows:
            latest_eps_date, latest_eps = eps_rows[-1]
            out["latest_eps"] = latest_eps
            out["latest_eps_date"] = latest_eps_date
            if len(eps_rows) >= 2:
                out["eps_qoq"] = _growth_pct(latest_eps, eps_rows[-2][1])
            if len(eps_rows) >= 5:
                out["eps_yoy"] = _growth_pct(latest_eps, eps_rows[-5][1])
        if out:
            out["accepted"] = True
            out["source"] = "YahooFinance fundamentals-timeseries"
    except Exception as exc:
        out = {
            "accepted": False,
            "source": "YahooFinance fundamentals-timeseries",
            "error": type(exc).__name__,
        }
    _QUARTERLY_CACHE[key] = (now, dict(out))
    return out


def _percent_from_ratio(value: Any) -> float | None:
    number = _num(value)
    if number is not None and abs(number) < 5:
        number *= 100.0
    return number


def build_us_fundamental_context(info: Dict[str, object], price_date: str) -> Dict[str, object]:
    """Build semantically correct US fundamental fields.

    ``qoq`` is populated only from an actual sequential-quarter revenue series.
    QuoteSummary growth fields are preserved under their correct YoY meaning.
    """
    quarter = (
        info.get("fiscalQuarterLabel")
        or info.get("mostRecentQuarter")
        or info.get("lastFiscalYearEnd")
        or "最新財報"
    )
    quarterly = info.get("_quarterly_metrics")
    quarterly = quarterly if isinstance(quarterly, dict) else {}

    trailing_eps = _num(info.get("trailingEps"))
    revenue_ttm = _num(info.get("totalRevenue"))
    latest_eps = _num(quarterly.get("latest_eps"))
    latest_revenue = _num(quarterly.get("latest_revenue"))
    eps = latest_eps if latest_eps is not None else trailing_eps
    revenue = latest_revenue if latest_revenue is not None else revenue_ttm

    quote_eps_yoy = _percent_from_ratio(info.get("earningsQuarterlyGrowth"))
    quote_revenue_yoy = _percent_from_ratio(info.get("revenueGrowth"))
    qoq = _num(quarterly.get("revenue_qoq"))
    revenue_yoy = _num(quarterly.get("revenue_yoy"))
    if revenue_yoy is None:
        revenue_yoy = quote_revenue_yoy
    eps_yoy = _num(quarterly.get("eps_yoy"))
    if eps_yoy is None:
        eps_yoy = quote_eps_yoy

    pe = _num(info.get("trailingPE"))
    next_date = info.get("nextEarningsDate") or info.get("earningsDate") or ""
    days = _num(info.get("earningsDays"))
    quarterly_ok = bool(quarterly.get("accepted"))
    source = "YahooFinance quoteSummary + V9 public memory"
    if quarterly_ok:
        source += " + fundamentals-timeseries"

    return {
        "accepted": bool(eps is not None or revenue is not None),
        "source": source,
        "date": price_date,
        "quarter": str(quarter or "最新財報"),
        "eps": eps,
        "eps_kind": "quarterly" if latest_eps is not None else "ttm",
        "eps_yoy": eps_yoy,
        "eps_yoy_verified": eps_yoy is not None,
        "revenue": revenue,
        "revenue_kind": "quarterly" if latest_revenue is not None else "ttm",
        "revenue_ttm": revenue_ttm,
        "qoq": qoq,
        "qoq_verified": bool(quarterly_ok and qoq is not None),
        "yoy": revenue_yoy,
        "revenue_yoy": revenue_yoy,
        "yoy_verified": revenue_yoy is not None,
        "pe": pe,
        "next_earnings": str(next_date or ""),
        "earnings_days": int(days) if days is not None else None,
        "growth_semantics": (
            "actual_quarterly_series"
            if quarterly_ok
            else "Yahoo quoteSummary YoY semantics"
        ),
        "quarterly_source_date": (
            quarterly.get("latest_revenue_date")
            or quarterly.get("latest_eps_date")
            or ""
        ),
    }
