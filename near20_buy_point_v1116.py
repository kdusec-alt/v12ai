# -*- coding: utf-8 -*-
"""Evidence-aware 20-session historical buy-zone and 3-session touch estimate.

This is a secondary observation model. It never changes the formal entry,
forecast, confidence, or action. Price-path probability is derived from the
last 20 completed daily bars; fresh accepted non-price evidence may apply a
small, explicit rule-based tilt. The tilt is deliberately labelled
uncalibrated until rolling outcome audits are available.
"""
from __future__ import annotations

from datetime import date
import math
import statistics
from typing import Any, Iterable, Mapping


SCHEMA = "TINO_NEAR20_BUY_POINT_V1116"
LOOKBACK_SESSIONS = 20
FORECAST_SESSIONS = 3
MAX_EVIDENCE_TILT = 0.12
_ACTIVE_SESSIONS = {"intraday", "pre_market", "after_hours", "close_confirm"}


def _num(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _field(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _business_age(then: date, now: date) -> int | None:
    if then > now:
        return None
    return sum(
        1 for ordinal in range(then.toordinal() + 1, now.toordinal() + 1)
        if date.fromordinal(ordinal).weekday() < 5
    )


def _as_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _blocked(reason: str, *, coverage: int = 0) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "unavailable",
        "reason": reason,
        "lookback_sessions": LOOKBACK_SESSIONS,
        "forecast_sessions": FORECAST_SESSIONS,
        "data_coverage": int(coverage),
        "touch_probability_pct": None,
        "base_probability_pct": None,
        "evidence_adjustment_pct": 0.0,
        "zone": {"lower": None, "upper": None},
        "distance_pct": None,
        "drivers": [],
        "probability_method": "not_estimated",
        "calibration_status": "unavailable",
        "formal_execution": False,
    }


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _first_passage_probability(distance: float, toward_drift: float, sigma: float) -> float:
    """Brownian first-passage estimate for a log-price barrier over 3 sessions."""
    if distance <= 0:
        return 1.0
    if sigma <= 1e-9:
        return 0.0
    horizon = float(FORECAST_SESSIONS)
    scale = sigma * math.sqrt(horizon)
    exponent = max(-50.0, min(50.0, 2.0 * toward_drift * distance / (sigma * sigma)))
    probability = (
        _normal_cdf((toward_drift * horizon - distance) / scale)
        + math.exp(exponent)
        * _normal_cdf((-toward_drift * horizon - distance) / scale)
    )
    return max(0.0, min(1.0, probability))


def _trailing_atr(highs: list[float], lows: list[float], closes: list[float]) -> float | None:
    if len(closes) < 15 or len(highs) < 15 or len(lows) < 15:
        return None
    ranges = []
    for index in range(max(1, len(closes) - 14), len(closes)):
        ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))
    return statistics.mean(ranges) if ranges else None


def _company_event_fact(forecast: Any, market: str, code: str, reference: date) -> dict[str, Any] | None:
    """Use only ticker-routed, dated company events inside the recent 3-session window."""
    scores: list[float] = []
    for item in list(getattr(forecast, "news_items", []) or []):
        tag = str(_field(item, "tag", "") or "").lower()
        if f"{market.lower()}_company_{code.lower()}" not in tag:
            continue
        published = _as_date(_field(item, "time", ""))
        age = _business_age(published, reference) if published else None
        score = _num(_field(item, "score"))
        if age is None or age > 2 or score is None:
            continue
        scores.append(score)
    # The Company News lane must itself have adjudicated the item; a routed
    # tag alone is not enough to turn an unreviewed feed mention into a driver.
    company_line = str((_field(getattr(forecast, "radar", {}), "Company News", "") or ""))
    if not scores or not company_line or any(token in company_line for token in (
        "未取得直接公司催化劑", "個股實體關聯新聞觀察中", "Global Event Core",
        "等待市場傳導確認", "事件時間或價格時間未完整標示",
    )):
        return None
    total = sum(scores)
    return {
        "family": "event", "correlation_group": "verified_company_event",
        "label": "近3日公司事件", "direction": 1 if total > 0 else -1 if total < 0 else 0,
        "strength": min(100.0, 50.0 + abs(total) * 12.0), "confidence": 65.0,
        "accepted": True, "as_of": reference.isoformat(),
    }


