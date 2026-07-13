# -*- coding: utf-8 -*-
"""Truth-guarded cross-symbol US fundamental semantics for TINO V12.

The module is intentionally generic: no ticker-specific exceptions.  It keeps
GAAP earnings visible, but only comparable/normalised earnings growth may enter
AI Bubble Radar.  Revenue QoQ/YoY are accepted only when the underlying report
dates form a valid quarterly/yearly comparison.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Tuple


_QUARTERLY_CACHE: Dict[str, Tuple[float, Dict[str, object]]] = {}
_CACHE_TTL_SEC = 6 * 60 * 60


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "NA", "--"):
            return default
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def detect_us_asset_type(info: Dict[str, object] | None) -> str:
    """Return ``etf`` or ``stock`` from Yahoo metadata without symbol lists."""
    info = info if isinstance(info, dict) else {}
    blob = " ".join(
        _text(info.get(key)).upper()
        for key in (
            "quoteType", "typeDisp", "legalType", "category", "fundFamily",
            "market", "exchange", "longName", "shortName",
        )
    )
    quote_type = _text(info.get("quoteType")).upper()
    if quote_type in {"ETF", "MUTUALFUND", "FUND"}:
        return "etf"
    if any(token in blob for token in ("EXCHANGE TRADED FUND", "EXCHANGE-TRADED FUND", " ETF")):
        return "etf"
    return "stock"


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
            stamp = _text(row.get("asOfDate") or row.get("date"))[:10]
            if value is not None and stamp:
                out.append((stamp, float(value)))
    dedup = {stamp: value for stamp, value in out}
    return sorted(dedup.items(), key=lambda item: item[0])


def _as_date(value: object) -> date | None:
    try:
        return date.fromisoformat(_text(value)[:10])
    except Exception:
        return None


def _growth_pct(current: float | None, anchor: float | None) -> float | None:
    if current is None or anchor in (None, 0):
        return None
    try:
        return (float(current) - float(anchor)) / abs(float(anchor)) * 100.0
    except Exception:
        return None


def _closest_anchor(
    rows: List[Tuple[str, float]],
    *,
    target_days: int,
    tolerance_days: int,
) -> Tuple[str, float] | None:
    if len(rows) < 2:
        return None
    latest_date = _as_date(rows[-1][0])
    if latest_date is None:
        return None
    candidates: List[Tuple[int, str, float]] = []
    for stamp, value in rows[:-1]:
        anchor_date = _as_date(stamp)
        if anchor_date is None:
            continue
        gap = (latest_date - anchor_date).days
        distance = abs(gap - target_days)
        if distance <= tolerance_days:
            candidates.append((distance, stamp, value))
    if not candidates:
        return None
    _, stamp, value = min(candidates, key=lambda item: item[0])
    return stamp, value


def _series_growth(rows: List[Tuple[str, float]], *, period: str) -> Tuple[float | None, str]:
    if not rows:
        return None, ""
    if period == "qoq":
        anchor = _closest_anchor(rows, target_days=91, tolerance_days=50)
    else:
        anchor = _closest_anchor(rows, target_days=365, tolerance_days=70)
    if anchor is None:
        return None, ""
    return _growth_pct(rows[-1][1], anchor[1]), anchor[0]


def _fresh_report(stamp: object, reference_date: object, max_age_days: int = 220) -> bool:
    report_date = _as_date(stamp)
    ref = _as_date(reference_date) or date.today()
    if report_date is None:
        return False
    age = (ref - report_date).days
    return -7 <= age <= max_age_days


def _positive_comparable_growth(rows: List[Tuple[str, float]]) -> Tuple[float | None, str]:
    """Return YoY only when both normalised EPS values are positive/comparable."""
    if not rows:
        return None, ""
    anchor = _closest_anchor(rows, target_days=365, tolerance_days=70)
    if anchor is None:
        return None, ""
    current = rows[-1][1]
    prior = anchor[1]
    # Crossing zero or an almost-zero base creates economically meaningless
    # percentages. Keep such values out of Decision for every ticker.
    if current <= 0 or prior <= 0 or abs(prior) < 0.01:
        return None, anchor[0]
    return _growth_pct(current, prior), anchor[0]


def fetch_us_quarterly_metrics(symbol: str) -> Dict[str, object]:
    """Fetch compact quarterly metrics with report-date continuity guards."""
    if os.environ.get("TINO_OFFLINE_TEST") == "1":
        return {}
    key = _text(symbol).upper()
    now = time.time()
    cached = _QUARTERLY_CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return dict(cached[1])

    out: Dict[str, object] = {}
    try:
        period2 = int(now + 86400)
        period1 = int(now - 4 * 366 * 86400)
        encoded = urllib.parse.quote(key)
        fields = ",".join(
            (
                "quarterlyTotalRevenue",
                "quarterlyDilutedEPS",
                "quarterlyNormalizedDilutedEPS",
                "quarterlyNetIncome",
            )
        )
        url = (
            "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
            f"{encoded}?symbol={encoded}&type={fields}&period1={period1}&period2={period2}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 TINO-V12-Fundamental"})
        with urllib.request.urlopen(request, timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))

        revenues = _quarterly_rows(payload, "quarterlyTotalRevenue")
        gaap_eps_rows = _quarterly_rows(payload, "quarterlyDilutedEPS")
        normalized_eps_rows = _quarterly_rows(payload, "quarterlyNormalizedDilutedEPS")
        net_income_rows = _quarterly_rows(payload, "quarterlyNetIncome")

        if revenues:
            latest_date, latest_revenue = revenues[-1]
            qoq, qoq_anchor = _series_growth(revenues, period="qoq")
            yoy, yoy_anchor = _series_growth(revenues, period="yoy")
            out.update(
                latest_revenue=latest_revenue,
                latest_revenue_date=latest_date,
                revenue_qoq=qoq,
                revenue_qoq_anchor_date=qoq_anchor,
                revenue_yoy=yoy,
                revenue_yoy_anchor_date=yoy_anchor,
            )
        if gaap_eps_rows:
            latest_date, latest_eps = gaap_eps_rows[-1]
            gaap_yoy, gaap_anchor = _series_growth(gaap_eps_rows, period="yoy")
            out.update(
                latest_gaap_eps=latest_eps,
                latest_gaap_eps_date=latest_date,
                gaap_eps_yoy=gaap_yoy,
                gaap_eps_yoy_anchor_date=gaap_anchor,
            )
        if normalized_eps_rows:
            latest_date, latest_eps = normalized_eps_rows[-1]
            adjusted_yoy, adjusted_anchor = _positive_comparable_growth(normalized_eps_rows)
            out.update(
                latest_adjusted_eps=latest_eps,
                latest_adjusted_eps_date=latest_date,
                adjusted_eps_yoy=adjusted_yoy,
                adjusted_eps_yoy_anchor_date=adjusted_anchor,
            )
        if net_income_rows:
            latest_date, latest_value = net_income_rows[-1]
            net_income_yoy, net_income_anchor = _series_growth(net_income_rows, period="yoy")
            out.update(
                latest_net_income=latest_value,
                latest_net_income_date=latest_date,
                net_income_yoy=net_income_yoy,
                net_income_yoy_anchor_date=net_income_anchor,
            )
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


def build_us_fundamental_context(
    info: Dict[str, object],
    price_date: str,
    asset_type: str | None = None,
) -> Dict[str, object]:
    """Build universal, semantically-correct US stock/ADR/ETF fundamentals."""
    info = info if isinstance(info, dict) else {}
    detected_asset = asset_type or detect_us_asset_type(info)
    if detected_asset == "etf":
        return {
            "accepted": False,
            "asset_type": "etf",
            "source": "YahooFinance ETF metadata",
            "date": price_date,
            "reason": "ETF_NO_SINGLE_COMPANY_FUNDAMENTALS",
        }

    quarter = info.get("fiscalQuarterLabel") or info.get("mostRecentQuarter") or "最新財報"
    quarterly = info.get("_quarterly_metrics")
    quarterly = quarterly if isinstance(quarterly, dict) else {}
    quarterly_ok = bool(quarterly.get("accepted"))

    revenue_ttm = _num(info.get("totalRevenue"))
    latest_revenue = _num(quarterly.get("latest_revenue"))
    revenue = latest_revenue if latest_revenue is not None else revenue_ttm
    revenue_date = _text(quarterly.get("latest_revenue_date"))
    revenue_fresh = bool(latest_revenue is not None and _fresh_report(revenue_date, price_date))

    revenue_qoq = _num(quarterly.get("revenue_qoq")) if revenue_fresh else None
    revenue_yoy = _num(quarterly.get("revenue_yoy")) if revenue_fresh else None
    quote_revenue_yoy = _percent_from_ratio(info.get("revenueGrowth"))
    if revenue_yoy is None:
        revenue_yoy = quote_revenue_yoy

    gaap_eps = _num(quarterly.get("latest_gaap_eps"))
    adjusted_eps = _num(quarterly.get("latest_adjusted_eps"))
    trailing_eps = _num(info.get("trailingEps"))
    eps = adjusted_eps if adjusted_eps is not None else (gaap_eps if gaap_eps is not None else trailing_eps)
    if adjusted_eps is not None:
        eps_basis = "normalized_diluted"
        eps_kind = "quarterly"
    elif gaap_eps is not None:
        eps_basis = "gaap_diluted"
        eps_kind = "quarterly"
    else:
        eps_basis = "gaap_ttm"
        eps_kind = "ttm"

    gaap_eps_yoy = _num(quarterly.get("gaap_eps_yoy"))
    adjusted_eps_yoy = _num(quarterly.get("adjusted_eps_yoy"))
    adjusted_date = _text(quarterly.get("latest_adjusted_eps_date"))
    adjusted_fresh = bool(adjusted_eps is not None and _fresh_report(adjusted_date, price_date))
    adjusted_eligible = bool(adjusted_fresh and adjusted_eps_yoy is not None)

    # QuoteSummary earningsQuarterlyGrowth is retained only as raw metadata. It
    # is not guaranteed to be adjusted EPS growth and never enters Decision.
    quote_earnings_yoy = _percent_from_ratio(info.get("earningsQuarterlyGrowth"))
    display_eps_yoy = adjusted_eps_yoy if adjusted_eligible else gaap_eps_yoy
    display_eps_yoy_label = "可比EPS YoY" if adjusted_eligible else "GAAP EPS YoY"

    pe = _num(info.get("trailingPE"))
    forward_pe = _num(info.get("forwardPE"))
    ps = _num(info.get("priceToSalesTrailing12Months"))
    peg = _num(info.get("pegRatio"))
    next_date = info.get("nextEarningsDate") or info.get("earningsDate") or ""
    days = _num(info.get("earningsDays"))

    source = "YahooFinance quoteSummary"
    if quarterly_ok:
        source += " + fundamentals-timeseries"

    return {
        "accepted": bool(eps is not None or revenue is not None or pe is not None or ps is not None),
        "asset_type": "stock",
        "source": source,
        "date": price_date,
        "quarter": str(quarter or "最新財報"),
        "revenue": revenue,
        "revenue_kind": "quarterly" if latest_revenue is not None else "ttm",
        "revenue_ttm": revenue_ttm,
        "qoq": revenue_qoq,
        "qoq_verified": bool(revenue_fresh and revenue_qoq is not None),
        "yoy": revenue_yoy,
        "revenue_yoy": revenue_yoy,
        "yoy_verified": bool(revenue_yoy is not None),
        "revenue_growth_source": "quarterly_series" if revenue_fresh else "quoteSummary_TTM_YoY",
        "eps": eps,
        "eps_kind": eps_kind,
        "eps_basis": eps_basis,
        "gaap_eps": gaap_eps,
        "adjusted_eps": adjusted_eps,
        "eps_yoy": display_eps_yoy,
        "eps_yoy_label": display_eps_yoy_label,
        "eps_yoy_verified": bool(display_eps_yoy is not None),
        "eps_yoy_decision_eligible": adjusted_eligible,
        "earnings_yoy_for_decision": adjusted_eps_yoy if adjusted_eligible else None,
        "adjusted_eps_yoy": adjusted_eps_yoy,
        "gaap_eps_yoy": gaap_eps_yoy,
        "quote_earnings_yoy": quote_earnings_yoy,
        "pe": pe,
        "forward_pe": forward_pe,
        "ps": ps,
        "peg": peg,
        "next_earnings": str(next_date or ""),
        "earnings_days": int(days) if days is not None else None,
        "growth_semantics": "universal_truth_guard_v2",
        "quarterly_source_date": revenue_date or _text(quarterly.get("latest_gaap_eps_date")),
    }
