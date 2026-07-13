# -*- coding: utf-8 -*-
"""TINO cross-market AI Bubble Radar.

This module is intentionally read-only with respect to the price, Direction,
Quantum and Forecast engines.  It converts already available price/fundamental
facts into a bounded *position-risk* temperature and a small AI Decision
adjustment.  It never changes T0/T1/High/Low.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, Mapping, Sequence

from models import NewsItem, PriceFrame


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _num(value: Any) -> float | None:
    if value in (None, "", "NA", "--", "待同步"):
        return None
    try:
        text = str(value).replace(",", "").replace("%", "").strip()
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        if not match:
            return None
        number = float(match.group(0))
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _pct_return(last: float, anchor: float | None) -> float | None:
    if anchor is None or anchor <= 0 or last <= 0:
        return None
    return (last / anchor - 1.0) * 100.0


def _price_metrics(price: PriceFrame) -> Dict[str, float | None]:
    closes = []
    for value in price.recent_closes or []:
        number = _num(value)
        if number is not None and number > 0:
            closes.append(number)
    last = _num(price.last) or (closes[-1] if closes else 0.0)
    ret20 = _pct_return(last, closes[-21] if len(closes) >= 21 else None)
    # The project keeps about sixty daily bars.  Use the oldest available bar
    # only when coverage is at least forty sessions, otherwise do not invent a
    # medium-term return.
    ret60 = _pct_return(last, closes[0] if len(closes) >= 40 else None)
    ma20_gap = None
    if len(closes) >= 20 and last > 0:
        ma20 = sum(closes[-20:]) / 20.0
        ma20_gap = _pct_return(last, ma20)
    return {"ret20": ret20, "ret60": ret60, "ma20_gap": ma20_gap}


def _news_text(news_items: Sequence[NewsItem] | None) -> str:
    rows = []
    for item in news_items or []:
        if isinstance(item, Mapping):
            rows.append(str(item.get("title") or ""))
        else:
            rows.append(str(getattr(item, "title", "") or ""))
    return " ".join(rows).lower()


def _growth_points(value: float | None, *, kind: str) -> float:
    if value is None:
        return 0.0
    if kind == "qoq":
        if value > 50:
            return 6.0
        if value > 20:
            return 4.0
        if value > 10:
            return 2.0
        return 0.0
    if kind == "eps_yoy":
        if value > 50:
            return 4.0
        if value > 30:
            return 3.0
        if value > 15:
            return 1.0
        return 0.0
    if value > 50:
        return 6.0
    if value > 30:
        return 4.0
    if value > 15:
        return 2.0
    return 0.0


def _temperature_label(score: float) -> tuple[str, str]:
    if score >= 75:
        return "泡沫警戒", "🚨"
    if score >= 60:
        return "高風險", "🔴"
    if score >= 45:
        return "過熱", "🟠"
    if score >= 25:
        return "偏熱", "🟡"
    return "健康", "🟢"


def assess_bubble_risk(
    price: PriceFrame,
    news_items: Sequence[NewsItem] | None = None,
) -> Dict[str, Any]:
    """Return a bounded, explainable bubble-risk assessment for TW and US.

    The score is a temperature, not a crash probability.  Strong verified
    growth reduces the temperature; price/valuation/expectation divergence and
    verified growth deterioration increase it.  Unverified QoQ values are
    ignored rather than guessed.
    """
    if str(getattr(price.ticker, "asset_type", "stock") or "stock") == "etf":
        return {
            "accepted": False,
            "score": 0.0,
            "temperature": 0,
            "level": "ETF成分觀察",
            "icon": "⚪",
            "decision_adjustment": 0.0,
            "quality": 0.0,
            "line": "AI泡沫雷達｜ETF不套單一公司估值；改看市場熱度與成分股",
            "reason": "ETF_ROUTE",
            "metrics": {},
        }

    context = price.context if isinstance(price.context, dict) else {}
    fundamental = context.get("fundamental") if isinstance(context.get("fundamental"), dict) else {}
    market = str(getattr(price.ticker, "market", "") or "").upper()
    metrics = _price_metrics(price)

    # Only actual sequential-quarter revenue may be called QoQ.  Legacy Yahoo
    # earningsQuarterlyGrowth is a YoY earnings field and must never enter here.
    qoq = _num(fundamental.get("qoq")) if bool(fundamental.get("qoq_verified")) else None
    revenue_yoy = _num(fundamental.get("revenue_yoy"))
    if revenue_yoy is None and bool(fundamental.get("yoy_verified", fundamental.get("accepted", False))):
        revenue_yoy = _num(fundamental.get("yoy"))
    eps_yoy = _num(fundamental.get("eps_yoy")) if bool(fundamental.get("eps_yoy_verified", True)) else None
    pe = _num(fundamental.get("pe"))

    # TW monthly revenue is not QoQ.  It is useful as a short acceleration
    # check, but receives a smaller weight and is labelled separately.
    monthly_mom = _num(fundamental.get("mom")) if market == "TW" else None
    accum_yoy = _num(fundamental.get("accum_yoy")) if market == "TW" else None
    if market == "TW" and revenue_yoy is None:
        revenue_yoy = _num(fundamental.get("yoy"))

    growth_score = _growth_points(qoq, kind="qoq")
    growth_score += _growth_points(revenue_yoy, kind="yoy")
    growth_score += _growth_points(eps_yoy, kind="eps_yoy")
    if monthly_mom is not None:
        growth_score += 2.0 if monthly_mom > 20 else (1.0 if monthly_mom > 10 else 0.0)
    if accum_yoy is not None and (revenue_yoy is None or accum_yoy > revenue_yoy + 5):
        growth_score += 1.5 if accum_yoy > 30 else (0.75 if accum_yoy > 15 else 0.0)
    if bool(fundamental.get("growth_accelerating")):
        growth_score += 2.0
    growth_score = _clamp(growth_score, 0.0, 16.0)

    deceleration = 0.0
    deceleration_reasons = []
    if qoq is not None and qoq <= -10:
        deceleration += 6.0
        deceleration_reasons.append("營收QoQ轉負")
    if revenue_yoy is not None and revenue_yoy <= -10:
        deceleration += 7.0
        deceleration_reasons.append("營收YoY衰退")
    if eps_yoy is not None and eps_yoy <= -20:
        deceleration += 7.0
        deceleration_reasons.append("獲利YoY衰退")
    if monthly_mom is not None and monthly_mom <= -15:
        deceleration += 2.0
        deceleration_reasons.append("月營收MoM轉弱")

    ret20 = metrics.get("ret20")
    ret60 = metrics.get("ret60")
    ma20_gap = metrics.get("ma20_gap")
    price_heat = 0.0
    if ret20 is not None:
        price_heat += _clamp((ret20 - 8.0) * 0.45, 0.0, 12.0)
    if ret60 is not None:
        price_heat += _clamp((ret60 - 20.0) * 0.35, 0.0, 18.0)
    if ma20_gap is not None:
        price_heat += _clamp((ma20_gap - 10.0) * 0.55, 0.0, 8.0)
    price_heat = _clamp(price_heat, 0.0, 30.0)

    valuation_heat = 0.0
    if pe is not None and pe > 0:
        if pe >= 80:
            valuation_heat = 18.0
        elif pe >= 50:
            valuation_heat = 12.0
        elif pe >= 35:
            valuation_heat = 7.0
        elif pe >= 25:
            valuation_heat = 3.0

    text = " ".join([
        str((context.get("persona") or {}).get("badge") if isinstance(context.get("persona"), dict) else ""),
        str((context.get("persona") or {}).get("label") if isinstance(context.get("persona"), dict) else ""),
        _news_text(news_items),
    ]).lower()
    ai_terms = (" ai ", "artificial intelligence", "blackwell", "rubin", "hbm", "gpu", "asic", "custom silicon", "資料中心", "記憶體", "半導體")
    ai_hits = sum(1 for term in ai_terms if term in f" {text} ")
    expectation_heat = _clamp(ai_hits * 1.6, 0.0, 10.0)

    divergence = 0.0
    divergence_reasons = []
    if price_heat >= 22 and growth_score <= 5:
        divergence += 15.0
        divergence_reasons.append("股價加速明顯領先成長")
    elif price_heat >= 16 and growth_score <= 7:
        divergence += 9.0
        divergence_reasons.append("股價領先基本面")
    if pe is not None and pe >= 50 and (revenue_yoy is None or revenue_yoy < 15):
        divergence += 8.0
        divergence_reasons.append("高估值但營收成長不足")
    if pe is not None and pe >= 80 and (revenue_yoy is None or revenue_yoy < 30):
        divergence += 7.0
        divergence_reasons.append("極高PE但營收未達高成長")
    if pe is not None and pe >= 80 and (eps_yoy is None or eps_yoy < 30):
        divergence += 6.0
        divergence_reasons.append("高PE未獲EPS加速度支撐")

    growth_support = _clamp(growth_score * 1.15, 0.0, 18.0)
    score = _clamp(
        price_heat + valuation_heat + expectation_heat + divergence + deceleration - growth_support,
        0.0,
        100.0,
    )

    available = sum(
        value is not None
        for value in (ret20, ret60, ma20_gap, qoq, revenue_yoy, eps_yoy, pe, monthly_mom, accum_yoy)
    )
    quality = _clamp(0.28 + available * 0.085, 0.28, 0.96)
    source = str(fundamental.get("source") or "")
    if any(token in source.upper() for token in ("SAMPLE", "FALLBACK", "PENDING")):
        quality = max(0.28, quality - 0.18)

    fundamental_available = any(
        value is not None
        for value in (qoq, revenue_yoy, eps_yoy, pe, monthly_mom, accum_yoy)
    )
    accepted = available >= 3 and quality >= 0.48 and fundamental_available
    if not accepted:
        score = min(score, 44.0)

    level, icon = _temperature_label(score)

    # AI Decision adjustment is deliberately small.  Growth may add at most +4;
    # bubble risk may subtract at most -6.  It changes only the decision/risk
    # wording and displayed decision score, never Direction/Quantum/Forecast.
    growth_adjustment = _clamp(growth_score * 0.32, 0.0, 4.0) if accepted else 0.0
    if score >= 75:
        bubble_penalty = 6.0
    elif score >= 60:
        bubble_penalty = 4.0
    elif score >= 45:
        bubble_penalty = 2.0
    elif score >= 25:
        bubble_penalty = 0.75
    else:
        bubble_penalty = 0.0
    decision_adjustment = _clamp(growth_adjustment - bubble_penalty, -6.0, 4.0)

    reasons = divergence_reasons + deceleration_reasons
    if not reasons:
        if growth_support >= 9:
            reasons.append("成長可支撐目前估值")
        elif price_heat >= 12:
            reasons.append("股價熱度升高")
        else:
            reasons.append("尚未出現明顯價格/基本面背離")
    if qoq is None:
        reasons.append("QoQ僅採正式季度序列；目前未取得則不計分")

    if accepted:
        parts = [
            f"AI泡沫雷達｜{icon} {score:.0f}℃ {level}",
            f"價熱 {price_heat:.0f}",
            f"估值 {valuation_heat:.0f}",
            f"預期 {expectation_heat:.0f}",
            f"成長支撐 -{growth_support:.0f}",
            f"Decision {decision_adjustment:+.1f}",
            f"資料 {quality * 100:.0f}%",
            reasons[0],
        ]
    else:
        decision_adjustment = 0.0
        parts = [
            "AI泡沫雷達｜資料不足，不做泡沫結論",
            f"價熱 {price_heat:.0f}",
            f"資料 {quality * 100:.0f}%",
            "Decision +0.0",
        ]
    return {
        "accepted": accepted,
        "score": round(score, 2),
        "temperature": int(round(score)),
        "level": level,
        "icon": icon,
        "decision_adjustment": round(decision_adjustment, 2),
        "quality": round(quality, 4),
        "growth_score": round(growth_score, 2),
        "price_heat": round(price_heat, 2),
        "valuation_heat": round(valuation_heat, 2),
        "expectation_heat": round(expectation_heat, 2),
        "divergence": round(divergence, 2),
        "deceleration": round(deceleration, 2),
        "growth_support": round(growth_support, 2),
        "line": "｜".join(parts),
        "reason": "；".join(reasons[:4]),
        "metrics": {
            "ret20": ret20,
            "ret60": ret60,
            "ma20_gap": ma20_gap,
            "qoq": qoq,
            "revenue_yoy": revenue_yoy,
            "eps_yoy": eps_yoy,
            "monthly_mom": monthly_mom,
            "accum_yoy": accum_yoy,
            "pe": pe,
        },
        "source": "TINO_BUBBLE_RADAR_V1",
    }


def bubble_radar_line(value: Mapping[str, Any] | None) -> str:
    if not isinstance(value, Mapping):
        return "AI泡沫雷達｜資料不足，不做泡沫結論"
    return str(value.get("line") or "AI泡沫雷達｜資料不足，不做泡沫結論")
