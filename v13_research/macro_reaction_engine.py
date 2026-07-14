# -*- coding: utf-8 -*-
"""Economic surprise, semantic verdict, and market-reaction classification.

This module deliberately separates the economic result from Wall Street's
actual price reaction so a consensus result can still be identified as
Sell-the-News, positioning liquidation, or bad-news-already-priced-in.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from zoneinfo import ZoneInfo

_TAIPEI = ZoneInfo("Asia/Taipei")
_NEW_YORK = ZoneInfo("America/New_York")


def _now_tw(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(_TAIPEI)
    if now.tzinfo is None:
        return now.replace(tzinfo=_TAIPEI)
    return now.astimezone(_TAIPEI)


def _number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(str(value).replace("%", "").replace(",", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None

def _surprise_map(actual: Mapping[str, Any], forecast: Mapping[str, Any]) -> Dict[str, float | None]:
    out: Dict[str, float | None] = {}
    for key in ("headline_mom", "headline_yoy", "core_mom", "core_yoy"):
        a = _number(actual.get(key))
        f = _number(forecast.get(key))
        out[key] = round(a - f, 4) if a is not None and f is not None else None
    return out


def _inflation_event_score(code: str, surprise: Mapping[str, Any], actual: Mapping[str, Any], previous: Mapping[str, Any]) -> Tuple[float, str]:
    """Blend consensus surprise with the underlying inflation impulse.

    A result can match consensus and still materially cool/warm versus the
    previous release.  That economic impulse is preserved here; the separate
    reaction engine then decides whether Wall Street confirms it or sells it.
    """
    scales = {
        "headline_mom": (0.10, 0.28),
        "headline_yoy": (0.20, 0.17),
        "core_mom": (0.10, 0.35),
        "core_yoy": (0.20, 0.20),
    }
    surprise_sum = 0.0
    surprise_weight = 0.0
    surprise_values: List[float] = []
    for key, (scale, weight) in scales.items():
        value = _number(surprise.get(key))
        if value is None:
            continue
        surprise_values.append(abs(value))
        # Hotter-than-consensus inflation is bearish for duration assets.
        surprise_sum += -math.tanh(value / scale) * weight
        surprise_weight += weight
    surprise_score = surprise_sum / surprise_weight if surprise_weight else 0.0

    trend_sum = 0.0
    trend_weight = 0.0
    for key, weight in (("headline_mom", 0.30), ("headline_yoy", 0.20), ("core_mom", 0.30), ("core_yoy", 0.20)):
        a = _number(actual.get(key))
        p = _number(previous.get(key))
        if a is None or p is None:
            continue
        scale = 0.15 if "mom" in key else 0.30
        # Cooling versus previous is positive; warming is negative.
        trend_sum += -math.tanh((a - p) / scale) * weight
        trend_weight += weight
    trend_score = trend_sum / trend_weight if trend_weight else 0.0

    if surprise_weight and trend_weight:
        near_consensus = max(surprise_values or [99.0]) <= 0.051
        if near_consensus:
            # When consensus is met, preserve the economic impulse instead of
            # incorrectly reducing the event to zero.
            score = trend_score * 0.72 + surprise_score * 0.28
            basis = "meet_plus_impulse"
        else:
            score = surprise_score * 0.68 + trend_score * 0.32
            basis = "surprise_plus_impulse"
        return max(-1.0, min(1.0, score)), basis
    if surprise_weight:
        return max(-1.0, min(1.0, surprise_score)), "surprise"
    if trend_weight:
        return max(-0.60, min(0.60, trend_score)), "trend"
    return 0.0, "none"


def _semantic_inflation(code: str, actual: Mapping[str, Any], previous: Mapping[str, Any], score: float) -> Tuple[str, str]:
    hm = _number(actual.get("headline_mom"))
    hy = _number(actual.get("headline_yoy"))
    cm = _number(actual.get("core_mom"))
    cy = _number(actual.get("core_yoy"))
    phm = _number(previous.get("headline_mom"))
    phy = _number(previous.get("headline_yoy"))
    pcm = _number(previous.get("core_mom"))
    pcy = _number(previous.get("core_yoy"))

    headline_cooling = (hm is not None and phm is not None and hm < phm - 0.049) or (hy is not None and phy is not None and hy < phy - 0.099)
    headline_warming = (hm is not None and phm is not None and hm > phm + 0.049) or (hy is not None and phy is not None and hy > phy + 0.099)
    core_cooling = (cm is not None and pcm is not None and cm < pcm - 0.049) or (cy is not None and pcy is not None and cy < pcy - 0.099)
    core_warming = (cm is not None and pcm is not None and cm > pcm + 0.049) or (cy is not None and pcy is not None and cy > pcy + 0.099)
    core_sticky = not core_cooling and (cm is not None and cm >= 0.2 or cy is not None and cy >= 2.8)

    parts: List[str] = []
    if headline_cooling:
        parts.append("Headline降溫")
    elif headline_warming:
        parts.append("Headline升溫")
    else:
        parts.append("Headline大致持平")
    if core_cooling:
        parts.append("Core降溫")
    elif core_warming:
        parts.append("Core升溫")
    elif core_sticky:
        parts.append("Core黏性")
    else:
        parts.append("Core平穩")

    if score >= 0.25:
        risk = "解除短線通膨尾端風險，但不等於快速降息"
    elif score <= -0.25:
        risk = "通膨再定價風險升高，成長股估值承壓"
    else:
        risk = "結果接近中性，方向交給殖利率與價格確認"
    if code == "PPI":
        risk = risk.replace("通膨", "上游成本") if score <= -0.35 else risk.replace("通膨尾端", "成本尾端")
    return "、".join(parts), risk


def _expectation_state(score: float, surprise: Mapping[str, Any], word_hint: str) -> str:
    values = [abs(_number(value) or 0.0) for value in surprise.values() if value is not None]
    if values and max(values) <= 0.051:
        return "meet"
    if score >= 0.22:
        return "cooler"
    if score <= -0.22:
        return "hotter"
    return word_hint if word_hint != "unknown" else "near_consensus"


def _parse_as_of(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Yahoo daily index strings are usually exchange-local/naive.  For
            # the freshness guard, treating them as Taipei would be unsafe;
            # date-only values are handled separately below.
            return None
        return parsed.astimezone(_TAIPEI)
    except Exception:
        return None


def _market_confirmation(
    macro: Mapping[str, Any],
    texts: Sequence[str],
    *,
    release_at_tw: str = "",
    event_code: str = "",
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Confirm the result only with post-event market observations.

    CPI/PPI are released one hour before the US cash open, so a pre-open daily
    proxy must never be mistaken for the reaction.  FOMC is released during the
    cash session and uses a shorter confirmation wait.
    """
    reference = _now_tw(now)
    try:
        release = datetime.fromisoformat(str(release_at_tw).replace("Z", "+00:00"))
        if release.tzinfo is None:
            release = release.replace(tzinfo=_TAIPEI)
        release = release.astimezone(_TAIPEI)
    except Exception:
        release = reference
    age_minutes = max(0.0, (reference - release).total_seconds() / 60.0)
    min_wait = 20.0 if str(event_code).upper() == "FOMC" else 65.0
    ready = age_minutes >= min_wait

    raw_values: Dict[str, float | None] = {}
    for key in ("sox", "nq", "qqq", "smh", "vix_change", "tx_night"):
        raw_values[key] = _number(macro.get(key)) if isinstance(macro, Mapping) else None

    as_of = macro.get("as_of") if isinstance(macro.get("as_of"), Mapping) else {}
    values: Dict[str, float | None] = {key: None for key in raw_values}
    fresh_keys: List[str] = []
    stale_keys: List[str] = []
    if ready:
        for key, value in raw_values.items():
            if value is None:
                continue
            stamp_raw = as_of.get(key)
            stamp = _parse_as_of(stamp_raw)
            fresh = False
            if stamp is not None:
                fresh = stamp >= release - timedelta(minutes=5)
            else:
                # Date-only Yahoo bars can still be accepted after the waiting
                # window if their market date matches the event's US date.
                stamp_date = str(stamp_raw or "")[:10]
                release_us_date = release.astimezone(_NEW_YORK).date().isoformat()
                fresh = bool(stamp_date and stamp_date == release_us_date)
            if fresh:
                values[key] = value
                fresh_keys.append(key)
            else:
                stale_keys.append(key)

    nq = values.get("nq") if values.get("nq") is not None else values.get("qqq")
    risk_assets = [value for value in (values.get("sox"), nq, values.get("smh")) if value is not None]
    market_score = sum(risk_assets) / len(risk_assets) if risk_assets else 0.0
    if values.get("vix_change") is not None:
        market_score -= float(values["vix_change"]) * 0.08
    market_score = max(-5.0, min(5.0, market_score))

    blob = " ".join(texts).lower()
    yield_up = ready and any(term in blob for term in ("yields rise", "yield rises", "殖利率上升", "殖利率走高"))
    yield_down = ready and any(term in blob for term in ("yields fall", "yield falls", "殖利率下降", "殖利率回落"))
    dollar_up = ready and any(term in blob for term in ("dollar rises", "dollar strengthens", "美元走強", "美元上漲"))
    dollar_down = ready and any(term in blob for term in ("dollar falls", "dollar weakens", "美元走弱", "美元下跌"))
    if yield_up:
        market_score -= 0.35
    if yield_down:
        market_score += 0.35
    if dollar_up:
        market_score -= 0.20
    if dollar_down:
        market_score += 0.20

    has_confirmation = bool(risk_assets or yield_up or yield_down or dollar_up or dollar_down)
    sign = (
        "bullish" if has_confirmation and market_score >= 0.35
        else "bearish" if has_confirmation and market_score <= -0.35
        else "neutral"
    )
    return {
        "ready": ready,
        "age_minutes": round(age_minutes, 1),
        "minimum_wait_minutes": min_wait,
        "score": round(market_score, 3),
        "sign": sign,
        "sox": values.get("sox"),
        "nq": nq,
        "smh": values.get("smh"),
        "vix_change": values.get("vix_change"),
        "tx_night": values.get("tx_night"),
        "yield_signal": "up" if yield_up else "down" if yield_down else "unknown",
        "dollar_signal": "up" if dollar_up else "down" if dollar_down else "unknown",
        "fresh_keys": fresh_keys,
        "stale_keys": stale_keys,
    }


