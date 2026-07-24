# -*- coding: utf-8 -*-
"""Refine V1062 market-shock levels without rewriting the base scanner."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping

import market_shock_indicator as _base
from models import NewsItem

_ORIGINAL_ASSESS = _base.assess_market_shock
_LEVEL_LABELS = {
    0: "觀察",
    1: "輕微擾動",
    2: "產業衝擊",
    3: "跨市場衝擊",
    4: "系統性壓力",
    5: "極端市場衝擊",
}


def _text(item: Mapping[str, Any] | Any) -> str:
    if isinstance(item, Mapping):
        title = item.get("title")
        tag = item.get("tag")
    else:
        title = getattr(item, "title", "")
        tag = getattr(item, "tag", "")
    return re.sub(r"\s+", " ", f"{title or ''} {tag or ''}").strip().lower()


def assess_market_shock_v1062(item: Mapping[str, Any] | Any) -> Dict[str, Any]:
    row = dict(_ORIGINAL_ASSESS(item) or {})
    text = _text(item)

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
    )) and "oil_price_down" not in text
    taiwan_strait = any(term in text for term in (
        "taiwan strait", "台海", "封鎖台灣", "台灣海峽",
    ))
    chip_control = any(term in text for term in (
        "export control", "chip ban", "出口管制", "晶片禁售",
    ))
    tariff = any(term in text for term in (
        "tariff", "section 301", "關稅", "trade war", "貿易戰",
    ))
    pmi = any(term in text for term in (
        "pmi", "採購經理人指數", "purchasing managers",
    ))

    deep_systemic = any((war, hormuz, oil_spike, taiwan_strait, chip_control))
    level = int(row.get("level") or 0)

    if hormuz or taiwan_strait:
        level = 5
    elif deep_systemic:
        level = max(4, min(level, 5))
    elif tariff or pmi:
        level = min(max(level, 2), 3)
    elif "oil_price_down" in text:
        level = min(level, 2)

    row["level"] = level
    row["label"] = _LEVEL_LABELS[level]
    row["color"] = "red" if level >= 4 else "yellow" if level >= 2 else "green"
    return row


def _clean_shock_tags(tag: str) -> str:
    parts = []
    for part in str(tag or "").split("|"):
        if part.startswith((
            "shock_level=", "shock_label=", "shock_score=", "shock_depth=",
            "shock_color=", "shock_drivers=",
        )):
            continue
        parts.append(part)
    return "|".join(part for part in parts if part)


def annotate_market_shock_news_v1062(rows: Iterable[NewsItem] | None) -> List[NewsItem]:
    out: List[NewsItem] = []
    for item in rows or []:
        tag = _clean_shock_tags(str(getattr(item, "tag", "") or ""))
        if "global_event_core" in tag:
            shock = assess_market_shock_v1062(item)
            drivers = ",".join(shock.get("drivers") or [])
            tag += "|" + "|".join([
                f"shock_level={shock['level']}",
                f"shock_label={shock['label']}",
                f"shock_score={shock.get('score', 0)}",
                f"shock_depth={shock.get('depth', 1)}",
                f"shock_color={shock['color']}",
                f"shock_drivers={drivers}",
            ])
        out.append(NewsItem(
            str(getattr(item, "source", "") or ""),
            str(getattr(item, "time", "") or ""),
            float(getattr(item, "score", 0.0) or 0.0),
            tag,
            str(getattr(item, "title", "") or ""),
            str(getattr(item, "link", "") or ""),
        ))
    return out


def install_market_shock_levels_v1062() -> None:
    _base.assess_market_shock = assess_market_shock_v1062
    _base.annotate_market_shock_news = annotate_market_shock_news_v1062
