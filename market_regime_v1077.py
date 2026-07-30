# -*- coding: utf-8 -*-
"""V1077 shadow market-regime transition engine.

The engine answers a narrower question than the direction model:

    Is selling still expanding, or is it beginning to exhaust?

It is deliberately read-only.  The result is persisted with Prediction DNA and
later audited, but it cannot change direction probabilities, T0/T1 prices,
confidence, entry levels, or learning weights.
"""
from __future__ import annotations

import math
import re
from statistics import mean
from typing import Any, Dict, Mapping, Sequence

from models import PriceFrame


SCHEMA = "TINO_MARKET_REGIME_V1077_SHADOW_V1"

STATE_LABELS = {
    "normal_correction": "正常修正",
    "selling_expansion": "賣壓擴張",
    "panic_acceleration": "恐慌加速",
    "deleveraging": "去槓桿清洗",
    "selling_exhaustion": "賣壓衰竭",
    "bottom_probe": "止跌探索",
    "rebound_confirmed": "反彈確認",
    "trend_repair": "趨勢修復",
}

_STATE_ORDER = {
    "normal_correction": 0,
    "selling_expansion": 1,
    "panic_acceleration": 2,
    "deleveraging": 3,
    "selling_exhaustion": 4,
    "bottom_probe": 5,
    "rebound_confirmed": 6,
    "trend_repair": 7,
}


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


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _positive(values: Sequence[Any] | None) -> list[float]:
    return [number for number in (_maybe_num(value) for value in (values or [])) if number and number > 0]


def _direction_dict(direction: Any) -> Dict[str, Any]:
    if isinstance(direction, Mapping):
        return dict(direction)
    if direction is None:
        return {}
    to_dict = getattr(direction, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict() or {})
        except Exception:
            pass
    return dict(getattr(direction, "__dict__", {}) or {})


def _aligned_bars(price: PriceFrame) -> Dict[str, list[float]]:
    """Return aligned bars ending at the current price without double-appending."""
    closes = _positive(price.recent_closes)
    highs = _positive(price.recent_highs)
    lows = _positive(price.recent_lows)
    volumes = _positive(price.recent_volumes)
    last = _num(price.last)
    tolerance = max(abs(last) * 0.0005, 0.0001)
    current_already_present = bool(closes and abs(closes[-1] - last) <= tolerance)

    if not closes:
        closes = [_num(price.previous_close, last), last]
    elif current_already_present:
        closes[-1] = last
    else:
        closes.append(last)

    def align(values: list[float], current: float) -> list[float]:
        if len(values) >= len(closes):
            out = list(values[-len(closes):])
            out[-1] = current
            return out
        if len(values) == len(closes) - 1:
            return list(values) + [current]
        missing = max(len(closes) - len(values) - 1, 0)
        pad = [current] * missing
        return (pad + list(values) + [current])[-len(closes):]

    return {
        "closes": closes,
        "highs": align(highs, max(_num(price.high, last), last)),
        "lows": align(lows, min(_num(price.low, last), last)),
        "volumes": align(volumes, max(_num(price.volume), 0.0)),
    }


def _return_pct(closes: Sequence[float], sessions: int) -> float:
    if len(closes) <= sessions or closes[-sessions - 1] <= 0:
        return 0.0
    return (float(closes[-1]) / float(closes[-sessions - 1]) - 1.0) * 100.0


def _down_streak(closes: Sequence[float]) -> int:
    streak = 0
    for index in range(len(closes) - 1, 0, -1):
        if closes[index] < closes[index - 1]:
            streak += 1
        else:
            break
    return streak


def _relative_strength(context: Mapping[str, Any]) -> float | None:
    direct = _maybe_num(context.get("relative_strength_pct"))
    if direct is not None:
        return direct
    block = context.get("relative_strength")
    if isinstance(block, Mapping):
        for key in ("vs_sector_pct", "vs_benchmark_pct", "score_pct"):
            value = _maybe_num(block.get(key))
            if value is not None:
                return value
    return None


