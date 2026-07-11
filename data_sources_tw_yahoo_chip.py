# -*- coding: utf-8 -*-
"""Yahoo Taiwan chip-data fallback for TINO V12.

Scope is intentionally narrow:
- individual-stock institutional trading
- individual-stock margin / short-sale changes

No Streamlit calls, no threads, no file writes, no pandas/pyarrow.
Yahoo failure raises a normal exception so data_sources_tw can fall back to FinMind.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
import re
import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import requests
from bs4 import BeautifulSoup

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)
_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_NUM_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")


def _number(text: Any) -> float | None:
    s = str(text or "").strip().replace(",", "").replace("％", "%")
    if not _NUM_RE.fullmatch(s):
        return None
    try:
        return float(s.rstrip("%"))
    except Exception:
        return None


def _iso(text: str) -> str:
    try:
        return date.fromisoformat(text.replace("/", "-")[:10]).isoformat()
    except Exception:
        return ""


def _sum_last(values: Sequence[int], n: int) -> int:
    return int(sum(values[-n:])) if values else 0


def _streak(values: Sequence[int], buy_word: str, sell_word: str) -> str:
    if not values or values[-1] == 0:
        return "方向觀察"
    positive = values[-1] > 0
    count = 0
    for value in reversed(values):
        if value == 0 or (value > 0) != positive:
            break
        count += 1
    return f"連{buy_word if positive else sell_word}{max(count, 1)}天"


def _filter_rows(rows: List[Tuple[str, List[float]]], price_date: str) -> List[Tuple[str, List[float]]]:
    cutoff = _iso(price_date)
    if cutoff:
        usable = [row for row in rows if _iso(row[0]) and _iso(row[0]) <= cutoff]
        if usable:
            rows = usable
    rows.sort(key=lambda item: item[0])
    return rows[-10:]


def _rows_after(tokens: Sequence[str], marker: str, min_numbers: int) -> List[Tuple[str, List[float]]]:
    try:
        start = tokens.index(marker)
    except ValueError as exc:
        raise RuntimeError(f"Yahoo marker missing: {marker}") from exc

    rows: List[Tuple[str, List[float]]] = []
    i = start + 1
    while i < len(tokens):
        tok = tokens[i]
        if _DATE_RE.fullmatch(tok):
            numbers: List[float] = []
            j = i + 1
            while j < len(tokens) and not _DATE_RE.fullmatch(tokens[j]) and j - i <= 28:
                value = _number(tokens[j])
                if value is not None:
                    numbers.append(value)
                j += 1
            if len(numbers) >= min_numbers:
                rows.append((tok, numbers))
            i = j
            continue
        i += 1
    if not rows:
        raise RuntimeError(f"Yahoo rows missing after: {marker}")
    return rows


@lru_cache(maxsize=24)
def _tokens(url: str, ten_minute_bucket: int) -> Tuple[str, ...]:
    del ten_minute_bucket
    response = requests.get(
        url,
        timeout=(2.5, 5.0),
        headers={
            "User-Agent": _UA,
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.5",
            "Cache-Control": "no-cache",
        },
    )
    response.raise_for_status()
    if len(response.content) < 3000:
        raise RuntimeError("Yahoo page too small")
    soup = BeautifulSoup(response.text, "html.parser")
    return tuple(s.strip() for s in soup.stripped_strings if s and s.strip())


def _get_tokens(url: str) -> Tuple[str, ...]:
    return _tokens(url, int(time.time() // 600))


def fetch_yahoo_institutional(symbol: str, price_date: str = "") -> Dict[str, object]:
    url = f"https://tw.stock.yahoo.com/quote/{symbol}/institutional-trading"
    rows = _filter_rows(_rows_after(_get_tokens(url), "法人逐日買賣超", 4), price_date)
    if not rows:
        raise RuntimeError("Yahoo institutional rows empty")

    dates = [_iso(d) for d, _ in rows]
    foreign = [int(round(nums[0])) for _, nums in rows]
    trust = [int(round(nums[1])) for _, nums in rows]
    dealer = [int(round(nums[2])) for _, nums in rows]

    return {
        "foreign": foreign[-1],
        "foreign_3": _sum_last(foreign, 3),
        "foreign_5": _sum_last(foreign, 5),
        "foreign_10": _sum_last(foreign, 10),
        "foreign_streak": _streak(foreign, "買", "賣"),
        "trust": trust[-1],
        "trust_3": _sum_last(trust, 3),
        "trust_5": _sum_last(trust, 5),
        "trust_10": _sum_last(trust, 10),
        "trust_streak": _streak(trust, "買", "賣"),
        "dealer": dealer[-1],
        "dealer_3": _sum_last(dealer, 3),
        "dealer_5": _sum_last(dealer, 5),
        "dealer_10": _sum_last(dealer, 10),
        "dealer_streak": _streak(dealer, "買", "賣"),
        "source": "YahooInstitutional",
        "date": dates[-1],
        "accepted": True,
        "reason": "法人同步｜Yahoo法人逐日買賣超",
        "symbol": symbol,
    }


def fetch_yahoo_margin(symbol: str, price_date: str = "") -> Dict[str, object]:
    url = f"https://tw.stock.yahoo.com/quote/{symbol}/margin"
    rows = _filter_rows(_rows_after(_get_tokens(url), "資券餘額逐日增減", 7), price_date)
    if not rows:
        raise RuntimeError("Yahoo margin rows empty")

    dates = [_iso(d) for d, _ in rows]
    margin = [int(round(nums[0])) for _, nums in rows]
    short = [int(round(nums[3])) for _, nums in rows]
    ratio = float(rows[-1][1][6])

    return {
        "margin": margin[-1],
        "margin_3": _sum_last(margin, 3),
        "margin_5": _sum_last(margin, 5),
        "margin_10": _sum_last(margin, 10),
        "margin_streak": _streak(margin, "增", "減"),
        "short": short[-1],
        "short_3": _sum_last(short, 3),
        "short_5": _sum_last(short, 5),
        "short_10": _sum_last(short, 10),
        "short_streak": _streak(short, "增", "減"),
        "ratio": max(0.0, ratio),
        "source": "YahooMargin",
        "date": dates[-1],
        "accepted": True,
        "reason": "資券同步｜Yahoo資券餘額逐日增減",
        "symbol": symbol,
    }
