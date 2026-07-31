# -*- coding: utf-8 -*-
"""V1080 session-aware entry-opportunity engine.

This module replaces the single-question "is the low-entry plan mature?" view
with a categorical execution decision:

* BUY_TODAY_CONFIRM       - a small confirmation order is allowed today.
* WAIT_INTRADAY_PULLBACK  - direction is constructive, wait for a same-session pullback.
* WAIT_NEXT_SESSION       - today's price formation is not practically tradable; re-evaluate next session.
* OVERHEATED_NO_CHASE     - direction may be right, but the current extension is too large.
* SELLING_EXPANSION_BLOCK - selling is still expanding; a lower price is not a buy signal.
* FAILED_BREAKOUT_EXIT    - the confirmation structure failed; cancel the entry plan.

It is an execution/narrative layer only.  It does not rewrite Direction,
T0/T1/High/Low, formal confidence, Prediction DNA, Audit, Genome, or learning
weights.  Original low-entry prices remain available as audit/reference facts,
but are explicitly demoted when a verified event/session repricing invalidates
the old anchor.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Mapping


SCHEMA = "TINO_ENTRY_OPPORTUNITY_V1080"

_STATE_META = {
    "BUY_TODAY_CONFIRM": ("green", "🟢", "今日可小量參與"),
    "WAIT_INTRADAY_PULLBACK": ("yellow", "🟡", "今日等回測"),
    "WAIT_NEXT_SESSION": ("yellow", "🟡", "等下一交易時段"),
    "OVERHEATED_NO_CHASE": ("yellow", "🟠", "過熱不追"),
    "SELLING_EXPANSION_BLOCK": ("red", "🔴", "賣壓未止"),
    "FAILED_BREAKOUT_EXIT": ("red", "🔴", "突破失敗／取消"),
    "DATA_WAIT": ("yellow", "⚪", "資料待確認"),
}

_POSITIVE_PROXY_ALIASES = {
    "SOX": ("SOX", "費半"),
    "NQ": ("NQ", "NASDAQ 100", "那斯達克100"),
    "QQQ": ("QQQ",),
    "SMH": ("SMH",),
    "TSM_ADR": ("TSM_ADR", "TSM ADR", "台積電ADR"),
}

_SELLING_STATES = {"panic_acceleration", "deleveraging", "selling_expansion"}
_EXHAUSTION_STATES = {"selling_exhaustion", "bottom_probe"}
_BLOCKED_THESIS_STATES = {
    "price_truth_blocked", "forecast_cooldown", "forecast_cooldown_event_pending",
    "event_awaiting_market_reaction", "event_reaction_in_progress",
    "earnings_expectation_reset", "good_news_rejected", "company_risk_confirmed",
    "capital_raise_repricing", "industry_narrative_repricing", "macro_beta_selloff",
    "positioning_selloff", "session_repricing", "trend_break",
}
_STRONG_THESIS_STATES = {
    "bad_news_absorbed", "surge_divergence", "limit_breakout",
    "strong_continuation", "countertrend_breakout", "conditional_attack",
}


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "--", "NA", "待同步", "暫停"):
            return None
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _first_number(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", _text(value))
    return _num(match.group(0)) if match else None


def _price(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "--"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _all_evidence_text(forecast: Any) -> str:
    decision = _mapping(getattr(forecast, "decision_card", {}))
    radar = _mapping(getattr(forecast, "radar", {}))
    pieces = [_text(value) for value in radar.values()]
    pieces.extend(_text(value) for value in decision.values() if isinstance(value, str))
    for item in list(getattr(forecast, "news_items", []) or []):
        if isinstance(item, Mapping):
            pieces.extend((_text(item.get("title")), _text(item.get("tag"))))
        else:
            pieces.extend((_text(getattr(item, "title", "")), _text(getattr(item, "tag", ""))))
    return "｜".join(piece for piece in pieces if piece)


def _proxy_changes(text: str) -> Dict[str, float]:
    """Extract only explicitly displayed proxy changes; never fabricate missing data."""
    output: Dict[str, float] = {}
    source = _text(text)
    for canonical, aliases in _POSITIVE_PROXY_ALIASES.items():
        candidates: list[float] = []
        for alias in aliases:
            pattern = rf"{re.escape(alias)}\s*[:：]?\s*([+-]?\d+(?:\.\d+)?)\s*%"
            for match in re.finditer(pattern, source, flags=re.I):
                value = _num(match.group(1))
                if value is not None:
                    candidates.append(value)
        if candidates:
            output[canonical] = candidates[-1]
    return output


def _regime_payload(decision: Mapping[str, Any]) -> Dict[str, Any]:
    for key in ("_market_regime_v1077", "_market_regime", "_regime_state", "_shadow_market_regime"):
        value = decision.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    thesis = _mapping(decision.get("_decision_thesis") or decision.get("_decision_narrative"))
    value = thesis.get("market_regime")
    return dict(value) if isinstance(value, Mapping) else {}


def _session(decision: Mapping[str, Any], thesis: Mapping[str, Any]) -> str:
    truth = _mapping(thesis.get("price_truth"))
    direct = _text(truth.get("session")).lower()
    if direct:
        return direct
    meta = _mapping(decision.get("_price_meta"))
    direct = _text(meta.get("session") or meta.get("market_status")).lower()
    if direct:
        return direct
    title = _text(decision.get("資料標題"))
    if title.startswith("盤中"):
        return "intraday"
    if title.startswith("盤前"):
        return "pre_market"
    if title.startswith("盤後"):
        return "after_hours"
    if title.startswith("收盤"):
        return "after_close"
    return ""


def _market(forecast: Any) -> str:
    ticker = getattr(forecast, "ticker", None)
    return _text(getattr(ticker, "market", "")).upper()


def _limit_like(market: str, day_pct: float, thesis_state: str, evidence: str) -> bool:
    if thesis_state == "limit_breakout" or "漲停" in evidence:
        return True
    return market == "TW" and day_pct >= 9.5


def _failed_breakout(*, last: float, stop: float | None, above_vwap: bool,
                     thesis_state: str, regime_state: str) -> bool:
    if stop is not None and last > 0 and last < stop:
        return True
    if thesis_state in {"good_news_rejected", "trend_break"} and not above_vwap:
        return True
    return regime_state in _SELLING_STATES and not above_vwap


def _dynamic_reference(*, state: str, last: float, vwap: float | None,
                       confirmation: float | None, first: float | None,
                       second: float | None, stop: float | None,
                       no_chase: float | None, anchor_invalidated: bool) -> Dict[str, Any]:
    original = {
        "first": first, "second": second, "confirmation": confirmation,
        "stop": stop, "no_chase": no_chase,
    }
    if state == "BUY_TODAY_CONFIRM":
        trigger = f"守住時段 VWAP {_price(vwap)}" if vwap is not None else "守住時段 VWAP／突破平台"
        strategy = f"小量確認｜{trigger}"
        secondary = f"失效 {_price(stop)}" if stop is not None else "跌破突破平台取消"
    elif state == "WAIT_INTRADAY_PULLBACK":
        if vwap is not None:
            strategy = f"等回測 VWAP {_price(vwap)}"
        elif not anchor_invalidated and first is not None:
            strategy = f"等回測 {_price(first)}"
        else:
            strategy = "等首次量縮回測"
        secondary = f"重新站穩 {_price(confirmation)} 可確認" if confirmation is not None else "回測不破才參與"
    elif state == "WAIT_NEXT_SESSION":
        strategy = "下一交易時段重新計算"
        secondary = f"原支撐 {_price(first)} 僅供稽核" if first is not None else "先看缺口與 VWAP 承接"
    elif state == "OVERHEATED_NO_CHASE":
        strategy = f"不追 {_price(last)}"
        secondary = f"等 VWAP {_price(vwap)}／突破平台" if vwap is not None else "等量縮回測／突破平台"
    elif state == "FAILED_BREAKOUT_EXIT":
        strategy = "取消新進場"
        secondary = f"重新站回 {_price(confirmation)} 後重評" if confirmation is not None else "等待價格結構修復"
    elif state == "SELLING_EXPANSION_BLOCK":
        strategy = "禁止接刀"
        secondary = f"收復 {_price(confirmation)} 後重評" if confirmation is not None else "等賣壓量縮與 VWAP 收復"
    else:
        strategy = "等待資料同步"
        secondary = "不以缺失資料建立買點"
    return {
        "primary": strategy,
        "secondary": secondary,
        "anchor_status": "event_repriced" if anchor_invalidated else "original_anchor_active",
        "original_model_prices": original,
        "narrative_only": True,
    }


def assess_entry_opportunity(forecast: Any) -> Dict[str, Any]:
    decision = _mapping(getattr(forecast, "decision_card", {}))
    thesis = _mapping(decision.get("_decision_thesis") or decision.get("_decision_narrative"))
    truth = _mapping(thesis.get("price_truth"))
    regime = _regime_payload(decision)

    market = _market(forecast)
    session = _session(decision, thesis)
    last = _num(decision.get("現價")) or _num(truth.get("current_price")) or 0.0
    day_pct = _num(decision.get("漲跌幅"))
    if day_pct is None:
        day_pct = _num(truth.get("current_return_pct")) or 0.0
    first = _num(decision.get("低接第一批"))
    second = _num(decision.get("低接第二批"))
    stop = _num(decision.get("防守"))
    no_chase = _num(decision.get("不追"))
    confirmation = _first_number(decision.get("轉強")) or _first_number(decision.get("攻擊"))
    vwap = _num(truth.get("vwap"))
    vwap_text = _text(decision.get("VWAP位置"))
    above_vwap = bool(truth.get("vwap_available") and vwap is not None and last >= vwap) or "VWAP 上方" in vwap_text or "VWAP上方" in vwap_text

    thesis_state = _text(thesis.get("state"))
    entry_permission = _text(thesis.get("entry_permission"))
    action_mode = _text(thesis.get("action_mode"))
    regime_state = _text(regime.get("state") or regime.get("current_state"))
    evidence = _all_evidence_text(forecast)
    proxy_changes = _proxy_changes(evidence)
    positive_proxies = {key: value for key, value in proxy_changes.items() if value >= 0.5}
    negative_proxies = {key: value for key, value in proxy_changes.items() if value <= -0.5}
    max_positive_proxy = max(positive_proxies.values(), default=0.0)
    cross_market_confirmed = len(positive_proxies) >= 2

    gate = _text(_mapping(decision.get("_direction_engine")).get("gate_state"))
    a_breakout = "A突破" in gate
    limit_like = _limit_like(market, day_pct, thesis_state, evidence)
    strong_tape = day_pct >= (3.0 if market == "US" else 4.0) or thesis_state in _STRONG_THESIS_STATES or a_breakout
    broad_event_repricing = bool(strong_tape and above_vwap and cross_market_confirmed)

    old_anchor_far = bool(first is not None and last > 0 and (last / first - 1.0) >= 0.07)
    old_no_chase_crossed = bool(no_chase is not None and last >= no_chase)
    anchor_invalidated = bool(broad_event_repricing and (old_anchor_far or old_no_chase_crossed))

    excess_vs_proxy = day_pct - max_positive_proxy if max_positive_proxy > 0 else day_pct
    overextended = bool(
        day_pct >= (20.0 if market == "US" else 9.8)
        or excess_vs_proxy >= 11.0
        or (old_no_chase_crossed and not anchor_invalidated)
    )

    hard_truth_block = bool(_mapping(decision.get("_price_meta")).get("decision_blocked"))
    selling_expansion = bool(
        regime_state in _SELLING_STATES
        or (day_pct <= -4.0 and not above_vwap)
        or (len(negative_proxies) >= 2 and day_pct < 0 and not above_vwap)
    )
    exhaustion = regime_state in _EXHAUSTION_STATES
    failed = _failed_breakout(
        last=last, stop=stop, above_vwap=above_vwap,
        thesis_state=thesis_state, regime_state=regime_state,
    )

    conditions: list[Dict[str, Any]] = []
    if hard_truth_block or last <= 0:
        state = "DATA_WAIT"
        reasons = ["即時價格或參考基準尚未通過同源驗證"]
    elif failed and not selling_expansion:
        state = "FAILED_BREAKOUT_EXIT"
        reasons = ["價格已跌破防守／突破結構，取消原進場計畫"]
    elif selling_expansion or (
        entry_permission == "blocked" and thesis_state in _BLOCKED_THESIS_STATES and day_pct <= 0
    ):
        state = "SELLING_EXPANSION_BLOCK"
        reasons = ["賣壓或風險重定價仍在擴張，跌深本身不是買點"]
    elif limit_like:
        state = "WAIT_NEXT_SESSION"
        reasons = ["今日極強／漲停價格缺乏合理新進場空間"]
        if cross_market_confirmed:
            reasons.append("大盤與產業同向確認，方向不否定，但不追鎖價")
    elif session == "after_hours" and (overextended or broad_event_repricing):
        state = "WAIT_NEXT_SESSION" if overextended else "WAIT_INTRADAY_PULLBACK"
        reasons = ["盤後已形成重新定價，正式盤開盤結構仍待驗證"]
    elif overextended:
        state = "OVERHEATED_NO_CHASE"
        reasons = ["方向可能正確，但相對大盤／產業代理已過度延伸"]
    elif broad_event_repricing and (a_breakout or thesis_state in _STRONG_THESIS_STATES):
        state = "BUY_TODAY_CONFIRM"
        reasons = ["價格站上 VWAP，且至少兩項跨市場代理同向"]
        if anchor_invalidated:
            reasons.append("原低接／不追錨點已被事件重新定價降級")
    elif strong_tape and above_vwap:
        state = "WAIT_INTRADAY_PULLBACK"
        reasons = ["方向轉強，但跨市場確認或價格換手尚未完整"]
    elif exhaustion and above_vwap:
        state = "BUY_TODAY_CONFIRM"
        reasons = ["賣壓已由擴張轉為衰竭，且價格收復 VWAP"]
    elif exhaustion:
        state = "WAIT_INTRADAY_PULLBACK"
        reasons = ["賣壓開始衰竭，但尚未完成 VWAP／止跌確認"]
    elif entry_permission == "blocked":
        state = "SELLING_EXPANSION_BLOCK"
        reasons = ["上層決策閘門仍禁止新進場"]
    elif action_mode in {"pullback", "pullback_or_confirmation", "confirmation_only"}:
        state = "WAIT_INTRADAY_PULLBACK"
        reasons = ["等待同一交易時段的回測或確認，不再預設一定回到舊低接價"]
    else:
        state = "WAIT_INTRADAY_PULLBACK"
        reasons = ["目前優勢不足以直接追價，先等價格完成可驗證回測"]

    color, icon, label = _STATE_META[state]
    strategy = _dynamic_reference(
        state=state, last=last, vwap=vwap, confirmation=confirmation,
        first=first, second=second, stop=stop, no_chase=no_chase,
        anchor_invalidated=anchor_invalidated,
    )

    conditions.append({"ok": above_vwap, "text": "價格站在時段 VWAP 上方" if above_vwap else "價格尚未站回時段 VWAP"})
    conditions.append({
        "ok": cross_market_confirmed,
        "text": f"跨市場同向 {len(positive_proxies)} 項" if cross_market_confirmed else "跨市場同向證據不足",
    })
    if anchor_invalidated:
        conditions.insert(0, {"ok": True, "text": "原低接錨點已因事件重新定價降級"})
    if selling_expansion:
        conditions.insert(0, {"ok": False, "text": "賣壓仍在擴張"})

    if state == "BUY_TODAY_CONFIRM":
        message = (
            f"今日可小量參與：{reasons[0]}。先鋒部位僅做確認單；"
            f"{strategy['primary']}，第二筆等待首次量縮回測；{strategy['secondary']}。"
        )
    elif state == "WAIT_INTRADAY_PULLBACK":
        message = (
            f"今日先等回測：{reasons[0]}。{strategy['primary']}；"
            f"回測不破且量能降溫才參與，{strategy['secondary']}。"
        )
    elif state == "WAIT_NEXT_SESSION":
        message = (
            f"等下一交易時段：{reasons[0]}。今日不追；"
            "下一交易時段先看缺口是否守住、賣壓是否量縮及 VWAP 是否承接，再產生新操作價。"
        )
    elif state == "OVERHEATED_NO_CHASE":
        message = (
            f"過熱不追：{reasons[0]}。{strategy['primary']}；{strategy['secondary']}，"
            "未回測前不新增部位。"
        )
    elif state == "FAILED_BREAKOUT_EXIT":
        message = f"突破失敗／取消：{reasons[0]}。{strategy['secondary']}。"
    elif state == "SELLING_EXPANSION_BLOCK":
        message = (
            f"賣壓未止，禁止接刀：{reasons[0]}。"
            "至少等待低點不再下移、賣壓量縮並收復 VWAP；"
            f"{strategy['secondary']}。"
        )
    else:
        message = "價格或時段資料尚未完成驗證，暫不建立進場價格。"

    summary_parts = list(reasons)
    if len(reasons) == 1 and anchor_invalidated:
        summary_parts.append("原低接價保留於 Audit，不再作為目前主要買點")

    return {
        "schema": SCHEMA,
        "state": state,
        "label": label,
        "color": color,
        "icon": icon,
        "summary": "；".join(summary_parts[:2]),
        "conditions": conditions,
        "canonical_main_message": message,
        "price_strategy": strategy,
        "price_strategy_text": f"{strategy['primary']}｜{strategy['secondary']}",
        "anchor_invalidated": anchor_invalidated,
        "original_low_entry_audit": {"first": first, "second": second, "no_chase": no_chase},
        "market": market,
        "session": session,
        "day_pct": round(day_pct, 4),
        "above_vwap": above_vwap,
        "proxy_changes": proxy_changes,
        "cross_market_confirmed": cross_market_confirmed,
        "excess_vs_proxy": round(excess_vs_proxy, 4),
        "thesis_state": thesis_state,
        "regime_state": regime_state,
        "show_score": False,
        "narrative_only": True,
        "formal_forecast_unchanged": True,
    }


assess_entry_timing = assess_entry_opportunity
