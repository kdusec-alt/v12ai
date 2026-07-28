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
        return "CAPITULATION", "🟣 恐慌宣洩／等待止跌確認", "停止追空；只觀察跌停打開、VIX回落與量價承接"
    if score >= 70:
        return "CRASH", "🔴 股災／流動性踩踏", "停止追價與擴大部位；保留現金，等待壓力收斂"
    if score >= 48:
        return "SELL_OFF", "🟠 廣泛賣壓", "降低單筆部位；不急抄底，等待指數與廣度止穩"
    if score >= 28:
        return "CAUTION", "🟡 風險升溫", "暫停追高；持股依支撐與部位紀律處理"
    return "NORMAL", "🟢 正常／風險可控", "維持原策略；仍以個股價格與風控確認"


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
    confidence = min(88, 28 + len(set(observed)) * 9 + min(12, radar_count))
    rebound = falling >= 2 and improving >= 1 and vix_change is not None and vix_change < 0
    code, label, action = _state(min(100.0, score), rebound)
    if len(set(observed)) < 2:
        code, label, action = "WAIT_CONFIRM", "⚪ 資料不足／等待市場確認", "暫不改變部位；等待至少兩項市場資料同步"
    return {
        "market": family,
        "code": code,
        "label": label,
        "score": round(min(100.0, score), 1),
        "confidence": confidence,
        "action": action,
        "facts": facts[:5],
        "event_reason": event_reason,
        "price_confirmed": price_confirmed,
        "radar_evidence_count": radar_count,
    }