def _fresh_independent_factors(
    forecast: Any, facts: Iterable[Any], reference: date,
) -> list[dict[str, Any]]:
    allowed_families = {"chip", "leverage", "market"}
    selected: dict[str, dict[str, Any]] = {}
    for raw in facts or ():
        family = str(_field(raw, "family", "") or "").lower()
        if family not in allowed_families or not bool(_field(raw, "accepted", False)):
            continue
        as_of = _as_date(_field(raw, "as_of", ""))
        age = _business_age(as_of, reference) if as_of else None
        if age is None or age > 2:
            continue
        group = str(_field(raw, "correlation_group", "") or family)
        direction = int(_num(_field(raw, "direction", 0)) or 0)
        strength = _num(_field(raw, "strength", 0)) or 0.0
        confidence = _num(_field(raw, "confidence", 0)) or 0.0
        candidate = {
            "family": family, "correlation_group": group,
            "label": str(_field(raw, "label", family) or family),
            "direction": 1 if direction > 0 else -1 if direction < 0 else 0,
            "strength": max(0.0, min(100.0, strength)),
            "confidence": max(0.0, min(100.0, confidence)),
            "accepted": True, "as_of": as_of.isoformat(),
        }
        old = selected.get(group)
        if old is None or candidate["strength"] * candidate["confidence"] > old["strength"] * old["confidence"]:
            selected[group] = candidate

    ticker = getattr(forecast, "ticker", None)
    market = str(getattr(ticker, "market", "") or "").upper()
    code = str(getattr(ticker, "resolved_symbol", "") or "").split(".", 1)[0]
    event = _company_event_fact(forecast, market, code, reference)
    if event:
        selected[event["correlation_group"]] = event
    return list(selected.values())


def _evidence_tilt(
    factors: list[dict[str, Any]], *, direction_to_zone: int,
) -> tuple[float, list[str]]:
    """Bounded directional evidence adjustment; no text parsing or proxies."""
    if len(factors) < 2:
        labels = [str(row["label"]) for row in factors]
        return 0.0, labels
    votes = []
    explanations = []
    for row in factors:
        direction = int(row["direction"])
        strength = float(row["strength"]) / 100.0
        confidence = float(row["confidence"]) / 100.0
        vote = direction * strength * confidence * direction_to_zone
        if direction:
            votes.append(vote)
            if vote > 0:
                explanations.append(f"{row['label']}支持觸及")
            else:
                explanations.append(f"{row['label']}降低觸及條件")
    if len(votes) < 2:
        return 0.0, explanations or [str(row["label"]) for row in factors]
    consensus = sum(votes) / len(votes)
    return max(-MAX_EVIDENCE_TILT, min(MAX_EVIDENCE_TILT, consensus * MAX_EVIDENCE_TILT)), explanations


def _limit_pct(forecast: Any) -> float | None:
    ticker = getattr(forecast, "ticker", None)
    if str(getattr(ticker, "market", "")).upper() != "TW":
        return None
    price = getattr(forecast, "price_frame", None)
    context = _field(price, "context", {}) or {}
    rule = context.get("exchange_rule", {}) if isinstance(context, Mapping) else {}
    card = getattr(forecast, "decision_card", {}) or {}
    if isinstance(card, Mapping):
        rule = card.get("_exchange_rule") or _field(card.get("_price_meta", {}), "exchange_rule", None) or rule
    if isinstance(rule, Mapping):
        if rule.get("fixed_daily_limit") is False:
            return None
        pct = _num(rule.get("price_limit_pct"))
        if pct is not None:
            return pct
    try:
        from exchange_rule_engine_v1069 import static_price_limit_pct
        return _num(static_price_limit_pct(ticker))
    except Exception:
        return None


