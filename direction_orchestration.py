# -*- coding: utf-8 -*-
"""Direction/price blending and tactical translation for TINO V12.2."""
from __future__ import annotations

from typing import Dict, List

from arbitration import clamp
from direction_engine import DirectionResult
from models import PriceFrame, SignalPacket


def direction_ensemble(
    price: PriceFrame,
    base_t1: float,
    direction: DirectionResult,
) -> tuple[float, float, float]:
    """Blend price path with independent direction, bounded by ATR."""
    last = float(price.last)
    atr = max(float(price.atr14), last * 0.012, 0.01)
    strength = min(abs(float(direction.score)) / 100.0, 1.0)
    weight = clamp(
        0.16
        + 0.30
        * strength
        * float(direction.quality)
        * (1.0 - float(direction.conflict)),
        0.16,
        0.46,
    )
    if direction.label == "NEUTRAL":
        weight = max(weight, 0.22)
    target = last + atr * float(direction.expected_move_atr)
    blended = float(base_t1) * (1.0 - weight) + target * weight

    if direction.confidence >= 58.0 and direction.conflict < 0.42:
        if direction.label == "UP" and blended <= last:
            blended = last + atr * 0.05
        elif direction.label == "DOWN" and blended >= last:
            blended = last - atr * 0.05
    elif direction.label == "NEUTRAL":
        blended = clamp(blended, last - atr * 0.30, last + atr * 0.30)
    return round(blended, 4), round(blended - float(base_t1), 4), round(weight, 4)


def direction_label_zh(direction: DirectionResult | None) -> str:
    if direction is None:
        return "方向觀察"
    return {"UP": "偏多", "DOWN": "偏空", "NEUTRAL": "中性"}.get(
        direction.label, "方向觀察"
    )


def _signal_confidence_family(module: str) -> str:
    name = str(module or "")
    if name in {"VWAP", "LCR", "FQC", "Liquidity", "QCRE", "ETF Liquidity"}:
        return "price_position"
    if name in {"Macro", "GRR", "市場風控", "RCRS", "US Macro", "Quantum Macro", "News"}:
        return "market_risk"
    if name in {"法人", "資券", "BSI", "Short Float", "TV外資買賣壓", "外資期貨"}:
        return "flow"
    if name in {"Fair Value", "基本面", "ETF Mode", "ETF Premium"}:
        return "fundamental"
    if name == "LearningProfile":
        return "learning"
    return name or "other"


def tactical_confidence(signals: List[SignalPacket]) -> float:
    """Deduplicate confidence/risk by evidence family."""
    groups: Dict[str, List[SignalPacket]] = {}
    for signal in signals:
        if signal.accepted:
            groups.setdefault(_signal_confidence_family(signal.module), []).append(signal)
    confidence_delta = 0.0
    risk_total = 0.0
    for rows in groups.values():
        chosen = max(rows, key=lambda row: abs(float(row.confidence)))
        confidence_delta += clamp(float(chosen.confidence), -6.0, 6.0)
        risk_total += max(clamp(float(row.risk), 0.0, 12.0) for row in rows)
    return clamp(52.0 + confidence_delta * 0.45 - risk_total * 0.14, 35.0, 82.0)


def quantum_tactical_overlay(direction: DirectionResult | None) -> Dict[str, object]:
    """Translate right-side evidence into left-side position/entry gates."""
    if direction is None:
        return {
            "note": "",
            "hard_defense": False,
            "pause_second": False,
            "catalyst": False,
        }
    fs = direction.family_scores or {}
    fund = float(fs.get("fundamental_event", 0.0) or 0.0)
    overnight = float(fs.get("overnight", 0.0) or 0.0)
    leverage = float(fs.get("leverage", 0.0) or 0.0)
    flow = float(fs.get("flow", 0.0) or 0.0)
    intraday = float(fs.get("intraday", 0.0) or 0.0)
    trend = float(fs.get("trend", 0.0) or 0.0)
    geo = float(fs.get("geo_policy", 0.0) or 0.0)
    heat = float(fs.get("market_heat", 0.0) or 0.0)
    futures = float(fs.get("futures", 0.0) or 0.0)
    uncertainty = float(getattr(direction, "uncertainty", 0.0) or 0.0)
    event_names = list(getattr(direction, "risk_factors", []) or [])
    event_caution = bool(uncertainty >= 0.11 and event_names)
    profile = str(direction.regime or "").split("｜")[-1]

    notes: List[str] = []
    if event_caution:
        notes.append(f"{event_names[0]}公布前不預設方向，縮小試單並等待跨市場確認")
    hard_defense = bool(
        profile in {"memory", "semiconductor"}
        and geo <= -16.0
        and overnight <= -14.0
    )
    pause_second = bool(
        leverage <= -20.0
        and (flow <= -8.0 or intraday <= -10.0 or trend <= -20.0)
    )
    catalyst = abs(fund) >= 16.0

    if hard_defense:
        notes.append("地緣風險與台指夜盤/費半共振，相關半導體先防守")
    elif geo <= -12.0:
        notes.append("地緣風險仍需海外市場確認")
    elif geo >= 12.0 and overnight >= 10.0:
        notes.append("政策事件獲海外市場正向確認")

    if pause_second:
        notes.append("融資連增且價量/法人偏弱，融資降溫前暫停第二批")
    elif leverage <= -12.0:
        notes.append("融資升溫，降低試單部位")
    elif leverage >= 15.0:
        notes.append("融資清洗有利籌碼沉澱")

    if fund >= 16.0:
        notes.append("月營收/財報正向催化只計近兩交易日，急拉不追")
    elif fund <= -16.0:
        notes.append("月營收/財報負向催化只計近兩交易日")

    if fund * overnight < 0 and abs(fund) >= 14.0 and abs(overnight) >= 12.0:
        notes.append("基本面事件與海外盤勢反向，等待開盤確認")
    if heat <= -18.0:
        notes.append("市場融資熱度偏高，追價門檻提高")
    if futures <= -18.0:
        notes.append("外資期貨偏空，轉強需先收復關鍵價")
    if direction.conflict >= 0.45:
        notes.append("多空證據衝突，先等確認不搶方向")

    return {
        "note": "；".join(notes[:3]),
        "hard_defense": hard_defense,
        "pause_second": pause_second,
        "catalyst": catalyst,
        "event_caution": event_caution,
        "event_name": event_names[0] if event_names else "",
        "event_uncertainty": round(uncertainty, 3),
        "fundamental_event": round(fund, 2),
        "overnight": round(overnight, 2),
        "leverage": round(leverage, 2),
        "geo_policy": round(geo, 2),
    }
