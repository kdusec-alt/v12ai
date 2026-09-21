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
from exchange_rule_engine_v1069 import attach_exchange_rule_context
from price_truth_v1072 import attach_price_truth

try:
    from emerging_session_v1070 import install_emerging_session_v1070
    fetch_tw_price = install_emerging_session_v1070(fetch_tw_price)
except Exception:
    pass

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
    from market_shock_levels_v1062 import (
        annotate_market_shock_news_v1062 as annotate_market_shock_news,
        install_market_shock_levels_v1062,
    )
    install_market_shock_levels_v1062()
except Exception:
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
    from v1068_runtime_patches import install_v1068_runtime_patches
    install_v1068_runtime_patches()
except Exception:
    pass

try:
    from ui_event_status_v1062 import inject_event_status_css
except Exception:
    def inject_event_status_css():
        return None

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


def _attach_exchange_rule(frame: PriceFrame) -> PriceFrame:
    """Add a read-only exchange-rule snapshot without changing forecast inputs."""
    if frame is None:
        return frame
    try:
        context = dict(getattr(frame, "context", {}) or {})
        meta = dict(context.get("price_meta") or {}) if isinstance(context.get("price_meta"), dict) else {}
        source = str(meta.get("source") or getattr(getattr(frame, "truth", None), "source", "") or "")
        closes = list(getattr(frame, "recent_closes", []) or [])
        historical_previous_close = closes[-2] if len(closes) >= 2 else None
        decorated = attach_exchange_rule_context(
            context,
            frame.ticker,
            reference_price=frame.previous_close,
            current_price=frame.last,
            reference_source=source,
            historical_previous_close=historical_previous_close,
            market_status=frame.market_status,
            price_date=frame.price_date,
            quote_name=frame.ticker.name,
        )
        meta["exchange_rule"] = dict(decorated.get("exchange_rule") or {})
        decorated["price_meta"] = meta
        frame.context = decorated
    except Exception:
        pass
    return frame


def _fetch_by_ticker(ticker: TickerInfo) -> PriceFrame:
    if ticker.market == "TW" and ticker.asset_type == "etf":
        frame = fetch_etf_price(ticker)
    elif ticker.market == "TW":
        frame = fetch_tw_price(ticker)
    else:
        frame = fetch_us_price(ticker)
    # Exchange rules and the session-aware price truth describe the same final
    # quote.  Attach them only after the market route has finished so every UI
    # and decision consumer reads one immutable price basis.
    return attach_price_truth(_attach_exchange_rule(frame))


def fetch_price(raw_ticker: str) -> PriceFrame:
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
    out: List[NewsItem] = []
    seen: set[str] = set()
    # Direct ticker evidence owns the first slots. Global Event rows remain as
    # Macro/Policy context, but cannot starve company catalysts.
    for item in [*(primary or []), *(global_rows or [])]:
        key = _news_identity(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def fetch_news(raw_ticker: str, force_refresh: bool = False) -> List[NewsItem]:
    inject_event_status_css()
    key = _cache_key(raw_ticker)
    ticker = _RESOLVED_ROUTE_CACHE.get(key) or resolve_ticker(raw_ticker)
    if ticker.market == "TW" and ticker.asset_type == "etf":
        primary = fetch_etf_news(ticker, force_refresh=force_refresh)
    elif ticker.market == "TW":
        primary = fetch_tw_news(ticker, force_refresh=force_refresh)
    else:
        primary = fetch_us_news(ticker, force_refresh=force_refresh)

    try:
        global_rows = fetch_global_event_news(force_refresh=force_refresh)
        global_rows = annotate_global_event_news(ticker, global_rows)
        global_rows = annotate_market_shock_news(global_rows)
    except Exception:
        global_rows = []
    return _merge_news(list(primary or []), list(global_rows or []))
