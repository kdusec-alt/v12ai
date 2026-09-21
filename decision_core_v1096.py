# -*- coding: utf-8 -*-
"""V1093-V1096 single-owner public trade decision core.

The public UI must never arbitrate.  This module consumes typed facts once,
builds the recovery lifecycle, calls the executable price planner, and returns
one frozen DecisionSnapshot that can be rendered, logged, replayed and audited.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha1
import math
from types import MappingProxyType
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from decision_architecture_v1081 import assess_entry_opportunity
from evidence_arbitration_v1083 import _decisive_action, _entry_plan
from evidence_reasoning_v1082 import build_evidence_reasoning as build_narrative_evidence


SCHEMA = "TINO_DECISION_SNAPSHOT_V1096"
EVIDENCE_SCHEMA = "TINO_TYPED_EVIDENCE_V1094"
LIFECYCLE_SCHEMA = "TINO_RECOVERY_LIFECYCLE_V1095"
FUNNEL_SCHEMA = "TINO_ENTRY_FUNNEL_V1093"


def _num(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "--", "NA"):
            return None
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted(_freeze(item) for item in value))
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, float(value)))


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def _iso_date(value: Any) -> str:
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except Exception:
        return text


@dataclass(frozen=True)
class EvidenceFact:
    evidence_id: str
    family: str
    correlation_group: str
    label: str
    direction: int
    strength: int
    confidence: float
    value: Optional[float]
    unit: str
    source: str
    as_of: str
    freshness: str
    state: str
    accepted: bool
    verified: bool
    reason: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id, "family": self.family,
            "correlation_group": self.correlation_group, "label": self.label,
            "direction": self.direction, "strength": self.strength,
            "confidence": self.confidence, "value": self.value, "unit": self.unit,
            "source": self.source, "as_of": self.as_of, "freshness": self.freshness,
            "state": self.state, "accepted": self.accepted, "verified": self.verified,
            "reason": self.reason, "metadata": _thaw(self.metadata),
        }


@dataclass(frozen=True)
class FunnelStage:
    stage: str
    status: str
    reason: str
    evidence_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DecisionSnapshot:
    schema: str
    snapshot_id: str
    ticker: str
    market: str
    session_date: str
    action_code: str
    situation_code: str
    label: str
    icon: str
    color: str
    instruction: str
    reason: str
    entry: Mapping[str, Any]
    reasoning: Mapping[str, Any]
    evidence: Tuple[EvidenceFact, ...]
    lifecycle: Mapping[str, Any]
    funnel: Tuple[FunnelStage, ...]
    position_status: str = "UNKNOWN"
    average_cost: Optional[float] = None
    position_size: Optional[float] = None
    source_version: str = "V1096"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "snapshot_id": self.snapshot_id,
            "ticker": self.ticker,
            "market": self.market,
            "session_date": self.session_date,
            "action_code": self.action_code,
            "situation_code": self.situation_code,
            "label": self.label,
            "icon": self.icon,
            "color": self.color,
            "instruction": self.instruction,
            "reason": self.reason,
            "entry": _thaw(self.entry),
            "reasoning": _thaw(self.reasoning),
            "evidence": [item.to_dict() for item in self.evidence],
            "lifecycle": _thaw(self.lifecycle),
            "funnel": [item.to_dict() for item in self.funnel],
            "position_status": self.position_status,
            "average_cost": self.average_cost,
            "position_size": self.position_size,
            "source_version": self.source_version,
        }


def _position_context(forecast: Any) -> Dict[str, Any]:
    """Resolve explicit portfolio identity; never infer holdings from market data."""
    raw = getattr(forecast, "position_status", None)
    portfolio = getattr(forecast, "position", None)
    if isinstance(portfolio, Mapping):
        raw = portfolio.get("status", raw)
        average_cost = _num(portfolio.get("average_cost"))
        position_size = _num(portfolio.get("position_size"))
    else:
        average_cost = _num(getattr(forecast, "average_cost", None))
        position_size = _num(getattr(forecast, "position_size", None))
    status = str(raw or "UNKNOWN").upper()
    if status not in {"FLAT", "HOLDING", "UNKNOWN"}:
        status = "UNKNOWN"
    return {"status": status, "average_cost": average_cost, "position_size": position_size}


def _fact(
    *, family: str, group: str, label: str, direction: int, strength: float,
    confidence: float, value: Optional[float], unit: str, source: str,
    as_of: str, accepted: bool, reason: str, freshness: str = "latest",
    metadata: Optional[Mapping[str, Any]] = None,
) -> EvidenceFact:
    state = "ACCEPTED" if accepted else "UNKNOWN"
    return EvidenceFact(
        evidence_id=_stable_id(family, group, source, as_of, label, value),
        family=family,
        correlation_group=group,
        label=label,
        direction=1 if direction > 0 else -1 if direction < 0 else 0,
        strength=int(round(_clamp(strength))),
        confidence=round(_clamp(confidence), 2),
        value=value,
        unit=unit,
        source=str(source or "UNKNOWN"),
        as_of=_iso_date(as_of),
        freshness=freshness,
        state=state,
        accepted=bool(accepted),
        verified=bool(accepted),
        reason=str(reason or ""),
        metadata=_freeze(dict(metadata or {})),
    )


def build_typed_evidence(forecast: Any) -> Tuple[EvidenceFact, ...]:
    """Build facts from numeric fields; public prose is never parsed."""
    card = _mapping(getattr(forecast, "decision_card", {}))
    price = getattr(forecast, "price_frame", None)
    context = _mapping(getattr(price, "context", {}))
    if not context:
        context = _mapping(card.get("_structured_context_v1094"))
    ticker = getattr(forecast, "ticker", None)
    market = str(getattr(ticker, "market", "") or "").upper()
    as_of = str(card.get("價格日期") or card.get("資料日期") or "")
    truths = list(getattr(forecast, "data_truths", []) or [])
    if truths:
        as_of = str(getattr(truths[0], "date", "") or as_of)
    last = _num(card.get("現價"))
    previous = _num(card.get("昨收")) or _num(context.get("previous_close"))
    vwap = _num(card.get("VWAP")) or _num(context.get("vwap"))
    if price is not None:
        last = _num(getattr(price, "last", None)) or last
        previous = _num(getattr(price, "previous_close", None)) or previous
        vwap = _num(getattr(price, "vwap", None)) or vwap
    facts = []

    price_ok = bool(last and last > 0 and vwap and vwap > 0)
    price_gap = ((last / vwap) - 1.0) * 100.0 if price_ok else None
    day_return = ((last / previous) - 1.0) * 100.0 if last and previous else None
    price_direction = 1 if price_gap is not None and price_gap >= 0 else -1 if price_gap is not None else 0
    facts.append(_fact(
        family="price", group="price_structure", label="價格／VWAP結構",
        direction=price_direction, strength=58 + min(abs(price_gap or 0) * 6, 30),
        confidence=92 if price_ok else 0, value=price_gap, unit="pct_above_vwap",
        source=str(getattr(truths[0], "source", "PRICE_SSOT") if truths else "PRICE_SSOT"),
        as_of=as_of, accepted=price_ok,
        reason="使用同源數值價格與VWAP" if price_ok else "價格或VWAP未驗證",
        metadata={"last": last, "previous_close": previous, "vwap": vwap, "day_return_pct": day_return},
    ))

    raw = getattr(forecast, "raw", None)
    abc = _mapping(getattr(raw, "raw_abc", {}))
    a, b, c = _num(abc.get("A") or abc.get("a")), _num(abc.get("B") or abc.get("b")), _num(abc.get("C") or abc.get("c"))
    abc_ok = all(value is not None for value in (a, b, c))
    abc_direction = 1 if abc_ok and a > c else -1 if abc_ok and c > max(a, b) else 0
    facts.append(_fact(
        family="abc", group="forecast_path", label="ABC路徑情境",
        direction=abc_direction, strength=max(a or 0, b or 0, c or 0), confidence=85 if abc_ok else 0,
        value=c, unit="defense_probability_pct", source="direction_engine", as_of=as_of,
        accepted=abc_ok, reason="ABC僅提供路徑背景，不覆蓋Truth",
        metadata={"a": a, "b": b, "c": c},
    ))

    if market == "TW":
        margin = _mapping(context.get("margin"))
        margin_ok = bool(margin.get("accepted")) and _num(margin.get("margin")) is not None
        margin_1 = _num(margin.get("margin"))
        margin_5 = _num(margin.get("margin_5"))
        balance = _num(margin.get("margin_balance"))
        volume = _num(getattr(price, "volume", None)) if price is not None else _num(context.get("volume"))
        normalized = None
        norm_unit = "lots"
        if balance and balance > 0 and margin_5 is not None:
            normalized, norm_unit = margin_5 / balance * 100.0, "pct_of_margin_balance"
        elif volume and volume > 0 and margin_5 is not None:
            normalized, norm_unit = margin_5 * 1000.0 / volume * 100.0, "pct_of_session_volume"
        margin_direction = 1 if margin_ok and margin_5 is not None and margin_5 < 0 else -1 if margin_ok and margin_5 is not None and margin_5 > 0 else 0
        facts.append(_fact(
            family="leverage", group="tw_margin", label="融資去槓桿",
            direction=margin_direction, strength=50 + min(abs(normalized or 0) * 8, 40),
            confidence=88 if margin_ok else 0, value=normalized, unit=norm_unit,
            source=str(margin.get("source") or "UNKNOWN"), as_of=str(margin.get("date") or as_of),
            accepted=margin_ok, reason=str(margin.get("reason") or "融資數值未驗證"),
            metadata={"margin_1d_lots": margin_1, "margin_5d_lots": margin_5, "margin_balance_lots": balance},
        ))
        inst = _mapping(context.get("inst"))
        inst_ok = bool(inst.get("accepted")) and _num(inst.get("foreign")) is not None
        foreign_1, foreign_3 = _num(inst.get("foreign")), _num(inst.get("foreign_3"))
        decelerating = bool(foreign_1 is not None and foreign_3 is not None and foreign_1 < 0 and foreign_3 < 0 and abs(foreign_1) < abs(foreign_3) / 3.0)
        flow_direction = 1 if inst_ok and ((foreign_1 or 0) >= 0 or decelerating) else -1 if inst_ok else 0
        facts.append(_fact(
            family="chip", group="tw_institutional", label="法人／外資流向",
            direction=flow_direction, strength=62 + (12 if decelerating else min(abs(foreign_1 or 0) / 1000.0, 24)),
            confidence=88 if inst_ok else 0, value=foreign_1, unit="lots",
            source=str(inst.get("source") or "UNKNOWN"), as_of=str(inst.get("date") or as_of),
            accepted=inst_ok, reason=str(inst.get("reason") or "法人數值未驗證"),
            metadata={"foreign_3d_lots": foreign_3, "foreign_sell_decelerating": decelerating},
        ))
        market_heat = _mapping(context.get("market_heat"))
        heat_ok = bool(market_heat.get("accepted"))
        heat_change = _num(market_heat.get("change") or market_heat.get("margin_change"))
        facts.append(_fact(
            family="market", group="tw_market_leverage", label="市場融資熱度",
            direction=1 if heat_ok and heat_change is not None and heat_change < 0 else -1 if heat_ok and heat_change is not None and heat_change > 0 else 0,
            strength=55 + min(abs(heat_change or 0) / 5.0, 25), confidence=82 if heat_ok else 0,
            value=heat_change, unit="twd_100m", source=str(market_heat.get("source") or "UNKNOWN"),
            as_of=str(market_heat.get("date") or as_of), accepted=heat_ok,
            reason=str(market_heat.get("reason") or "市場融資熱度未驗證"),
        ))
    else:
        short = _mapping(context.get("short"))
        short_ok = bool(short.get("accepted")) and _num(short.get("short_float")) is not None
        short_float = _num(short.get("short_float"))
        facts.append(_fact(
            family="chip", group="us_short_interest", label="Short Float",
            direction=-1 if short_ok and (short_float or 0) >= 15 else 0,
            strength=55 + min(short_float or 0, 35), confidence=82 if short_ok else 0,
            value=short_float, unit="pct_float", source=str(short.get("source") or "UNKNOWN"),
            as_of=str(short.get("date") or as_of), accepted=short_ok,
            reason=str(short.get("reason") or "Short Float未驗證"),
        ))

    macro = _mapping(context.get("macro"))
    macro_ok = bool(macro.get("accepted"))
    cross_score = _num(macro.get("score") or macro.get("market_score") or macro.get("sox"))
    facts.append(_fact(
        family="market", group=f"{market.lower()}_cross_market", label="跨市場確認",
        direction=1 if macro_ok and (cross_score or 0) > 0 else -1 if macro_ok and (cross_score or 0) < 0 else 0,
        strength=55 + min(abs(cross_score or 0) * 3, 30), confidence=80 if macro_ok else 0,
        value=cross_score, unit="normalized_score", source=str(macro.get("source") or "MACRO_CONTEXT"),
        as_of=str(macro.get("date") or as_of), accepted=macro_ok,
        reason=str(macro.get("reason") or "跨市場數值未驗證"),
    ))

    news = list(getattr(forecast, "news_items", []) or [])
    scored_news = [item for item in news if _num(getattr(item, "score", None)) is not None]
    event_score = sum(_num(getattr(item, "score", 0)) or 0 for item in scored_news[:12])
    event_ok = bool(scored_news)
    facts.append(_fact(
        family="event", group="verified_event", label="事件催化",
        direction=1 if event_score > 0.15 else -1 if event_score < -0.15 else 0,
        strength=55 + min(abs(event_score) * 12, 38), confidence=78 if event_ok else 0,
        value=event_score if event_ok else None, unit="event_score", source="news_causal_intelligence",
        as_of=as_of, accepted=event_ok, reason="採用具分數與時間的事件物件" if event_ok else "沒有可驗證事件",
        metadata={"event_count": len(scored_news)},
    ))

    return tuple(facts)


def _family_vote(facts: Iterable[EvidenceFact], family: str, direction: int) -> Tuple[EvidenceFact, ...]:
    winners: Dict[str, EvidenceFact] = {}
    for fact in facts:
        if not fact.accepted or fact.family != family or fact.direction != direction:
            continue
        old = winners.get(fact.correlation_group)
        if old is None or fact.strength > old.strength:
            winners[fact.correlation_group] = fact
    return tuple(winners.values())


def _abc_from_facts(facts: Sequence[EvidenceFact]) -> Dict[str, Any]:
    fact = next((item for item in facts if item.family == "abc"), None)
    meta = dict(fact.metadata) if fact else {}
    return {"a": meta.get("a"), "b": meta.get("b"), "c": meta.get("c"), "text": "ABC只作路徑背景"}


def _prior_lifecycle(prior_snapshot: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    prior = _mapping(prior_snapshot)
    return _mapping(prior.get("lifecycle") or prior.get("public_lifecycle"))


def build_recovery_lifecycle(
    forecast: Any, entry: Mapping[str, Any], facts: Sequence[EvidenceFact],
    prior_snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    prior = _prior_lifecycle(prior_snapshot)
    prior_state = str(prior.get("state") or "UNINITIALIZED")
    state = str(entry.get("state") or "DATA_WAIT")
    deleveraging = bool(_family_vote(facts, "leverage", 1))
    price_fact = next((item for item in facts if item.family == "price"), None)
    price_meta = dict(price_fact.metadata) if price_fact else {}
    above_vwap = bool(price_fact and price_fact.accepted and price_fact.direction > 0)
    day_return = _num(price_meta.get("day_return_pct")) or _num(entry.get("operative_return_pct")) or 0.0
    invalid = _num(entry.get("invalidation_price") or entry.get("stop_price"))
    last = _num(entry.get("operative_price")) or _num(price_meta.get("last"))
    zone = _mapping(entry.get("low_entry_zone"))
    zone_hi = _num(zone.get("upper"))
    in_pullback = bool(last is not None and zone_hi is not None and last <= zone_hi and (invalid is None or last >= invalid))
    hard_failed = bool(state in {"SELLING_EXPANSION_BLOCK", "FAILED_BREAKOUT_EXIT"} or (last is not None and invalid is not None and last < invalid))
    confirmed = state == "BUY_TODAY_CONFIRM"

    next_state = "OBSERVING"
    transition = "INITIAL_OBSERVATION"
    sequence_verified = False
    if hard_failed:
        next_state, transition = "FAILED", "STRUCTURE_INVALIDATED"
    elif prior_state == "PULLBACK" and confirmed and above_vwap:
        next_state, transition, sequence_verified = "RECLAIMED", "PULLBACK_RECLAIMED", True
    elif prior_state in {"RECLAIMED", "ENTRY_ARMED"} and confirmed and above_vwap:
        next_state, transition, sequence_verified = "ENTRY_TRIGGERED", "CONFIRMATION_HELD", True
    elif prior_state in {"REBOUNDING", "STABILIZING", "DELEVERAGING"} and in_pullback:
        next_state, transition = "PULLBACK", "CONTROLLED_PULLBACK"
    elif above_vwap and day_return >= 1.0:
        next_state, transition = "REBOUNDING", "PRICE_REBOUND_CONFIRMED"
    elif deleveraging and state not in {"DATA_WAIT", "WAIT_NEXT_SESSION"}:
        next_state, transition = "DELEVERAGING", "LEVERAGE_CLEANUP"
    elif state in {"WAIT_VWAP_RECLAIM", "WAIT_RECLAIM_HOLD"}:
        next_state, transition = "STABILIZING", "SELLING_PRESSURE_STABILIZING"
    elif state in {"DATA_WAIT", "WAIT_NEXT_SESSION"}:
        next_state, transition = "UNKNOWN", "TRUTH_NOT_VERIFIED"

    if prior_state == "RECLAIMED" and next_state == "RECLAIMED":
        next_state, transition, sequence_verified = "ENTRY_ARMED", "RECLAIM_HELD_ONE_SESSION", True
    return {
        "schema": LIFECYCLE_SCHEMA,
        "state": next_state,
        "previous_state": prior_state,
        "transition": transition,
        "sequence_verified": sequence_verified,
        "deleveraging_verified": deleveraging,
        "price_above_vwap": above_vwap,
        "in_pullback_zone": in_pullback,
        "session_date": str(getattr(getattr(forecast, "data_truths", [None])[0], "date", "") or ""),
        "write_policy": "PREDICTION_LOG_ONLY",
        "public_runtime_write": False,
    }


def _structured_gate(
    forecast: Any, entry: Mapping[str, Any], facts: Sequence[EvidenceFact], lifecycle: Mapping[str, Any]
) -> Dict[str, Any]:
    card = _mapping(getattr(forecast, "decision_card", {}))
    last = _num(entry.get("operative_price")) or _num(card.get("現價"))
    t1 = _num(getattr(forecast, "final_t1", None))
    t1_return = ((t1 / last) - 1.0) * 100.0 if t1 is not None and last else None
    abc = _abc_from_facts(facts)
    a, b, c = _num(abc.get("a")), _num(abc.get("b")), _num(abc.get("c"))
    chip_bear = sum(item.strength for item in _family_vote(facts, "chip", -1))
    chip_bull = sum(item.strength for item in _family_vote(facts, "chip", 1))
    event_bear = any(item.strength >= 78 for item in _family_vote(facts, "event", -1))
    positive_event = bool(_family_vote(facts, "event", 1))
    positive_market = bool(_family_vote(facts, "market", 1))
    deleveraging = bool(_family_vote(facts, "leverage", 1))
    price_above_vwap = bool(lifecycle.get("price_above_vwap"))
    day_return = next((_num(item.metadata.get("day_return_pct")) for item in facts if item.family == "price"), None) or 0.0
    source_groups = {
        item.correlation_group for item in facts
        if item.accepted and item.direction > 0 and item.family in {"leverage", "event", "market", "chip"}
    }
    recovery_confirmation_count = len(source_groups)
    state = str(entry.get("state") or "DATA_WAIT")
    # Left-side low entry is a separate execution lane.  It requires a positive
    # forward edge, a pullback-dominant path and verified institutional support;
    # it does not require price to be green or already above VWAP.
    left_low_candidate = bool(
        state == "WAIT_VWAP_RECLAIM"
        and t1_return is not None and t1_return > 0
        and -4.0 < day_return <= 0.5
        and b is not None and b >= 40
        and (c is None or c <= 25)
        and chip_bull >= 55 and chip_bear < 78 and not event_bear
    )
    controlled_low = bool(
        (
            t1_return is not None and -0.8 < t1_return <= 0
            and b is not None and b >= 45 and (c is None or c <= 30)
        )
        or left_low_candidate
    ) and chip_bear < 78 and not event_bear
    recovery_candidate = bool(
        deleveraging and t1_return is not None and -2.5 <= t1_return <= 0.8
        and day_return >= 1.0 and price_above_vwap and b is not None and b >= 45
        and (c is None or c <= 30) and chip_bear < 78 and not event_bear
        and recovery_confirmation_count >= 2
    )
    recovery_setup = bool(recovery_candidate and lifecycle.get("sequence_verified"))
    reasons = []
    allow_immediate = True
    allow_pullback = True
    allow_breakout = True
    if state in {"DATA_WAIT", "WAIT_NEXT_SESSION"}:
        allow_immediate = allow_pullback = allow_breakout = False
        reasons.append("Session或同源價格未完成驗證")
    if t1_return is not None and t1_return <= 0:
        allow_immediate = False
        reasons.append(f"T1預期報酬 {t1_return:+.2f}% 未轉正")
    if c is not None and c >= 35:
        allow_immediate = allow_pullback = allow_breakout = False
        reasons.append(f"ABC防守情境 {c:.0f}% 過高")
    if event_bear:
        allow_immediate = False
        reasons.append("重大負面事件尚未被價格吸收")
    if chip_bear >= 78 and chip_bull == 0:
        allow_immediate = False
        reasons.append("法人／籌碼偏空尚未改善")
    if recovery_candidate and not recovery_setup:
        allow_immediate = False
        allow_breakout = False
        reasons.append("修復條件已形成，但跨Session回測收復順序尚未完成")
    if recovery_setup or controlled_low:
        allow_immediate = False
        allow_pullback = True
        allow_breakout = False
    if left_low_candidate:
        reasons.append("左側低接候選：T1轉正、回測情境主導且法人支持；只在止跌回升後試單")
    if state in {"OVERHEATED_NO_CHASE", "LIMIT_LIQUIDITY_WAIT"}:
        allow_immediate = allow_pullback = allow_breakout = False
        reasons.append("過熱或流動性限制，當日不追價")
    qualified = bool(allow_immediate or allow_pullback or allow_breakout)
    if not qualified:
        code = "DATA_BLOCK" if state in {"DATA_WAIT", "WAIT_NEXT_SESSION"} else "NO_ENTRY_ABC" if c is not None and c >= 35 else "NO_ENTRY_CONFLICT"
    elif recovery_setup:
        code = "RECOVERY_SETUP"
    elif controlled_low:
        code = "CONTROLLED_LOW_TRADE"
    elif not allow_immediate:
        code = "CONDITIONAL_ONLY"
    else:
        code = "ENTRY_QUALIFIED"
    label = {
        "DATA_BLOCK": "資料未驗證，禁止進場", "NO_ENTRY_ABC": "ABC防守過高，本日無買點",
        "NO_ENTRY_CONFLICT": "跨模組衝突，本日無買點", "RECOVERY_SETUP": "修復布局｜回測確認首倉",
        "CONTROLLED_LOW_TRADE": "可以交易｜小倉試單", "CONDITIONAL_ONLY": "僅條件式買進",
        "ENTRY_QUALIFIED": "買進資格通過",
    }[code]
    return {
        "schema": "TINO_STRUCTURED_TRADE_GATE_V1094", "code": code, "label": label,
        "entry_qualified": qualified, "allow_immediate_buy": allow_immediate,
        "allow_pullback": allow_pullback, "allow_breakout": allow_breakout, "reasons": reasons,
        "t1_price": t1, "t1_return_pct": t1_return, "abc_a": a, "abc_b": b, "abc_c": c,
        "chip_bear_strength": chip_bear, "chip_bull_strength": chip_bull,
        "event_bearish_veto": event_bear, "controlled_low_trade": controlled_low,
        "left_low_candidate": left_low_candidate,
        "recovery_candidate": recovery_candidate, "recovery_setup": recovery_setup,
        "recovery_confirmation_count": recovery_confirmation_count,
        "recovery_confirmation_groups": sorted(source_groups),
        "deleveraging_evidence": deleveraging, "positive_event": positive_event,
        "positive_market": positive_market, "rebound_monitor": bool(day_return >= 3 and price_above_vwap),
        "day_return_pct": day_return, "price_above_vwap": price_above_vwap,
        "trade_level": "RECOVERY_SETUP" if recovery_candidate else "TRADEABLE" if controlled_low else "REBOUND_MONITOR" if day_return >= 3 and price_above_vwap else "LOW_MONITOR" if t1_return is not None and t1_return <= 0 else "TREND_CONFIRMED",
        "source": "V1094 Typed Evidence + V1095 Lifecycle",
    }


def _drivers(facts: Sequence[EvidenceFact]) -> list[Dict[str, Any]]:
    rows = []
    for fact in facts:
        rows.append({
            "category": fact.family, "label": fact.label, "stance_value": fact.direction,
            "stance": "偏多" if fact.direction > 0 else "偏空" if fact.direction < 0 else "中性",
            "strength": fact.strength, "verified": fact.verified,
            "text": fact.reason, "source": fact.source, "evidence_id": fact.evidence_id,
            "correlation_group": fact.correlation_group,
            "stars": "★" * max(1, min(5, math.ceil(fact.strength / 20))) + "☆" * max(0, 5 - max(1, min(5, math.ceil(fact.strength / 20)))),
        })
    return sorted(rows, key=lambda item: (item["strength"], item["verified"]), reverse=True)


def _acceptance(entry: Mapping[str, Any], facts: Sequence[EvidenceFact]) -> Dict[str, Any]:
    score = 50
    price = next((item for item in facts if item.family == "price"), None)
    if price and price.accepted:
        # VWAP is one price-location fact, not three independent confirmations.
        score += 10 if price.direction > 0 else -10 if price.direction < 0 else 0
    state = str(entry.get("state") or "")
    score += {"BUY_TODAY_CONFIRM": 6, "WAIT_VWAP_PULLBACK": 3, "WAIT_VWAP_RECLAIM": 0, "SELLING_EXPANSION_BLOCK": -25, "FAILED_BREAKOUT_EXIT": -30}.get(state, 0)
    # Price/VWAP was already counted above.  Excluding it here prevents the
    # same green/red location from being counted a second time as evidence.
    positive = sum(1 for item in facts if item.family != "price" and item.accepted and item.direction > 0)
    negative = sum(1 for item in facts if item.family != "price" and item.accepted and item.direction < 0)
    score = int(round(_clamp(score + min(positive, 3) * 3 - min(negative, 3) * 3)))
    grade = "已形成" if score >= 72 else "待加強" if score >= 55 else "尚未完成" if score >= 40 else "明顯不足"
    return {"code": "STRUCTURED_ACCEPTANCE", "title": "進場品質", "score": score, "grade": grade, "label": f"進場品質 {score}%｜{grade}"}


def _funnel(
    entry: Mapping[str, Any], facts: Sequence[EvidenceFact], gate: Mapping[str, Any],
    lifecycle: Mapping[str, Any], plan: Mapping[str, Any], acceptance: Mapping[str, Any],
) -> Tuple[FunnelStage, ...]:
    def stage(name: str, passed: Optional[bool], reason: str, ids: Iterable[str] = ()) -> FunnelStage:
        return FunnelStage(name, "PASS" if passed is True else "FAIL" if passed is False else "UNKNOWN", reason, tuple(ids))
    leverage = [item for item in facts if item.family == "leverage"]
    event = [item for item in facts if item.family == "event"]
    chip = [item for item in facts if item.family == "chip"]
    return (
        stage("truth", str(entry.get("state")) not in {"DATA_WAIT", "WAIT_NEXT_SESSION"}, "Session與價格真實性"),
        stage("deleveraging", bool(gate.get("deleveraging_evidence")) if leverage else None, "融資清洗必須來自數值欄位", (x.evidence_id for x in leverage)),
        stage("entry_path", bool(gate.get("price_above_vwap") or gate.get("left_low_candidate")), "右側站回VWAP，或左側低接條件完整"),
        stage("abc_risk", (_num(gate.get("abc_c")) or 0) <= 30 if gate.get("abc_c") is not None else None, "ABC防守受控"),
        stage("independent_confirmation", int(gate.get("recovery_confirmation_count") or 0) >= 2, "至少兩個獨立相關性群組"),
        stage("event_veto", not bool(gate.get("event_bearish_veto")), "無重大負面事件否決", (x.evidence_id for x in event)),
        stage("chip_veto", int(gate.get("chip_bear_strength") or 0) < 78, "無強空籌碼否決", (x.evidence_id for x in chip)),
        stage("lifecycle_sequence", bool(lifecycle.get("sequence_verified")), "跨Session完成反彈、回測、收復"),
        stage("price_order", bool(plan.get("price_order_valid")), "現價、觸發、突破與失效價順序有效"),
        stage("acceptance", int(acceptance.get("score") or 0) >= 55, "證據接受度至少55分"),
    )


def load_prior_public_snapshot(ticker: str, session_date: str = "") -> Dict[str, Any]:
    """Read-only history lookup. Public runtime never writes lifecycle state."""
    try:
        from memory_store import read_prediction_log
        for row in reversed(read_prediction_log(800)):
            if str(row.get("ticker") or "") != str(ticker or ""):
                continue
            snap = row.get("public_decision_snapshot")
            if not isinstance(snap, Mapping):
                continue
            row_date = str(snap.get("session_date") or row.get("run_date_tw") or row.get("target_trade_date") or "")
            if session_date and row_date >= str(session_date):
                continue
            return dict(snap)
    except Exception:
        pass
    return {}


def build_decision_snapshot(
    forecast: Any, *, prior_snapshot: Optional[Mapping[str, Any]] = None
) -> DecisionSnapshot:
    ticker = getattr(forecast, "ticker", None)
    symbol = str(getattr(ticker, "resolved_symbol", "") or "")
    market = str(getattr(ticker, "market", "") or "")
    truths = list(getattr(forecast, "data_truths", []) or [])
    session_date = str(getattr(truths[0], "date", "") or "") if truths else ""
    if prior_snapshot is None:
        prior_snapshot = load_prior_public_snapshot(symbol, session_date)
    entry = dict(assess_entry_opportunity(forecast) or {})
    facts = build_typed_evidence(forecast)
    lifecycle = build_recovery_lifecycle(forecast, entry, facts, prior_snapshot)
    gate = _structured_gate(forecast, entry, facts, lifecycle)
    plan = _entry_plan(forecast, entry, gate)
    price_frame = getattr(forecast, "price_frame", None)
    atr14 = _num(getattr(price_frame, "atr14", None)) if price_frame is not None else None
    recent_lows = list(getattr(price_frame, "recent_lows", []) or []) if price_frame is not None else []
    local_low_20 = min((_num(value) for value in recent_lows[-20:] if _num(value) is not None), default=None)
    current_price = _num(plan.get("current_price"))
    plan["atr14"] = atr14
    plan["local_low_20"] = local_low_20
    plan["distance_from_local_low_atr"] = (
        round((current_price - local_low_20) / atr14, 4)
        if current_price is not None and local_low_20 is not None and atr14 and atr14 > 0 else None
    )
    acceptance = _acceptance(entry, facts)
    drivers = _drivers(facts)
    action = _decisive_action(entry, plan, acceptance, drivers, gate, forecast)
    position = _position_context(forecast)
    if position["status"] == "UNKNOWN" and action.get("code") == "REDUCE":
        action = dict(action)
        action.update({
            "code": "HOLD",
            "situation_code": "HOLD_POSITION_UNKNOWN",
            "exit_basis": "POSITION_UNKNOWN_CONDITIONAL_ONLY",
            "label": "條件式風險觀察",
            "instruction": f"部位資料未知｜若有持股才依 {plan.get('invalidation_price') or '--'} 管理；空手不進場",
            "reason": f"{action.get('reason')}；因未取得持股身分，不輸出立即減碼",
            "position_guard_applied": True,
        })
    funnel = _funnel(entry, facts, gate, lifecycle, plan, acceptance)
    blockers = [item for item in funnel if item.status in {"FAIL", "UNKNOWN"}]
    narrative = dict(build_narrative_evidence(forecast, entry) or {})
    top = drivers[:3]
    top_summary = "｜".join(f"{i} {row['label']} {row['stance']}：{row['text']}" for i, row in enumerate(top, 1)) or "有效證據不足"
    reasoning = {
        **narrative,
        "schema": "TINO_PUBLIC_REASONING_V1096",
        "headline": narrative.get("headline") or "AI交易決策",
        "one_line_conclusion": f"最終決策：{action.get('label')}｜{action.get('instruction')}。主因：{action.get('reason')}。",
        "decision_message": f"最終決策：{action.get('label')}｜{action.get('instruction')}。主因：{action.get('reason')}。",
        "recommended_entry": dict(plan),
        "action_decision": dict(action),
        "cross_module_gate": dict(gate),
        "price_acceptance": dict(acceptance),
        "top_drivers": top,
        "top_driver_summary": top_summary,
        "abc_context": _abc_from_facts(facts),
        "quantum_context": narrative.get("quantum_context") or {},
        "entry_funnel": [item.to_dict() for item in funnel],
        "blocking_stage": blockers[0].stage if blockers else None,
        "position_context": dict(position),
        "decision_influence": True,
        "formal_price_model_unchanged": True,
    }
    snapshot_id = _stable_id(SCHEMA, symbol, session_date, action.get("code"), lifecycle.get("state"), plan.get("current_price"), plan.get("confirmation_price"))
    return DecisionSnapshot(
        schema=SCHEMA, snapshot_id=snapshot_id, ticker=symbol, market=market,
        session_date=session_date, action_code=str(action.get("code") or "BLOCK"),
        situation_code=str(action.get("situation_code") or "BLOCK_DATA"),
        label=str(action.get("label") or "禁止進場"), icon=str(action.get("icon") or "🔴"),
        color=str(action.get("color") or "red"), instruction=str(action.get("instruction") or "禁止進場"),
        reason=str(action.get("reason") or "資料未完成"), entry=_freeze(plan), reasoning=_freeze(reasoning),
        evidence=tuple(facts), lifecycle=_freeze(lifecycle), funnel=funnel,
        position_status=position["status"], average_cost=position["average_cost"],
        position_size=position["position_size"],
    )


def blocked_decision_snapshot(forecast: Any, reason: str) -> DecisionSnapshot:
    ticker = getattr(forecast, "ticker", None)
    symbol = str(getattr(ticker, "resolved_symbol", "") or "")
    market = str(getattr(ticker, "market", "") or "")
    funnel = (FunnelStage("truth", "FAIL", reason),)
    return DecisionSnapshot(
        schema=SCHEMA, snapshot_id=_stable_id(SCHEMA, symbol, reason), ticker=symbol,
        market=market, session_date="", action_code="BLOCK", situation_code="BLOCK_DATA",
        label="禁止進場", icon="🔴", color="red", instruction="禁止進場｜不建立新部位",
        reason=reason, entry=_freeze({}), reasoning=_freeze({"headline": "資料待確認", "decision_message": reason,
        "one_line_conclusion": reason, "recommended_entry": {}, "action_decision": {"code": "BLOCK", "label": "禁止進場", "instruction": "禁止進場｜不建立新部位", "reason": reason},
        "top_drivers": [], "top_driver_summary": "有效證據不足", "price_acceptance": {}, "cross_module_gate": {}},
        ), evidence=(), lifecycle=_freeze({"schema": LIFECYCLE_SCHEMA, "state": "UNKNOWN", "sequence_verified": False}), funnel=funnel,
    )
