# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List
from models import PriceFrame, TickerInfo, NewsItem
from data_sources_tw import fetch_tw_price, fetch_tw_news


def fetch_etf_price(ticker: TickerInfo) -> PriceFrame:
    price = fetch_tw_price(ticker)
    price.context["etf_mode"] = True
    price.context["distribution"] = "ETF Mode：成分股 / 溢折價 / 流動性 / 除息"
    return price


def fetch_etf_news(ticker: TickerInfo) -> List[NewsItem]:
    rows = fetch_tw_news(ticker)
    return rows[:3]
