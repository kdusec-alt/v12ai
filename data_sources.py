# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Dict, List

from ticker_resolver import (
    resolve_ticker,
    is_unmapped_tw_numeric,
    alternate_tw_ticker,
)
from models import PriceFrame, NewsItem, TickerInfo
from data_sources_tw import fetch_tw_price, fetch_tw_news
from data_sources_us import fetch_us_price, fetch_us_news
from data_sources_etf import fetch_etf_price, fetch_etf_news

try:
    from global_event_scanner import fetch_global_event_news, ensure_global_macro_calendar
except Exception:
    def fetch_global_event_news(*, force_refresh: bool = False):
        return []
    def ensure_global_macro_calendar():
        return None

try:
    from ticker_event_exposure import annotate_global_event_news
except Exception:
    def annotate_global_event_news(ticker, rows):
        return list(rows or [])

try:
    from market_shock_indicator import annotate_market_shock_news
except Exception:
    def annotate_market_shock_news(rows):
        return list(rows or [])

try:
    from event_intelligence_v1062 import install_event_intelligence_v1062
    install_event_intelligence_v1062()
except Exception:
    pass

try:
    from event_reassessment_v1062 import install_event_reassessment_v1062
    install_event_reassessment_v1062()
except Exception:
    pass

try:
    from decision_narrative_v1062 import install_decision_narrative_v1062
    install_decision_narrative_v1062()
except Exception:
    pass

try:
    from ui_event_status_v1062 import inject_event_status_css
except Exception:
    def inject_event_status_css():
        return None

# Process-local routing cache. It contains only compact TickerInfo objects and
# avoids probing both exchanges again during the subsequent fetch_news call.
_RESOLVED_ROUTE_CACHE: Dict[str, TickerInfo] = {}


def _cache_key(raw_ticker: str) -> str:
    return str(raw_ticker or "").strip().upper().replace(" ", "")


def _price_usable(frame: PriceFrame | None) -> bool:
    if frame is None:
        return False
    truth = getattr(frame, "truth", None)
    context = getattr(frame, "context", None) or {}
    return bool(
        float(getattr(frame, "last", 0.0) or 0.0) > 0
        and bool(getattr(truth, "accepted", False))
        and not bool(context.get("invalid_price"))
        and not bool((context.get("price_meta") or {}).get("decision_blocked"))
    )


def _fetch_by_ticker(ticker: TickerInfo) -> PriceFrame:
    # Taiwan ETFs keep the dedicated TW route. US ETFs must stay on the US
    # pipeline because their quote/news/fund metadata are not compatible with
    # TWSE/TPEX sources. Asset type may be upgraded dynamically after Yahoo
    # metadata is read, so this branch must remain market-aware.
    if ticker.market == "TW" and ticker.asset_type == "etf":
        return fetch_etf_price(ticker)
    if ticker.market == "TW":
        return fetch_tw_price(ticker)
    return fetch_us_price(ticker)


def fetch_price(raw_ticker: str) -> PriceFrame:
    # Ensure the shared macro calendar includes scheduled Global Event Core rows
    # before any TW/US price context is built. Offline tests remain unchanged.
    try:
        ensure_global_macro_calendar()
    except Exception:
        pass
    key = _cache_key(raw_ticker)
    cached = _RESOLVED_ROUTE_CACHE.get(key)
    if cached is not None:
        return _fetch_by_ticker(cached)

    ticker = resolve_ticker(raw_ticker)
    primary = _fetch_by_ticker(ticker)
    if ticker.market != "TW" or ticker.asset_type == "etf" or not is_unmapped_tw_numeric(raw_ticker):
        _RESOLVED_ROUTE_CACHE[key] = getattr(primary, "ticker", ticker)
        return primary

    # Unknown plain numeric code: only probe the other Taiwan market if the
    # primary route is unusable. This keeps normal analysis fast and prevents
    # an unknown TPEx/emerging code from being silently treated as TWSE.
    if _price_usable(primary):
        _RESOLVED_ROUTE_CACHE[key] = getattr(primary, "ticker", ticker)
        return primary
    alternate = alternate_tw_ticker(ticker)
    if alternate is None:
        return primary
    secondary = _fetch_by_ticker(alternate)
    if _price_usable(secondary):
        _RESOLVED_ROUTE_CACHE[key] = getattr(secondary, "ticker", alternate)
        return secondary
    return primary


def _news_identity(item: NewsItem) -> str:
    title = re.sub(r"\s+[-–—]\s+[^-–—]{2,60}$", "", str(getattr(item, "title", "") or "").lower())
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", title).strip()


def _merge_news(primary: List[NewsItem], global_rows: List[NewsItem], limit: int = 24) -> List[NewsItem]:
    """Put market-wide events first without duplicating company/news routes."""
    out: List[NewsItem] = []
    seen: set[str] = set()
    for item in [*(global_rows or []), *(primary or [])]:
        key = _news_identity(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def fetch_news(raw_ticker: str, force_refresh: bool = False) -> List[NewsItem]:
    # The five-minute status line is operational state, not muted helper text.
    inject_event_status_css()

    key = _cache_key(raw_ticker)
    ticker = _RESOLVED_ROUTE_CACHE.get(key) or resolve_ticker(raw_ticker)
    if ticker.market == "TW" and ticker.asset_type == "etf":
        primary = fetch_etf_news(ticker, force_refresh=force_refresh)
    elif ticker.market == "TW":
        primary = fetch_tw_news(ticker, force_refresh=force_refresh)
    else:
        primary = fetch_us_news(ticker, force_refresh=force_refresh)

    # Independent route: the watcher sees oil/tariff/PMI even when the headline
    # never names the active stock. V1062 attaches both ticker exposure DNA and
    # a strong market-shock level so oil spikes/war outrank ordinary headlines.
    try:
        global_rows = fetch_global_event_news(force_refresh=force_refresh)
        global_rows = annotate_global_event_news(ticker, global_rows)
        global_rows = annotate_market_shock_news(global_rows)
    except Exception:
        global_rows = []
    return _merge_news(list(primary or []), list(global_rows or []))
