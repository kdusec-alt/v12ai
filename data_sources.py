# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List
from ticker_resolver import resolve_ticker
from models import PriceFrame, NewsItem
from data_sources_tw import fetch_tw_price, fetch_tw_news
from data_sources_us import fetch_us_price, fetch_us_news
from data_sources_etf import fetch_etf_price, fetch_etf_news


def fetch_price(raw_ticker: str) -> PriceFrame:
    ticker = resolve_ticker(raw_ticker)
    if ticker.asset_type == "etf":
        return fetch_etf_price(ticker)
    if ticker.market == "TW":
        return fetch_tw_price(ticker)
    return fetch_us_price(ticker)


def fetch_news(raw_ticker: str) -> List[NewsItem]:
    ticker = resolve_ticker(raw_ticker)
    if ticker.asset_type == "etf":
        return fetch_etf_news(ticker)
    if ticker.market == "TW":
        return fetch_tw_news(ticker)
    return fetch_us_news(ticker)
