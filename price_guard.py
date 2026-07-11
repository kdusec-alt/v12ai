# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import os
from typing import Optional, Tuple
from models import PriceFrame


def is_valid_number(value) -> bool:
    try:
        x = float(value)
        return math.isfinite(x) and x > 0
    except Exception:
        return False


def validate_price_frame(price: PriceFrame) -> Tuple[bool, str]:
    ctx = price.context or {}
    meta = ctx.get("price_meta") if isinstance(ctx.get("price_meta"), dict) else {}
    if bool(ctx.get("invalid_price")) or bool(meta.get("invalid_price")):
        return False, str(meta.get("label") or price.truth.reason or "價格來源不可用，STOP。")
    if bool(meta.get("decision_blocked")) and not bool(meta.get("emerging_price_grace")):
        return False, str(meta.get("label") or "價格延遲或來源未確認，STOP，不產生正式預測。")
    source_text = " ".join([str(price.truth.source or ""), str(meta.get("source") or "")]).upper()
    if any(token in source_text for token in ("SAMPLE", "FALLBACK", "MOCK", "SYNTHETIC")) and os.environ.get("TINO_OFFLINE_TEST") != "1":
        return False, "樣本價格不得進入正式預測，STOP。"
    required = [price.last, price.previous_close, price.open, price.high, price.low]
    if any(not is_valid_number(x) for x in required):
        return False, "價格抓不到或為 0，STOP，不產生 T0/T1/ABC。"
    if price.high < price.low:
        return False, "高低價異常，STOP。"
    if not price.truth.accepted:
        return False, f"資料未採納：{price.truth.reason}"
    return True, "OK"


def tw_tick(price: float) -> float:
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


def round_to_tick(price: float, market: str) -> float:
    if market == "TW":
        tick = tw_tick(abs(price))
        return round(round(price / tick) * tick, 2)
    return round(float(price), 2)


def apply_market_bounds(value: float, previous_close: float, market: str, price_limit_pct: Optional[float]) -> float:
    v = float(value)
    if market == "TW" and price_limit_pct and previous_close > 0:
        upper = previous_close * (1 + price_limit_pct)
        lower = previous_close * (1 - price_limit_pct)
        v = min(max(v, lower), upper)
    return round_to_tick(v, market)
