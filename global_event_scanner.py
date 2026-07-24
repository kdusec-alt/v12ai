# -*- coding: utf-8 -*-
"""Ticker-independent Global Event Core for TINO V12.

This module is deliberately additive. It scans market-wide commodity, trade,
and macro-event evidence independently of the currently analysed ticker, then
returns ordinary ``NewsItem`` rows so the established V12 routes can reuse the
same evidence in:

    Event Watch -> Event Lifecycle -> Macro/Policy -> Quantum -> AI narrative

The scanner never writes Prediction/Audit/Research memory and never sets a stock
price direction by itself. Price reality and existing cross-market confirmation
remain the final veto.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from models import NewsItem

_TAIPEI = ZoneInfo("Asia/Taipei")
_NEW_YORK = ZoneInfo("America/New_York")
_CACHE_TTL_SECONDS = 4 * 60
_CACHE: tuple[float, List[NewsItem]] | None = None
_PUBLISHED_LIFECYCLE_FPS: set[str] = set()
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TINO-Global-Event-Core/1.0)",
    "Accept": "application/json, application/rss+xml, application/xml, text/xml, */*",
}

_DEFAULT_MACRO_EVENTS = (
    {
        "code": "SP_GLOBAL_US_MFG_PMI_FLASH",
        "name": "S&P Global美國製造業PMI初值（預期54.5）",
        "datetime": "2026-07-24T09:45:00",
        "timezone": "America/New_York",
        "tier": 1,
    },
)

_GOOGLE_QUERIES: Sequence[Tuple[str, str]] = (
    ("oil prices WTI Brent Middle East supply disruption", "energy"),
    ("Taiwan tariff Section 301 10 percent trade", "trade_tariff"),
    ("S&P Global US flash PMI July 2026", "macro_pmi"),
)


def _now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(_TAIPEI)
    if now.tzinfo is None:
        return now.replace(tzinfo=_TAIPEI)
    return now.astimezone(_TAIPEI)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _json_request(url: str, timeout: float = 4.0) -> Mapping[str, Any]:
    request = Request(url, headers=_DEFAULT_HEADERS)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _text_request(url: str, timeout: float = 4.0) -> str:
    request = Request(url, headers=_DEFAULT_HEADERS)
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _merge_macro_calendar_defaults() -> None:
    """Add missing scheduled releases without overwriting deployment config."""
    raw = str(os.environ.get("TINO_MACRO_EVENTS_JSON") or "").strip()
    try:
        rows = json.loads(raw) if raw else []
    except Exception:
        rows = []
    if not isinstance(rows, list):
        rows = []
    existing = {
        (str(row.get("code") or "").upper(), str(row.get("datetime") or ""))
        for row in rows
        if isinstance(row, Mapping)
    }
    changed = False
    for row in _DEFAULT_MACRO_EVENTS:
        key = (str(row["code"]).upper(), str(row["datetime"]))
        if key not in existing:
            rows.append(dict(row))
            existing.add(key)
            changed = True
    if changed or not raw:
        os.environ["TINO_MACRO_EVENTS_JSON"] = json.dumps(rows, ensure_ascii=False)


def ensure_global_macro_calendar() -> None:
    """Install missing macro releases for live analysis, never for offline tests."""
    if str(os.environ.get("TINO_OFFLINE_TEST") or "").strip() == "1":
        return
    _merge_macro_calendar_defaults()


def _quote_change(symbol: str) -> Dict[str, Any]:
    encoded = quote(symbol, safe="")
    params = urlencode({"range": "5d", "interval": "15m", "includePrePost": "true"})
    payload = _json_request(f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?{params}")
    result = (((payload.get("chart") or {}).get("result") or [None])[0]) or {}
    meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
    current = meta.get("regularMarketPrice")
    previous = meta.get("previousClose")
    if previous in (None, 0, ""):
        previous = meta.get("chartPreviousClose")
    try:
        current_f = float(current)
        previous_f = float(previous)
    except Exception:
        current_f = previous_f = 0.0
    if current_f <= 0 or previous_f <= 0:
        closes = []
        for block in (((result.get("indicators") or {}).get("quote") or [])):
            if isinstance(block, Mapping):
                closes.extend(float(v) for v in (block.get("close") or []) if v not in (None, ""))
        if closes:
            current_f = closes[-1]
        if previous_f <= 0 and len(closes) >= 2:
            previous_f = closes[-2]
    pct = ((current_f / previous_f) - 1.0) * 100.0 if current_f > 0 and previous_f > 0 else 0.0
    return {
        "symbol": symbol,
        "price": round(current_f, 4),
        "previous": round(previous_f, 4),
        "pct": round(pct, 4),
        "market_time": meta.get("regularMarketTime"),
    }


def _severity_for_move(move: float) -> int:
    magnitude = abs(float(move))
    if magnitude >= 6.0:
        return 4
    if magnitude >= 3.0:
        return 3
    if magnitude >= 1.5:
        return 2
    return 0


def _global_tag(family: str, severity: int, event_id: str, *labels: str) -> str:
    parts = [
        "global_event_core",
        f"family={family}",
        f"severity={int(severity)}",
        f"eventid={event_id}",
        *[label for label in labels if label],
    ]
    return "|".join(parts)


def _oil_event(now: datetime) -> NewsItem | None:
    quotes: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_quote_change, "CL=F"): "WTI",
            executor.submit(_quote_change, "BZ=F"): "Brent",
        }
        for future, label in futures.items():
            try:
                quotes[label] = future.result()
            except Exception:
                continue
    valid = [row for row in quotes.values() if float(row.get("price") or 0.0) > 0]
    if not valid:
        return None
    lead_move = max((float(row.get("pct") or 0.0) for row in valid), key=abs)
    severity = _severity_for_move(lead_move)
    if severity < 2:
        return None
    rising = lead_move > 0
    score = {2: -0.12, 3: -0.18, 4: -0.24}[severity] if rising else {2: 0.06, 3: 0.10, 4: 0.14}[severity]
    quote_text = "／".join(
        f"{name} {float(row.get('pct') or 0.0):+.2f}%"
        for name, row in (("WTI", quotes.get("WTI", {})), ("Brent", quotes.get("Brent", {})))
        if float(row.get("price") or 0.0) > 0
    )
    day_key = now.strftime("%Y%m%d")
    if rising:
        title = (
            f"Global Event Core｜中東/美伊供應風險推升油價｜{quote_text}｜"
            "傳導：能源成本與通膨預期↑→降息空間↓→殖利率/美元壓力↑→科技、半導體與記憶體估值承壓；價格可否決"
        )
    else:
        title = (
            f"Global Event Core｜油價快速回落｜{quote_text}｜"
            "傳導：能源成本與通膨預期↓→利率壓力緩和；仍待美債、美元與股價確認"
        )
    return NewsItem(
        "TINO_GlobalEventCore_YahooFinance",
        now.isoformat(timespec="seconds"),
        round(score, 3),
        _global_tag(
            "energy",
            severity,
            f"oil_supply_shock_{day_key}",
            "daily_headline",
            "policy_geo",
            "macro_event",
            "oil_price_up" if rising else "oil_price_down",
        ),
        title,
        "https://finance.yahoo.com/quote/CL=F/",
    )


def _tariff_seed(now: datetime) -> NewsItem | None:
    start = datetime(2026, 7, 23, 0, 0, tzinfo=_TAIPEI)
    end = datetime(2026, 8, 15, 23, 59, tzinfo=_TAIPEI)
    if not (start <= now <= end):
        return None
    title = (
        "Global Event Core｜美國Section 301對台產品10%稅負架構｜"
        "依品項為MFN與新增稅率合計至10%，並非所有商品一律額外加10%，豁免品項另計｜"
        "傳導：出口成本/轉嫁能力→毛利率→訂單移轉→電子與半導體供應鏈差異化；價格與實際公司曝險負責驗證"
    )
    return NewsItem(
        "US Government USTR Official GlobalEventCore",
        now.isoformat(timespec="seconds"),
        -0.16,
        _global_tag(
            "trade_tariff",
            3,
            "ustr_tw_section301_10_20260723",
            "daily_headline",
            "policy_geo",
            "trade_controls",
            "tariff",
        ),
        title,
        "https://ustr.gov/",
    )


def _pmi_seed(now: datetime) -> NewsItem | None:
    release = datetime(2026, 7, 24, 9, 45, tzinfo=_NEW_YORK).astimezone(_TAIPEI)
    delta_hours = (release - now).total_seconds() / 3600.0
    if delta_hours < -4.0 or delta_hours > 18.0:
        return None
    if delta_hours >= 0:
        countdown = max(0, int((release - now).total_seconds() // 60))
        phase = f"倒數{countdown}分鐘"
        scenario = "高於預期→成長/美元/殖利率可能上行；明顯低於預期→衰退疑慮與Risk-Off；方向等待公布與價格確認"
    else:
        phase = "已公布，等待正式數值與市場反應確認"
        scenario = "禁止用預期值代替實際值；由殖利率、美元、SOX/NQ與個股價格驗證"
    title = (
        f"Global Event Core｜美國7月S&P Global製造業PMI初值｜台灣21:45｜預期54.5｜{phase}｜{scenario}"
    )
    return NewsItem(
        "S&P_Global_MacroCalendar",
        release.isoformat(timespec="seconds"),
        0.0,
        _global_tag(
            "macro_pmi",
            2,
            "sp_global_us_mfg_pmi_flash_20260724",
            "daily_headline",
            "macro_event",
            "pmi_pending",
        ),
        title,
        "https://www.spglobal.com/marketintelligence/en/mi/products/pmi.html",
    )


def _parse_rss_date(value: str, fallback: datetime) -> datetime:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_TAIPEI)
        return parsed.astimezone(_TAIPEI)
    except Exception:
        return fallback


def _headline_family(title: str, hinted: str) -> str:
    text = title.lower()
    if any(term in text for term in ("wti", "brent", "crude", "oil price", "油價", "原油")):
        return "energy"
    if any(term in text for term in ("tariff", "section 301", "trade war", "關稅")):
        return "trade_tariff"
    if "pmi" in text or "purchasing managers" in text:
        return "macro_pmi"
    return hinted


def _headline_signal(title: str, family: str) -> Tuple[float, int, Sequence[str]]:
    text = title.lower()
    if family == "energy":
        up = any(term in text for term in ("surge", "jump", "rise", "climb", "spike", "上漲", "飆升", "大漲"))
        disruption = any(term in text for term in ("disrupt", "supply risk", "attack", "war", "iran", "hormuz", "中東", "伊朗", "斷供"))
        if up:
            severity = 3 if disruption else 2
            return (-0.18 if severity == 3 else -0.12), severity, ("daily_headline", "policy_geo", "macro_event", "oil_price_up")
    if family == "trade_tariff":
        return -0.16, 3, ("daily_headline", "policy_geo", "trade_controls", "tariff")
    if family == "macro_pmi":
        negative = any(term in text for term in ("miss", "slump", "contracts", "weaker", "低於預期", "萎縮"))
        positive = any(term in text for term in ("beats", "expands", "stronger", "高於預期", "擴張"))
        score = -0.12 if negative else 0.10 if positive else 0.0
        return score, 2, ("daily_headline", "macro_event", "pmi_update")
    return 0.0, 0, ()


def _google_rss(query_text: str, hinted_family: str, now: datetime) -> List[NewsItem]:
    params = urlencode({"q": f"{query_text} when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    xml_text = _text_request(f"https://news.google.com/rss/search?{params}", timeout=4.0)
    root = ET.fromstring(xml_text)
    rows: List[NewsItem] = []
    for item in root.findall(".//item")[:8]:
        title = _clean(item.findtext("title"))
        if not title:
            continue
        family = _headline_family(title, hinted_family)
        score, severity, labels = _headline_signal(title, family)
        if severity < 2:
            continue
        published = _parse_rss_date(_clean(item.findtext("pubDate")), now)
        source_node = item.find("source")
        publisher = _clean(source_node.text if source_node is not None else "GoogleNews")
        normalized = re.sub(r"[^0-9a-z]+", "", title.lower())
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
        event_id = f"wire_{family}_{published.strftime('%Y%m%d')}_{digest}"
        rows.append(NewsItem(
            f"GoogleNewsGlobal/{publisher}",
            published.isoformat(timespec="seconds"),
            score,
            _global_tag(family, severity, event_id, *labels),
            title,
            _clean(item.findtext("link")),
        ))
    return rows


def _news_rows(now: datetime) -> List[NewsItem]:
    out: List[NewsItem] = []
    with ThreadPoolExecutor(max_workers=len(_GOOGLE_QUERIES)) as executor:
        jobs = {
            executor.submit(_google_rss, query, family, now): family
            for query, family in _GOOGLE_QUERIES
        }
        for future in as_completed(jobs):
            try:
                out.extend(future.result())
            except Exception:
                continue
    return out


def _dedupe(rows: Iterable[NewsItem], limit: int = 12) -> List[NewsItem]:
    def severity(item: NewsItem) -> int:
        match = re.search(r"severity=(\d+)", str(item.tag or ""))
        return int(match.group(1)) if match else 0

    ordered = sorted(
        list(rows),
        key=lambda row: (severity(row), abs(float(row.score or 0.0)), str(row.time or "")),
        reverse=True,
    )
    seen_title: set[str] = set()
    seen_event: set[str] = set()
    out: List[NewsItem] = []
    for row in ordered:
        title_key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(row.title or "").lower()).strip()
        event_match = re.search(r"eventid=([^|]+)", str(row.tag or ""))
        event_key = event_match.group(1) if event_match else ""
        if title_key in seen_title or (event_key and event_key in seen_event):
            continue
        seen_title.add(title_key)
        if event_key:
            seen_event.add(event_key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _inject_event_watch_css() -> None:
    """Make recent event rows readable without coupling the scanner to app.py."""
    try:
        import streamlit as st
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is None:
            return
        st.markdown(
            """
            <style>
            [data-testid="stExpander"] [data-testid="stCaptionContainer"],
            [data-testid="stExpander"] [data-testid="stCaptionContainer"] p,
            [data-testid="stExpander"] [data-testid="stCaptionContainer"] span,
            [data-testid="stExpander"] [data-testid="stCaptionContainer"] div {
                color:#f4fbff !important;
                opacity:1 !important;
                font-weight:800 !important;
                text-shadow:0 0 10px rgba(90,220,255,.18) !important;
            }
            [data-testid="stExpander"] [data-testid="stCaptionContainer"] {
                background:rgba(7,23,39,.82) !important;
                border-left:3px solid rgba(255,217,106,.78) !important;
                border-radius:8px !important;
                padding:7px 10px !important;
                margin:4px 0 !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        return


def _publish_initial_lifecycle(rows: Sequence[NewsItem], now: datetime) -> None:
    """Seed Admin recent-event UI on first analysis without a rerun loop."""
    if not rows:
        return
    try:
        import streamlit as st
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx() is None or not bool(st.session_state.get("admin_authenticated", False)):
            return
        from event_reassessment import assess_event_delta, event_fingerprint
        from event_lifecycle import update_global_event_state
        fresh = [row for row in rows if event_fingerprint(row) not in _PUBLISHED_LIFECYCLE_FPS]
        if not fresh:
            return
        plan = assess_event_delta([], fresh, ticker="GLOBAL", market="GLOBAL", now=now)
        update_global_event_state(plan, ticker="GLOBAL", market="GLOBAL")
        _PUBLISHED_LIFECYCLE_FPS.update(event_fingerprint(row) for row in fresh)
    except Exception:
        return


def fetch_global_event_news(*, force_refresh: bool = False, now: datetime | None = None) -> List[NewsItem]:
    """Return bounded market-wide evidence independent of the active ticker."""
    global _CACHE
    reference = _now(now)
    _inject_event_watch_css()
    ensure_global_macro_calendar()
    if (
        str(os.environ.get("TINO_OFFLINE_TEST") or "").strip() == "1"
        and str(os.environ.get("TINO_GLOBAL_EVENT_TEST") or "").strip() != "1"
    ):
        return []
    now_epoch = time.time()
    if not force_refresh and _CACHE and now_epoch - _CACHE[0] < _CACHE_TTL_SECONDS:
        return list(_CACHE[1])

    rows: List[NewsItem] = []
    for builder in (_oil_event, _tariff_seed, _pmi_seed):
        try:
            item = builder(reference)
            if item is not None:
                rows.append(item)
        except Exception:
            continue
    try:
        rows.extend(_news_rows(reference))
    except Exception:
        pass
    result = _dedupe(rows, 12)
    _publish_initial_lifecycle(result, reference)
    _CACHE = (now_epoch, result)
    return list(result)
