# -*- coding: utf-8 -*-
"""TINO universal AI Bubble Radar for TW/US stocks, ADRs and ETFs.

The radar is an explainable *temperature*, not a crash probability.  It reads
facts already present in ``PriceFrame`` and never fetches data, builds a
DataFrame, or changes Direction/Quantum/Forecast/T0/T1/High/Low.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Mapping, Sequence

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
    ret60 = _pct_return(last, closes[0] if len(closes) >= 40 else None)
    ma20_gap = None
    if len(closes) >= 20 and last > 0:
        ma20 = sum(closes[-20:]) / 20.0
        ma20_gap = _pct_return(last, ma20)
    return {
        "ret20": ret20,
        "ret60": ret60,
        "ma20_gap": ma20_gap,
        "history_count": float(len(closes)),
    }


def _price_heat(metrics: Mapping[str, Any]) -> float:
    ret20 = _num(metrics.get("ret20"))
    ret60 = _num(metrics.get("ret60"))
    ma20_gap = _num(metrics.get("ma20_gap"))
    score = 0.0
    if ret20 is not None:
        score += _clamp((ret20 - 8.0) * 0.45, 0.0, 12.0)
    if ret60 is not None:
        score += _clamp((ret60 - 20.0) * 0.35, 0.0, 18.0)
    if ma20_gap is not None:
        score += _clamp((ma20_gap - 10.0) * 0.55, 0.0, 8.0)
    return _clamp(score, 0.0, 30.0)


def _news_text(news_items: Sequence[NewsItem] | None) -> str:
    rows = []
    for item in news_items or []:
        if isinstance(item, Mapping):
            rows.append(str(item.get("title") or ""))
        else:
            rows.append(str(getattr(item, "title", "") or ""))
    return " ".join(rows).lower()


def _expectation_heat(price: PriceFrame, news_items: Sequence[NewsItem] | None) -> float:
    context = price.context if isinstance(price.context, dict) else {}
    persona = context.get("persona") if isinstance(context.get("persona"), dict) else {}
    text = " ".join(
        (
            str(persona.get("badge") or ""),
            str(persona.get("label") or ""),
            _news_text(news_items),
        )
    ).lower()
    terms = (
        " ai ", "artificial intelligence", "blackwell", "rubin", "hbm",
        "gpu", "asic", "custom silicon", "data center", "資料中心",
        "記憶體", "半導體", "ai伺服器", "cpo", "cowoS",
    )
    hits = sum(1 for term in terms if term.lower() in f" {text} ")
    return _clamp(hits * 1.5, 0.0, 10.0)


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
    if kind == "earnings":
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


def _valuation_heat(pe: float | None, forward_pe: float | None, ps: float | None) -> float:
    pe_heat = 0.0
    if pe is not None and pe > 0:
        if pe >= 100:
            pe_heat = 22.0
        elif pe >= 80:
            pe_heat = 18.0
        elif pe >= 50:
            pe_heat = 12.0
        elif pe >= 35:
            pe_heat = 7.0
        elif pe >= 25:
            pe_heat = 3.0
    fpe_heat = 0.0
    if forward_pe is not None and forward_pe > 0:
        if forward_pe >= 70:
            fpe_heat = 14.0
        elif forward_pe >= 45:
            fpe_heat = 9.0
        elif forward_pe >= 30:
            fpe_heat = 5.0
    ps_heat = 0.0
    if ps is not None and ps > 0:
        if ps >= 30:
            ps_heat = 18.0
        elif ps >= 20:
            ps_heat = 13.0
        elif ps >= 12:
            ps_heat = 8.0
        elif ps >= 8:
            ps_heat = 4.0
    # Metrics are alternative valuation views; use the strongest one rather
    # than triple-counting the same overvaluation.
    return _clamp(max(pe_heat, fpe_heat, ps_heat), 0.0, 22.0)


def _decision_adjustment(score: float, growth_score: float, *, mode: str, accepted: bool) -> float:
    """Compatibility field retained for old Prediction Log readers.

    V13 architecture contract: Bubble Engine is research-only and therefore
    always contributes exactly zero to the formal AI Decision.
    """
    return 0.0


def _extreme_growth_guard(
    market: str,
    fundamental: Mapping[str, Any],
    qoq: float | None,
    revenue_yoy: float | None,
) -> tuple[float, bool, bool, str]:
    """Scale only unverified extreme growth; never discard verified facts.

    A very large percentage can be economically real, but it can also come
    from a low base, a spin-off, or a mismatched accounting period.  The guard
    therefore gives full credit only when the existing source has already
    proved a continuous quarterly/monthly series.  No network request is made.
    """
    extreme_qoq = qoq is not None and qoq > 80.0
    extreme_yoy = revenue_yoy is not None and revenue_yoy > 200.0
    if not (extreme_qoq or extreme_yoy):
        return 1.0, False, True, ""

    market = str(market or "").upper()
    if market == "TW":
        verified = bool(
            fundamental.get("growth_metrics_eligible")
            and not fundamental.get("revenue_month_anchor_risk")
        )
        basis = "月營收連續序列"
    else:
        source = str(fundamental.get("revenue_growth_source") or "").lower()
        qoq_ok = (not extreme_qoq) or bool(
            fundamental.get("qoq_verified") and source == "quarterly_series"
        )
        yoy_ok = (not extreme_yoy) or bool(
            fundamental.get("yoy_verified") and source == "quarterly_series"
        )
        verified = bool(qoq_ok and yoy_ok)
        basis = "季度連續序列"

    if verified:
        return 1.0, True, True, f"極端成長已通過{basis}檢查"
    return 0.5, True, False, "極端成長待口徑驗證，成長支撐減半"


def _valuation_temperature_floor(
    pe: float | None,
    ps: float | None,
    eps_value: float | None,
    valuation_heat: float,
) -> tuple[float, str]:
    """Prevent strong growth from washing expensive valuation down to 0°C."""
    floor = 0.0
    reason = ""
    if ps is not None and ps >= 15.0:
        floor = 25.0
        reason = "高PS估值設定最低溫度"
    if pe is not None and pe >= 60.0 and ps is not None and ps >= 15.0:
        floor = max(floor, 30.0)
        reason = "高PE與高PS並存"
    if eps_value is not None and eps_value < 0.0 and ps is not None and ps >= 15.0:
        floor = max(floor, 30.0)
        reason = "負EPS且PS偏高"
    if valuation_heat >= 18.0:
        floor = max(floor, 30.0)
        reason = reason or "極高估值設定最低溫度"
    elif valuation_heat >= 12.0:
        floor = max(floor, 25.0)
        reason = reason or "高估值設定最低溫度"
    return floor, reason


def _cap_positive_decision_for_valuation(
    adjustment: float,
    *,
    valuation_available: bool,
    valuation_heat: float,
    pe: float | None,
    ps: float | None,
    eps_value: float | None,
) -> float:
    """Keep a risk overlay conservative without touching Direction/Forecast."""
    value = float(adjustment)
    if value <= 0.0:
        return value
    if not valuation_available:
        value = min(value, 1.5)
    if valuation_heat >= 12.0 or (ps is not None and ps >= 15.0):
        value = min(value, 1.5)
    if (ps is not None and ps >= 20.0) or (
        pe is not None and pe >= 80.0 and ps is not None and ps >= 15.0
    ):
        value = min(value, 0.5)
    if eps_value is not None and eps_value < 0.0 and ps is not None and ps >= 15.0:
        value = min(value, 0.0)
    return value


def _assess_etf(
    price: PriceFrame,
    metrics: Dict[str, float | None],
    news_items: Sequence[NewsItem] | None,
) -> Dict[str, Any]:
    price_heat = _price_heat(metrics)
    expectation = _expectation_heat(price, news_items)
    context = price.context if isinstance(price.context, dict) else {}
    macro = context.get("macro") if isinstance(context.get("macro"), dict) else {}
    macro_risk = _num(macro.get("event_risk", macro.get("risk"))) or 0.0
    macro_heat = _clamp(macro_risk * 0.35, 0.0, 8.0)
    score = _clamp(price_heat + expectation * 0.65 + macro_heat, 0.0, 85.0)
    price_count = sum(metrics.get(key) is not None for key in ("ret20", "ret60", "ma20_gap"))
    quality = _clamp(0.30 + price_count * 0.16, 0.30, 0.78)
    accepted = price_count >= 1 and quality >= 0.45
    if not accepted:
        score = min(score, 44.0)
    level, icon = _temperature_label(score)
    adjustment = _decision_adjustment(score, 0.0, mode="etf", accepted=accepted)
    reason = "ETF不套單一公司PE/EPS；僅看價格、事件與市場風險"
    line = "｜".join(
        (
            f"AI泡沫雷達｜{icon} {score:.0f}℃ ETF{level if accepted else '資料待補'}",
            f"價熱 {price_heat:.0f}",
            f"預期 {expectation:.0f}",
            "研究模式，不介入決策",
            f"資料 {quality * 100:.0f}%",
            reason,
        )
    )
    return {
        "accepted": accepted,
        "mode": "etf",
        "score": round(score, 2),
        "temperature": int(round(score)),
        "level": f"ETF{level}" if accepted else "ETF資料待補",
        "icon": icon,
        "decision_adjustment": round(adjustment, 2),
        "research_only": True,
        "decision_influence": False,
        "quality": round(quality, 4),
        "line": line,
        "reason": reason,
        "alert": bool(accepted and score >= 60.0),
        "alert_level": (
            "critical" if accepted and score >= 75.0
            else "high" if accepted and score >= 60.0
            else "none"
        ),
        "bubble_conclusion_eligible": bool(accepted),
        "metrics": {**metrics, "price_heat": price_heat, "expectation_heat": expectation, "macro_heat": macro_heat},
    }


def assess_bubble_risk(
    price: PriceFrame,
    news_items: Sequence[NewsItem] | None = None,
) -> Dict[str, Any]:
    """Return a universal, bounded bubble-temperature assessment."""
    asset_type = str(getattr(price.ticker, "asset_type", "stock") or "stock").lower()
    metrics = _price_metrics(price)
    if asset_type == "etf":
        return _assess_etf(price, metrics, news_items)

    context = price.context if isinstance(price.context, dict) else {}
    fundamental = context.get("fundamental") if isinstance(context.get("fundamental"), dict) else {}
    market = str(getattr(price.ticker, "market", "") or "").upper()

    qoq = _num(fundamental.get("qoq")) if bool(fundamental.get("qoq_verified")) else None
    revenue_yoy = (
        _num(fundamental.get("revenue_yoy", fundamental.get("yoy")))
        if bool(fundamental.get("yoy_verified")) else None
    )
    earnings_yoy = (
        _num(fundamental.get("earnings_yoy_for_decision"))
        if bool(fundamental.get("eps_yoy_decision_eligible")) else None
    )
    gaap_eps_yoy = _num(fundamental.get("gaap_eps_yoy"))

    tw_growth_eligible = bool(
        fundamental.get(
            "growth_metrics_eligible",
            fundamental.get("revenue_model_usable", fundamental.get("cross_checked", False)),
        )
    )
    monthly_mom = (
        _num(fundamental.get("monthly_mom", fundamental.get("mom")))
        if market == "TW" and tw_growth_eligible and bool(fundamental.get("mom_verified", True))
        else None
    )
    accum_yoy = (
        _num(fundamental.get("accum_yoy"))
        if market == "TW" and tw_growth_eligible and bool(fundamental.get("accum_yoy_verified", True))
        else None
    )
    if market == "TW" and not tw_growth_eligible:
        revenue_yoy = None

    pe = _num(fundamental.get("pe"))
    forward_pe = _num(fundamental.get("forward_pe"))
    ps = _num(fundamental.get("ps"))
    eps_value = _num(fundamental.get("adjusted_eps"))
    if eps_value is None:
        eps_value = _num(fundamental.get("gaap_eps"))
    if eps_value is None:
        eps_value = _num(fundamental.get("eps"))

    raw_growth_score = _growth_points(qoq, kind="qoq")
    raw_growth_score += _growth_points(revenue_yoy, kind="yoy")
    raw_growth_score += _growth_points(earnings_yoy, kind="earnings")
    if monthly_mom is not None:
        raw_growth_score += 2.0 if monthly_mom > 20 else (1.0 if monthly_mom > 10 else 0.0)
    if accum_yoy is not None and (revenue_yoy is None or accum_yoy > revenue_yoy + 5):
        raw_growth_score += 1.5 if accum_yoy > 30 else (0.75 if accum_yoy > 15 else 0.0)
    if bool(fundamental.get("growth_accelerating")):
        raw_growth_score += 2.0

    extreme_scale, extreme_growth, extreme_verified, extreme_reason = _extreme_growth_guard(
        market, fundamental, qoq, revenue_yoy
    )
    growth_score = _clamp(raw_growth_score * extreme_scale, 0.0, 16.0)

    deceleration = 0.0
    decel_reasons = []
    if qoq is not None and qoq <= -10:
        deceleration += 6.0
        decel_reasons.append("營收QoQ轉負")
    if revenue_yoy is not None and revenue_yoy <= -10:
        deceleration += 7.0
        decel_reasons.append("營收YoY衰退")
    if earnings_yoy is not None and earnings_yoy <= -20:
        deceleration += 7.0
        decel_reasons.append("可比獲利YoY衰退")
    if monthly_mom is not None and monthly_mom <= -15:
        deceleration += 2.0
        decel_reasons.append("月營收MoM轉弱")

    price_heat = _price_heat(metrics)
    valuation_heat = _valuation_heat(pe, forward_pe, ps)
    expectation_heat = _expectation_heat(price, news_items)

    divergence = 0.0
    divergence_reasons = []
    if price_heat >= 22 and growth_score <= 5:
        divergence += 15.0
        divergence_reasons.append("股價加速明顯領先成長")
    elif price_heat >= 16 and growth_score <= 7:
        divergence += 9.0
        divergence_reasons.append("股價領先基本面")
    if valuation_heat >= 12 and (revenue_yoy is None or revenue_yoy < 15):
        divergence += 8.0
        divergence_reasons.append("高估值但營收成長不足")
    if valuation_heat >= 18 and (revenue_yoy is None or revenue_yoy < 30):
        divergence += 7.0
        divergence_reasons.append("極高估值未獲高成長支撐")
    if valuation_heat >= 18 and earnings_yoy is not None and earnings_yoy < 30:
        divergence += 5.0
        divergence_reasons.append("極高估值未獲可比EPS加速度支撐")

    growth_support = _clamp(growth_score * 1.25, 0.0, 20.0)
    raw_score = _clamp(
        price_heat + valuation_heat + expectation_heat + divergence + deceleration - growth_support,
        0.0,
        100.0,
    )

    price_count = sum(metrics.get(key) is not None for key in ("ret20", "ret60", "ma20_gap"))
    fundamental_values = (qoq, revenue_yoy, earnings_yoy, pe, forward_pe, ps, monthly_mom, accum_yoy)
    fundamental_count = sum(value is not None for value in fundamental_values)
    growth_count = sum(value is not None for value in (qoq, revenue_yoy, earnings_yoy, monthly_mom, accum_yoy))
    quality = _clamp(0.24 + price_count * 0.09 + fundamental_count * 0.075, 0.24, 0.96)
    source = str(fundamental.get("source") or "")
    if any(token in source.upper() for token in ("SAMPLE", "FALLBACK", "PENDING", "MEMORY")):
        quality = max(0.24, quality - 0.15)

    accepted = bool(price_count >= 1 and fundamental_count >= 2 and growth_count >= 1 and quality >= 0.50)
    mode = "company" if accepted else "price_only"
    valuation_available = any(value is not None and value > 0.0 for value in (pe, forward_pe, ps))
    valuation_floor, valuation_floor_reason = _valuation_temperature_floor(
        pe, ps, eps_value, valuation_heat
    )
    if accepted:
        score = max(raw_score, valuation_floor) if valuation_available else raw_score
    else:
        score = _clamp(price_heat + expectation_heat * 0.45, 0.0, 44.0)

    level, icon = _temperature_label(score)
    adjustment = _decision_adjustment(score, growth_score, mode=mode, accepted=accepted)
    adjustment = _cap_positive_decision_for_valuation(
        adjustment,
        valuation_available=valuation_available,
        valuation_heat=valuation_heat,
        pe=pe,
        ps=ps,
        eps_value=eps_value,
    )

    reasons = divergence_reasons + decel_reasons
    if valuation_floor_reason and valuation_floor > raw_score:
        reasons.append(valuation_floor_reason)
    if extreme_reason:
        reasons.append(extreme_reason)
    if gaap_eps_yoy is not None and earnings_yoy is None:
        reasons.append("GAAP EPS波動僅揭露，不納入Decision")
    if not reasons:
        if accepted and growth_support >= 9:
            reasons.append("成長可支撐目前估值")
        elif price_heat >= 12:
            reasons.append("股價熱度升高")
        else:
            reasons.append("尚未出現明顯價格/基本面背離")

    bubble_conclusion_eligible = bool(accepted and valuation_available)
    alert = bool(bubble_conclusion_eligible and score >= 60.0)
    alert_level = "critical" if alert and score >= 75.0 else ("high" if alert else "none")

    if accepted and valuation_available:
        line = "｜".join(
            (
                f"AI泡沫雷達｜{icon} {score:.0f}℃ {level}",
                f"價熱 {price_heat:.0f}",
                f"估值 {valuation_heat:.0f}",
                f"預期 {expectation_heat:.0f}",
                f"成長支撐 -{growth_support:.0f}",
                "研究模式，不介入決策",
                f"資料 {quality * 100:.0f}%",
                reasons[0],
            )
        )
    elif accepted:
        icon = "⚪"
        level = "估值待確認"
        line = "｜".join(
            (
                f"AI泡沫雷達｜{icon} {score:.0f}℃ 成長/價格觀察",
                f"價熱 {price_heat:.0f}",
                "估值 NA",
                f"預期 {expectation_heat:.0f}",
                f"成長支撐 -{growth_support:.0f}",
                "研究模式，不介入決策",
                f"資料 {quality * 100:.0f}%",
                "估值待確認，不做完整泡沫結論",
            )
        )
    else:
        icon = "⚪"
        level = "資料待補"
        adjustment = 0.0
        line = "｜".join(
            (
                f"AI泡沫雷達｜{icon} {score:.0f}℃ 價格熱度觀察",
                f"價熱 {price_heat:.0f}",
                "研究模式，不介入決策",
                f"資料 {quality * 100:.0f}%",
                "基本面不足，不做泡沫結論",
            )
        )

    return {
        "accepted": accepted,
        "mode": mode,
        "score": round(score, 2),
        "temperature": int(round(score)),
        "level": level,
        "icon": icon,
        "decision_adjustment": round(adjustment, 2),
        "research_only": True,
        "decision_influence": False,
        "quality": round(quality, 4),
        "line": line,
        "reason": "；".join(reasons[:3]),
        "alert": alert,
        "alert_level": alert_level,
        "bubble_conclusion_eligible": bubble_conclusion_eligible,
        "metrics": {
            **metrics,
            "price_heat": round(price_heat, 3),
            "valuation_heat": round(valuation_heat, 3),
            "expectation_heat": round(expectation_heat, 3),
            "growth_score": round(growth_score, 3),
            "raw_growth_score": round(raw_growth_score, 3),
            "growth_support": round(growth_support, 3),
            "divergence": round(divergence, 3),
            "deceleration": round(deceleration, 3),
            "qoq": qoq,
            "revenue_yoy": revenue_yoy,
            "earnings_yoy_for_decision": earnings_yoy,
            "gaap_eps_yoy_disclosure": gaap_eps_yoy,
            "pe": pe,
            "forward_pe": forward_pe,
            "ps": ps,
            "eps_value": eps_value,
            "valuation_available": valuation_available,
            "valuation_floor": round(valuation_floor, 3),
            "extreme_growth": extreme_growth,
            "extreme_growth_verified": extreme_verified,
            "extreme_growth_scale": extreme_scale,
            "monthly_mom": monthly_mom,
            "accum_yoy": accum_yoy,
        },
    }


def bubble_radar_line(value: Mapping[str, Any] | None) -> str:
    if not isinstance(value, Mapping):
        return "AI泡沫雷達｜⚪ 0℃ 資料待補｜研究模式，不介入決策"
    return str(value.get("line") or "AI泡沫雷達｜⚪ 0℃ 資料待補｜研究模式，不介入決策")
