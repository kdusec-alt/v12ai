# -*- coding: utf-8 -*-
from __future__ import annotations

from models import PriceFrame, RawForecast
from price_guard import apply_market_bounds


def _trend(price: PriceFrame, n: int = 6) -> float:
    closes = [float(x) for x in price.recent_closes if float(x) > 0]
    if str(price.market_status) in {"intraday", "close_confirm", "pre_market", "after_hours"} and len(closes) > n:
        # Trend is close-to-close. Live price is handled by the intraday/VWAP layer.
        closes = closes[:-1]
    if len(closes) < n:
        return 0.0
    return (closes[-1] - closes[-n]) / closes[-n]


def _abc(price: PriceFrame, base_shift: float, vol_pressure: float) -> dict[str, float]:
    last = float(price.last)
    vwap = float(price.vwap or last)
    momentum = base_shift * 120.0 + (1.5 if last >= vwap else -1.5) - vol_pressure * 0.18
    a = max(8.0, min(72.0, 32.0 + momentum * 4.2))
    c = max(10.0, min(76.0, 34.0 - momentum * 3.7 + vol_pressure * 0.55))
    b = max(10.0, 100.0 - a - c)
    total = a + b + c
    return {"A": round(a / total * 100, 1), "B": round(b / total * 100, 1), "C": round(c / total * 100, 1)}


def build_raw_forecast(price: PriceFrame) -> RawForecast:
    last = float(price.last)
    atr = max(float(price.atr14), last * 0.012, 0.01)
    trend = _trend(price, 6)
    trend20 = _trend(price, 20)
    vwap_gap = (last - float(price.vwap or last)) / last
    vwap_z = (last - float(price.vwap or last)) / atr
    if abs(vwap_z) < 0.15:
        vwap_gap = 0.0
    vwap_gap = max(-0.025, min(0.025, vwap_gap))
    vol_pressure = max(0.0, (atr / max(last, 0.01)) * 100.0)
    # V12.1: confirmed trend drives the price path; VWAP is a small tactical term
    # with a neutral zone and is not counted again by feature modules.
    base_shift = trend * 0.46 + trend20 * 0.24 + vwap_gap * 0.18
    raw_t1 = last + atr * base_shift * 2.15
    raw_t0 = last + atr * base_shift * 0.55
    high = max(raw_t1 + atr * (0.45 + max(base_shift, 0) * 2.0), last + atr * 0.16)
    low = min(raw_t1 - atr * (0.55 + max(-base_shift, 0) * 2.4), last - atr * 0.18)
    low_entry = min(last - atr * 0.22, raw_t1 - atr * 0.28)
    no_chase = max(last + atr * 0.48, high - atr * 0.08)
    return RawForecast(
        raw_t0=apply_market_bounds(raw_t0, price.previous_close, price.ticker.market, price.ticker.price_limit_pct),
        raw_t1=apply_market_bounds(raw_t1, last, price.ticker.market, price.ticker.price_limit_pct),
        raw_t1_high=apply_market_bounds(high, last, price.ticker.market, price.ticker.price_limit_pct),
        raw_t1_low=apply_market_bounds(low, last, price.ticker.market, price.ticker.price_limit_pct),
        raw_abc=_abc(price, base_shift, vol_pressure),
        raw_low_entry=apply_market_bounds(low_entry, last, price.ticker.market, price.ticker.price_limit_pct),
        raw_no_chase=apply_market_bounds(no_chase, last, price.ticker.market, price.ticker.price_limit_pct),
    )
