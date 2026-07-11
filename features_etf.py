# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List
from models import PriceFrame, SignalPacket


def etf_signals(price: PriceFrame) -> List[SignalPacket]:
    d = price.truth.date
    last = price.last
    vwap = price.vwap or last
    return [
        SignalPacket("ETF Mode", "ETF 獨立模式｜不套 EPS/個股 BSI/個股財報", 0.0, 2.0, 1.0, 0.0, "ETF guard locked", "features_etf", d, True),
        SignalPacket("ETF Liquidity", "流動性可控" if price.volume > 0 else "流動性待驗證", 2.0 if price.volume > 0 else -2.0, 1.0, 2.0, 0.02 if last >= vwap else -0.02, "price/volume/VWAP only", "features_etf", d, True),
        SignalPacket("ETF Premium", "NAV/溢折價待接｜不硬改價", 0.0, 0.0, 1.0, 0.0, "資料未接前僅敘事", "features_etf", d, True),
    ]
