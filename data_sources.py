# -*- coding: utf-8 -*-
from __future__ import annotations

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

# Process-local routing cache.  It contains only compact TickerInfo objects and
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
    if ticker.asset_type == "etf":
        return fetch_etf_price(ticker)
    if ticker.market == "TW":
        return fetch_tw_price(ticker)
    return fetch_us_price(ticker)


def fetch_price(raw_ticker: str) -> PriceFrame:
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
    # primary route is unusable.  This keeps normal analysis fast and prevents
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


def fetch_news(raw_ticker: str) -> List[NewsItem]:
    key = _cache_key(raw_ticker)
    ticker = _RESOLVED_ROUTE_CACHE.get(key) or resolve_ticker(raw_ticker)
    if ticker.asset_type == "etf":
        return fetch_etf_news(ticker)
    if ticker.market == "TW":
        return fetch_tw_news(ticker)
    return fetch_us_news(ticker)
