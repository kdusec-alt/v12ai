# -*- coding: utf-8 -*-
"""V1081 ticker-independent market command with Session Truth.

Red means risk is still accelerating. Purple means fear has been released but
price has not completed a bottom; purple is never a direct entry signal.
Cross-asset votes require at least two exact-session rows. A single Taiwan cash
index may benchmark individual relative strength, but cannot prove cross-asset
market confirmation by itself.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

try:
    from news_timestamp_provenance_v1079 import timestamp_is_model_eligible
except Exception:
    def timestamp_is_model_eligible(item):
        return True


SCHEMA = "TINO_MARKET_COMMAND_V1081"


def _num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except Exception:
        return None


def _item_value(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, Mapping) else getattr(item, key, None)


def _tag_truth(tag: str, key: str) -> bool | None:
    match = re.search(rf"(?:^|[|;,\s]){re.escape(key)}\s*=\s*([01])(?:$|[|;,\s])", tag.lower())
    return bool(int(match.group(1))) if match else None


def _event_risk(news: Sequence[Any] | None) -> tuple[float, str, bool]:
    score = 0.0
    reason = ""
    verified = False
    for item in news or []:
        if not timestamp_is_model_eligible(item):
            continue
        tag = str(_item_value(item, "tag") or "")
        explicit_values = [
            _tag_truth(tag, "event_verified"),
            _tag_truth(tag, "source_verified"),
            _tag_truth(tag, "content_verified"),
            _tag_truth(tag, "model_eligible"),
        ]
        explicit = next((value for value in explicit_values if value is not None), None)
        # Severity identifies importance, not authenticity. Without an explicit
        # positive truth field, the headline remains context-only.
        if explicit is not True:
            continue
        title = str(_item_value(item, "title") or "")
        match = re.search(r"(?:shock_level|severity)=([0-5])", tag.lower())
        level = int(match.group(1)) if match else 0
        candidate = level * 6.0
        if candidate > score:
            score = candidate
            reason = title[:72]
            verified = True
    return min(25.0, score), reason, verified


def _state(score: float, rebound: bool, market: str) -> tuple[str, str, str]:
    if score >= 70:
        return (
            "CRASH", "🔴 風險加速／流動性踩踏",
            "停止新增方向性部位並保留現金；至少等待跨市場跌勢、成交賣壓與波動率同步收斂",
        )
    if rebound and score >= 45:
        return (
            "CAPITULATION", "🟣 恐慌已宣洩／尚未止跌",
            "停止追空；等待缺口收斂、拋售量縮、VIX回落與VWAP承接。紫燈不是直接進場訊號",
        )
    if score >= 48:
        return (
            "SELL_OFF", "🟠 廣泛賣壓",
            "將新倉降為確認單；不把急跌視為折價，先等指數、廣度與波動率止穩",
        )
    if score >= 28:
        return (
            "CAUTION", "🟡 風險升溫",
            "風險預算降至中性偏低；不追價，既有部位依支撐與曝險比例管理",
        )
    return (
        "NORMAL", "🟢 正常／風險可控",
        "維持既定風險預算；個股仍須通過價格、事件、基本面與部位確認",
    )


def _market_thesis(
    *, observed_count: int, falling: int, improving: int, vix: float | None,
    price_confirmed: bool, same_session_confirmed: bool,
    event_reason: str, event_verified: bool, vote_scope: str, excluded_count: int,
) -> str:
    if observed_count < 2:
        return "可用同時段跨市場證據不足，現階段不能對大盤方向形成高品質判讀"
    breadth = f"{falling}/{observed_count} 項風險代理惡化"
    volatility = f"VIX {vix:.2f}" if vix is not None else "波動率資料尚未同步"
    exclusion = f"；另排除 {excluded_count} 項不同Session資料" if excluded_count else ""
    if same_session_confirmed and event_reason and event_verified:
        return (
            f"{breadth}，{volatility}{exclusion}；已驗證事件《{event_reason}》與同Session價格反應一致，"
            "但不把相關性宣稱為唯一因果"
        )
    if price_confirmed and vote_scope == "coherent_context_session":
        return (
            f"{breadth}，{volatility}{exclusion}；價格共振屬一致時段背景，"
            "與目前個股Session不同，不宣稱跨資產因果已確認"
        )
    if price_confirmed:
        return (
            f"{breadth}，{volatility}{exclusion}；弱勢由同組跨市場價格共振主導，"
            "尚無足夠證據把它歸因於單一新聞"
        )
    if event_reason and event_verified:
        return (
            f"事件《{event_reason}》仍屬已驗證風險背景，但只有 {breadth}{exclusion}；"
            "同Session價格尚未完成確認，不以標題直接宣告趨勢"
        )
    if improving:
        balance = (
            "市場環境改善" if improving > falling else
            "風險升溫" if falling > improving else
            "市場訊號分歧"
        )
        return f"{breadth}｜{improving} 項風險代理改善{exclusion}；{balance}"
    return f"{breadth}，{volatility}{exclusion}；目前屬局部風險升溫，尚未形成廣泛同步"


def assess_market_command(
    market: str,
    proxies: Mapping[str, Any] | None,
    news: Sequence[Any] | None = None,
    radar: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    family = str(market or "").upper()
    data = dict(proxies or {})
    event_score, event_reason, event_verified = _event_risk(news)
    fields = (
        (("tx_night", 16.0), ("tsm_adr", 10.0), ("sox", 8.0), ("nq", 6.0))
        if family == "TW" else
        (("sox", 15.0), ("nq", 13.0), ("qqq", 9.0), ("smh", 8.0))
    )

    session_truth = dict(data.get("_session_truth_v1081") or {})
    eligible_keys = {str(key).lower() for key in (session_truth.get("eligible_keys") or [])}
    excluded_keys = {str(key).lower() for key in (session_truth.get("excluded_keys") or [])}
    guard_active = bool(session_truth.get("schema"))
    vote_scope = str(session_truth.get("vote_scope") or "legacy_unscoped")
    cross_asset_same_session = bool(
        session_truth.get("cross_asset_same_session")
        if "cross_asset_same_session" in session_truth
        else session_truth.get("same_session")
    )

    score = 0.0
    observed: list[str] = []
    falling = 0
    improving = 0
    facts: list[str] = []
    for key, weight in fields:
        if guard_active and key not in eligible_keys:
            continue
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
    vix_eligible = not guard_active or "vix_change" in eligible_keys
    if vix is not None and vix_eligible:
        observed.append("vix")
        facts.append(f"VIX {vix:.2f}")
        score += max(0.0, min(14.0, (vix - 18.0) * 0.9))
    if vix_change is not None and vix_change > 0 and vix_eligible:
        score += min(8.0, vix_change * 0.7)

    price_confirmed = falling >= 2 or (vix is not None and vix >= 25 and vix_eligible)
    same_session_confirmed = bool(price_confirmed and cross_asset_same_session)
    score += event_score if same_session_confirmed and event_verified else min(6.0, event_score)

    radar_count = sum(1 for value in (radar or {}).values() if str(value or "").strip())
    coverage = min(88, 28 + len(set(observed)) * 9 + min(12, radar_count))
    rebound = bool(
        falling >= 2 and improving >= 1 and vix_change is not None
        and vix_change < 0 and vix_eligible
    )
    code, label, action = _state(min(100.0, score), rebound, family)
    if len(set(observed)) < 2:
        code, label, action = (
            "WAIT_CONFIRM", "⚪ 同時段資料不足／等待確認",
            "暫不改變部位；等待至少兩項一致Session市場資料同步",
        )
    thesis = _market_thesis(
        observed_count=len(set(observed)), falling=falling, improving=improving,
        vix=vix if vix_eligible else None, price_confirmed=price_confirmed,
        same_session_confirmed=same_session_confirmed, event_reason=event_reason,
        event_verified=event_verified, vote_scope=vote_scope,
        excluded_count=len(excluded_keys),
    )
    session_fact = {
        "same_session": "同Session",
        "same_session_benchmark": "同Session單一基準",
        "coherent_context_session": "一致背景Session",
        "insufficient": "Session不足",
    }.get(vote_scope, "Session未分組")
    if guard_active:
        facts.append(f"Session {session_fact}")

    return {
        "schema": SCHEMA,
        "market": family,
        "code": code,
        "label": label,
        "score": round(min(100.0, score), 1),
        "coverage": coverage,
        "confidence": coverage,
        "confidence_semantics": "data_coverage_only",
        "action": action,
        "thesis": thesis,
        "facts": facts[:6],
        "event_reason": event_reason,
        "event_verified": event_verified,
        "price_confirmed": price_confirmed,
        "same_session_confirmed": same_session_confirmed,
        "session_guard_active": guard_active,
        "session_truth": session_truth,
        "excluded_proxy_keys": sorted(excluded_keys),
        "radar_evidence_count": radar_count,
        "purple_is_entry_signal": False,
    }
