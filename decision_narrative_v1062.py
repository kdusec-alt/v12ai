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

    # Capture the original price/tactical conclusion before adding the detailed
    # market-shock appendix.  Orchestrator uses this for the bottom 「一句話」,
    # avoiding a verbatim repeat of L4/L5 transmission and Policy/Geo notes.
    base_message = str(result.get("message") or "").strip()
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

    state = str(result.get("state") or "")
    price_strong_states = {
        "bad_news_absorbed", "surge_divergence", "limit_breakout", "strong_continuation",
    }
    message = base_message.rstrip("。")
    if shock_level >= 4:
        if state in price_strong_states:
            message += (
                f"。目前雖為{shock_text}，但價格仍在吸收；只要價格失守既定防守位，才確認衝擊轉為實質賣壓"
            )
        else:
            message += (
                f"。{shock_text}，已進入多層傳導；縮小部位並等待海外市場、籌碼與個股價格止穩"
            )
    elif shock_level >= 2:
        message += f"。{shock_text}，提高風險警戒但不單獨決定方向"
    result["message"] = message + "。"

    axis = str(result.get("axis") or "").strip("｜")
    result["axis"] = f"{axis}｜市場衝擊L{shock_level}" if axis else f"市場衝擊L{shock_level}"
    result["market_shock"] = shock
    result["market_shock_text"] = shock_text
    result["narrative_only"] = True
    return result


def install_decision_narrative_v1062() -> None:
    _legacy.build_ai_decision_narrative = build_ai_decision_narrative_v1062