def _historical_best_zone(
    lows: list[float], highs: list[float], closes: list[float], volumes: list[float], atr: float,
    market: str, forecast: Any,
) -> dict[str, Any] | None:
    count = len(closes)
    candidates: list[dict[str, Any]] = []
    for index in range(2, count - 3):
        pivot = lows[index]
        if pivot <= 0:
            continue
        if not (pivot <= min(lows[index - 2:index]) and pivot <= min(lows[index + 1:index + 3])):
            continue
        future_high = max(highs[index + 1:index + 4])
        future_low = min(lows[index + 1:index + 4])
        bounce = max(0.0, future_high / pivot - 1.0)
        drawdown = max(0.0, 1.0 - future_low / pivot)
        atr_pct = max(atr / pivot, 1e-5)
        if bounce < max(0.01, atr_pct * 0.55) or drawdown > atr_pct * 1.15:
            continue
        prior_volume = [v for v in volumes[max(0, index - 5):index] if v > 0]
        volume_ratio = volumes[index] / statistics.median(prior_volume) if prior_volume and volumes[index] > 0 else None
        retests = sum(1 for low in lows if abs(low - pivot) <= max(atr * 0.28, pivot * 0.003))
        score = (bounce / atr_pct) - 0.7 * (drawdown / atr_pct)
        if volume_ratio is not None:
            score += 0.12 if volume_ratio <= 1.15 else -0.20 if volume_ratio >= 1.8 else 0.0
        score += min(max(retests - 1, 0), 3) * 0.08
        candidates.append({
            "pivot": pivot, "score": score, "bounce_pct": bounce * 100.0,
            "drawdown_pct": drawdown * 100.0, "volume_ratio": volume_ratio,
            "retests": retests, "index": index,
        })
    if not candidates:
        return None
    chosen = max(candidates, key=lambda item: (item["score"], item["bounce_pct"], -item["pivot"]))
    radius = max(atr * 0.22, chosen["pivot"] * 0.0025)
    lower = max(min(lows), chosen["pivot"] - radius)
    upper = chosen["pivot"] + radius
    if market == "TW":
        try:
            from exchange_rule_engine_v1069 import classify_static_rule, tw_tick
            ticker = getattr(forecast, "ticker", None)
            rule = classify_static_rule(
                market=market, exchange=getattr(ticker, "exchange", ""),
                asset_type=getattr(ticker, "asset_type", "stock"),
                symbol=getattr(ticker, "resolved_symbol", ""), name=getattr(ticker, "name", ""),
            )
            family = str(rule.get("product_family") or "COMMON_STOCK")
            tick_lo, tick_hi = tw_tick(lower, family), tw_tick(upper, family)
            lower = math.floor((lower + 1e-10) / tick_lo) * tick_lo
            upper = math.ceil((upper - 1e-10) / tick_hi) * tick_hi
        except Exception:
            pass
    return {**chosen, "lower": round(lower, 4), "upper": round(upper, 4), "candidates": len(candidates)}


