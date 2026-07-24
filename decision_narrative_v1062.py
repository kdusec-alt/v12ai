# -*- coding: utf-8 -*-
"""Narrative-only Market Shock overlay for the AI decision card."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

import decision_narrative as _legacy
from market_shock_indicator import assess_market_shock

_LEGACY_BUILD = _legacy.build_ai_decision_narrative


def dominant_market_shock(news_items: Sequence[Any] | None) -> Dict[str, Any]:
    rows = []
    for item in news_items or []:
        tag = str(item.get("tag") if isinstance(item, Mapping) else getattr(item, "tag", "") or "")
        if "global_event_core" not in tag:
            continue
        rows.append(assess_market_shock(item))
    if not rows:
        return {
            "level": 0, "label": "觀察", "score": 0.0, "depth": 1,
            "drivers": [], "transmission": [], "color": "green", "price_veto": True,
        }
    return max(rows, key=lambda row: (int(row.get("level") or 0), float(row.get("score") or 0.0)))


def _concise_operation_line(message: str) -> str:
    """Keep the bottom one-liner tactical; detailed shock evidence stays above."""
    text = str(message or "").strip()
    if "：" in text:
        text = text.split("：", 1)[-1]
    text = text.strip().rstrip("。")
    return text + "。" if text else ""


def build_ai_decision_narrative_v1062(*args, **kwargs) -> Dict[str, Any]:
    result = dict(_LEGACY_BUILD(*args, **kwargs) or {})

    # The formal message remains a concise price/tactical conclusion.  Detailed
    # L2-L5 transmission belongs to evidence_line / Policy-Geo / market_shock,
    # otherwise Orchestrator's bottom 「一句話」 repeats the whole paragraph.
    base_message = str(result.get("message") or "").strip()
    result["message"] = base_message
    result["one_liner"] = _concise_operation_line(base_message)

    news_items = args[2] if len(args) >= 3 else kwargs.get("news_items")
    shock = dominant_market_shock(news_items)
    shock_level = int(shock.get("level") or 0)
    if shock_level < 2:
        result["market_shock"] = shock
        return result

    shock_score = float(shock.get("score") or 0.0)
    shock_drivers = "+".join(shock.get("drivers") or []) or "全球事件"
    path = "→".join((shock.get("transmission") or [])[:5])
    shock_text = (
        f"市場衝擊 L{shock_level} {shock.get('label') or '觀察'} {shock_score:.0f}/100"
        f"｜{shock_drivers}｜{path or '等待跨市場傳導確認'}"
    )

    evidence = str(result.get("evidence_line") or "").strip()
    result["evidence_line"] = f"{evidence}；{shock_text}" if evidence else shock_text

    axis = str(result.get("axis") or "").strip("｜")
    result["axis"] = f"{axis}｜市場衝擊L{shock_level}" if axis else f"市場衝擊L{shock_level}"
    result["market_shock"] = shock
    result["market_shock_text"] = shock_text
    result["narrative_only"] = True
    return result


def install_decision_narrative_v1062() -> None:
    _legacy.build_ai_decision_narrative = build_ai_decision_narrative_v1062
