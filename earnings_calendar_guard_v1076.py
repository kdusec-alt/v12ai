# -*- coding: utf-8 -*-
"""V1076 US earnings calendar truth guard.

Yahoo/yfinance does not expose the next earnings event consistently.  Some
versions put it in ``get_info()``, others only in calendar or earnings-date
routes.  This module reconciles those shapes and keeps the exchange date
separate from the Taiwan display date.
"""
from __future__ import annotations

from datetime import date, datetime, time
import json
import math
from typing import Any, Callable, Dict, Iterable, Mapping
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


_NEW_YORK = ZoneInfo("America/New_York")
_TAIPEI = ZoneInfo("Asia/Taipei")


def _iter_values(value: Any) -> Iterable[Any]:
    if value in (None, "", "NA"):
        return
    if isinstance(value, Mapping):
        for key in ("raw", "fmt", "date", "timestamp"):
            if value.get(key) not in (None, "", "NA"):
                yield value.get(key)
                return
        for nested in value.values():
            yield from _iter_values(nested)
        return
    if isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from _iter_values(nested)
        return
    yield value


def _as_event_datetime(
    value: Any,
    *,
    date_only_hint: bool = False,
) -> tuple[datetime | None, bool]:
    """Return New York datetime and whether a meaningful clock time exists."""
    if isinstance(value, datetime):
        if date_only_hint:
            event_day = value.date() if value.tzinfo is None else value.astimezone(ZoneInfo("UTC")).date()
            return datetime.combine(event_day, time.min, _NEW_YORK), False
        if value.tzinfo is None:
            return value.replace(tzinfo=_NEW_YORK), value.time() != time.min
        return value.astimezone(_NEW_YORK), True
    if isinstance(value, date):
        return datetime.combine(value, time.min, _NEW_YORK), False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            return None, False
        if abs(number) > 10_000_000_000:
            number /= 1000.0
        try:
            if date_only_hint:
                event_day = datetime.fromtimestamp(number, ZoneInfo("UTC")).date()
                return datetime.combine(event_day, time.min, _NEW_YORK), False
            return datetime.fromtimestamp(number, ZoneInfo("UTC")).astimezone(_NEW_YORK), True
        except Exception:
            return None, False
    text = str(value or "").strip()
    if not text:
        return None, False
    if text.replace(".", "", 1).isdigit():
        try:
            return _as_event_datetime(float(text), date_only_hint=date_only_hint)
        except Exception:
            pass
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        if date_only_hint:
            event_day = parsed.date() if parsed.tzinfo is None else parsed.astimezone(ZoneInfo("UTC")).date()
            return datetime.combine(event_day, time.min, _NEW_YORK), False
        has_time = "T" in normalized or ":" in normalized
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_NEW_YORK)
        else:
            parsed = parsed.astimezone(_NEW_YORK)
        return parsed, has_time
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=_NEW_YORK), False
        except Exception:
            continue
    return None, False


def _calendar_values(calendar: Any) -> Iterable[Any]:
    keys = ("Earnings Date", "EarningsDate", "earningsDate")
    if isinstance(calendar, Mapping):
        for key in keys:
            if key in calendar:
                yield from _iter_values(calendar.get(key))
        return
    if calendar is None:
        return
    try:
        empty = bool(getattr(calendar, "empty"))
        if empty:
            return
    except Exception:
        pass
    for key in keys:
        try:
            if key in calendar.index:
                row = calendar.loc[key]
                values = row.values if hasattr(row, "values") else [row]
                yield from _iter_values(list(values))
        except Exception:
            pass
        try:
            if key in calendar.columns:
                yield from _iter_values(calendar[key].tolist())
        except Exception:
            pass


def _earnings_index_values(table: Any) -> Iterable[Any]:
    if table is None:
        return
    try:
        if bool(getattr(table, "empty")):
            return
    except Exception:
        pass
    try:
        yield from list(table.index)
    except Exception:
        return


def _public_quote_summary(symbol: str) -> Dict[str, Any]:
    modules = "calendarEvents,earnings"
    encoded = urllib.parse.quote(str(symbol or "").upper(), safe=".-")
    headers = {
        "User-Agent": "Mozilla/5.0 TINO-V1076-EarningsCalendar",
        "Accept": "application/json,text/plain,*/*",
        "Referer": f"https://finance.yahoo.com/quote/{encoded}",
    }
    for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
        try:
            url = f"https://{host}/v10/finance/quoteSummary/{encoded}?modules={modules}"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=4) as response:
                payload = json.loads(response.read(300_000).decode("utf-8", errors="replace"))
            rows = ((payload.get("quoteSummary") or {}).get("result") or [])
            if rows and isinstance(rows[0], dict):
                return rows[0]
        except Exception:
            continue
    return {}


def _session_label(event_dt: datetime, has_time: bool) -> str:
    if not has_time:
        return ""
    hour = event_dt.astimezone(_NEW_YORK).hour
    if hour < 12:
        return "美股盤前"
    if hour >= 16:
        return "美股盤後"
    return "美股盤中"


def _taipei_label(event_dt: datetime, session: str, has_time: bool) -> str:
    if not has_time:
        if session == "美股盤後":
            local_day = event_dt.date()
            local_day = date.fromordinal(local_day.toordinal() + 1)
            return f"台灣 {local_day:%m/%d} 凌晨"
        if session == "美股盤前":
            return f"台灣 {event_dt.date():%m/%d} 晚間"
        return ""
    local = event_dt.astimezone(_TAIPEI)
    return f"台灣 {local:%m/%d %H:%M}"


