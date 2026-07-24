# -*- coding: utf-8 -*-
"""Global market-shock indicator for TINO V1062.

The indicator is intentionally stronger than headline severity. It measures how
many market layers an event can transmit through:

    event -> commodity/currency/yields -> index/sector -> ticker -> price veto

It never overwrites price, T0/T1/High/Low, confidence, Prediction DNA or Audit.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping


_LEVEL_LABELS = {
    0: "觀察",
    1: "輕微擾動",
    2: "產業衝擊",
    3: "跨市場衝擊",
    4: "系統性壓力",
    5: "極端市場衝擊",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tag_value(tag: str, key: str) -> str:
    match = re.search(rf"(?:^|\|){re.escape(key)}=([^|]+)", str(tag or ""), flags=re.I)
    return _clean(match.group(1)) if match else ""


def _severity(tag: str) -> int:
    try:
        return max(0, min(4, int(_tag_value(tag, "severity") or 0)))
    except Exception:
        return 0


def _event_text(item: Mapping[str, Any] | Any) -> str:
    if isinstance(item, Mapping):
        title = item.get("title")
        tag = item.get("tag")
    else:
        title = getattr(item, "title", "")
        tag = getattr(item, "tag", "")
    return f"{_clean(title)} {_clean(tag)}".lower()


def _profile(item: Mapping[str, Any] | Any) -> str:
    tag = item.get("tag") if isinstance(item, Mapping) else getattr(item, "tag", "")
    return _tag_value(str(tag or ""), "ticker_profile").lower() or "broad"


def _family(item: Mapping[str, Any] | Any) -> str:
    tag = item.get("tag") if isinstance(item, Mapping) else getattr(item, "tag", "")
    return _tag_value(str(tag or ""), "family").lower()


def assess_market_shock(item: Mapping[str, Any] | Any) -> Dict[str, Any]:
    text = _event_text(item)
    tag = item.get("tag") if isinstance(item, Mapping) else getattr(item, "tag", "")
    family = _family(item)
    profile = _profile(item)
    severity = _severity(str(tag or ""))

    level = 0
    score = 0.0
    depth = 1
    drivers = []
    transmission = []

    war = any(term in text for term in (
        "iran war", "war with iran", "美伊戰爭", "伊朗戰爭", "開戰", "宣戰",
        "airstrike", "missile strike", "軍事行動", "戰火重啟",
    ))
    hormuz = any(term in text for term in (
        "hormuz", "荷姆茲", "霍爾木茲", "封鎖海峽", "關閉海峽", "blockade the strait",
    ))
    oil_spike = any(term in text for term in (
        "oil_price_up", "油價飆升", "油價大漲", "oil surge", "oil spike",
        "brent tops", "brent above", "wti jumps", "crude jumps",
    ))
    tariff = any(term in text for term in ("tariff", "section 301", "關稅", "trade war", "貿易戰"))
    chip_control = any(term in text for term in ("export control", "chip ban", "出口管制", "晶片禁售"))
    taiwan_strait = any(term in text for term in ("taiwan strait", "台海", "封鎖台灣", "台灣海峽"))
    pmi = any(term in text for term in ("pmi", "採購經理人指數", "purchasing managers"))

    if war:
        level = max(level, 4)
        score += 34.0
        depth = max(depth, 5)
        drivers.append("伊朗/中東戰爭")
        transmission.extend(["原油供給", "避險資金", "通膨預期", "殖利率", "科技估值"])
    if hormuz:
        level = max(level, 5)
        score += 42.0
        depth = max(depth, 5)
        drivers.append("荷姆茲航道")
        transmission.extend(["原油供給", "航運保險", "運價", "通膨", "全球風險溢價"])
    if oil_spike or family == "energy":
        oil_level = 2 if severity <= 2 else 4 if severity == 3 else 5
        level = max(level, oil_level)
        score += {0: 0.0, 1: 8.0, 2: 16.0, 3: 28.0, 4: 40.0}.get(severity, 16.0)
        depth = max(depth, 4)
        drivers.append("油價急升")
        transmission.extend(["能源成本", "通膨", "降息空間", "美元/殖利率", "成長股估值"])
    if taiwan_strait:
        level = max(level, 5)
        score += 40.0
        depth = max(depth, 5)
        drivers.append("台海風險")
        transmission.extend(["外資風險溢價", "供應鏈", "匯率", "半導體", "台股大盤"])
    if chip_control:
        level = max(level, 4)
        score += 27.0
        depth = max(depth, 4)
        drivers.append("晶片/出口管制")
        transmission.extend(["中國營收", "訂單", "資本支出", "半導體估值"])
    if tariff:
        level = max(level, 3)
        score += 22.0
        depth = max(depth, 4)
        drivers.append("關稅/貿易政策")
        transmission.extend(["出口成本", "毛利率", "訂單移轉", "通膨"])
    if pmi:
        level = max(level, 3 if severity >= 2 else 2)
        score += 18.0 if severity >= 2 else 10.0
        depth = max(depth, 4)
        drivers.append("PMI成長訊號")
        transmission.extend(["景氣預期", "美元/殖利率", "NQ/SOX", "台指夜盤"])

    # Ticker profile changes the interpretation, not the existence of the shock.
    if profile in {"memory", "semiconductor", "ai_power"} and any((oil_spike, tariff, chip_control, war, hormuz)):
        score *= 1.12
    elif profile == "airline" and any((oil_spike, war, hormuz)):
        score *= 1.28
        level = max(level, 4)
    elif profile == "energy" and any((oil_spike, war, hormuz)):
        score *= 0.88
    elif profile == "biotech" and any((oil_spike, tariff)):
        score *= 0.72

    if severity >= 4:
        level = max(level, 5)
    elif severity >= 3:
        level = max(level, 4)
    elif severity >= 2:
        level = max(level, 2)

    level = max(0, min(5, int(level)))
    score = max(0.0, min(100.0, float(score)))

    ordered_transmission = []
    for value in transmission:
        if value not in ordered_transmission:
            ordered_transmission.append(value)
    ordered_drivers = []
    for value in drivers:
        if value not in ordered_drivers:
            ordered_drivers.append(value)

    color = "red" if level >= 4 else "yellow" if level >= 2 else "green"
    return {
        "level": level,
        "label": _LEVEL_LABELS[level],
        "score": round(score, 1),
        "depth": depth,
        "color": color,
        "drivers": ordered_drivers,
        "transmission": ordered_transmission,
        "profile": profile,
        "family": family,
        "price_veto": True,
    }


def shock_tag(item: Mapping[str, Any] | Any) -> str:
    shock = assess_market_shock(item)
    drivers = ",".join(shock.get("drivers") or [])
    return "|".join([
        f"shock_level={shock['level']}",
        f"shock_label={shock['label']}",
        f"shock_score={shock['score']}",
        f"shock_depth={shock['depth']}",
        f"shock_color={shock['color']}",
        f"shock_drivers={drivers}",
    ])
