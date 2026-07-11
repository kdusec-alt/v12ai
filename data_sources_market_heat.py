# -*- coding: utf-8 -*-
"""TINO V12 market heat — direct Yahoo reader, isolated from chip modules.

This module performs one small synchronous Yahoo request with a short timeout.
It never starts threads, never writes cache files, and never modifies institution
or individual-stock margin data.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
import re
import time
from typing import Any, Dict, List, Sequence, Tuple

import requests
from bs4 import BeautifulSoup

_URL = "https://tw.stock.yahoo.com/margin-balance/"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)
_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_NUM_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")


def _num(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not _NUM_RE.fullmatch(text):
        return None
    try:
        return float(text.rstrip("%"))
    except Exception:
        return None


def _iso(value: Any) -> str:
    text = str(value or "").replace("/", "-")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except Exception:
        return ""


@lru_cache(maxsize=4)
def _yahoo_tokens(ten_minute_bucket: int) -> Tuple[str, ...]:
    del ten_minute_bucket
    response = requests.get(
        _URL,
        timeout=(2.5, 5.0),
        headers={
            "User-Agent": _UA,
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
            "Cache-Control": "no-cache",
        },
    )
    response.raise_for_status()
    if len(response.content) < 3000:
        raise RuntimeError("Yahoo market-margin page too small")
    soup = BeautifulSoup(response.text, "html.parser")
    return tuple(s.strip() for s in soup.stripped_strings if s and s.strip())


def _parse_rows(tokens: Sequence[str]) -> List[Tuple[str, float, float]]:
    try:
        start = tokens.index("融資餘額變化")
    except ValueError as exc:
        raise RuntimeError("Yahoo 融資餘額變化 marker missing") from exc

    rows: List[Tuple[str, float, float]] = []
    i = start + 1
    while i < len(tokens):
        if _DATE_RE.fullmatch(tokens[i]):
            nums: List[float] = []
            j = i + 1
            while j < len(tokens) and not _DATE_RE.fullmatch(tokens[j]) and j - i <= 20:
                n = _num(tokens[j])
                if n is not None:
                    nums.append(n)
                j += 1
            if len(nums) >= 2:
                change_yi, balance_yi = nums[0], nums[1]
                if 500.0 <= balance_yi <= 10000.0 and abs(change_yi) <= 1000.0:
                    rows.append((_iso(tokens[i]), balance_yi, change_yi))
            i = j
            continue
        i += 1
    if not rows:
        raise RuntimeError("Yahoo market-margin rows missing")
    rows.sort(key=lambda row: row[0])
    return rows


def _classify(balance: float) -> Dict[str, Any]:
    if balance < 6000:
        return {"icon": "🟢", "level": "偏健康", "note": "可積極布局", "risk_score": 20}
    if balance < 6100:
        return {"icon": "🟡", "level": "中性", "note": "觀察法人是否同步", "risk_score": 40}
    if balance < 6300:
        return {"icon": "🟡", "level": "中性偏熱", "note": "觀察外資是否同步買超", "risk_score": 55}
    if balance < 6500:
        return {"icon": "🟠", "level": "偏熱", "note": "法人沒跟容易震盪", "risk_score": 72}
    return {"icon": "🔴", "level": "警戒", "note": "留意融資殺盤", "risk_score": 90}


def fetch_tw_market_heat(price_date: str = "") -> Dict[str, Any]:
    try:
        rows = _parse_rows(_yahoo_tokens(int(time.time() // 600)))
        cutoff = _iso(price_date)
        if cutoff:
            eligible = [row for row in rows if row[0] <= cutoff]
            if eligible:
                rows = eligible
        data_date, balance, change = rows[-1]
        cls = _classify(balance)
        return {
            "accepted": True,
            "source": "Yahoo股市資券餘額",
            "date": data_date,
            "balance_yi": round(balance, 2),
            "change_yi": round(change, 2),
            **cls,
        }
    except Exception as exc:
        return {
            "accepted": False,
            "source": "Yahoo股市資券餘額",
            "date": _iso(price_date),
            "reason": f"Yahoo市場融資暫時無法同步：{type(exc).__name__}",
            "risk_score": 0,
        }


def market_heat_radar_line(heat: Dict[str, Any]) -> str:
    if not isinstance(heat, dict) or not heat.get("accepted"):
        return "Yahoo市場融資暫時無法同步｜先看法人/VWAP/資券"
    balance = float(heat.get("balance_yi") or 0.0)
    change = float(heat.get("change_yi") or 0.0)
    data_date = str(heat.get("date") or "")
    date_text = data_date[5:].replace("-", "/") if len(data_date) >= 10 else "待確認"
    return (
        f"{heat.get('icon', '🟡')} 上市融資 {balance:,.2f}億｜"
        f"單日 {change:+,.2f}億｜{heat.get('level', '觀察')}｜"
        f"{heat.get('note', '觀察法人是否同步')}｜資料日 {date_text}｜Yahoo"
    )
