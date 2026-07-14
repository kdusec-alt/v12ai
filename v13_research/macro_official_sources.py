# -*- coding: utf-8 -*-
"""Official-source and consensus parsers for V13 Macro Event Intelligence.

Network work is bounded, cached, and isolated from the scoring engine.
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
_FED_RSS_URL = "https://www.federalreserve.gov/feeds/press_monetary.xml"
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_FETCH_CACHE: Dict[str, Tuple[float, str]] = {}
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
    if any(word in action_text for word in ("unchanged", "was flat", "持平", "不變")):
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
        r"(?:\s+after\s+(rising|increasing|falling|decreasing|being unchanged)\s*"
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
        r"(?:([+-]?\d+(?:\.\d+)?)\s*percent)?[^.]*?(?:in\s+[A-Za-z]+)?"
        r"(?:\s+after\s+(rising|increasing|falling|decreasing|being unchanged)\s*"
        r"([+-]?\d+(?:\.\d+)?)?\s*percent)?",
        text,
        flags=re.I,
    )
    if core:
        out["actual"]["core_mom"] = _signed_word_value(core.group(1), core.group(2))
        if core.group(3):
            out["previous"]["core_mom"] = _signed_word_value(core.group(3), core.group(4))

    core_yoy_patterns = (
        r"all items less food and energy index\s+(?:increased|rose|decreased|fell)\s+([+-]?\d+(?:\.\d+)?)\s*percent\s+(?:over the year|over the past 12 months)",
        r"all items less food and energy index\s+(?:increased|rose|decreased|fell)\s+([+-]?\d+(?:\.\d+)?)\s*percent\s+for the 12 months",
    )
    for pattern in core_yoy_patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            out["actual"]["core_yoy"] = abs(float(match.group(1)))
            break

    for key in ("headline_mom", "headline_yoy", "core_mom", "core_yoy"):
        if key not in out["actual"]:
            out["actual"][key] = None
            out["quality_flags"].append(f"missing_{key}")
    return out


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
        r"(?:\s+after\s+(rising|increasing|advancing|falling|decreasing|declining|being unchanged)\s*"
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
    if "headline_mom" not in out["previous"]:
        previous_match = re.search(
            r"Final demand prices\s+(advanced|increased|rose|declined|decreased|fell|were unchanged)\s*"
            r"(?:([+-]?\d+(?:\.\d+)?)\s*percent)?\s+in\s+([A-Za-z]+)",
            text,
            flags=re.I,
        )
        if previous_match:
            out["previous"]["headline_mom"] = _signed_word_value(previous_match.group(1), previous_match.group(2))

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

    for key in ("headline_mom", "headline_yoy", "core_mom", "core_yoy"):
        if key not in out["actual"]:
            out["actual"][key] = None
            out["quality_flags"].append(f"missing_{key}")
    return out


def _fetch_text(url: str, *, ttl_seconds: int = 900, timeout: float = 4.5) -> str:
    if str(os.environ.get("TINO_OFFLINE_TEST", "0")).strip().lower() in _TRUE_VALUES:
        return ""
    if str(os.environ.get("TINO_V13_MACRO_OFFICIAL_FETCH", "1")).strip().lower() in _FALSE_VALUES:
        return ""
    now_ts = time.time()
    with _CACHE_LOCK:
        cached = _FETCH_CACHE.get(url)
        if cached and now_ts - cached[0] <= max(30, int(ttl_seconds)):
            return cached[1]
    try:
        import requests
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 TINO-V13-MacroResult/1.0"},
        )
        response.raise_for_status()
        text = response.text
    except Exception:
        text = ""
    with _CACHE_LOCK:
        _FETCH_CACHE[url] = (now_ts, text)
    return text


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        return _normalise_text(BeautifulSoup(html, "html.parser").get_text(" "))
    except Exception:
        return _normalise_text(re.sub(r"<[^>]+>", " ", html))


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
            # Median-like robust centre without importing numpy.
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
    text = _html_to_text(_fetch_text(url, ttl_seconds=180))
    if not text:
        return {}
    parsed = parse_bls_cpi_text(text) if code == "CPI" else parse_bls_ppi_text(text)
    if release_date and parsed.get("release_date") and str(parsed.get("release_date")) != release_date:
        return {}
    parsed.update({"source": "BLS_OFFICIAL", "source_url": url, "official_confirmed": True})
    return parsed


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
        # Permit overnight Taipei conversion around the US release date.
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
]
