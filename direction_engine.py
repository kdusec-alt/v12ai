# -*- coding: utf-8 -*-
"""TINO RC4 Quantum Direction Precision Engine.

The price engine and the direction engine are intentionally separated:
- Price engine estimates T0/T1/High/Low paths.
- Direction engine estimates UP / NEUTRAL / DOWN probabilities.

Signals are grouped into independent families so the same evidence (especially
VWAP) cannot be counted repeatedly through several feature modules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from models import NewsItem, PriceFrame, SignalPacket
from quantum_entanglement import build_quantum_evidence
try:
    from quantum_interactions import dynamic_family_multiplier, entanglement_adjustment
except Exception:
    # Keep formal prediction online even if an optional RC4.6 helper was not
    # uploaded yet.  Compatibility exports live in quantum_entanglement.
    from quantum_entanglement import dynamic_family_multiplier, entanglement_adjustment
from trend_engine import build_trend_snapshot
try:
    from learning_calibration import bounded_learning_calibration
except Exception:
    def bounded_learning_calibration(ticker, family_contributions):
        return {
            "eligible": False,
            "ticker": str(ticker or "").upper(),
            "direction_audit_count": 0,
            "direction_hit_rate": None,
            "maturity": 0.0,
            "raw_delta": 0.0,
            "delta": 0.0,
            "confidence_delta": 0.0,
            "applied_families": {},
            "gate": "learning_module_unavailable",
        }


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
    family_contributions: Dict[str, float] = field(default_factory=dict)
    factor_contributions: Dict[str, float] = field(default_factory=dict)
    interaction_contribution: float = 0.0
    uncertainty: float = 0.0
    risk_factors: List[str] = field(default_factory=list)
    risk_contributions: Dict[str, float] = field(default_factory=dict)
    confidence_adjustments: Dict[str, float] = field(default_factory=dict)
    gate_state: str = ""
    confidence_components: Dict[str, float] = field(default_factory=dict)
    learning_calibration: Dict[str, Any] = field(default_factory=dict)

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


def _intraday_components(price: PriceFrame) -> Tuple[Dict[str, float], bool]:
    """Return weighted intraday components whose values sum to the family score.

    Same-day return is intentionally low-weight.  Today rising/falling is one
    molecule, not tomorrow's answer; VWAP acceptance and close location carry
    more information about whether the move was actually held.
    """
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
    day_score = _tanh100(((last - prev) / atr) / 1.70)
    day_range = max(high - low, atr * 0.15, 0.01)
    close_location = _clamp(((last - low) / day_range) * 2.0 - 1.0, -1.0, 1.0) * 100.0

    weights = {"VWAP": 0.60, "當日漲跌": 0.14, "收盤位置": 0.26}
    raw = {"VWAP": vwap_score, "當日漲跌": day_score, "收盤位置": close_location}
    components = {name: raw[name] * weight for name, weight in weights.items()}
    return components, last > 0 and vwap > 0


def _intraday_family(price: PriceFrame) -> Tuple[float, bool]:
    components, ok = _intraday_components(price)
    score = sum(components.values())
    return _clamp(score, -100.0, 100.0), ok


def _exhaustion_family(price: PriceFrame) -> Tuple[float, bool]:
    """Bounded anti-inertia evidence for stretched one-day moves.

    It never predicts a reversal by itself.  It only shifts an extended move
    toward retest/neutral unless other families confirm continuation.
    """
    last = _num(price.last)
    prev = _num(price.previous_close, last) or last
    vwap = _num(price.vwap, last) or last
    atr = max(_num(price.atr14), last * 0.012, 0.01)
    realised = (last - prev) / atr
    vwap_z = (last - vwap) / atr
    snap = build_trend_snapshot(price)
    ret5 = _num(snap.ret_5d, 0.0)

    stretch = max(
        max(abs(realised) - 1.00, 0.0),
        max(abs(vwap_z) - 1.20, 0.0) * 0.85,
        max(abs(ret5) - 7.0, 0.0) / 7.0,
    )
    if stretch <= 0.05:
        return 0.0, False

    directional = realised * 0.58 + vwap_z * 0.27 + (ret5 / 4.0) * 0.15
    if abs(directional) < 0.20:
        return 0.0, False
    score = -math.copysign(_tanh100(stretch / 1.35), directional)

    # A close held at the extreme reduces, but does not erase, exhaustion risk.
    high = max(_num(price.high, last), last)
    low = min(_num(price.low, last), last)
    day_range = max(high - low, atr * 0.15, 0.01)
    clv = _clamp(((last - low) / day_range) * 2.0 - 1.0, -1.0, 1.0)
    if directional * clv > 0.35:
        score *= 0.68
    return _clamp(score, -62.0, 62.0), True

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
    micro = (price.context or {}).get("market_microstructure")
    micro = micro if isinstance(micro, Mapping) else {}
    if bool(micro.get("is_emerging")):
        quality -= 0.06
        if int(_num(micro.get("history_count"), 0.0)) < 20:
            quality -= 0.05
        if str(micro.get("liquidity") or "") == "薄量":
            quality -= 0.04
    if int(_num(micro.get("coverage_score"), 5.0)) <= 2:
        quality -= 0.04
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


_FAMILY_LABELS_TW = {
    "trend": "趨勢", "intraday": "盤中結構", "price_action": "價格結構",
    "flow": "法人", "news": "新聞", "fundamental_event": "月營收",
    "overnight": "跨市場", "leverage": "融資", "market_heat": "市場融資",
    "futures": "外資期貨", "foreign_pressure": "外資匯率",
    "geo_policy": "政策/地緣", "analyst_event": "評等/目標價背離", "exhaustion": "過熱/耗竭",
}
_FAMILY_LABELS_US = {
    "trend": "趨勢", "intraday": "盤中結構", "price_action": "價格結構",
    "short": "Short Float", "news": "新聞", "fundamental_event": "財報/guidance",
    "overnight": "跨市場", "geo_policy": "政策/地緣", "analyst_event": "評等/目標價背離", "exhaustion": "過熱/耗竭",
}


def _split_family_contribution(
    family_contribution: float,
    family_score: float,
    components: Mapping[str, float] | None,
) -> Dict[str, float]:
    if not components or abs(family_score) < 1e-9:
        return {}
    return {
        str(label): family_contribution * float(value) / family_score
        for label, value in components.items()
        if math.isfinite(float(value))
    }


def _quantum_gate_state(p_up: float, p_neutral: float, p_down: float) -> str:
    rows = (("A突破", p_up), ("B回測", p_neutral), ("C防守", p_down))
    label, probability = max(rows, key=lambda item: item[1])
    return f"{label} {probability * 100.0:.0f}%"


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

    Revenue, earnings, policy, geopolitical and analyst target/rating effects
    decay by trading session or headline age. Stale events are not allowed to
    keep pushing tomorrow's direction.  A higher target is never bullish by
    default; it requires price/flow confirmation and can become a bounded
    distribution-risk signal when the market rejects it.
    """
    signals = list(signals or [])
    news_items = list(news_items or [])
    trend, trend_ok = _trend_family(price)
    intraday, intraday_ok = _intraday_family(price)
    action, action_ok = _price_action_family(price)
    exhaustion, exhaustion_ok = _exhaustion_family(price)
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
            "exhaustion": (exhaustion, 0.055, exhaustion_ok),
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
            "exhaustion": (exhaustion, 0.055, exhaustion_ok),
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
    raw_family_contributions = {name: scores[name] * weights[name] for name in scores}
    base_score = sum(raw_family_contributions.values())
    interaction, interaction_reasons = entanglement_adjustment(scores, market)
    learning_calibration = bounded_learning_calibration(
        price.ticker.resolved_symbol,
        raw_family_contributions,
    )
    learning_delta_raw = _clamp(_num(learning_calibration.get("delta"), 0.0), -6.0, 6.0)
    unbounded_raw = base_score + interaction + learning_delta_raw
    raw_score = _clamp(unbounded_raw, -100.0, 100.0)

    conflict = _family_conflict(scores, weights)
    quality = _data_quality(price, len(scores))
    uncertainty = _clamp(float(quantum.uncertainty), 0.0, 0.30)
    scale = (1.0 - 0.45 * conflict) * quality * (1.0 - 0.35 * uncertainty)
    clip_ratio = raw_score / unbounded_raw if abs(unbounded_raw) > 1e-9 else 1.0
    effective = _clamp(raw_score * scale, -100.0, 100.0)

    family_contributions = {
        name: scores[name] * weights[name] * scale * clip_ratio
        for name in scores
    }
    interaction_contribution = interaction * scale * clip_ratio
    learning_contribution = learning_delta_raw * scale * clip_ratio

    # Split only when the child components are mathematically traceable to the
    # parent family.  Their sum plus interaction equals the displayed total.
    factor_contributions: Dict[str, float] = {}
    labels = _FAMILY_LABELS_TW if market == "TW" else _FAMILY_LABELS_US
    intraday_components, _ = _intraday_components(price)
    component_map: Dict[str, Mapping[str, float]] = dict(quantum.family_components)
    component_map["intraday"] = intraday_components
    for family, contribution in family_contributions.items():
        split = _split_family_contribution(
            contribution,
            scores.get(family, 0.0),
            component_map.get(family),
        )
        if split:
            for label, value in split.items():
                factor_contributions[label] = factor_contributions.get(label, 0.0) + value
        else:
            label = labels.get(family, family)
            factor_contributions[label] = factor_contributions.get(label, 0.0) + contribution
    if abs(interaction_contribution) >= 0.005:
        factor_contributions["糾纏確認"] = interaction_contribution
    if abs(learning_contribution) >= 0.005:
        factor_contributions["學習校準"] = learning_contribution

    # Contribution Truth Guard: every visible direction component must reconcile
    # to the exact effective direction score.  Any tiny numeric/clip remainder is
    # carried as a neutral "其他因子" bucket instead of being hidden.
    contribution_total = sum(float(value) for value in factor_contributions.values())
    contribution_residual = effective - contribution_total
    if abs(contribution_residual) >= 0.0005:
        factor_contributions["其他因子"] = factor_contributions.get("其他因子", 0.0) + contribution_residual

    risk_contributions = {
        str(name): round(max(0.0, float(value)), 3)
        for name, value in (getattr(quantum, "risk_contributions", {}) or {}).items()
        if float(value) > 0.01
    }
    risk_total = sum(risk_contributions.values())
    # Confidence is reduced by the engine's bounded uncertainty, not by the raw
    # severity scale. Allocate the exact confidence cut proportionally so the
    # panel reconciles with the confidence formula.
    confidence_cut_total = -uncertainty * 28.0
    confidence_adjustments = {
        str(name): round(confidence_cut_total * float(value) / risk_total, 3)
        for name, value in risk_contributions.items()
    } if risk_total > 0 else {}
    learning_confidence_delta = _clamp(_num(learning_calibration.get("confidence_delta"), 0.0), -2.0, 2.0)
    if abs(learning_confidence_delta) >= 0.005:
        confidence_adjustments["學習成熟度"] = round(learning_confidence_delta, 3)
    p_up, p_neutral, p_down = _probabilities(effective, quality, conflict, uncertainty)

    if p_up >= 0.50 and (p_up - p_down) >= 0.15:
        label = "UP"
    elif p_down >= 0.50 and (p_down - p_up) >= 0.15:
        label = "DOWN"
    else:
        label = "NEUTRAL"

    strength = abs(effective) / 100.0
    confidence_components = {
        "基礎": 45.0,
        "方向強度": 38.0 * strength,
        "資料品質": 10.0 * (quality - 0.5),
        "多空衝突": -18.0 * conflict,
        "事件不確定性": -28.0 * uncertainty,
        "學習成熟度": learning_confidence_delta,
    }
    confidence = _clamp(sum(confidence_components.values()), 32.0, 88.0)
    expected_move_atr = _clamp(
        (effective / 100.0) * (0.65 + 0.15 * quality) * (1.0 - 0.40 * uncertainty),
        -0.80,
        0.80,
    )
    top = sorted(factor_contributions.items(), key=lambda item: abs(item[1]), reverse=True)[:5]
    reasons = [f"{name} {value:+.1f}" for name, value in top]
    reasons.extend(quantum.reasons[:3])
    if interaction_reasons:
        reasons.append("糾纏確認：" + ",".join(interaction_reasons[:2]))
    if conflict >= 0.45:
        reasons.append("多空家族衝突")
    if uncertainty >= 0.08:
        reasons.append("事件前不確定性降權")
    if quality < 0.60:
        reasons.append("資料品質降權")

    # Round first, then reconcile once more.  This guarantees that the values
    # persisted in Prediction DNA and rendered in the Quantum row sum to the
    # exact same two-decimal direction score (not merely the unrounded float).
    rounded_score = round(effective, 2)
    rounded_factors = {k: round(v, 3) for k, v in factor_contributions.items()}
    rounded_residual = round(rounded_score - sum(rounded_factors.values()), 3)
    if abs(rounded_residual) >= 0.001:
        rounded_factors["其他因子"] = round(rounded_factors.get("其他因子", 0.0) + rounded_residual, 3)

    return DirectionResult(
        label=label,
        score=rounded_score,
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
        family_contributions={k: round(v, 3) for k, v in family_contributions.items()},
        factor_contributions=rounded_factors,
        interaction_contribution=round(interaction_contribution, 3),
        uncertainty=round(uncertainty, 3),
        risk_factors=list(quantum.risk_factors),
        risk_contributions=risk_contributions,
        confidence_adjustments=confidence_adjustments,
        gate_state=_quantum_gate_state(p_up, p_neutral, p_down),
        confidence_components={k: round(v, 3) for k, v in confidence_components.items()},
        learning_calibration=dict(learning_calibration or {}),
    )

