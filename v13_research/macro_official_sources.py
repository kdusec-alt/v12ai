# -*- coding: utf-8 -*-
"""Official-source and consensus parsers for V13 Macro Event Intelligence.

Network work is bounded, cached, and isolated from the scoring engine.  BLS
release pages are the primary source; the official BLS Public Data API fills
only fields that the release prose could not parse.  Forecast values are never
invented here.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
import re
import threading
import time
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

_TAIPEI = ZoneInfo("Asia/Taipei")
_NEW_YORK = ZoneInfo("America/New_York")
_BLS_URLS = {
    "CPI": "https://www.bls.gov/news.release/cpi.nr0.htm",
    "PPI": "https://www.bls.gov/news.release/ppi.nr0.htm",
}
_BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_BLS_SERIES = {
    "CPI": {
        "headline": "CUSR0000SA0",
        "core": "CUSR0000SA0L1E",
    },
    "PPI": {
        "headline": "WPSFD4",
        "core": "WPSFD49116",
    },
}
_FED_RSS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_FETCH_CACHE: Dict[str, Tuple[float, str]] = {}
_JSON_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_LOCK = threading.RLock()


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(str(value).replace("%", "").replace(",", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _signed_word_value(action: str, raw: str | None) -> float | None:
    action_text = str(action or "").lower()
    if any(word in action_text for word in ("unchanged", "was flat", "were unchanged", "持平", "不變")):
        return 0.0
    value = _number(raw)
    if value is None:
        return None
    if any(word in action_text for word in ("decreased", "declined", "fell", "dropped", "下降", "下跌", "回落")):
        return -abs(value)
    return abs(value)


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("−", "-").replace("–", "-")).strip()


def _extract_release_date(text: str) -> str:
    match = re.search(
        r"embargoed until\s+8:30\s+a\.m\.\s*\(ET\)\s+\w+,\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        text,
        flags=re.I,
    )
    if not match:
        return ""
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
    except Exception:
        return ""


def _month_period(text: str, code: str) -> str:
    match = re.search(rf"{re.escape(code)}[^\n-]*-\s*([A-Z]+)\s+(\d{{4}})", text, flags=re.I)
    if not match:
        match = re.search(r"(?:INDEX|PRICE INDEX)[^\n-]*-\s*([A-Z]+)\s+(\d{4})", text, flags=re.I)
    if match:
        return f"{match.group(1).title()} {match.group(2)}"
    return ""


def _first_signed_match(text: str, patterns: Sequence[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        if len(match.groups()) >= 2:
            return _signed_word_value(match.group(1), match.group(2))
        value = _number(match.group(1))
        if value is not None:
            return value
    return None


def _fill_previous_cpi(text: str, out: Dict[str, Any]) -> None:
    previous = out.setdefault("previous", {})
    actual = out.setdefault("actual", {})

    if previous.get("headline_mom") is None:
        previous["headline_mom"] = _first_signed_match(text, (
            r"CPI-U[^.]*?in\s+[A-Za-z]+,?\s+after\s+(rising|increasing|falling|decreasing|being unchanged)\s*([+-]?\d+(?:\.\d+)?)?\s*percent",
            r"all items index[^.]*?in\s+[A-Za-z]+,?\s+after\s+(rising|increasing|falling|decreasing|being unchanged)\s*([+-]?\d+(?:\.\d+)?)?\s*percent",
        ))

    if previous.get("core_mom") is None:
        previous["core_mom"] = _first_signed_match(text, (
            r"all items less food and energy[^.]*?(?:in|for)\s+[A-Za-z]+,?\s+after\s+(rising|increasing|falling|decreasing|being unchanged)\s*([+-]?\d+(?:\.\d+)?)?\s*percent",
            r"all items less food and energy[^.]*?(rose|increased|decreased|fell|was unchanged)\s*([+-]?\d+(?:\.\d+)?)?\s*percent[^.]*?,\s+as it did in\s+[A-Za-z]+",
        ))
        if previous.get("core_mom") is None and re.search(
            r"all items less food and energy[^.]*?,\s+as it did in\s+[A-Za-z]+", text, flags=re.I
        ):
            previous["core_mom"] = actual.get("core_mom")

    if previous.get("headline_yoy") is None:
        previous["headline_yoy"] = _first_signed_match(text, (
            r"all items index.{0,260}?(?:following|after)\s+(?:a\s+)?(?:rising|increasing)?\s*([+-]?\d+(?:\.\d+)?)\s*(?:-percent|percent)\s+(?:increase\s+)?(?:over|for)\s+the\s+12\s+months\s+ending",
        ))
    if previous.get("core_yoy") is None:
        previous["core_yoy"] = _first_signed_match(text, (
            r"all items less food and energy index.{0,260}?(?:following|after)\s+(?:a\s+)?(?:rising|increasing)?\s*([+-]?\d+(?:\.\d+)?)\s*(?:-percent|percent)\s+(?:increase\s+)?(?:over|for)\s+the\s+12\s+months\s+ending",
        ))


def parse_bls_cpi_text(raw_text: str) -> Dict[str, Any]:
    """Parse the official BLS CPI summary without relying on page layout."""
    text = _normalise_text(raw_text)
    out: Dict[str, Any] = {
        "event_code": "CPI",
        "release_date": _extract_release_date(text),
        "period": _month_period(text, "CPI"),
        "actual": {},
        "previous": {},
        "quality_flags": [],
    }

    headline = re.search(
        r"Consumer Price Index for All Urban Consumers \(CPI-U\)\s+"
        r"(increased|decreased|rose|fell|was unchanged)\s*"
        r"(?:([+-]?\d+(?:\.\d+)?)\s*percent)?[^.]*?in\s+([A-Za-z]+)"
        r"(?:,?\s+after\s+(rising|increasing|falling|decreasing|being unchanged)\s*"
        r"([+-]?\d+(?:\.\d+)?)?\s*percent\s+in\s+([A-Za-z]+))?",
        text,
        flags=re.I,
    )
    if headline:
        out["actual"]["headline_mom"] = _signed_word_value(headline.group(1), headline.group(2))
        if headline.group(4):
            out["previous"]["headline_mom"] = _signed_word_value(headline.group(4), headline.group(5))
        if not out["period"]:
            out["period"] = headline.group(3).title()

    headline_yoy = re.search(
        r"(?:Over the last 12 months,\s+the all items index|The all items index)\s+"
        r"(increased|rose|decreased|fell)\s+([+-]?\d+(?:\.\d+)?)\s*percent",
        text,
        flags=re.I,
    )
    if headline_yoy:
        out["actual"]["headline_yoy"] = _signed_word_value(headline_yoy.group(1), headline_yoy.group(2))

    core = re.search(
        r"index for all items less food and energy\s+"
        r"(increased|rose|decreased|fell|was unchanged)\s*"
        r"(?:([+-]?\d+(?:\.\d+)?)\s*percent)?[^.]*?(?:in\s+[A-Za-z]+)?",
        text,
        flags=re.I,
    )
    if core:
        out["actual"]["core_mom"] = _signed_word_value(core.group(1), core.group(2))

    core_yoy_patterns = (
        r"all items less food and energy index\s+(?:increased|rose|decreased|fell)\s+([+-]?\d+(?:\.\d+)?)\s*percent\s+(?:over the year|over the past 12 months)",
        r"all items less food and energy index\s+(?:increased|rose|decreased|fell)\s+([+-]?\d+(?:\.\d+)?)\s*percent\s+for the 12 months",
    )
    for pattern in core_yoy_patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            out["actual"]["core_yoy"] = abs(float(match.group(1)))
            break

    _fill_previous_cpi(text, out)
    for key in ("headline_mom", "headline_yoy", "core_mom", "core_yoy"):
        if key not in out["actual"]:
            out["actual"][key] = None
            out["quality_flags"].append(f"missing_{key}")
        if key not in out["previous"]:
            out["previous"][key] = None
    return out


def _fill_previous_ppi(text: str, out: Dict[str, Any]) -> None:
    previous = out.setdefault("previous", {})
    actual = out.setdefault("actual", {})
    if previous.get("headline_mom") is None:
        previous["headline_mom"] = _first_signed_match(text, (
            r"Producer Price Index for final demand.{0,220}?in\s+[A-Za-z]+,?\s+after\s+(rising|increasing|advancing|falling|decreasing|declining|being unchanged)\s*([+-]?\d+(?:\.\d+)?)?\s*percent",
            r"Final demand prices\s+(advanced|increased|rose|declined|decreased|fell|were unchanged)\s*([+-]?\d+(?:\.\d+)?)?\s*percent\s+in\s+[A-Za-z]+",
        ))
    if previous.get("core_mom") is None:
        previous["core_mom"] = _first_signed_match(text, (
            r"final demand less foods?, energy, and trade services.{0,220}?(?:in|for)\s+[A-Za-z]+,?\s+after\s+(rising|increasing|advancing|falling|decreasing|declining|being unchanged)\s*([+-]?\d+(?:\.\d+)?)?\s*percent",
            r"final demand less foods?, energy, and trade services.{0,220}?(increased|advanced|rose|decreased|declined|fell|was unchanged)\s*([+-]?\d+(?:\.\d+)?)?\s*percent[^.]*?,\s+the same as",
        ))
        if previous.get("core_mom") is None and re.search(
            r"final demand less foods?, energy, and trade services.{0,220}?,\s+(?:the same as|as it did in)", text, flags=re.I
        ):
            previous["core_mom"] = actual.get("core_mom")

    if previous.get("headline_yoy") is None:
        previous["headline_yoy"] = _first_signed_match(text, (
            r"index for final demand.{0,260}?(?:following|after)\s+(?:a\s+)?(?:rising|increasing|advancing)?\s*([+-]?\d+(?:\.\d+)?)\s*(?:-percent|percent)\s+(?:increase\s+)?(?:over|for)\s+the\s+12\s+months\s+ending",
        ))
    if previous.get("core_yoy") is None:
        previous["core_yoy"] = _first_signed_match(text, (
            r"final demand less foods?, energy, and trade services.{0,260}?(?:following|after)\s+(?:a\s+)?(?:rising|increasing|advancing)?\s*([+-]?\d+(?:\.\d+)?)\s*(?:-percent|percent)\s+(?:increase\s+)?(?:over|for)\s+the\s+12\s+months\s+ending",
        ))


def parse_bls_ppi_text(raw_text: str) -> Dict[str, Any]:
    """Parse the official BLS PPI summary without relying on HTML tables."""
    text = _normalise_text(raw_text)
    out: Dict[str, Any] = {
        "event_code": "PPI",
        "release_date": _extract_release_date(text),
        "period": _month_period(text, "PPI"),
        "actual": {},
        "previous": {},
        "quality_flags": [],
    }
    headline = re.search(
        r"Producer Price Index for final demand\s+"
        r"(increased|advanced|rose|decreased|declined|fell|was unchanged)\s*"
        r"(?:([+-]?\d+(?:\.\d+)?)\s*percent)?\s+in\s+([A-Za-z]+)"
        r"(?:,?\s+after\s+(rising|increasing|advancing|falling|decreasing|declining|being unchanged)\s*"
        r"([+-]?\d+(?:\.\d+)?)?\s*percent\s+in\s+([A-Za-z]+))?",
        text,
        flags=re.I,
    )
    if headline:
        out["actual"]["headline_mom"] = _signed_word_value(headline.group(1), headline.group(2))
        if headline.group(4):
            out["previous"]["headline_mom"] = _signed_word_value(headline.group(4), headline.group(5))
        if not out["period"]:
            out["period"] = headline.group(3).title()

    yoy = re.search(
        r"index for final demand\s+(?:advanced|increased|rose|declined|decreased|fell)\s+"
        r"([+-]?\d+(?:\.\d+)?)\s*percent\s+for the 12 months",
        text,
        flags=re.I,
    )
    if yoy:
        out["actual"]["headline_yoy"] = abs(float(yoy.group(1)))

    core = re.search(
        r"final demand less foods?, energy, and trade services\s+"
        r"(increased|advanced|rose|decreased|declined|fell|was unchanged)\s*"
        r"(?:([+-]?\d+(?:\.\d+)?)\s*percent)?",
        text,
        flags=re.I,
    )
    if core:
        out["actual"]["core_mom"] = _signed_word_value(core.group(1), core.group(2))

    core_yoy = re.search(
        r"final demand less foods?, energy, and trade services\s+"
        r"(?:advanced|increased|rose|moved up|declined|decreased|fell)\s+"
        r"([+-]?\d+(?:\.\d+)?)\s*percent\s+for the 12 months",
        text,
        flags=re.I,
    )
    if not core_yoy:
        core_yoy = re.search(
            r"For the 12 months ended in [A-Za-z]+,\s+prices for final demand less foods?, energy, and trade services\s+"
            r"(?:advanced|increased|rose|moved up|declined|decreased|fell)\s+([+-]?\d+(?:\.\d+)?)\s*percent",
            text,
            flags=re.I,
        )
    if core_yoy:
        out["actual"]["core_yoy"] = abs(float(core_yoy.group(1)))

    _fill_previous_ppi(text, out)
    for key in ("headline_mom", "headline_yoy", "core_mom", "core_yoy"):
        if key not in out["actual"]:
            out["actual"][key] = None
            out["quality_flags"].append(f"missing_{key}")
        if key not in out["previous"]:
            out["previous"][key] = None
    return out


def _network_enabled() -> bool:
    if str(os.environ.get("TINO_OFFLINE_TEST", "0")).strip().lower() in _TRUE_VALUES:
        return False
    return str(os.environ.get("TINO_V13_MACRO_OFFICIAL_FETCH", "1")).strip().lower() not in _FALSE_VALUES


def _fetch_text(url: str, *, ttl_seconds: int = 900, timeout: float = 4.5) -> str:
    if not _network_enabled():
        return ""
    now_ts = time.time()
    with _CACHE_LOCK:
        cached = _FETCH_CACHE.get(url)
        if cached:
            positive_ttl = max(30, int(ttl_seconds))
            effective_ttl = positive_ttl if cached[1] else min(30, positive_ttl)
            if now_ts - cached[0] <= effective_ttl:
                return cached[1]
    try:
        import requests
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 TINO-V13-MacroResult/1.1"},
        )
        response.raise_for_status()
        text = response.text
    except Exception:
        text = ""
    with _CACHE_LOCK:
        _FETCH_CACHE[url] = (now_ts, text)
    return text


def _fetch_bls_api_payload(series_ids: Sequence[str], start_year: int, end_year: int, *, timeout: float = 5.5) -> Dict[str, Any]:
    if not _network_enabled() or not series_ids:
        return {}
    cache_key = f"{','.join(series_ids)}:{start_year}:{end_year}"
    now_ts = time.time()
    with _CACHE_LOCK:
        cached = _JSON_CACHE.get(cache_key)
        if cached:
            effective_ttl = 900 if cached[1] else 30
            if now_ts - cached[0] <= effective_ttl:
                return dict(cached[1])
    payload: Dict[str, Any] = {}
    try:
        import requests
        response = requests.post(
            _BLS_API_URL,
            json={"seriesid": list(series_ids), "startyear": str(start_year), "endyear": str(end_year)},
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 TINO-V13-BLS-API/1.1"},
        )
        response.raise_for_status()
        raw = response.json()
        if isinstance(raw, Mapping) and str(raw.get("status") or "").upper() == "REQUEST_SUCCEEDED":
            payload = dict(raw)
    except Exception:
        payload = {}
    with _CACHE_LOCK:
        _JSON_CACHE[cache_key] = (now_ts, dict(payload))
    return payload


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        return _normalise_text(BeautifulSoup(html, "html.parser").get_text(" "))
    except Exception:
        return _normalise_text(re.sub(r"<[^>]+>", " ", html))


def _series_levels(payload: Mapping[str, Any], series_id: str) -> List[Tuple[int, int, float]]:
    rows: List[Tuple[int, int, float]] = []
    series = (((payload.get("Results") or {}).get("series") or []) if isinstance(payload, Mapping) else [])
    for item in series:
        if not isinstance(item, Mapping) or str(item.get("seriesID") or "") != series_id:
            continue
        for row in item.get("data") or []:
            if not isinstance(row, Mapping):
                continue
            period = str(row.get("period") or "")
            if not re.fullmatch(r"M\d{2}", period) or period == "M13":
                continue
            value = _number(row.get("value"))
            try:
                year = int(row.get("year"))
                month = int(period[1:])
            except Exception:
                continue
            if value is not None and 1 <= month <= 12:
                rows.append((year, month, value))
    rows.sort(key=lambda item: (item[0], item[1]))
    return rows


def _growth_from_levels(levels: Sequence[Tuple[int, int, float]]) -> Dict[str, float | None]:
    data = list(levels)
    if len(data) < 3:
        return {"mom": None, "previous_mom": None, "yoy": None, "previous_yoy": None}
    index = {(year, month): value for year, month, value in data}
    year, month, latest = data[-1]
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev2_year, prev2_month = (prev_year - 1, 12) if prev_month == 1 else (prev_year, prev_month - 1)
    prev = index.get((prev_year, prev_month))
    prev2 = index.get((prev2_year, prev2_month))
    year_ago = index.get((year - 1, month))
    prev_year_ago = index.get((prev_year - 1, prev_month))

    def pct(a: float | None, b: float | None) -> float | None:
        return round((a / b - 1.0) * 100.0, 2) if a is not None and b not in (None, 0.0) else None

    return {
        "mom": pct(latest, prev),
        "previous_mom": pct(prev, prev2),
        "yoy": pct(latest, year_ago),
        "previous_yoy": pct(prev, prev_year_ago),
        "period": f"{datetime(year, month, 1).strftime('%B')} {year}",
    }


def _bls_api_result(code: str, release_date: str) -> Dict[str, Any]:
    ids = _BLS_SERIES.get(str(code).upper())
    if not ids:
        return {}
    try:
        end_year = int(str(release_date or datetime.now(_TAIPEI).date().isoformat())[:4])
    except Exception:
        end_year = datetime.now(_TAIPEI).year
    payload = _fetch_bls_api_payload(list(ids.values()), end_year - 2, end_year)
    if not payload:
        return {}
    headline = _growth_from_levels(_series_levels(payload, ids["headline"]))
    core = _growth_from_levels(_series_levels(payload, ids["core"]))
    actual = {
        "headline_mom": headline.get("mom"),
        "headline_yoy": headline.get("yoy"),
        "core_mom": core.get("mom"),
        "core_yoy": core.get("yoy"),
    }
    previous = {
        "headline_mom": headline.get("previous_mom"),
        "headline_yoy": headline.get("previous_yoy"),
        "core_mom": core.get("previous_mom"),
        "core_yoy": core.get("previous_yoy"),
    }
    available = sum(value is not None for value in actual.values())
    if available < 2:
        return {}
    period = str(headline.get("period") or core.get("period") or "")
    if release_date and period:
        try:
            released = datetime.fromisoformat(release_date).date()
            expected_year = released.year if released.month > 1 else released.year - 1
            expected_month = released.month - 1 if released.month > 1 else 12
            expected_period = datetime(expected_year, expected_month, 1).strftime("%B %Y")
            if period != expected_period:
                return {}
        except Exception:
            pass
    return {
        "event_code": str(code).upper(),
        "release_date": release_date,
        "period": period,
        "actual": actual,
        "previous": previous,
        "quality_flags": [key for key, value in actual.items() if value is None],
        "source": "BLS_PUBLIC_API",
        "source_url": _BLS_API_URL,
        "official_confirmed": True,
    }


def _merge_official_payload(primary: Mapping[str, Any], fallback: Mapping[str, Any]) -> Dict[str, Any]:
    if not primary:
        return dict(fallback or {})
    if not fallback:
        return dict(primary or {})
    out = dict(primary)
    for section in ("actual", "previous"):
        merged = dict(primary.get(section) or {})
        for key, value in dict(fallback.get(section) or {}).items():
            if merged.get(key) is None and value is not None:
                merged[key] = value
        out[section] = merged
    if not out.get("period") or not re.search(r"\d{4}", str(out.get("period") or "")):
        out["period"] = fallback.get("period") or out.get("period")
    flags = [str(flag) for flag in list(primary.get("quality_flags") or [])]
    flags = [flag for flag in flags if not (flag.startswith("missing_") and out.get("actual", {}).get(flag[8:]) is not None)]
    flags.extend(str(flag) for flag in list(fallback.get("quality_flags") or []) if str(flag) not in flags)
    out["quality_flags"] = flags
    out["source"] = "BLS_OFFICIAL_RELEASE+BLS_PUBLIC_API"
    out["source_url"] = str(primary.get("source_url") or fallback.get("source_url") or "")
    out["official_confirmed"] = bool(primary.get("official_confirmed") or fallback.get("official_confirmed"))
    return out


def _structured_overrides() -> List[Dict[str, Any]]:
    raw = str(os.environ.get("TINO_MACRO_RESULTS_JSON") or "").strip()
    if not raw:
        return []
    try:
        rows = json.loads(raw)
    except Exception:
        return []
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _override_for(code: str, release_date: str) -> Dict[str, Any]:
    for row in reversed(_structured_overrides()):
        if str(row.get("event_code") or row.get("code") or "").upper() != str(code).upper():
            continue
        row_date = str(row.get("release_date") or row.get("date") or "")[:10]
        if release_date and row_date and row_date != release_date:
            continue
        return row
    return {}


def _news_texts(news_items: Sequence[Any] | None) -> List[str]:
    out: List[str] = []
    for item in list(news_items or [])[:40]:
        if isinstance(item, Mapping):
            title = item.get("title")
            tag = item.get("tag")
        else:
            title = getattr(item, "title", "")
            tag = getattr(item, "tag", "")
        text = _normalise_text(f"{title or ''} {tag or ''}")
        if text:
            out.append(text)
    return out


def _metric_aliases(code: str) -> Dict[str, Tuple[str, ...]]:
    prefix = "CPI" if code == "CPI" else "PPI"
    return {
        "headline_mom": (f"{prefix} MoM", f"{prefix} month-over-month", f"{prefix}月增", f"{prefix} 月增", "headline mom"),
        "headline_yoy": (f"{prefix} YoY", f"{prefix} year-over-year", f"{prefix}年增", f"{prefix} 年增", "headline yoy"),
        "core_mom": (f"Core {prefix} MoM", f"core {prefix} month-over-month", f"核心{prefix}月增", f"核心 {prefix} 月增", "core mom"),
        "core_yoy": (f"Core {prefix} YoY", f"core {prefix} year-over-year", f"核心{prefix}年增", f"核心 {prefix} 年增", "core yoy"),
    }


def _extract_forecast_from_news(code: str, texts: Sequence[str]) -> Dict[str, float | None]:
    """Extract only explicit consensus numbers; never infer a missing forecast."""
    out: Dict[str, float | None] = {}
    aliases = _metric_aliases(code)
    for metric, names in aliases.items():
        found: List[float] = []
        for text in texts:
            lower = text.lower()
            for alias in names:
                pos = lower.find(alias.lower())
                if pos < 0:
                    continue
                segment = text[max(0, pos - 12): pos + 140]
                patterns = (
                    r"(?:forecast|expected|expectation|consensus|estimate|預期|市場預期)\s*(?:was|is|為|:|：)?\s*([+-]?\d+(?:\.\d+)?)\s*%",
                    r"(?:vs\.?|versus|對比)\s*([+-]?\d+(?:\.\d+)?)\s*%\s*(?:expected|forecast|預期)",
                    r"([+-]?\d+(?:\.\d+)?)\s*%\s*(?:expected|forecast|consensus|預期)",
                )
                for pattern in patterns:
                    match = re.search(pattern, segment, flags=re.I)
                    if match:
                        value = _number(match.group(1))
                        if value is not None and -20 <= value <= 30:
                            found.append(value)
                        break
        if found:
            values = sorted(found)
            out[metric] = values[len(values) // 2]
        else:
            out[metric] = None
    return out


def _extract_expectation_words(texts: Sequence[str]) -> str:
    blob = " ".join(texts).lower()
    if any(term in blob for term in ("in line with expectations", "matched expectations", "as expected", "符合預期", "與預期一致")):
        return "meet"
    if any(term in blob for term in ("hotter than expected", "above expectations", "高於預期", "超預期")):
        return "hotter"
    if any(term in blob for term in ("cooler than expected", "below expectations", "低於預期", "不及預期")):
        return "cooler"
    return "unknown"


def _official_bls_result(code: str, release_date: str) -> Dict[str, Any]:
    url = _BLS_URLS.get(code)
    if not url:
        return {}
    primary: Dict[str, Any] = {}
    text = _html_to_text(_fetch_text(url, ttl_seconds=180))
    if text:
        parsed = parse_bls_cpi_text(text) if code == "CPI" else parse_bls_ppi_text(text)
        if not (release_date and parsed.get("release_date") and str(parsed.get("release_date")) != release_date):
            parsed.update({"source": "BLS_OFFICIAL_RELEASE", "source_url": url, "official_confirmed": True})
            primary = parsed
    required = ("headline_mom", "headline_yoy", "core_mom", "core_yoy")
    need_fallback = not primary or any(
        (primary.get(section) or {}).get(key) is None
        for section in ("actual", "previous")
        for key in required
    )
    fallback = _bls_api_result(code, release_date) if need_fallback else {}
    return _merge_official_payload(primary, fallback)


def _latest_fomc_statement(reference: datetime, release_date: str) -> Dict[str, Any]:
    """Read the latest official FOMC statement through the Federal Reserve RSS."""
    xml = _fetch_text(_FED_RSS_URL, ttl_seconds=300)
    if not xml:
        return {}
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
    except Exception:
        return {}
    candidates: List[Tuple[datetime, str, str]] = []
    for item in root.findall(".//item"):
        title = _normalise_text(item.findtext("title") or "")
        link = _normalise_text(item.findtext("link") or "")
        pub = _normalise_text(item.findtext("pubDate") or "")
        if "FOMC statement" not in title and "Federal Reserve issues" not in title:
            continue
        try:
            import email.utils
            dt = email.utils.parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            dt = dt.astimezone(_TAIPEI)
        except Exception:
            continue
        candidates.append((dt, link, title))
    if not candidates:
        return {}
    candidates.sort(key=lambda row: row[0], reverse=True)
    dt, link, title = candidates[0]
    if release_date and dt.date().isoformat() != release_date:
        expected = datetime.fromisoformat(release_date).date()
        if abs((dt.date() - expected).days) > 1:
            return {}
    page = _html_to_text(_fetch_text(link, ttl_seconds=300))
    if not page:
        return {}
    action = "unknown"
    lower = page.lower()
    if "decided to lower the target range" in lower or "reduce the target range" in lower:
        action = "cut"
    elif "decided to raise the target range" in lower or "increase the target range" in lower:
        action = "hike"
    elif "decided to maintain the target range" in lower or "maintain the target range" in lower:
        action = "hold"
    target = re.search(
        r"target range for the federal funds rate (?:at|to)\s+([0-9.]+)\s+to\s+([0-9.]+)\s+percent",
        page,
        flags=re.I,
    )
    actual: Dict[str, Any] = {"action": action}
    if target:
        actual["target_low"] = float(target.group(1))
        actual["target_high"] = float(target.group(2))
    return {
        "event_code": "FOMC",
        "release_date": dt.date().isoformat(),
        "period": dt.strftime("%B %Y"),
        "actual": actual,
        "previous": {},
        "quality_flags": [] if action != "unknown" else ["action_not_parsed"],
        "source": "FED_OFFICIAL_RSS",
        "source_url": link,
        "official_confirmed": action != "unknown",
        "official_title": title,
    }


__all__ = [
    "parse_bls_cpi_text",
    "parse_bls_ppi_text",
    "_number",
    "_override_for",
    "_news_texts",
    "_extract_forecast_from_news",
    "_extract_expectation_words",
    "_official_bls_result",
    "_latest_fomc_statement",
    "_bls_api_result",
    "_growth_from_levels",
]
