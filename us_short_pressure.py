# -*- coding: utf-8 -*-
"""Bounded, observational FINRA off-exchange short-sale volume history.

Daily short-sale *transactions* are not open short positions or buy-to-cover
transactions.  Keep this evidence separate from Short Float and price targets.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache
import math
import os
import re
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

_SOURCE = "FINRA Consolidated NMS (off-exchange TRF/ADF)"
_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day}.txt"
_NY = ZoneInfo("America/New_York")


def _candidate_days(now: datetime) -> list[str]:
    ny = now.astimezone(_NY)
    # Today's file is not complete before FINRA's 18:00 ET publication window.
    start = ny.date() if ny.hour >= 18 else ny.date() - timedelta(days=1)
    days = []
    for offset in range(14):
        day = start - timedelta(days=offset)
        if day.weekday() < 5:
            days.append(day.strftime("%Y%m%d"))
        if len(days) == 9:  # two spare weekdays for ordinary market holidays
            break
    return days


def parse_daily_file(payload: bytes, symbol: str, expected_day: str) -> dict | None:
    """Read one symbol; denominator is FINRA-reported volume only."""
    if not payload.startswith(b"Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market"):
        return None
    wanted = symbol.upper()
    for line in payload.decode("utf-8", "replace").splitlines()[1:]:
        fields = line.split("|")
        if len(fields) < 5 or fields[0] != expected_day or fields[1].upper() != wanted:
            continue
        try:
            short, exempt, total = map(float, fields[2:5])
            if not all(math.isfinite(x) for x in (short, exempt, total)):
                return None
            if not (total > 0 and 0 <= short <= total and 0 <= exempt <= total):
                return None
            return {
                "date": f"{expected_day[:4]}-{expected_day[4:6]}-{expected_day[6:]}",
                "short_volume": round(short, 3),
                "reported_volume": round(total, 3),
                "ratio_pct": round(short / total * 100, 2),
            }
        except (TypeError, ValueError):
            return None
    return None


@lru_cache(maxsize=12)
def _download_file(day: str, time_bucket: int) -> bytes:
    # One shared cache across tickers.  Absent/unavailable files are retried
    # after ten minutes; no large file is retained in prediction memory.
    del time_bucket
    try:
        with urllib.request.urlopen(
            urllib.request.Request(_URL.format(day=day), headers={"User-Agent": "TINO-short-pressure/1.0"}),
            timeout=1.25,
        ) as response:
            if response.status != 200:
                return b""
            data = response.read(1_500_001)
            return data if len(data) <= 1_500_000 else b""
    except (OSError, urllib.error.URLError, ValueError):
        return b""


def summarize(rows: list[dict], now: datetime) -> dict:
    ordered = sorted({row["date"]: row for row in rows}.values(), key=lambda row: row["date"])
    latest = ordered[-1]["date"] if ordered else ""
    try:
        age = (now.astimezone(_NY).date() - datetime.fromisoformat(latest).date()).days
    except ValueError:
        age = 999
    recent = ordered[-7:]
    three = recent[-3:]
    accepted = len(three) == 3 and age <= 5
    avg3 = round(sum(row["ratio_pct"] for row in three) / 3, 2) if accepted else None
    avg7 = round(sum(row["ratio_pct"] for row in recent) / 7, 2) if len(recent) == 7 else None
    trend = "UNAVAILABLE"
    if accepted:
        vals = [row["ratio_pct"] for row in three]
        if vals[0] < vals[1] < vals[2] and vals[2] - vals[0] >= 3:
            trend = "RISING_ACTIVITY"
        elif vals[0] > vals[1] > vals[2] and vals[0] - vals[2] >= 3:
            trend = "EASING_ACTIVITY"
        else:
            trend = "MIXED_ACTIVITY"
    return {
        "accepted": accepted,
        "source": _SOURCE,
        "as_of": latest,
        "last_3": three if accepted else [],
        "last_7": recent,
        "avg_3_pct": avg3,
        "avg_7_pct": avg7,
        "trend": trend,
        "scope": "off_exchange_only",
        "meaning": "short_sale_transactions_not_open_positions",
    }


def fetch_us_short_pressure(symbol: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(_NY)
    empty = summarize([], now)
    if os.environ.get("TINO_OFFLINE_TEST") == "1" or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol.upper()):
        return empty
    dates = _candidate_days(now)
    bucket = int(now.timestamp() // 600)
    # Bounded I/O; failed FINRA requests never hold an individual stock query
    # for an unbounded time or affect a price/learning write.
    with ThreadPoolExecutor(max_workers=5) as pool:
        blobs = list(pool.map(lambda day: _download_file(day, bucket), dates))
    rows = [parse_daily_file(blob, symbol, day) for day, blob in zip(dates, blobs) if blob]
    return summarize([row for row in rows if row], now)