def _leverage_wash(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    heat = context.get("market_heat")
    if isinstance(heat, Mapping) and bool(heat.get("accepted")):
        change = _maybe_num(heat.get("change_yi"))
        balance = _maybe_num(heat.get("balance_yi"))
        change_ratio = (change / balance * 100.0) if change is not None and balance and balance > 0 else 0.0
        if change is not None and (change <= -80.0 or change_ratio <= -1.2):
            reasons.append(f"市場融資 {change:+.2f}億")

    margin = context.get("margin")
    if isinstance(margin, Mapping) and bool(margin.get("accepted")):
        today = _maybe_num(margin.get("margin"))
        three = _maybe_num(margin.get("margin_3"))
        streak = str(margin.get("margin_streak") or "")
        if (
            (today is not None and today < 0 and (three is None or three < 0))
            or ("減" in streak and not re.search(r"增", streak))
        ):
            reasons.append("個股融資下降")
    return bool(reasons), reasons


def _flow_relief(direction: Mapping[str, Any]) -> tuple[bool, list[str]]:
    scores = direction.get("family_scores")
    scores = scores if isinstance(scores, Mapping) else {}
    available: list[tuple[str, float]] = []
    for key in ("flow", "futures", "foreign_pressure", "short"):
        value = _maybe_num(scores.get(key))
        if value is not None:
            available.append((key, value))
    if not available:
        return False, []
    average = sum(value for _, value in available) / len(available)
    positive = [name for name, value in available if value >= 0]
    return bool(average >= -3.0), positive


def _breadth_state(context: Mapping[str, Any]) -> tuple[bool, bool, bool, list[str]]:
    """Read optional market breadth without fabricating missing observations."""
    block = context.get("market_breadth")
    if not isinstance(block, Mapping) or not bool(block.get("accepted")):
        return False, False, False, []
    comparisons: list[tuple[str, float, float]] = []
    for label, current_keys, previous_keys in (
        (
            "創新低家數",
            ("new_low_count", "new_lows"),
            ("previous_new_low_count", "prior_new_lows"),
        ),
        (
            "跌停家數",
            ("limit_down_count", "limit_downs"),
            ("previous_limit_down_count", "prior_limit_downs"),
        ),
        (
            "下跌家數",
            ("decliner_count", "decliners"),
            ("previous_decliner_count", "prior_decliners"),
        ),
    ):
        current = next(
            (value for value in (_maybe_num(block.get(key)) for key in current_keys) if value is not None),
            None,
        )
        previous = next(
            (value for value in (_maybe_num(block.get(key)) for key in previous_keys) if value is not None),
            None,
        )
        if current is not None and previous is not None:
            comparisons.append((label, current, previous))
    if not comparisons:
        return True, False, False, []
    improving = [label for label, current, previous in comparisons if current < previous]
    worsening = [label for label, current, previous in comparisons if current > previous]
    stabilizing = len(improving) >= max(1, (len(comparisons) + 1) // 2)
    expanding = len(worsening) >= max(1, (len(comparisons) + 1) // 2)
    reasons = [f"{label}{'收斂' if current < previous else '擴張' if current > previous else '持平'}" for label, current, previous in comparisons]
    return True, stabilizing, expanding, reasons


def _features(
    price: PriceFrame,
    bars: Mapping[str, Sequence[float]],
    direction: Mapping[str, Any],
    *,
    use_live_context: bool,
) -> Dict[str, Any]:
    closes = list(bars.get("closes") or [])
    highs = list(bars.get("highs") or [])
    lows = list(bars.get("lows") or [])
    volumes = list(bars.get("volumes") or [])
    last = closes[-1] if closes else _num(price.last)
    previous = closes[-2] if len(closes) >= 2 else _num(price.previous_close, last)
    high = max(highs[-1] if highs else last, last)
    low = min(lows[-1] if lows else last, last)
    current_open = _num(price.open, previous) if use_live_context else previous
    day_range = max(high - low, abs(last) * 0.001, 0.01)
    close_location = _clip((last - low) / day_range)
    lower_shadow_ratio = _clip((min(current_open, last) - low) / day_range)

    prior_lows = lows[:-1][-20:] if len(lows) > 1 else closes[:-1][-20:]
    new_low_20 = bool(prior_lows and low <= min(prior_lows) * 1.001)
    previous_volumes = [value for value in volumes[:-1][-20:] if value > 0]
    volume_ratio = (
        volumes[-1] / mean(previous_volumes)
        if volumes and volumes[-1] > 0 and previous_volumes and mean(previous_volumes) > 0
        else 1.0
    )

    ret1 = ((last / previous) - 1.0) * 100.0 if previous > 0 else 0.0
    prior_ret1 = (
        ((closes[-2] / closes[-3]) - 1.0) * 100.0
        if len(closes) >= 3 and closes[-3] > 0 else 0.0
    )
    ret3 = _return_pct(closes, 3)
    ret5 = _return_pct(closes, 5)
    ret10 = _return_pct(closes, 10)
    ma20 = mean(closes[-20:]) if closes else last
    ma20_gap_pct = ((last / ma20) - 1.0) * 100.0 if ma20 > 0 else 0.0

    vwap = _maybe_num(price.vwap) if use_live_context else None
    above_vwap = bool(vwap is not None and vwap > 0 and last >= vwap)
    below_vwap = bool(vwap is not None and vwap > 0 and last < vwap)
    loss_decelerating = bool(ret1 < 0 and prior_ret1 < 0 and ret1 >= prior_ret1 + 0.20)

    context = price.context if isinstance(price.context, Mapping) and use_live_context else {}
    leverage_wash, leverage_reasons = _leverage_wash(context)
    flow_relief, flow_relief_families = _flow_relief(direction if use_live_context else {})
    relative_strength_pct = _relative_strength(context)
    breadth_available, breadth_stabilizing, breadth_worsening, breadth_reasons = _breadth_state(context)

    stress = 0.0
    stress += 24.0 * _clip(max(-ret1, 0.0) / 5.0)
    stress += 25.0 * _clip(max(-ret5, 0.0) / 12.0)
    stress += 10.0 * _clip(_down_streak(closes) / 4.0)
    stress += 14.0 if below_vwap else 0.0
    stress += 13.0 if new_low_20 else 0.0
    stress += 8.0 * _clip(max(volume_ratio - 1.0, 0.0) / 0.8) if ret1 < 0 else 0.0
    stress += 6.0 if close_location <= 0.25 else 0.0
    stress += 8.0 if breadth_worsening else 0.0
    stress = _clip(stress, 0.0, 100.0)

    exhaustion = 10.0 if stress >= 45.0 else 0.0
    exhaustion += 18.0 if loss_decelerating else 0.0
    exhaustion += 17.0 if close_location >= 0.55 else 0.0
    exhaustion += 13.0 if lower_shadow_ratio >= 0.25 else 0.0
    exhaustion += 11.0 if volume_ratio >= 1.20 and close_location >= 0.42 else 0.0
    exhaustion += 14.0 if leverage_wash else 0.0
    exhaustion += 10.0 if flow_relief else 0.0
    exhaustion += 10.0 if above_vwap else 0.0
    exhaustion += 7.0 if relative_strength_pct is not None and relative_strength_pct >= 0.50 else 0.0
    exhaustion += 12.0 if breadth_stabilizing else 0.0
    exhaustion -= 18.0 if ret1 <= -3.0 and new_low_20 and close_location <= 0.28 else 0.0
    exhaustion = _clip(exhaustion, 0.0, 100.0)

    family_scores = direction.get("family_scores")
    family_scores = family_scores if isinstance(family_scores, Mapping) else {}
    intraday_score = _num(family_scores.get("intraday"))
    price_action_score = _num(family_scores.get("price_action"))
    confirmation = 0.0
    confirmation += 21.0 if ret1 > 0.30 else 0.0
    confirmation += 24.0 if above_vwap else 0.0
    confirmation += 15.0 if close_location >= 0.65 else 0.0
    confirmation += 10.0 if not new_low_20 else 0.0
    confirmation += 12.0 if price_action_score >= 10.0 else 0.0
    confirmation += 10.0 if intraday_score >= 10.0 else 0.0
    confirmation += 8.0 if flow_relief else 0.0
    confirmation += 8.0 if relative_strength_pct is not None and relative_strength_pct >= 0.50 else 0.0
    confirmation += 8.0 if breadth_stabilizing else 0.0
    confirmation = _clip(confirmation, 0.0, 100.0)

    availability = 6
    availability += 1 if vwap is not None else 0
    availability += 1 if len(closes) >= 11 else 0
    availability += 1 if previous_volumes else 0
    availability += 1 if leverage_reasons else 0
    availability += 1 if family_scores else 0
    availability += 1 if relative_strength_pct is not None else 0
    availability += 1 if breadth_available else 0

    return {
        "last": round(last, 4),
        "atr": round(max(_num(price.atr14), last * 0.012, 0.01), 4),
        "ret_1d_pct": round(ret1, 4),
        "prior_ret_1d_pct": round(prior_ret1, 4),
        "ret_3d_pct": round(ret3, 4),
        "ret_5d_pct": round(ret5, 4),
        "ret_10d_pct": round(ret10, 4),
        "ma20_gap_pct": round(ma20_gap_pct, 4),
        "down_streak": _down_streak(closes),
        "new_low_20": new_low_20,
        "close_location": round(close_location, 4),
        "lower_shadow_ratio": round(lower_shadow_ratio, 4),
        "volume_ratio": round(volume_ratio, 4),
        "above_vwap": above_vwap,
        "below_vwap": below_vwap,
        "loss_decelerating": loss_decelerating,
        "leverage_wash": leverage_wash,
        "leverage_reasons": leverage_reasons,
        "flow_relief": flow_relief,
        "flow_relief_families": flow_relief_families,
        "relative_strength_pct": (
            round(relative_strength_pct, 4) if relative_strength_pct is not None else None
        ),
        "breadth_available": breadth_available,
        "breadth_stabilizing": breadth_stabilizing,
        "breadth_worsening": breadth_worsening,
        "breadth_reasons": breadth_reasons,
        "stress_score": round(stress, 2),
        "exhaustion_score": round(exhaustion, 2),
        "confirmation_score": round(confirmation, 2),
        "feature_coverage": round(availability / 13.0, 4),
    }


def _classify(features: Mapping[str, Any]) -> str:
    stress = _num(features.get("stress_score"))
    exhaustion = _num(features.get("exhaustion_score"))
    confirmation = _num(features.get("confirmation_score"))
    ret1 = _num(features.get("ret_1d_pct"))
    ret5 = _num(features.get("ret_5d_pct"))
    ret10 = _num(features.get("ret_10d_pct"))
    ma20_gap = _num(features.get("ma20_gap_pct"))
    had_selloff = bool(stress >= 50.0 or ret5 <= -4.0 or ret10 <= -7.0)

    if had_selloff and ret1 > 0.30 and confirmation >= 64.0:
        return "rebound_confirmed"
    if ma20_gap >= 0.0 and ret5 >= 1.5 and confirmation >= 55.0:
        return "trend_repair"
    if stress >= 50.0 and exhaustion >= 55.0:
        return "selling_exhaustion"
    if stress >= 50.0 and bool(features.get("leverage_wash")) and confirmation < 55.0:
        return "deleveraging"
    if (
        stress >= 72.0
        and ret1 <= -3.0
        and bool(features.get("new_low_20"))
        and _num(features.get("close_location")) <= 0.35
    ):
        return "panic_acceleration"
    if stress >= 52.0:
        return "selling_expansion"
    if had_selloff and (exhaustion >= 35.0 or confirmation >= 35.0):
        return "bottom_probe"
    if ret5 < 0.0 or bool(features.get("below_vwap")):
        return "normal_correction"
    return "trend_repair"


def _transition_note(previous: str, current: str) -> str:
    if previous == current:
        return f"{STATE_LABELS[current]}延續"
    distance = _STATE_ORDER.get(current, 0) - _STATE_ORDER.get(previous, 0)
    if distance >= 2:
        return f"{STATE_LABELS[previous]} → {STATE_LABELS[current]}｜快速改善"
    if distance == 1:
        return f"{STATE_LABELS[previous]} → {STATE_LABELS[current]}｜改善一階"
    if distance <= -2:
        return f"{STATE_LABELS[previous]} → {STATE_LABELS[current]}｜風險再擴張"
    return f"{STATE_LABELS[previous]} → {STATE_LABELS[current]}"


def build_market_regime_shadow(
    price: PriceFrame,
    direction: Any = None,
) -> Dict[str, Any]:
    """Build current and prior price-state candidates for shadow validation."""
    direction_map = _direction_dict(direction)
    bars = _aligned_bars(price)
    current_features = _features(
        price,
        bars,
        direction_map,
        use_live_context=True,
    )
    current_state = _classify(current_features)

    previous_state = "normal_correction"
    previous_features: Dict[str, Any] = {}
    if len(bars["closes"]) >= 7:
        previous_bars = {key: list(values[:-1]) for key, values in bars.items()}
        previous_features = _features(
            price,
            previous_bars,
            {},
            use_live_context=False,
        )
        previous_state = _classify(previous_features)

    coverage = _num(current_features.get("feature_coverage"))
    state_confidence = _clip(
        0.42
        + coverage * 0.28
        + abs(_num(current_features.get("stress_score")) - 50.0) / 250.0
        + abs(_num(current_features.get("confirmation_score")) - 50.0) / 300.0,
        0.35,
        0.88,
    )
    return {
        "schema": SCHEMA,
        "shadow": True,
        "decision_influence": False,
        "ticker": str(price.ticker.resolved_symbol or ""),
        "market": str(price.ticker.market or ""),
        "price_date": str(price.price_date or price.truth.date or ""),
        "state": current_state,
        "state_label": STATE_LABELS[current_state],
        "previous_state": previous_state,
        "previous_state_label": STATE_LABELS[previous_state],
        "transition": f"{previous_state}->{current_state}",
        "transition_text": _transition_note(previous_state, current_state),
        "state_confidence": round(state_confidence, 4),
        "stress_score": current_features["stress_score"],
        "exhaustion_score": current_features["exhaustion_score"],
        "confirmation_score": current_features["confirmation_score"],
        "features": current_features,
        "previous_features": {
            key: previous_features.get(key)
            for key in (
                "ret_1d_pct",
                "ret_5d_pct",
                "down_streak",
                "new_low_20",
                "close_location",
                "volume_ratio",
                "stress_score",
                "exhaustion_score",
            )
            if key in previous_features
        },
        "evidence_missing": [
            label
            for available, label in (
                (current_features.get("relative_strength_pct") is not None, "相對產業強弱"),
                (bool(current_features.get("leverage_reasons")), "融資清洗"),
                (bool(current_features.get("breadth_available")), "市場廣度/跌停家數"),
                (bool(direction_map.get("family_scores")), "方向家族"),
            )
            if not available
        ],
        "price_history_only_previous_state": True,
        "requires_price_confirmation": current_state not in {"rebound_confirmed", "trend_repair"},
        "formal_weights_changed": False,
    }