def classify_market_reaction(event_score: float, expectation_state: str, confirmation: Mapping[str, Any]) -> Tuple[str, float, str]:
    """Separate economic result from positioning/reaction.

    This is the non-conspiratorial implementation of Tino's intuition:
    good/expected news can still be sold if positioning was crowded or the
    benefit was already priced in.
    """
    market_score = _number(confirmation.get("score")) or 0.0
    market_sign = str(confirmation.get("sign") or "neutral")
    expected_sign = "bullish" if event_score >= 0.18 else "bearish" if event_score <= -0.18 else "neutral"

    if market_sign == "neutral":
        return "pending", market_score, "市場尚未形成確認，等待殖利率、美元與開盤價格"
    if expected_sign == "bullish" and market_sign == "bearish":
        return "sell_the_news", market_score, "數據偏正向但風險資產走弱，疑似利多出盡／部位去風險"
    if expected_sign == "bearish" and market_sign == "bullish":
        return "bad_news_priced_in", market_score, "數據偏空但價格上漲，可能已提前反映／空方回補"
    if expected_sign == "neutral" and market_sign == "bearish":
        label = "符合預期但市場仍賣" if expectation_state in {"meet", "near_consensus"} else "中性數據下的風險去化"
        return "positioning_selloff", market_score, label + "，判斷重點轉向部位與流動性"
    if expected_sign == "neutral" and market_sign == "bullish":
        return "positioning_rally", market_score, "數據接近中性但市場上漲，可能在交易降息／流動性預期"
    return "confirmed", market_score, "官方結果與風險資產反應方向一致"


def _format_expectation(value: str) -> str:
    return {
        "meet": "符合預期",
        "near_consensus": "接近預期",
        "cooler": "低於預期／降溫",
        "hotter": "高於預期／升溫",
        "unknown": "預期差待確認",
    }.get(value, value or "預期差待確認")


def _format_metric(value: Any) -> str:
    number = _number(value)
    return "NA" if number is None else f"{number:+.1f}%"


def _build_summary(code: str, score: float, expectation: str, semantic: str, risk: str, reaction: str) -> str:
    return (
        f"Macro Event｜{code} {score:+.2f}｜{_format_expectation(expectation)}｜"
        f"{semantic}｜{risk}｜{reaction}"
    )




__all__ = [
    "_surprise_map",
    "_inflation_event_score",
    "_semantic_inflation",
    "_expectation_state",
    "_market_confirmation",
    "classify_market_reaction",
    "_format_expectation",
    "_build_summary",
]
