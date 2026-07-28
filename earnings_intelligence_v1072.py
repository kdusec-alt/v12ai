# -*- coding: utf-8 -*-
"""Forward-first earnings evidence for the V1072 thesis layer.

Quarterly results describe what already happened.  Guidance and the market's
reaction describe what is being repriced.  This module keeps those horizons
separate so a historical beat cannot average away a forward disappointment.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Sequence


_EARNINGS_TERMS = (
    "earnings", "results", "revenue", "sales", "eps", "quarter", "q1", "q2", "q3", "q4",
    "財報", "營收", "獲利", "每股盈餘", "季度",
)
_BACKWARD_POSITIVE = (
    "beat estimates", "beats estimates", "beat expectations", "beats expectations",
    "tops estimates", "above estimates", "better than expected", "strong results",
    "higher profit", "record sales", "record revenue", "優於預期", "擊敗預期", "超越預期",
)
_BACKWARD_NEGATIVE = (
    "missed estimates", "misses estimates", "below estimates", "weaker results",
    "revenue miss", "earnings miss", "低於預期", "未達預期",
)
_FORWARD_POSITIVE = (
    "raises guidance", "raised guidance", "guidance raised", "boosts outlook",
    "strong outlook", "guides above", "above consensus", "上調財測", "上調展望", "財測優於預期",
)
_FORWARD_NEGATIVE = (
    "weak guidance", "soft guidance", "underwhelming guidance", "guidance disappoints",
    "guidance falls short", "cuts guidance", "cut guidance", "lowered guidance",
    "weak outlook", "soft outlook", "lower outlook", "outlook below", "forecast below",
    "demand slowdown", "slowing demand", "財測疲軟", "財測下修", "展望下修",
    "財測未達預期", "展望未達預期", "需求放緩",
)
_HIGH_BAR = (
    "not enough", "tepid guidance", "lack of a stronger outlook", "high expectations",
    "lofty expectations", "priced for perfection", "valuation reset", "expectation reset",
    "in line with expectations", "matches expectations", "market high bar",
    "未達市場高標", "市場期待過高", "估值修正", "預期修正",
)
_GUIDANCE_TERMS = ("guidance", "outlook", "forecast", "財測", "展望", "指引")
_PRICE_REACTION = (
    "stock sinks", "stock tumbles", "shares sink", "shares tumble", "plunges",
    "falls sharply", "slides", "暴跌", "重挫", "大跌",
)


def _value(item: Any, name: str) -> str:
    if isinstance(item, Mapping):
        return str(item.get(name) or "")
    return str(getattr(item, name, "") or "")


def _clean(value: Any, limit: int = 72) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _has(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def assess_earnings_evidence(news_items: Sequence[Any] | None) -> Dict[str, Any]:
    rows: list[tuple[str, str]] = []
    for item in news_items or []:
        tag = _value(item, "tag").lower()
        title = _value(item, "title").strip()
        text = title.lower()
        if not title or any(token in tag for token in ("policy_geo", "macro_event", "daily_headline", "tw_daily")):
            continue
        if "earnings" in tag or _has(text, _EARNINGS_TERMS):
            rows.append((title, text))
    if not rows:
        return {
            "accepted": False,
            "state": "none",
            "sign": 0,
            "text": "",
            "forward_priority": False,
        }

    backward_positive = any(_has(text, _BACKWARD_POSITIVE) for _, text in rows)
    backward_negative = any(
        _has(text, _BACKWARD_NEGATIVE)
        and not _has(text, _GUIDANCE_TERMS)
        for _, text in rows
    )
    forward_positive = any(_has(text, _FORWARD_POSITIVE) for _, text in rows)
    forward_negative = any(_has(text, _FORWARD_NEGATIVE) for _, text in rows)
    high_bar = any(
        _has(text, _HIGH_BAR)
        or (_has(text, ("in line", "matches consensus")) and _has(text, _GUIDANCE_TERMS))
        for _, text in rows
    )
    adverse_reaction = any(_has(text, _PRICE_REACTION) for _, text in rows)

    if backward_positive and forward_negative:
        state, sign = "backward_beat_forward_miss", -1
        text = "本季實績優於預期，但前瞻財測偏弱；T+1 以財測與價格反應為主"
    elif backward_positive and high_bar:
        state, sign = "backward_beat_high_bar_reset", -1
        text = "本季實績優於預期，但財測未跨過市場隱含高標；屬預期／估值重定價"
    elif forward_negative:
        state, sign = "forward_miss", -1
        text = "前瞻財測偏弱，優先於歷史季度數字"
    elif forward_positive:
        state, sign = "beat_and_raise" if backward_positive else "forward_raise", 1
        text = "前瞻財測上修，且價格仍需確認" if not backward_positive else "本季實績優於預期且前瞻財測上修"
    elif backward_negative:
        state, sign = "backward_miss", -1
        text = "本季實績未達預期；等待公司前瞻與價格確認"
    elif backward_positive:
        state, sign = "backward_beat_only", 1
        text = "本季實績優於預期，但尚無足夠前瞻證據"
    else:
        state, sign = "earnings_unresolved", 0
        text = "財報已公布，但實績與前瞻落差尚待交叉確認"

    ranked = sorted(
        rows,
        key=lambda row: (
            _has(row[1], _FORWARD_NEGATIVE),
            _has(row[1], _HIGH_BAR),
            _has(row[1], _FORWARD_POSITIVE),
            _has(row[1], _BACKWARD_POSITIVE),
        ),
        reverse=True,
    )
    return {
        "accepted": True,
        "state": state,
        "sign": sign,
        "text": text,
        "forward_priority": bool(forward_negative or forward_positive or high_bar),
        "backward_positive": backward_positive,
        "backward_negative": backward_negative,
        "forward_positive": forward_positive,
        "forward_negative": forward_negative,
        "high_bar_reset": high_bar,
        "adverse_price_reaction_in_news": adverse_reaction,
        "headline": _clean(ranked[0][0]) if ranked else "",
        "headline_count": len(rows),
    }
