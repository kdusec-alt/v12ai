# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List
from models import PriceFrame, SignalPacket


def _sig(module: str, signal: str, score: float, confidence: float, risk: float, bias: float, reason: str, source: str, date: str, accepted: bool=True) -> SignalPacket:
    return SignalPacket(module, signal, score, confidence, risk, bias, reason, source, date, accepted)


def common_signals(price: PriceFrame, manual_macro: str = "neutral") -> List[SignalPacket]:
    d = price.truth.date
    last = float(price.last)
    vwap = float(price.vwap or last)
    vwap_gap = (last - vwap) / last if last else 0.0
    vwap_signal = "VWAP 上方" if last >= vwap else "VWAP 下方"
    vwap_score = 8.0 if last >= vwap else -8.0
    macro_bias = {"bullish": 0.0, "neutral": 0.0, "bearish": 0.0}.get(manual_macro, 0.0)
    macro_conf = {"bullish": 2.0, "neutral": 0.0, "bearish": -4.0}.get(manual_macro, 0.0)
    fair_low = last - price.atr14 * 0.7
    fair_high = last + price.atr14 * 0.7
    macro = price.context.get("macro", {})
    try:
        nq = float(macro.get("nq", 0) or 0)
    except Exception:
        nq = 0.0
    market_risk = "系統性去槓桿" if nq < -1.0 and last < vwap and bool(macro.get("accepted", False)) else "風險可控"
    return [
        _sig("VWAP", vwap_signal, vwap_score, 3.0, 3.0 if last < vwap else 0.0, vwap_gap * 0.55, f"last={last:.2f}, vwap={vwap:.2f}", "price_frame", d),
        _sig("LCR", "流動性壓力偏高" if price.volume > 0 and last < vwap else "流動性正常", -4.0 if last < vwap else 2.0, 1.0, 5.0 if last < vwap else 1.0, -0.08 if last < vwap else 0.03, "VWAP 與量價位置", "features_common", d),
        _sig("Macro", "中性｜只調信心" if manual_macro == "neutral" else f"{manual_macro}｜只調信心", 0.0, macro_conf, 2.0, macro_bias, "Macro/GRR/Event 預設不直接改 T1", "manual_macro", d),
        _sig("GRR", "中性｜風險監控", 0.0, -1.0 if market_risk != "風險可控" else 0.0, 4.0 if market_risk != "風險可控" else 1.0, 0.0, "Global Risk Regime confidence-only", "features_common", d),
        _sig("市場風控", market_risk, -6.0 if market_risk != "風險可控" else 2.0, -2.0 if market_risk != "風險可控" else 1.0, 8.0 if market_risk != "風險可控" else 2.0, -0.12 if market_risk != "風險可控" else 0.02, "NQ/QQQ/VIX/VWAP composite", "features_common", d),
        _sig("Fair Value", f"保守 {fair_low:.2f}｜中性 {last:.2f}｜樂觀 {fair_high:.2f}", 0.0, 0.0, 1.0, 0.0, "研究參考，不硬改價", "features_common", d),
    ]
