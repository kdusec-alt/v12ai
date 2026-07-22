# -*- coding: utf-8 -*-
"""Evidence-grounded wording for the V9/V12 AI entry card.

This module is intentionally narrative-only.  It may explain or gate an entry
plan, but it must never change Direction probabilities, T0/T1/High/Low, Trace,
Prediction DNA, or learning weights.

The visible reasoning order is:

    price reality -> overseas confirmation -> news -> positioning -> model

That order prevents a limit-up/strong close from being described by a generic
bearish template merely because one slower chip family is still negative.
Conflicts remain visible instead of being silently averaged away.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Dict, Mapping, Sequence

from models import NewsItem, PriceFrame


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _maybe_num(value: Any) -> float | None:
    if value in (None, "", "NA", "待同步"):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _clip_text(value: Any, limit: int = 28) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(limit - 1, 1)] + "…"


def _tag(item: NewsItem | Mapping[str, Any]) -> str:
    return str(item.get("tag") if isinstance(item, Mapping) else getattr(item, "tag", "") or "").lower()


def _title(item: NewsItem | Mapping[str, Any]) -> str:
    return str(item.get("title") if isinstance(item, Mapping) else getattr(item, "title", "") or "").strip()


def _score(item: NewsItem | Mapping[str, Any]) -> float:
    return _num(item.get("score") if isinstance(item, Mapping) else getattr(item, "score", 0.0), 0.0)


def _accepted(block: Mapping[str, Any] | None) -> bool:
    if not isinstance(block, Mapping):
        return False
    source = str(block.get("source") or "").upper()
    return bool(block.get("accepted")) and not any(token in source for token in ("SAMPLE", "MOCK", "FALLBACK"))


def _profile(price: PriceFrame) -> str:
    symbol = str(price.ticker.resolved_symbol or "").upper()
    code = symbol.split(".")[0]
    name = str(price.ticker.name or "").upper()
    persona = (price.context or {}).get("persona")
    if isinstance(persona, Mapping):
        persona_text = " ".join(str(value) for value in persona.values()).upper()
    else:
        persona_text = str(persona or "").upper()
    blob = f"{symbol} {name} {persona_text}"
    if code in {"2408", "2344", "2337", "8299", "3260", "5351", "4967", "2451", "6770", "2349"} or any(
        word in blob for word in ("記憶體", "DRAM", "NAND", "HBM", "MICRON", "旺宏", "華邦", "力積電")
    ):
        return "memory"
    if any(word in blob for word in ("SEMICONDUCTOR", "半導體", "晶圓", "IC設計", "台積電", "聯發科", "SOXX", "SMH")):
        return "semiconductor"
    if any(word in blob for word in ("BIOTECH", "PHARMA", "HEALTHCARE", "生技", "生醫", "醫療", "新藥", "醣基")):
        return "biotech"
    return "broad"


@dataclass(frozen=True)
class PriceReality:
    day_pct: float
    atr_move: float
    close_location: float
    above_vwap: bool
    breakout: bool
    limit_like: bool
    strong_up: bool
    strong_down: bool
    deep_stabilizing: bool
    weak_rebound: bool
    trend_break: bool


def price_reality(price: PriceFrame) -> PriceReality:
    last = _num(price.last)
    previous = _num(price.previous_close, last) or last
    vwap = _num(price.vwap, last) or last
    atr = max(_num(price.atr14), last * 0.012, 0.01)
    high = max(_num(price.high, last), last)
    low = min(_num(price.low, last), last)
    day_range = max(high - low, atr * 0.15, 0.01)
    close_location = max(0.0, min(1.0, (last - low) / day_range))
    day_pct = (last - previous) / previous * 100.0 if previous > 0 else 0.0
    atr_move = (last - previous) / atr

    prior_highs = [_num(value) for value in (price.recent_highs or [])[:-1] if _num(value) > 0]
    prior_lows = [_num(value) for value in (price.recent_lows or [])[:-1] if _num(value) > 0]
    breakout = bool(prior_highs and last >= max(prior_highs[-20:]) - atr * 0.08)
    breakdown = bool(prior_lows and last <= min(prior_lows[-20:]) + atr * 0.08)

    market = str(price.ticker.market or "").upper()
    limit = _maybe_num(price.ticker.price_limit_pct)
    limit_threshold = abs(limit) * 100.0 * 0.94 if limit and limit > 0 else 9.2
    limit_like = bool(market == "TW" and day_pct >= limit_threshold and close_location >= 0.82)

    # A +4% move can be very meaningful for a mature TW stock, while a high
    # beta US name needs ATR confirmation.  Both still require price acceptance.
    atr_pct = atr / max(previous, 0.01) * 100.0
    strong_threshold = (
        min(4.2, max(2.5, atr_pct * 0.80))
        if market == "TW"
        else min(6.0, max(3.5, atr_pct * 1.00))
    )
    strong_up = bool(
        day_pct >= strong_threshold
        and last >= vwap
        and close_location >= 0.68
        and (breakout or atr_move >= 1.05 or limit_like)
    )
    strong_down = bool(
        day_pct <= -strong_threshold
        and last < vwap
        and close_location <= 0.34
        and (breakdown or atr_move <= -1.05)
    )
    deep_stabilizing = bool(day_pct <= -2.5 and close_location >= 0.68 and last >= vwap)

    closes = [_num(value) for value in (price.recent_closes or []) if _num(value) > 0]
    ret5 = ((closes[-1] / closes[-6]) - 1.0) * 100.0 if len(closes) >= 6 and closes[-6] else 0.0
    ret20 = ((closes[-1] / closes[-21]) - 1.0) * 100.0 if len(closes) >= 21 and closes[-21] else 0.0
    weak_rebound = bool(day_pct > 0.5 and (ret5 < -3.0 or ret20 < -8.0) and (last < vwap or close_location < 0.62))
    trend_break = bool(strong_down or (breakdown and day_pct < -1.5 and last < vwap and close_location <= 0.40))
    return PriceReality(
        day_pct=round(day_pct, 4),
        atr_move=round(atr_move, 4),
        close_location=round(close_location, 4),
        above_vwap=last >= vwap,
        breakout=breakout,
        limit_like=limit_like,
        strong_up=strong_up or limit_like,
        strong_down=strong_down,
        deep_stabilizing=deep_stabilizing,
        weak_rebound=weak_rebound,
        trend_break=trend_break,
    )


def _overseas_evidence(price: PriceFrame) -> Dict[str, Any]:
    macro = (price.context or {}).get("macro")
    macro = macro if isinstance(macro, Mapping) else {}
    if not bool(macro.get("accepted")):
        return {"sign": 0, "score": 0.0, "text": "海外代理待確認", "profile": _profile(price), "available": False}
    profile = _profile(price)
    values = {
        "費半": _maybe_num(macro.get("sox")),
        "那指": _maybe_num(macro.get("nq") if macro.get("nq") is not None else macro.get("qqq")),
        "MU": _maybe_num(macro.get("mu")),
        "TSM ADR": _maybe_num(macro.get("tsm_adr")),
        "台指夜盤": _maybe_num(macro.get("tx_night")),
    }
    if profile == "memory":
        weights = {"費半": 0.30, "那指": 0.15, "MU": 0.35, "TSM ADR": 0.08, "台指夜盤": 0.12}
    elif profile == "semiconductor":
        weights = {"費半": 0.38, "那指": 0.16, "MU": 0.10, "TSM ADR": 0.20, "台指夜盤": 0.16}
    else:
        weights = {"費半": 0.18, "那指": 0.42, "MU": 0.05, "TSM ADR": 0.08, "台指夜盤": 0.27}
    valid = [(name, value, weights[name]) for name, value in values.items() if value is not None]
    weight_total = sum(weight for _, _, weight in valid)
    composite = sum(value * weight for _, value, weight in valid) / weight_total if weight_total > 0 else 0.0
    sign = 1 if composite >= 0.45 else -1 if composite <= -0.45 else 0
    shown = sorted(valid, key=lambda row: (weights[row[0]], abs(row[1])), reverse=True)[:3]
    text = "／".join(f"{name} {value:+.2f}%" for name, value, _ in shown) or "海外代理待確認"
    return {"sign": sign, "score": composite, "text": text, "profile": profile, "available": bool(valid)}


def _news_evidence(news_items: Sequence[NewsItem | Mapping[str, Any]] | None) -> Dict[str, Any]:
    company: list[NewsItem | Mapping[str, Any]] = []
    global_rows: list[NewsItem | Mapping[str, Any]] = []
    for item in news_items or []:
        tag = _tag(item)
        title = _title(item)
        if not title or "待同步" in title or "syncing" in title.lower():
            continue
        if any(key in tag for key in ("policy_geo", "macro_event", "daily_headline", "tw_daily")):
            global_rows.append(item)
        else:
            company.append(item)

    company_score = sum(_score(item) for item in company if abs(_score(item)) >= 0.06)
    global_score = sum(_score(item) for item in global_rows if abs(_score(item)) >= 0.06)
    combined = company_score + global_score * 0.45
    sign = 1 if combined >= 0.06 else -1 if combined <= -0.06 else 0
    ranked = sorted(company + global_rows, key=lambda item: abs(_score(item)), reverse=True)
    top = ranked[0] if ranked else None
    top_title = _clip_text(_title(top), 30) if top is not None else ""
    if sign > 0:
        text = f"新聞偏多《{top_title}》" if top_title else "新聞偏多"
    elif sign < 0:
        text = f"新聞偏空《{top_title}》" if top_title else "新聞偏空"
    else:
        text = f"新聞待價格確認《{top_title}》" if top_title else "新聞無明確方向"
    return {
        "sign": sign,
        "score": combined,
        "company_score": company_score,
        "global_score": global_score,
        "text": text,
        "top_title": top_title,
        "available": bool(ranked),
    }


def _positioning_evidence(price: PriceFrame, direction: Any) -> Dict[str, Any]:
    market = str(price.ticker.market or "").upper()
    family = dict(getattr(direction, "family_contributions", {}) or {})
    factors = dict(getattr(direction, "factor_contributions", {}) or {})
    if market == "TW":
        flow = _num(family.get("flow"), _num(factors.get("法人"), 0.0))
        leverage = _num(family.get("leverage"), _num(factors.get("融資"), 0.0))
        inst = (price.context or {}).get("inst")
        inst = inst if isinstance(inst, Mapping) else {}
        if _accepted(inst):
            actors = [_num(inst.get("foreign")), _num(inst.get("trust")), _num(inst.get("dealer"))]
            negative_actors = sum(value < 0 for value in actors)
            positive_actors = sum(value > 0 for value in actors)
        else:
            negative_actors = positive_actors = 0
        sign = -1 if flow <= -1.2 or negative_actors >= 2 else 1 if flow >= 1.2 or positive_actors >= 2 else 0
        if sign > 0:
            text = "法人籌碼偏多"
        elif sign < 0:
            text = "法人籌碼未跟上"
        else:
            text = "法人籌碼分歧／待確認"
        if leverage <= -1.0:
            text += "，融資壓力偏高"
        elif leverage >= 1.0:
            text += "，去槓桿改善"
        return {"sign": sign, "score": flow + leverage * 0.35, "text": text, "available": bool(flow or leverage or _accepted(inst))}

    short = (price.context or {}).get("short")
    short = short if isinstance(short, Mapping) else {}
    short_float = _maybe_num(short.get("short_float"))
    trend = _num(family.get("trend"), 0.0)
    if short_float is not None and short_float >= 12.0:
        if trend > 0:
            return {"sign": 1, "score": trend, "text": f"Short Float {short_float:.1f}%，回補可放大強勢", "available": True}
        return {"sign": -1 if trend < 0 else 0, "score": trend, "text": f"Short Float {short_float:.1f}%，空方壓力仍高", "available": True}
    return {"sign": 0, "score": trend, "text": "Short／機構部位待價格確認", "available": short_float is not None}


def _model_evidence(direction: Any) -> Dict[str, Any]:
    label = str(getattr(direction, "label", "NEUTRAL") or "NEUTRAL")
    score = _num(getattr(direction, "score", 0.0), 0.0)
    conflict = _num(getattr(direction, "conflict", 0.0), 0.0)
    p_up = _num(getattr(direction, "p_up", 0.0), 0.0) * 100.0
    p_neutral = _num(getattr(direction, "p_neutral", 0.0), 0.0) * 100.0
    p_down = _num(getattr(direction, "p_down", 0.0), 0.0) * 100.0
    sign = 1 if label == "UP" else -1 if label == "DOWN" else 0
    return {
        "sign": sign,
        "score": score,
        "conflict": conflict,
        "text": f"模型 A/B/C {p_up:.0f}/{p_neutral:.0f}/{p_down:.0f}｜衝突 {conflict * 100:.0f}%",
    }


def build_ai_decision_narrative(
    price: PriceFrame,
    direction: Any,
    news_items: Sequence[NewsItem | Mapping[str, Any]] | None,
    *,
    session_prefix: str,
    low1: float,
    low2: float,
    attack: float,
    stop: float,
    no_chase: float,
    hard_defense: bool = False,
    event_caution: bool = False,
    event_name: str = "一級宏觀事件",
    pause_second: bool = False,
) -> Dict[str, Any]:
    """Return visible wording plus an auditable narrative payload.

    The model result is read-only.  The state machine only selects wording and
    entry discipline from already-computed evidence.
    """
    reality = price_reality(price)
    overseas = _overseas_evidence(price)
    news = _news_evidence(news_items)
    positioning = _positioning_evidence(price, direction)
    model = _model_evidence(direction)

    negative = [name for name, row in (("海外", overseas), ("新聞", news), ("籌碼", positioning), ("模型", model)) if row.get("sign", 0) < 0]
    positive = [name for name, row in (("海外", overseas), ("新聞", news), ("籌碼", positioning), ("模型", model)) if row.get("sign", 0) > 0]
    price_up = reality.strong_up
    price_down = reality.strong_down or reality.trend_break

    if price_up and news["sign"] < 0:
        state = "bad_news_absorbed"
        title = "AI進場決策卡｜利空未壓低價格｜強勢吸收"
        message = (
            f"{session_prefix}：負面新聞未能壓低價格，今日 {reality.day_pct:+.2f}% 且守在 VWAP 上方；"
            f"先尊重價格強度，不開高追價，守住 {low1:.2f} 可續強，爆量跌破 {stop:.2f} 才視為吸收失敗。"
        )
        axis = "利空吸收｜價格優先｜回測確認"
    elif price_down and news["sign"] > 0:
        state = "good_news_rejected"
        title = "AI進場決策卡｜利多未獲價格確認｜暫不追價"
        message = (
            f"{session_prefix}：新聞偏多但價格未買單，今日 {reality.day_pct:+.2f}% 且位於 VWAP 下方；"
            f"利多先降權，站回 {attack:.2f} 才恢復攻擊，{stop:.2f} 收不回維持防守。"
        )
        axis = "利多失效｜等待價格確認"
    elif price_up and (negative or hard_defense):
        state = "surge_divergence"
        missing = "、".join(negative[:3]) or "外部風險"
        strength = "漲停／極強收高" if reality.limit_like else "強勢收高"
        title = "AI進場決策卡｜急漲但證據背離｜不直接判空"
        message = (
            f"{session_prefix}：今日{strength} {reality.day_pct:+.2f}%，價格已先轉強，但{missing}尚未同步；"
            f"不因慢速證據直接判空，也不追開高，守住 {low1:.2f} 可續強，失守 {stop:.2f} 才降級。"
        )
        axis = "價格強｜證據背離｜守支撐不追高"
    elif reality.limit_like and len(positive) >= 2:
        state = "limit_breakout"
        title = "AI進場決策卡｜漲停／極強突破｜同向確認"
        message = (
            f"{session_prefix}：今日漲停／極強收高，價格與{'、'.join(positive[:3])}同向；"
            f"明日不追開高，守住 {low1:.2f} 可續強，跌破 {stop:.2f} 才視為突破失敗。"
        )
        axis = "極強突破｜回測守穩再續攻"
    elif price_up:
        state = "strong_continuation"
        title = "AI進場決策卡｜強勢續攻｜回測確認"
        message = (
            f"{session_prefix}：今日 {reality.day_pct:+.2f}% 且收在 VWAP 上方，價格動能成立；"
            f"守住 {low1:.2f} 可續抱／分批，{no_chase:.2f} 上方急拉不追，跌破 {stop:.2f} 才轉弱。"
        )
        axis = "強勢續攻｜守支撐不追高"
    elif reality.deep_stabilizing:
        state = "deep_stabilization"
        title = "AI進場決策卡｜跌深止穩｜只做確認單"
        message = (
            f"{session_prefix}：盤中雖跌 {abs(reality.day_pct):.2f}%，但收回 VWAP 且靠近日高，賣壓出現吸收；"
            f"站穩 {attack:.2f} 才試單，回落 {low1:.2f} 不破可分批，破 {stop:.2f} 停。"
        )
        axis = "跌深止穩｜確認後試單"
    elif reality.weak_rebound:
        state = "weak_rebound"
        title = "AI進場決策卡｜弱勢反彈｜尚未翻多"
        message = (
            f"{session_prefix}：今日反彈但中短趨勢與 VWAP 尚未完全收復；"
            f"先視為弱勢反彈，站穩 {attack:.2f} 才轉強，{low1:.2f} 失守則不接。"
        )
        axis = "弱勢反彈｜等待站回關鍵價"
    elif reality.trend_break:
        state = "trend_break"
        title = "AI進場決策卡｜趨勢破壞｜防守優先"
        message = (
            f"{session_prefix}：今日 {reality.day_pct:+.2f}%、收在 VWAP 下方且接近日低，價格結構已破壞；"
            f"未站回 {attack:.2f} 前不搶反彈，{low1:.2f} 附近只看止穩，破 {stop:.2f} 停。"
        )
        axis = "趨勢破壞｜先防守等止穩"
    elif hard_defense:
        state = "risk_resonance"
        title = "AI進場決策卡｜風險共振｜等待海外止穩"
        message = (
            f"{session_prefix}：地緣／海外盤勢形成負向共振，但價格尚未出現極端破壞；"
            f"未站回 {attack:.2f} 前不搶，回測 {low1:.2f} 只做止穩確認，破 {stop:.2f} 停。"
        )
        axis = "跨市場風險｜防守優先"
    elif event_caution:
        state = "event_caution"
        title = "AI進場決策卡｜事件卡｜公布後確認"
        message = (
            f"{session_prefix}：{event_name}公布前新聞只能提出假設；"
            f"站穩 {attack:.2f} 才試小單，回測 {low1:.2f} 止穩再分批，破 {stop:.2f} 停。"
        )
        axis = "一級事件前｜縮小部位｜等待確認"
    elif model["conflict"] >= 0.45:
        state = "evidence_conflict"
        title = "AI進場決策卡｜證據衝突｜等待確認"
        message = (
            f"{session_prefix}：價格、海外、新聞與籌碼尚未同向；"
            f"站穩 {attack:.2f} 才轉強，回測 {low1:.2f} 止穩才試單，破 {stop:.2f} 停。"
        )
        axis = "證據衝突｜等待價格裁決"
    elif model["sign"] > 0:
        state = "conditional_attack"
        title = "AI進場決策卡｜條件式偏多｜站穩再攻"
        message = (
            f"{session_prefix}：模型偏多但尚未形成極強價格確認；"
            f"站穩 {attack:.2f} 可攻，回測 {low1:.2f} 不破再分批，{no_chase:.2f} 上方不追。"
        )
        axis = "條件式偏多｜站穩再攻"
    elif model["sign"] < 0:
        state = "conditional_defense"
        title = "AI進場決策卡｜條件式偏弱｜等止穩"
        message = (
            f"{session_prefix}：模型偏弱且價格尚未給出反向否決；"
            f"{low1:.2f} 附近只試止穩單，{low2:.2f} 才第二批，破 {stop:.2f} 停。"
        )
        axis = "條件式偏弱｜防守低接"
    else:
        state = "range_wait"
        title = "AI進場決策卡｜盤整等待｜讓價格裁決"
        message = (
            f"{session_prefix}：目前證據沒有形成單一方向；"
            f"站穩 {attack:.2f} 才轉強，回測 {low1:.2f} 止穩才試單，破 {stop:.2f} 停。"
        )
        axis = "盤整等待｜價格確認"

    if pause_second:
        message = message.replace(f"{low2:.2f} 才第二批", "融資降溫前暫停第二批")
        axis += "｜第二批暫停"

    price_text = (
        f"價格 {reality.day_pct:+.2f}%／{'VWAP上方' if reality.above_vwap else 'VWAP下方'}／"
        f"收盤位置 {reality.close_location * 100:.0f}%"
    )
    evidence_parts = [price_text]
    if overseas["available"]:
        evidence_parts.append("海外 " + overseas["text"])
    if news["available"]:
        evidence_parts.append(news["text"])
    if positioning["available"]:
        evidence_parts.append(positioning["text"])
    evidence_parts.append(model["text"])
    evidence_line = "；".join(evidence_parts)

    if state in {"bad_news_absorbed", "surge_divergence", "limit_breakout", "strong_continuation"}:
        attack_text = f"守住 {low1:.2f} 續強"
        turn_text = f"突破 {attack:.2f} 確認延伸"
    elif state in {"good_news_rejected", "weak_rebound", "trend_break", "risk_resonance"}:
        attack_text = f"站回 {attack:.2f} 才攻"
        turn_text = f"收復 {attack:.2f} 才轉強"
    elif state == "deep_stabilization":
        attack_text = f"站穩 {attack:.2f} 試單"
        turn_text = f"突破 {attack:.2f} 確認止穩"
    elif state == "event_caution":
        attack_text = "事件前縮小試單"
        turn_text = f"公布後站穩 {attack:.2f}"
    else:
        attack_text = f"站穩 {attack:.2f} 才攻"
        turn_text = f"突破 {attack:.2f} 加碼"

    return {
        "state": state,
        "title": title,
        "message": message,
        "axis": axis,
        "evidence_line": evidence_line,
        "attack_text": attack_text,
        "turn_text": turn_text,
        "price_reality": reality.__dict__,
        "overseas": overseas,
        "news": news,
        "positioning": positioning,
        "model": model,
        "positive_groups": positive,
        "negative_groups": negative,
        "narrative_only": True,
    }
