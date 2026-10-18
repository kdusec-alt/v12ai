"""Timestamped market observations; completed-session trends never include live bars."""
from __future__ import annotations

from datetime import datetime, timezone, time
import math
from zoneinfo import ZoneInfo


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def observation_from_history(key, symbol, history, metadata=None, now=None):
    import pandas as pd
    meta = dict(metadata or {})
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    zone = ZoneInfo(str(meta.get("exchangeTimezoneName") or ("Asia/Taipei" if key in {"taiex", "tpex"} else "America/New_York")))
    local_now = now.astimezone(zone)
    if history is None or history.empty or "Close" not in history:
        return None
    clean = history.copy()
    clean["Close"] = pd.to_numeric(clean["Close"], errors="coerce")
    clean = clean.dropna(subset=["Close"]).sort_index()
    clean = clean[~clean.index.duplicated(keep="last")]
    clean = clean[clean["Close"].map(lambda value: finite(value) is not None and value > 0)]
    clean = clean[[pd.Timestamp(stamp).date() <= local_now.date() for stamp in clean.index]]
    if len(clean) < 2:
        return None
    last_date = pd.Timestamp(clean.index[-1]).date()
    stale = (local_now.date() - last_date).days > 4
    equity = key not in {"wti", "brent", "nq"}
    close_time = time(13, 35) if key in {"taiex", "tpex"} else time(16, 0)
    live_bar = last_date == local_now.date() and local_now.weekday() < 5 and (not equity or local_now.time().replace(tzinfo=None) < close_time)
    completed = clean.iloc[:-1] if live_bar else clean
    prices = [float(value) for value in completed["Close"]]
    last, previous = float(clean["Close"].iloc[-1]), float(clean["Close"].iloc[-2])
    quote_at = ""
    epoch = finite(meta.get("regularMarketTime"))
    if epoch is not None:
        stamp = datetime.fromtimestamp(epoch, timezone.utc)
        if stamp <= now and stamp.astimezone(zone).date() == last_date:
            quote_at = stamp.isoformat()
            quote = finite(meta.get("regularMarketPrice"))
            # The quote and daily baseline must refer to the same observed date.
            if quote is not None and quote > 0:
                last = quote
    streak = 0
    for i in range(len(prices) - 1, 0, -1):
        if prices[i] >= prices[i - 1]:
            break
        streak += 1
    def change(sessions):
        return round((prices[-1] / prices[-sessions-1] - 1) * 100, 3) if len(prices) > sessions else None
    ma20 = sum(prices[-20:]) / 20 if len(prices) >= 20 else None
    return {
        "key": key, "symbol": symbol, "name": str(meta.get("longName") or meta.get("shortName") or symbol),
        "price": round(last, 4), "previous_close": previous,
        "change_pct": round((last / previous - 1) * 100, 3),
        "data_date": last_date.isoformat(), "quote_at": quote_at,
        "timestamp_basis": "provider_quote_time" if quote_at else "daily_bar_date_only",
        "session": ("intraday" if equity else "overnight") if live_bar else "official_close",
        "source": "YahooFinance", "currency": str(meta.get("currency") or ("TWD" if key in {"taiex", "tpex"} else "USD")),
        "instrument": "futures_continuous" if not equity else "index" if symbol.startswith("^") else "equity_etf",
        "contract": str(meta.get("contractSymbol") or meta.get("shortName") or "未提供明確合約月份") if not equity else "",
        "price_basis": "連續期貨報價，非現貨／官方結算價" if not equity else "最近可得市場報價／日線",
        "delayed": True, "stale": stale,
        "completed_as_of": pd.Timestamp(completed.index[-1]).date().isoformat() if len(completed) else "",
        "completed_sessions": len(prices), "consecutive_down": streak,
        "return_3d_pct": change(3), "return_5d_pct": change(5),
        "below_ma20": prices[-1] < ma20 if prices and ma20 is not None else None,
        "ma20": round(ma20, 4) if ma20 is not None else None,
        "drawdown_20d_pct": round((prices[-1] / max(prices[-20:]) - 1) * 100, 3) if prices else None,
    }
