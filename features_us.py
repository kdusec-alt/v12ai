# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List
from models import PriceFrame, SignalPacket


def us_signals(price: PriceFrame) -> List[SignalPacket]:
    d = price.truth.date
    last = float(price.last)
    vwap = float(price.vwap or last)
    strong = last >= vwap
    short = price.context.get("short", {}) or {}
    sf = float(short.get("short_float") or 0.0)
    macro = price.context.get("macro", {}) or {}
    sox = macro.get("sox")
    nq = macro.get("nq") or macro.get("qqq")
    high_short = sf >= 20
    return [
        SignalPacket("QCRE", "盤中 VWAP 驗證" if strong else "估值重定價", 4.0 if strong else -4.0, 2.0, 3.0 if not strong else 1.5, 0.05 if strong else -0.05, "US engine：不套台股法人/資券/BSI", "features_us", d, True),
        SignalPacket("Short Float", "Short Float 回補火種" if high_short else "Short Float 觀察", 3.0 if high_short and strong else 1.0, 1.0 if sf else 0.0, 5.0 if high_short and not strong else 2.0, 0.04 if high_short and strong else 0.0, f"Short Float {sf:.2f}%｜來源 {short.get('short_source','US_PUBLIC')}", short.get("source", "US_SHORT"), d, bool(sf)),
        SignalPacket("US Macro", "SOX/NQ 風險同步" if (sox is not None or nq is not None) else "Macro 觀察", 0.0, 0.0, 4.0 if (sox is not None and float(sox) < -2) else 2.0, 0.0, f"SOX {sox if sox is not None else 'NA'}%｜NQ/QQQ {nq if nq is not None else 'NA'}%", macro.get("source", "US_MARKET"), d, bool(macro.get("accepted"))),
    ]