def resolve_us_earnings_calendar(
    symbol: str,
    info: Mapping[str, Any] | None,
    *,
    ticker_obj: Any = None,
    now: datetime | None = None,
    quote_summary_fetcher: Callable[[str], Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Reconcile all available next-earnings routes into one auditable record."""
    current_ny = now or datetime.now(_NEW_YORK)
    if current_ny.tzinfo is None:
        current_ny = current_ny.replace(tzinfo=_NEW_YORK)
    current_ny = current_ny.astimezone(_NEW_YORK)
    candidates: list[Dict[str, Any]] = []

    def add(
        value: Any,
        source: str,
        priority: int,
        *,
        date_only_hint: bool = False,
    ) -> None:
        for raw in _iter_values(value):
            event_dt, has_time = _as_event_datetime(raw, date_only_hint=date_only_hint)
            if event_dt is None:
                continue
            days = (event_dt.date() - current_ny.date()).days
            if days < -1 or days > 370:
                continue
            candidates.append({
                "event_dt": event_dt,
                "has_time": has_time,
                "days": days,
                "source": source,
                "priority": priority,
            })

    raw_info = dict(info or {})
    for key in ("nextEarningsDate", "earningsDate"):
        add(raw_info.get(key), f"Yahoo info {key}", 30, date_only_hint=True)
    for key in ("earningsTimestamp", "earningsTimestampStart", "earningsTimestampEnd"):
        # Timestamped values are more specific than a date-only info field, so
        # they don't need the extra quoteSummary cross-check request.
        add(raw_info.get(key), f"Yahoo info {key}", 25)

    if ticker_obj is not None:
        try:
            add(
                list(_calendar_values(ticker_obj.get_calendar())),
                "Yahoo get_calendar",
                10,
                date_only_hint=True,
            )
        except Exception:
            pass
        try:
            add(
                list(_calendar_values(ticker_obj.calendar)),
                "Yahoo calendar",
                12,
                date_only_hint=True,
            )
        except Exception:
            pass
        try:
            add(
                list(_earnings_index_values(ticker_obj.get_earnings_dates(limit=12))),
                "Yahoo get_earnings_dates",
                20,
            )
        except Exception:
            pass
        try:
            add(
                list(_earnings_index_values(ticker_obj.earnings_dates)),
                "Yahoo earnings_dates",
                22,
            )
        except Exception:
            pass

    # Yahoo info can keep a formerly scheduled date after the company updates
    # its calendar. When info is the only live route, cross-check quoteSummary
    # rather than treating that stale value as authoritative.
    if not candidates or all(row["priority"] >= 30 for row in candidates):
        fetcher = quote_summary_fetcher or _public_quote_summary
        try:
            summary = dict(fetcher(str(symbol or "").upper()) or {})
        except Exception:
            summary = {}
        earnings = ((summary.get("calendarEvents") or {}).get("earnings") or {})
        add(
            earnings.get("earningsDate"),
            "Yahoo quoteSummary calendarEvents",
            5,
            date_only_hint=True,
        )
        for key in ("earningsTimestamp", "earningsTimestampStart", "earningsTimestampEnd"):
            add(earnings.get(key), f"Yahoo quoteSummary {key}", 5)

    if not candidates:
        return {"accepted": False, "source": "NO_EARNINGS_CALENDAR_SOURCE"}

    future = [row for row in candidates if row["days"] >= 0]
    pool = future or candidates
    # Prefer the strongest current source first, then the nearest event from
    # that source. Sorting by date first lets an older info/cache date beat a
    # later confirmed Yahoo calendar date.
    pool.sort(key=lambda row: (
        row["priority"],
        max(row["days"], 0),
        0 if row["has_time"] else 1,
        row["event_dt"],
    ))
    chosen = dict(pool[0])
    same_day = [row for row in pool if row["event_dt"].date() == chosen["event_dt"].date()]
    timed = sorted(
        (row for row in same_day if row["has_time"]),
        key=lambda row: (row["priority"], row["event_dt"]),
    )
    if timed:
        chosen = dict(timed[0])

    event_dt = chosen["event_dt"].astimezone(_NEW_YORK)
    session = _session_label(event_dt, bool(chosen["has_time"]))
    return {
        "accepted": True,
        "next_earnings_date": event_dt.date().isoformat(),
        "next_earnings_timestamp": event_dt.isoformat() if chosen["has_time"] else "",
        "earnings_days": int((event_dt.date() - current_ny.date()).days),
        "earnings_session": session,
        "earnings_taipei": _taipei_label(event_dt, session, bool(chosen["has_time"])),
        "source": chosen["source"],
    }


def merge_us_earnings_calendar(
    symbol: str,
    info: Mapping[str, Any] | None,
    *,
    ticker_obj: Any = None,
    now: datetime | None = None,
    quote_summary_fetcher: Callable[[str], Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    out = dict(info or {})
    resolved = resolve_us_earnings_calendar(
        symbol,
        out,
        ticker_obj=ticker_obj,
        now=now,
        quote_summary_fetcher=quote_summary_fetcher,
    )
    if not resolved.get("accepted"):
        return out
    out["nextEarningsDate"] = resolved["next_earnings_date"]
    out["earningsDays"] = resolved["earnings_days"]
    out["nextEarningsTimestamp"] = resolved["next_earnings_timestamp"]
    out["earningsSession"] = resolved["earnings_session"]
    out["earningsTaipei"] = resolved["earnings_taipei"]
    out["earningsCalendarSource"] = resolved["source"]
    return out
