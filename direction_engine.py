# -*- coding: utf-8 -*-
"""TINO V12.2 Quantum Direction Precision Engine.

The price engine and the direction engine are intentionally separated:
- Price engine estimates T0/T1/High/Low paths.
- Direction engine estimates UP / NEUTRAL / DOWN probabilities.

Signals are grouped into independent families so the same evidence (especially
VWAP) cannot be counted repeatedly through several feature modules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from models import NewsItem, PriceFrame, SignalPacket
from quantum_entanglement import (
    build_quantum_evidence, dynamic_family_multiplier, entanglement_adjustment,
)
from trend_engine import build_trend_snapshot


@dataclass(frozen=True)
class DirectionResult:
    label: str  # UP / NEUTRAL / DOWN
    score: float  # -100 ... +100
    p_up: float
    p_neutral: float
    p_down: float
    confidence: float
    quality: float
    conflict: float
    expected_move_atr: float
    regime: str
    family_scores: Dict[str, float]
    family_weights: Dict[str, float]
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def abc(self) -> Dict[str, float]:
        """Map independent direction probabilities to the V9 A/B/C contract."""
        return {
            "A": round(self.p_up * 100.0, 1),
            "B": round(self.p_neutral * 100.0, 1),
            "C": round(self.p_down * 100.0, 1),
        }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _tanh100(value: float) -> float:
    return math.tanh(float(value)) * 100.0


def _accepted(block: Mapping[str, Any] | None) -> bool:
    if not isinstance(block, Mapping):
        return False
    src = str(block.get("source") or "").upper()
    return bool(block.get("accepted")) and "PROXY" not in src and "SAMPLE" not in src


def _positive(values: Iterable[Any]) -> List[float]:
    out: List[float] = []
    for value in values or []:
        x = _num(value, 0.0)
        if x > 0:
            out.append(x)
    return out


def _weighted_average(parts: Sequence[Tuple[float, float]]) -> Tuple[float, bool]:
    valid = [(float(value), float(weight)) for value, weight in parts if weight > 0 and math.isfinite(float(value))]
    total = sum(weight for _, weight in valid)
    if total <= 0:
        return 0.0, False
    return sum(value * weight for value, weight in valid) / total, True


def _trend_family(price: PriceFrame) -> Tuple[float, bool]:
    snap = build_trend_snapshot(price)
    parts: List[Tuple[float, float]] = []
    scales = (
        (snap.ret_5d, 4.0, 0.27),
        (snap.ret_10d, 7.0, 0.22),
        (snap.ret_20d, 12.0, 0.23),
        (snap.ret_60d, 25.0, 0.10),
        (snap.ma20_gap_pct, 8.0, 0.12),
        (snap.ma60_gap_pct, 15.0, 0.06),
    )
    for value, scale, weight in scales:
        if value is not None:
            parts.append((_tanh100(_num(value) / scale), weight))
    return _weighted_average(parts)


def _intraday_family(price: PriceFrame) -> Tuple[float, bool]:
    last = _num(price.last)
    atr = max(_num(price.atr14), last * 0.012, 0.01)
    vwap = _num(price.vwap, last) or last
    prev = _num(price.previous_close, last) or last
    high = max(_num(price.high, last), last)
    low = min(_num(price.low, last), last)

    vwap_z = (last - vwap) / atr
    if abs(vwap_z) < 0.15:
        vwap_score = 0.0
    else:
        vwap_score = math.copysign(_tanh100((abs(vwap_z) - 0.15) / 0.70), vwap_z)
    day_score = _tanh100(((last - prev) / atr) / 1.45)
    day_range = max(high - low, atr * 0.15, 0.01)
    clv = _clamp(((last - low) / day_range) * 2.0 - 1.0, -1.0, 1.0) * 100.0

    score, ok = _weighted_average(((vwap_score, 0.55), (day_score, 0.27), (clv, 0.18)))
    return _clamp(score, -100.0, 100.0), ok and last > 0 and vwap > 0


def _price_action_family(price: PriceFrame) -> Tuple[float, bool]:
    closes = _positive(price.recent_closes)
    highs = _positive(price.recent_highs)
    lows = _positive(price.recent_lows)
    last = _num(price.last)
    atr = max(_num(price.atr14), last * 0.012, 0.01)
    if len(closes) < 5:
        return 0.0, False

    confirmed = closes[:-1] if str(price.market_status) in {"intraday", "close_confirm", "pre_market", "after_hours"} and len(closes) > 5 else closes
    anchor3 = confirmed[-3] if len(confirmed) >= 3 else confirmed[0]
    momentum3 = _tanh100(((last - anchor3) / atr) / 2.2)

    h20 = max(highs[-20:]) if highs else max(confirmed[-20:])
    l20 = min(lows[-20:]) if lows else min(confirmed[-20:])
    span = max(h20 - l20, atr, 0.01)
    range_position = _clamp(((last - l20) / span) * 2.0 - 1.0, -1.0, 1.0) * 100.0

    recent = confirmed[-8:] if len(confirmed) >= 8 else confirmed
    slope = (recent[-1] - recent[0]) / max(atr * max(len(recent) - 1, 1), 0.01)
    slope_score = _tanh100(slope / 0.45)
    score, ok = _weighted_average(((momentum3, 0.42), (range_position, 0.25), (slope_score, 0.33)))
    return _clamp(score, -100.0, 100.0), ok


def _average_volume_lots(price: PriceFrame) -> float:
    vols = _positive(price.recent_volumes)
    if not vols:
        vols = [_num(price.volume)] if _num(price.volume) > 0 else []
    if not vols:
        return 1.0
    avg = mean(vols[-20:])
    # TW quote/history sources may report shares, while official chip blocks use lots.
    if avg >= 100_000:
        avg /= 1000.0
    return max(avg, 1.0)


def _actor_flow_score(block: Mapping[str, Any], key: str, avg_lots: float) -> Tuple[float, bool]:
    parts: List[Tuple[float, float]] = []
    horizon_weights = (("", 1, 0.36), ("_3", 3, 0.22), ("_5", 5, 0.25), ("_10", 10, 0.17))
    for suffix, days, weight in horizon_weights:
        raw_key = f"{key}{suffix}"
        if block.get(raw_key) is None:
            continue
        per_day = _num(block.get(raw_key)) / float(days)
        ratio = per_day / max(avg_lots, 1.0)
        parts.append((_tanh100(ratio / 0.035), weight))
    return _weighted_average(parts)


def _tw_flow_family(price: PriceFrame, trend_score: float, intraday_score: float) -> Tuple[float, bool]:
    ctx = price.context or {}
    inst = ctx.get("inst") if isinstance(ctx.get("inst"), Mapping) else {}
    margin = ctx.get("margin") if isinstance(ctx.get("margin"), Mapping) else {}
    bsi = ctx.get("bsi") if isinstance(ctx.get("bsi"), Mapping) else {}
    avg_lots = _average_volume_lots(price)
    parts: List[Tuple[float, float]] = []

    if _accepted(inst):
        actor_parts: List[Tuple[float, float]] = []
        for key, weight in (("foreign", 0.46), ("trust", 0.39), ("dealer", 0.15)):
            score, ok = _actor_flow_score(inst, key, avg_lots)
            if ok:
                actor_parts.append((score, weight))
        inst_score, inst_ok = _weighted_average(actor_parts)
        if inst_ok:
            parts.append((inst_score, 0.72))

    if _accepted(margin):
        short_score, short_ok = _actor_flow_score(margin, "short", avg_lots)
        if short_ok:
            # Short balance falling = covering = positive direction evidence.
            # Margin financing itself is interpreted by the adaptive leverage
            # family, so it is not counted here a second time.
            parts.append((-short_score, 0.10))

    if _accepted(bsi) and bsi.get("cover_rate") is not None:
        cover = _num(bsi.get("cover_rate"), 50.0)
        bsi_score = _tanh100((cover - 50.0) / 24.0)
        parts.append((bsi_score, 0.18))

    score, ok = _weighted_average(parts)
    return _clamp(score, -100.0, 100.0), ok


def _macro_family(price: PriceFrame) -> Tuple[float, bool]:
    macro = (price.context or {}).get("macro")
    if not isinstance(macro, Mapping) or not bool(macro.get("accepted")):
        return 0.0, False
    values: List[Tuple[float, float]] = []
    if macro.get("sox") is not None:
        values.append((_tanh100(_num(macro.get("sox")) / 2.6), 0.42))
    # NQ and QQQ describe the same Nasdaq family; use one, never both.
    nq_value = macro.get("nq") if macro.get("nq") is not None else macro.get("qqq")
    if nq_value is not None:
        values.append((_tanh100(_num(nq_value) / 2.6), 0.36))
    if macro.get("tw_gravity") is not None:
        values.append((_tanh100(_num(macro.get("tw_gravity")) / 2.6), 0.22))
    return _weighted_average(values)


def _us_short_family(price: PriceFrame, trend_score: float, intraday_score: float) -> Tuple[float, bool]:
    short = (price.context or {}).get("short")
    if not isinstance(short, Mapping):
        return 0.0, False
    sf = _num(short.get("short_float"), 0.0)
    if sf <= 0:
        return 0.0, False
    structural = _clamp(trend_score * 0.58 + intraday_score * 0.42, -100.0, 100.0)
    amplifier = _clamp((sf - 8.0) / 32.0, 0.10, 0.65)
    # High short interest amplifies the prevailing direction; it does not
    # automatically become a bullish squeeze signal without price confirmation.
    return _clamp(structural * amplifier, -55.0, 55.0), True


def _news_family(signals: Sequence[SignalPacket]) -> Tuple[float, bool]:
    parts: List[Tuple[float, float]] = []
    for signal in signals or []:
        if not signal.accepted:
            continue
        if signal.module == "News":
            # Policy/geopolitical headlines are handled by geo_policy with
            # time decay and overnight confirmation.  Do not count Quantum
            # Macro here a second time.
            parts.append((_clamp(signal.score * 2.0, -35.0, 35.0), 1.0))
    if not parts or max(abs(value) for value, _ in parts) < 1.0:
        return 0.0, False
    return _weighted_average(parts)


def _data_quality(price: PriceFrame, available_count: int) -> float:
    truth = price.truth
    meta = (price.context or {}).get("price_meta")
    meta = meta if isinstance(meta, Mapping) else {}
    source = str(getattr(truth, "source", "") or "").upper()
    history = len(_positive(price.recent_closes))

    quality = 0.42
    if bool(getattr(truth, "accepted", False)):
        quality += 0.14
    if not bool(getattr(truth, "fallback", False)) and not any(k in source for k in ("SAMPLE", "FALLBACK", "MOCK")):
        quality += 0.14
    else:
        quality -= 0.22
    if history >= 20:
        quality += 0.09
    if history >= 55:
        quality += 0.05
    if bool(meta.get("price_verified")):
        quality += 0.08
    if bool(meta.get("limited_price_mode")):
        quality -= 0.13
    if bool(meta.get("decision_blocked")):
        quality -= 0.30
    quality += min(max(available_count - 3, 0), 3) * 0.025
    return _clamp(quality, 0.20, 0.98)


def _family_conflict(scores: Mapping[str, float], weights: Mapping[str, float]) -> float:
    pos = sum(weights[k] * max(scores[k], 0.0) for k in scores)
    neg = sum(weights[k] * max(-scores[k], 0.0) for k in scores)
    total = pos + neg
    if total <= 1e-9:
        return 0.0
    return _clamp((2.0 * min(pos, neg)) / total, 0.0, 1.0)


def _regime(scores: Mapping[str, float]) -> str:
    trend = scores.get("trend", 0.0)
    intraday = scores.get("intraday", 0.0)
    if trend >= 25 and intraday >= 15:
        return "趨勢偏多"
    if trend <= -25 and intraday <= -15:
        return "趨勢偏空"
    if abs(trend) >= 25 and abs(intraday) >= 20 and trend * intraday < 0:
        return "趨勢與盤中衝突"
    if abs(trend) < 15 and abs(intraday) < 15:
        return "盤整等待"
    return "轉折觀察"


def _probabilities(effective_score: float, quality: float, conflict: float, uncertainty: float = 0.0) -> Tuple[float, float, float]:
    strength = abs(effective_score) / 100.0
    neutral = _clamp(
        0.48 - 0.34 * strength + 0.28 * conflict + 0.18 * (1.0 - quality) + 0.30 * uncertainty,
        0.16,
        0.78,
    )
    directional_mass = 1.0 - neutral
    try:
        up_share = 1.0 / (1.0 + math.exp(-effective_score / 15.0))
    except OverflowError:
        up_share = 1.0 if effective_score > 0 else 0.0
    up = directional_mass * up_share
    down = directional_mass * (1.0 - up_share)
    total = up + neutral + down
    return up / total, neutral / total, down / total


def build_direction_forecast(
    price: PriceFrame,
    signals: Sequence[SignalPacket] | None = None,
    news_items: Sequence[NewsItem] | None = None,
) -> DirectionResult:
    """Build an adaptive, market/sector-aware direction forecast.

    V12.2 uses evidence families once, then adds only bounded *interactions*:
    TW -> trend/intraday/price action + institutional flow + financing/market
          heat/futures + fresh monthly-revenue catalyst + night/global proxies.
    US -> trend/intraday/price action + sector proxies + fresh earnings/guidance
          catalyst + short-float confirmation.

    Revenue, earnings, policy and geopolitical effects decay by trading session
    or headline age. Stale events are not allowed to keep pushing tomorrow's
    direction.
    """
    signals = list(signals or [])
    news_items = list(news_items or [])
    trend, trend_ok = _trend_family(price)
    intraday, intraday_ok = _intraday_family(price)
    action, action_ok = _price_action_family(price)
    news, news_ok = _news_family(signals)

    market = str(price.ticker.market or "").upper()
    if market == "TW":
        flow, flow_ok = _tw_flow_family(price, trend, intraday)
        short = 0.0
        short_ok = False
        base_candidates = {
            "trend": (trend, 0.240, trend_ok),
            "intraday": (intraday, 0.150, intraday_ok),
            "price_action": (action, 0.100, action_ok),
            "flow": (flow, 0.200, flow_ok),
            "news": (news, 0.030, news_ok),
        }
        quantum = build_quantum_evidence(
            price,
            signals,
            news_items,
            trend_score=trend,
            intraday_score=intraday,
            flow_score=flow if flow_ok else 0.0,
        )
    else:
        short, short_ok = _us_short_family(price, trend, intraday)
        base_candidates = {
            "trend": (trend, 0.260, trend_ok),
            "intraday": (intraday, 0.170, intraday_ok),
            "price_action": (action, 0.120, action_ok),
            "short": (short, 0.070, short_ok),
            "news": (news, 0.030, news_ok),
        }
        quantum = build_quantum_evidence(
            price,
            signals,
            news_items,
            trend_score=trend,
            intraday_score=intraday,
            flow_score=0.0,
        )

    candidates = {**base_candidates, **quantum.families}
    fund_available = bool(candidates.get("fundamental_event", (0.0, 0.0, False))[2])
    geo_available = bool(candidates.get("geo_policy", (0.0, 0.0, False))[2])

    valid: Dict[str, Tuple[float, float]] = {}
    for name, (score, base_weight, ok) in candidates.items():
        if not ok:
            continue
        multiplier = dynamic_family_multiplier(
            name,
            market_status=str(price.market_status or ""),
            profile=quantum.profile,
            fundamental_event_available=fund_available,
            geo_available=geo_available,
        )
        weight = float(base_weight) * float(multiplier)
        if weight > 0:
            valid[name] = (score, weight)

    total_weight = sum(weight for _, weight in valid.values()) or 1.0
    scores = {name: round(_clamp(score, -100.0, 100.0), 4) for name, (score, _) in valid.items()}
    weights = {name: weight / total_weight for name, (_, weight) in valid.items()}
    base_score = sum(scores[name] * weights[name] for name in scores)
    interaction, interaction_reasons = entanglement_adjustment(scores, market)
    raw_score = _clamp(base_score + interaction, -100.0, 100.0)

    conflict = _family_conflict(scores, weights)
    quality = _data_quality(price, len(scores))
    uncertainty = _clamp(float(quantum.uncertainty), 0.0, 0.30)
    effective = _clamp(
        raw_score * (1.0 - 0.45 * conflict) * quality * (1.0 - 0.35 * uncertainty),
        -100.0,
        100.0,
    )
    p_up, p_neutral, p_down = _probabilities(effective, quality, conflict, uncertainty)

    if p_up >= 0.50 and (p_up - p_down) >= 0.15:
        label = "UP"
    elif p_down >= 0.50 and (p_down - p_up) >= 0.15:
        label = "DOWN"
    else:
        label = "NEUTRAL"

    strength = abs(effective) / 100.0
    confidence = _clamp(
        45.0 + 38.0 * strength + 10.0 * (quality - 0.5) - 18.0 * conflict - 28.0 * uncertainty,
        32.0,
        88.0,
    )
    expected_move_atr = _clamp(
        (effective / 100.0) * (0.65 + 0.15 * quality) * (1.0 - 0.40 * uncertainty),
        -0.80,
        0.80,
    )
    top = sorted(scores.items(), key=lambda item: abs(item[1]), reverse=True)[:4]
    reasons = [f"{name} {value:+.0f}" for name, value in top]
    reasons.extend(quantum.reasons[:3])
    if interaction_reasons:
        reasons.append("糾纏確認：" + ",".join(interaction_reasons[:2]))
    if conflict >= 0.45:
        reasons.append("多空家族衝突")
    if uncertainty >= 0.08:
        reasons.append("事件前不確定性降權")
    if quality < 0.60:
        reasons.append("資料品質降權")

    return DirectionResult(
        label=label,
        score=round(effective, 2),
        p_up=round(p_up, 4),
        p_neutral=round(p_neutral, 4),
        p_down=round(p_down, 4),
        confidence=round(confidence, 2),
        quality=round(quality, 3),
        conflict=round(conflict, 3),
        expected_move_atr=round(expected_move_atr, 4),
        regime=f"{_regime(scores)}｜{quantum.profile}",
        family_scores={k: round(v, 2) for k, v in scores.items()},
        family_weights={k: round(v, 4) for k, v in weights.items()},
        reasons=reasons,
    )