def forecast_near20_buy_point(forecast: Any, facts: Iterable[Any] = ()) -> dict[str, Any]:
    """Return a transparent historical zone and its estimated 3-session touch chance."""
    frame = getattr(forecast, "price_frame", None)
    ticker = getattr(forecast, "ticker", None)
    truth = getattr(frame, "truth", None)
    if frame is None or truth is None or not bool(getattr(truth, "accepted", False)):
        return _blocked("價格資料未通過正式驗證")
    if bool(getattr(truth, "fallback", False)):
        return _blocked("目前使用備援／樣本價格，不以此估算近20日買點")

    context = getattr(frame, "context", {}) or {}
    price_meta = context.get("price_meta", {}) if isinstance(context, Mapping) else {}
    if not isinstance(price_meta, Mapping) or price_meta.get("price_verified") is not True:
        return _blocked("價格來源未標記為正式驗證，不輸出回測機率")
    if isinstance(price_meta, Mapping):
        if price_meta.get("price_verified") is False or price_meta.get("decision_blocked") is True:
            return _blocked("目前價格尚未驗證，不輸出回測機率")

    closes = [_num(value) for value in (getattr(frame, "recent_closes", []) or [])]
    highs = [_num(value) for value in (getattr(frame, "recent_highs", []) or [])]
    lows = [_num(value) for value in (getattr(frame, "recent_lows", []) or [])]
    volumes = [_num(value) for value in (getattr(frame, "recent_volumes", []) or [])]
    size = min(len(closes), len(highs), len(lows), len(volumes))
    if size:
        closes, highs, lows, volumes = [row[-size:] for row in (closes, highs, lows, volumes)]
    else:
        return _blocked("正式日K不足，無法辨識近20日有效買點")
    valid = [
        index for index in range(size)
        if closes[index] is not None and closes[index] > 0
        and highs[index] is not None and lows[index] is not None
        and highs[index] >= lows[index] > 0
    ]
    if len(valid) != size:
        closes, highs, lows, volumes = [
            [row[index] for index in valid] for row in (closes, highs, lows, volumes)
        ]
    else:
        closes = [float(x) for x in closes]
        highs = [float(x) for x in highs]
        lows = [float(x) for x in lows]
        volumes = [float(x or 0.0) for x in volumes]

    market = str(getattr(ticker, "market", "") or "").upper()
    status = str(getattr(frame, "market_status", "") or "").lower()
    freshness = str(getattr(truth, "freshness", "") or "").lower()
    if market == "TW" and (status in _ACTIVE_SESSIONS or freshness.startswith("intraday")):
        if closes:
            closes, highs, lows, volumes = [row[:-1] for row in (closes, highs, lows, volumes)]
    elif market == "US" and status in _ACTIVE_SESSIONS:
        if not isinstance(price_meta, Mapping) or price_meta.get("history_scope") != "formal_daily_only":
            return _blocked("美股盤中缺少正式日K界線，避免把未完成K線當歷史資料", coverage=len(closes))

    if len(closes) < LOOKBACK_SESSIONS + 1:
        return _blocked("正式完整日K少於21根，無法計算20日支撐與波動", coverage=len(closes))
    closes, highs, lows, volumes = [row[-(LOOKBACK_SESSIONS + 1):] for row in (closes, highs, lows, volumes)]
    hist_closes, hist_highs, hist_lows, hist_volumes = [row[-LOOKBACK_SESSIONS:] for row in (closes, highs, lows, volumes)]
    current = _num(getattr(frame, "last", None))
    if current is None or current <= 0:
        return _blocked("目前價格無效，不輸出回測機率", coverage=len(hist_closes))
    atr = _num(getattr(frame, "atr14", None)) or _trailing_atr(highs, lows, closes)
    if atr is None or atr <= 0:
        return _blocked("ATR／近端波動資料不足，無法建立買點寬度", coverage=len(hist_closes))

    best = _historical_best_zone(hist_lows, hist_highs, hist_closes, hist_volumes, atr, market, forecast)
    if not best:
        return _blocked("近20日沒有出現可驗證的局部止跌及後續反彈，不硬造最佳買點", coverage=len(hist_closes))

    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes)) if closes[index] > 0 and closes[index - 1] > 0]
    if len(returns) < 10:
        return _blocked("有效日報酬樣本不足，暫不估三日觸及機率", coverage=len(hist_closes))
    sigma = statistics.stdev(returns)
    if not math.isfinite(sigma) or sigma <= 1e-6:
        return _blocked("近20日波動不足以建立穩定機率估算", coverage=len(hist_closes))
    drift = statistics.mean(returns) * 0.35  # shrink the very short-window drift toward zero

    lower, upper = float(best["lower"]), float(best["upper"])
    if current > upper:
        target = upper
        direction_to_zone = -1
        distance = math.log(current / target)
        label = "未來3交易日回測"
    elif current < lower:
        target = lower
        direction_to_zone = 1
        distance = math.log(target / current)
        label = "未來3交易日回到區間"
    else:
        target = current
        direction_to_zone = 0
        distance = 0.0
        label = "已進入歷史買點區"

    base_probability = _first_passage_probability(distance, direction_to_zone * drift, sigma)
    reference_date = _as_date(getattr(frame, "price_date", ""))
    if reference_date is None:
        return _blocked("缺少正式價格日期，無法核對證據新鮮度", coverage=len(hist_closes))
    factors = _fresh_independent_factors(forecast, facts, reference_date)
    evidence_adjustment = 0.0
    drivers: list[str] = []
    if direction_to_zone:
        evidence_adjustment, drivers = _evidence_tilt(factors, direction_to_zone=direction_to_zone)
    else:
        drivers = [str(row["label"]) for row in factors]

    probability = 1.0 if distance == 0 else max(0.0, min(1.0, base_probability + evidence_adjustment))
    limit_pct = _limit_pct(forecast)
    reachable = True
    if market == "TW" and direction_to_zone < 0 and limit_pct is not None and limit_pct > 0:
        lowest_theoretical = current * ((1.0 - limit_pct) ** FORECAST_SESSIONS)
        if upper < lowest_theoretical:
            reachable = False
            base_probability = probability = 0.0
            evidence_adjustment = 0.0

    distance_pct = (current / upper - 1.0) * 100.0 if current > upper else (current / lower - 1.0) * 100.0 if current < lower else 0.0
    bounce_reason = f"入選低點後3日最高反彈 {best['bounce_pct']:.1f}%"
    risk_reason = f"期間最大回撤 {best['drawdown_pct']:.1f}%"
    volume_reason = (
        f"低點量能 {best['volume_ratio']:.2f}×前5日均量"
        if best["volume_ratio"] is not None else "歷史低點量能缺值"
    )
    drivers = (drivers + [bounce_reason, risk_reason, volume_reason])[:5]
    if not reachable:
        status_out = "unreachable_within_3_sessions"
        reason = "台股每日漲跌幅規則下，3個交易日理論可達範圍尚未到此區"
    elif current < lower:
        status_out = "zone_broken"
        reason = "現價已低於歷史區間；原支撐不再當作有效買點，須等待結構重建"
    elif current <= upper:
        status_out = "in_zone"
        reason = "現價已進入歷史觀察區；仍須等止跌／量價確認，不等於自動買進"
    else:
        status_out = "watch_pullback"
        reason = "歷史買點在現價下方；距離越遠，三日回測機率通常越低"

    trend5 = (closes[-1] / closes[-6] - 1.0) * 100.0 if len(closes) >= 6 else None
    return {
        "schema": SCHEMA,
        "status": status_out,
        "reason": reason,
        "zone": {"lower": lower, "upper": upper, "pivot": round(float(best["pivot"]), 4)},
        "distance_pct": round(distance_pct, 2),
        "touch_probability_pct": round(probability * 100.0, 1),
        "base_probability_pct": round(base_probability * 100.0, 1),
        "evidence_adjustment_pct": round(evidence_adjustment * 100.0, 1),
        "probability_label": label,
        "lookback_sessions": LOOKBACK_SESSIONS,
        "forecast_sessions": FORECAST_SESSIONS,
        "data_coverage": len(hist_closes),
        "candidate_pivots": best["candidates"],
        "support_retests": best["retests"],
        "trend_5d_pct": round(trend5, 2) if trend5 is not None else None,
        "daily_volatility_pct": round(sigma * 100.0, 2),
        "daily_drift_pct": round(drift * 100.0, 3),
        "fresh_factor_count": len(factors),
        "drivers": drivers,
        "probability_method": "20日歷史局部低點＋後3日反彈/回撤評分；三交易日對數價格首達估算；新鮮獨立證據修正最多±12個百分點",
        "calibration_status": "初版規則估算，需以逐日實際觸及結果滾動校準",
        "formal_execution": False,
        "exchange_limit_pct": limit_pct,
    }
