# -*- coding: utf-8 -*-
"""Ticker-independent market command assessment for the V1071 front panel."""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence


def _num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except Exception:
        return None


def _event_risk(news: Sequence[Any] | None) -> tuple[float, str]:
    score = 0.0
    reason = ""
    for item in news or []:
        tag = str(getattr(item, "tag", "") or (item.get("tag") if isinstance(item, Mapping) else ""))
        title = str(getattr(item, "title", "") or (item.get("title") if isinstance(item, Mapping) else ""))
        match = re.search(r"(?:shock_level|severity)=([0-5])", tag)
        level = int(match.group(1)) if match else 0
        candidate = level * 6.0
        if candidate > score:
            score = candidate
            reason = title[:48]
    return min(25.0, score), reason


def _state(score: float, rebound: bool) -> tuple[str, str, str]:
    if rebound and score >= 45:
        return "CAPITULATION", "🟣 恐慌宣洩／等待止跌確認", "風險預算維持低檔；停止追空，等待跌停打開、波動率回落與量價承接同時出現"
    if score >= 70:
        return "CRASH", "🔴 股災／流動性踩踏", "停止新增方向性部位並保留現金；至少等跨市場跌勢與波動率同步收斂"
    if score >= 48:
        return "SELL_OFF", "🟠 廣泛賣壓", "將新倉降為確認單；不把急跌視為折價，先等指數、廣度與波動率止穩"
    if score >= 28:
        return "CAUTION", "🟡 風險升溫", "風險預算降至中性偏低；不追價，既有部位依支撐與曝險比例管理"
    return "NORMAL", "🟢 正常／風險可控", "維持既定風險預算；個股仍須通過價格、事件與部位確認"


def _market_thesis(
    *,
    observed_count: int,
    falling: int,
    improving: int,
    vix: float | None,
    price_confirmed: bool,
    event_reason: str,
) -> str:
    """Explain what the cross-asset tape is pricing without inventing causality."""
    if observed_count < 2:
        return "可用跨市場證據不足，現階段不能對大盤方向形成高品質判讀"

    breadth = f"{falling}/{observed_count} 項風險代理走弱"
    volatility = (
        f"VIX {vix:.2f}"
        if vix is not None
        else "波動率資料尚未同步"
    )
    if price_confirmed and event_reason:
        return (
            f"{breadth}，{volatility}；價格已對事件《{event_reason}》形成跨資產確認，"
            "目前應先按風險重定價處理，而非視為單一股票雜訊"
        )
    if price_confirmed:
        return (
            f"{breadth}，{volatility}；弱勢由跨市場價格共振主導，"
            "尚無足夠證據把它歸因於單一新聞"
        )
    if event_reason:
        return (
            f"事件《{event_reason}》仍屬風險背景，但只有 {breadth}；"
            "跨資產價格尚未完成確認，不以標題直接宣告趨勢"
        )
    if improving:
        return f"{breadth}，另有 {improving} 項代理改善；市場訊號分歧，等待方向收斂"
    return f"{breadth}，{volatility}；目前屬局部風險升溫，尚未形成廣泛同步"


def assess_market_command(
    market: str,
    proxies: Mapping[str, Any] | None,
    news: Sequence[Any] | None = None,
    radar: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine observed prices, volatility, events and existing radar evidence.

    Missing observations add no risk and reduce confidence.  A headline cannot
    independently declare a crash; price/volatility confirmation is required.
    """
    family = str(market or "").upper()
    data = dict(proxies or {})
    event_score, event_reason = _event_risk(news)
    if family == "TW":
        fields = (("tx_night", 16.0), ("tsm_adr", 10.0), ("sox", 8.0), ("nq", 6.0))
    else:
        fields = (("sox", 15.0), ("nq", 13.0), ("qqq", 9.0), ("smh", 8.0))

    score = 0.0
    observed = []
    falling = 0
    improving = 0
    facts = []
    for key, weight in fields:
        value = _num(data.get(key))
        if value is None:
            continue
        observed.append(key)
        facts.append(f"{key.upper()} {value:+.2f}%")
        if value < 0:
            falling += 1
            score += min(weight, abs(value) * weight / 4.0)
        elif value > 0.6:
            improving += 1

    vix = _num(data.get("vix"))
    vix_change = _num(data.get("vix_change"))
    if vix is not None:
        observed.append("vix")
        facts.append(f"VIX {vix:.2f}")
        score += max(0.0, min(14.0, (vix - 18.0) * 0.9))
    if vix_change is not None and vix_change > 0:
        score += min(8.0, vix_change * 0.7)

    price_confirmed = falling >= 2 or (vix is not None and vix >= 25)
    if price_confirmed:
        score += event_score
    else:
        score += min(6.0, event_score)

    # Existing right-side radar is explicitly acknowledged as a corroborating
    # evidence source.  It cannot be parsed into fabricated precision.
    radar_count = sum(1 for value in (radar or {}).values() if str(value or "").strip())
    # This is data coverage, not a backtested directional hit rate.  Keep the
    # legacy confidence key as a compatibility alias until downstream readers
    # migrate, but never present it as prediction confidence in the UI.
    coverage = min(88, 28 + len(set(observed)) * 9 + min(12, radar_count))
    rebound = falling >= 2 and improving >= 1 and vix_change is not None and vix_change < 0
    code, label, action = _state(min(100.0, score), rebound)
    if len(set(observed)) < 2:
        code, label, action = "WAIT_CONFIRM", "⚪ 資料不足／等待市場確認", "暫不改變部位；等待至少兩項市場資料同步"
    thesis = _market_thesis(
        observed_count=len(set(observed)),
        falling=falling,
        improving=improving,
        vix=vix,
        price_confirmed=price_confirmed,
        event_reason=event_reason,
    )
    return {
        "market": family,
        "code": code,
        "label": label,
        "score": round(min(100.0, score), 1),
        "coverage": coverage,
        "confidence": coverage,
        "confidence_semantics": "data_coverage_only",
        "action": action,
        "thesis": thesis,
        "facts": facts[:5],
        "event_reason": event_reason,
        "price_confirmed": price_confirmed,
        "radar_evidence_count": radar_count,
    }
