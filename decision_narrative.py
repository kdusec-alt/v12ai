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
from decision_thesis_v1072 import build_decision_thesis
from earnings_intelligence_v1072 import assess_earnings_evidence
from news_causal_intelligence_v1073 import analyze_news_causality
from price_truth_v1072 import price_truth


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
    vwap_available: bool
    breakout: bool
    limit_like: bool
    strong_up: bool
    strong_down: bool
    deep_stabilizing: bool
    weak_rebound: bool
    trend_break: bool


def price_reality(price: PriceFrame) -> PriceReality:
    truth = price_truth(price)
    last = _num(truth.get("current_price"), _num(price.last))
    previous = _num(truth.get("current_reference_close"), _num(price.previous_close, last)) or last
    vwap = _num(truth.get("vwap"), _num(price.vwap, last)) or last
    vwap_available = bool(truth.get("vwap_available"))
    atr = max(_num(price.atr14), last * 0.012, 0.01)
    high = max(_num(price.high, last), last)
    low = min(_num(price.low, last), last)
    day_range = max(high - low, atr * 0.15, 0.01)
    close_location = max(0.0, min(1.0, (last - low) / day_range))
    day_pct = (last - previous) / previous * 100.0 if previous > 0 else 0.0
    atr_move = (last - previous) / atr
    market = str(price.ticker.market or "").upper()

    history_highs = list(price.recent_highs or [])
    history_lows = list(price.recent_lows or [])
    price_meta = (price.context or {}).get("price_meta")
    price_meta = price_meta if isinstance(price_meta, Mapping) else {}
    # US active-session bars are kept outside the formal daily history, so its
    # last row is still a valid prior-session comparison. TW live quotes are
    # written into the last daily row and must be excluded from prior highs/lows.
    formal_history_only = bool(
        market == "US"
        and truth.get("live_session_quote")
        and str(price_meta.get("history_scope") or "") == "formal_daily_only"
    )
    if not formal_history_only:
        history_highs = history_highs[:-1]
        history_lows = history_lows[:-1]
    prior_highs = [_num(value) for value in history_highs if _num(value) > 0]
    prior_lows = [_num(value) for value in history_lows if _num(value) > 0]
    breakout = bool(prior_highs and last >= max(prior_highs[-20:]) - atr * 0.08)
    breakdown = bool(prior_lows and last <= min(prior_lows[-20:]) + atr * 0.08)

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
        and vwap_available
        and last >= vwap
        and close_location >= 0.68
        and (breakout or atr_move >= 1.05 or limit_like)
    )
    strong_down = bool(
        day_pct <= -strong_threshold
        and (not vwap_available or last < vwap)
        and close_location <= 0.34
        and (breakdown or atr_move <= -1.05)
    )
    deep_stabilizing = bool(day_pct <= -2.5 and close_location >= 0.68 and vwap_available and last >= vwap)

    closes = [_num(value) for value in (price.recent_closes or []) if _num(value) > 0]
    ret5 = ((closes[-1] / closes[-6]) - 1.0) * 100.0 if len(closes) >= 6 and closes[-6] else 0.0
    ret20 = ((closes[-1] / closes[-21]) - 1.0) * 100.0 if len(closes) >= 21 and closes[-21] else 0.0
    weak_rebound = bool(
        day_pct > 0.5
        and (ret5 < -3.0 or ret20 < -8.0)
        and ((vwap_available and last < vwap) or close_location < 0.62)
    )
    trend_break = bool(
        strong_down
        or (
            breakdown
            and day_pct < -1.5
            and (not vwap_available or last < vwap)
            and close_location <= 0.40
        )
    )
    return PriceReality(
        day_pct=round(day_pct, 4),
        atr_move=round(atr_move, 4),
        close_location=round(close_location, 4),
        above_vwap=bool(vwap_available and last >= vwap),
        vwap_available=vwap_available,
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


def _news_evidence(
    price: PriceFrame,
    news_items: Sequence[NewsItem | Mapping[str, Any]] | None,
) -> Dict[str, Any]:
    """Return family-deduplicated news evidence with a price-time causal gate."""
    causal = {}
    try:
        existing = (price.context or {}).get("news_causal_v1073")
        causal = (
            dict(existing)
            if isinstance(existing, Mapping)
            else analyze_news_causality(price, news_items)
        )
    except Exception:
        causal = {}
    if causal:
        company_score = _num(causal.get("company_score"))
        global_score = _num(causal.get("global_score"))
        combined = _num(causal.get("combined_score"), company_score + global_score * 0.35)
        sign = 1 if combined >= 0.06 else -1 if combined <= -0.06 else 0
        top_title = _clip_text(causal.get("dominant_headline"), 30)
        return {
            "sign": sign,
            "company_sign": int(causal.get("company_sign") or 0),
            "global_sign": int(causal.get("global_sign") or 0),
            "score": combined,
            "company_score": company_score,
            "global_score": global_score,
            "text": (
                f"新聞偏多《{top_title}》"
                if sign > 0 and top_title
                else f"新聞偏空《{top_title}》"
                if sign < 0 and top_title
                else f"新聞待價格確認《{top_title}》"
                if top_title
                else "新聞無明確方向"
            ),
            "top_title": top_title,
            "company_text": str(causal.get("company_text") or "公司新聞無明確方向"),
            "global_text": str(causal.get("global_text") or "宏觀事件無明確方向"),
            "company_available": bool(causal.get("company_family_count")),
            "global_available": bool(causal.get("global_family_count")),
            "earnings": dict(causal.get("earnings") or {}),
            "causal": causal,
            "available": bool(causal.get("selected_count")),
        }

    # Fail-safe for malformed legacy objects: preserve the bounded V1072
    # sentiment path, but do not fabricate causal timing metadata.
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
    company_sign = 1 if company_score >= 0.06 else -1 if company_score <= -0.06 else 0
    global_sign = 1 if global_score >= 0.06 else -1 if global_score <= -0.06 else 0
    earnings = assess_earnings_evidence(company)
    if earnings.get("accepted") and earnings.get("forward_priority"):
        company_sign = int(earnings.get("sign") or 0)
    combined = company_score + global_score * 0.45
    sign = 1 if combined >= 0.06 else -1 if combined <= -0.06 else 0
    ranked = sorted(company + global_rows, key=lambda item: abs(_score(item)), reverse=True)
    top = ranked[0] if ranked else None
    top_title = _clip_text(_title(top), 30) if top is not None else ""
    top_company = max(company, key=lambda item: abs(_score(item)), default=None)
    top_global = max(global_rows, key=lambda item: abs(_score(item)), default=None)
    company_title = _clip_text(_title(top_company), 30) if top_company is not None else ""
    global_title = _clip_text(_title(top_global), 30) if top_global is not None else ""
    company_text = (
        str(earnings.get("text"))
        if earnings.get("accepted") and earnings.get("forward_priority")
        else f"公司新聞偏多《{company_title}》"
        if company_sign > 0 and company_title
        else f"公司新聞偏空《{company_title}》"
        if company_sign < 0 and company_title
        else f"公司新聞待價格確認《{company_title}》"
        if company_title
        else "公司新聞無明確方向"
    )
    global_text = (
        f"宏觀事件偏多《{global_title}》"
        if global_sign > 0 and global_title
        else f"宏觀事件偏空《{global_title}》"
        if global_sign < 0 and global_title
        else f"宏觀事件待價格確認《{global_title}》"
        if global_title
        else "宏觀事件無明確方向"
    )
    if sign > 0:
        text = f"新聞偏多《{top_title}》" if top_title else "新聞偏多"
    elif sign < 0:
        text = f"新聞偏空《{top_title}》" if top_title else "新聞偏空"
    else:
        text = f"新聞待價格確認《{top_title}》" if top_title else "新聞無明確方向"
    return {
        "sign": sign,
        "company_sign": company_sign,
        "global_sign": global_sign,
        "score": combined,
        "company_score": company_score,
        "global_score": global_score,
        "text": text,
        "top_title": top_title,
        "company_text": company_text,
        "global_text": global_text,
        "company_available": bool(company),
        "global_available": bool(global_rows),
        "earnings": earnings,
        "causal": {},
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
    news = _news_evidence(price, news_items)
    positioning = _positioning_evidence(price, direction)
    model = _model_evidence(direction)

    # V1072: one structured thesis owns the visible decision.  It separates
    # company news from macro events, respects session VWAP scope and can gate
    # entry after a severe official forecast miss.
    trust = (price.context or {}).get("prediction_trust_v1072")
    return build_decision_thesis(
        price,
        direction,
        reality.__dict__,
        overseas,
        news,
        positioning,
        model,
        session_prefix=session_prefix,
        low1=low1,
        low2=low2,
        attack=attack,
        stop=stop,
        no_chase=no_chase,
        hard_defense=hard_defense,
        event_caution=event_caution,
        event_name=event_name,
        pause_second=pause_second,
        prediction_trust=trust if isinstance(trust, Mapping) else {},
    )
