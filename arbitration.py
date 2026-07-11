# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable, Tuple
from config import MAX_T1_ADJUSTMENT_ATR, PRICE_CONFIDENCE_ONLY_MODULES
from models import PriceFrame, SignalPacket


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def is_price_neutral_module(module: str) -> bool:
    return str(module or "") in PRICE_CONFIDENCE_ONLY_MODULES


def signal_price_adjustment(signal: SignalPacket, price: PriceFrame) -> float:
    """Return a bounded price-path adjustment.

    V12.2 contract:
    - risk never means price must fall; risk only reduces confidence/position size.
    - duplicated VWAP/liquidity/narrative modules are confidence-only.
    - only unique, accepted directional evidence may make a small T1 adjustment.
    """
    if not signal.accepted or is_price_neutral_module(signal.module):
        return 0.0
    atr = max(float(price.atr14), float(price.last) * 0.012, 0.01)
    bias_component = clamp(float(signal.bias), -0.22, 0.22) * atr
    score_component = clamp(float(signal.score) / 100.0, -0.16, 0.16) * atr
    return round(bias_component + score_component, 4)


def cap_total_adjustment(adjustments: Iterable[float], price: PriceFrame) -> Tuple[float, float]:
    raw_total = float(sum(adjustments))
    cap = max(float(price.atr14), float(price.last) * 0.012, 0.01) * MAX_T1_ADJUSTMENT_ATR
    return round(clamp(raw_total, -cap, cap), 4), round(cap, 4)
