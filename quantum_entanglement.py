# -*- coding: utf-8 -*-
"""TINO V12.2 adaptive evidence and bounded interaction engine.

The right-side radar is an evidence layer, not decoration.  This module turns
verified radar data into short-horizon direction families while enforcing four
rules:

1. Event catalysts decay by *trading session* (monthly revenue / earnings only
   affect the announcement session and the next trading session).
2. Correlated proxies are grouped once (SOX+SMH, NQ+QQQ) to avoid double count.
3. Financing, policy and geopolitical data are interpreted in context instead
   of receiving a permanent bullish/bearish sign.
4. Interactions are bounded confirmations, never a second copy of a signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import math
import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from models import NewsItem, PriceFrame, SignalPacket
from macro_event_calendar import event_risk_from_context


@dataclass(frozen=True)
class QuantumEvidence:
    profile: str
    families: Dict[str, Tuple[float, float, bool]]
    uncertainty: float
    reasons: List[str]
    family_components: Dict[str, Dict[str, float]] = field(default_factory=dict)
    risk_factors: List[str] = field(default_factory=list)

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        x = float(str(value).replace(",", "").replace("%", "").strip())
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _maybe_num(value: Any) -> float | None:
    if value in (None, "", "NA", "待同步"):
        return None
    try:
        x = float(str(value).replace(",", "").replace("%", "").strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _tanh100(value: float) -> float:
    return math.tanh(float(value)) * 100.0


def _accepted(block: Mapping[str, Any] | None) -> bool:
    if not isinstance(block, Mapping):
        return False
    source = str(block.get("source") or "").upper()
    return bool(block.get("accepted")) and not any(
        token in source for token in ("SAMPLE", "MOCK", "FALLBACK", "PENDING")
    )


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _reference_date(price: PriceFrame) -> date:
    return (
        _parse_date(price.price_date)
        or _parse_date(getattr(price.truth, "date", ""))
        or date.today()
    )


def _business_session_age(event_date: Any, reference: date) -> int | None:
    start = _parse_date(event_date)
    if not start or start > reference:
        return None
    sessions = 0
    cur = start
    while cur < reference and sessions <= 30:
        cur = cur.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            sessions += 1
    return sessions


def _block_is_fresh(block: Mapping[str, Any], reference: date, max_sessions: int = 2) -> bool:
    raw_date = block.get("date") or block.get("data_date") or block.get("session_date")
    if not raw_date:
        return True
    age = _business_session_age(raw_date, reference)
    return age is not None and age <= max_sessions


def _proxy_value(
    macro: Mapping[str, Any],
    key: str,
    reference: date,
    *,
    max_sessions: int = 3,
) -> float | None:
    value = _maybe_num(macro.get(key))
    if value is None:
        return None
    as_of = macro.get("as_of")
    stamp = as_of.get(key) if isinstance(as_of, Mapping) else None
    if stamp:
        age = _business_session_age(stamp, reference)
        if age is None or age > max_sessions:
            return None
    return value


def _news_time(item: NewsItem | Mapping[str, Any]) -> str:
    return str(item.get("time") if isinstance(item, Mapping) else getattr(item, "time", "") or "")


def _news_title(item: NewsItem | Mapping[str, Any]) -> str:
    return str(item.get("title") if isinstance(item, Mapping) else getattr(item, "title", "") or "")


def _news_tag(item: NewsItem | Mapping[str, Any]) -> str:
    return str(item.get("tag") if isinstance(item, Mapping) else getattr(item, "tag", "") or "")


def _news_score(item: NewsItem | Mapping[str, Any]) -> float:
    raw = item.get("score") if isinstance(item, Mapping) else getattr(item, "score", 0.0)
    return _num(raw, 0.0)


def _news_age_hours(item: NewsItem | Mapping[str, Any], reference: date) -> float | None:
    text = _news_time(item).strip()
    if text.lower() in {"sample", "latest", "待同步", ""}:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        naive = dt.replace(tzinfo=None)
        ref_end = datetime.combine(reference, datetime.max.time())
        return max(0.0, (ref_end - naive).total_seconds() / 3600.0)
    except Exception:
        parsed = _parse_date(text)
        if not parsed:
            return None
        return float(max((reference - parsed).days, 0) * 24)


def _average_volume_lots(price: PriceFrame) -> float:
    values = [_num(v) for v in (price.recent_volumes or []) if _num(v) > 0]
    if not values and _num(price.volume) > 0:
        values = [_num(price.volume)]
    if not values:
        return 1.0
    avg = sum(values[-20:]) / len(values[-20:])
    if avg >= 100_000:
        avg /= 1000.0
    return max(avg, 1.0)


def _horizon_flow(block: Mapping[str, Any], key: str, avg_lots: float) -> Tuple[float, bool]:
    parts: List[Tuple[float, float]] = []
    for suffix, days, weight in (
        ("", 1, 0.34),
        ("_3", 3, 0.22),
        ("_5", 5, 0.25),
        ("_10", 10, 0.19),
    ):
        raw = _maybe_num(block.get(f"{key}{suffix}"))
        if raw is None:
            continue
        per_day_ratio = (raw / days) / max(avg_lots, 1.0)
        parts.append((_tanh100(per_day_ratio / 0.035), weight))
    total = sum(weight for _, weight in parts)
    if total <= 0:
        return 0.0, False
    return sum(value * weight for value, weight in parts) / total, True


def _streak(block: Mapping[str, Any], key: str) -> Tuple[int, int]:
    text = str(block.get(f"{key}_streak") or "")
    match = re.search(r"連(增|減)\s*(\d+)\s*天", text)
    if not match:
        return 0, 0
    sign = 1 if match.group(1) == "增" else -1
    return sign, max(int(match.group(2)), 1)


def sector_profile(price: PriceFrame) -> str:
    symbol = str(price.ticker.resolved_symbol or "").upper()
    name = str(price.ticker.name or "")
    persona = (price.context or {}).get("persona")
    persona_text = (
        " ".join(str(v) for v in persona.values())
        if isinstance(persona, Mapping)
        else str(persona or "")
    )
    blob = f"{symbol} {name} {persona_text}".upper()
    code = symbol.split(".")[0]
    memory_codes = {
        "2408", "2344", "2337", "8299", "3260", "5351", "4967",
        "2451", "6770", "2349",
    }
    memory_words = (
        "記憶體", "DRAM", "NAND", "HBM", "MICRON", "南亞科", "華邦",
        "旺宏", "群聯", "力積電", "威剛", "創見", "十銓", "宇瞻", "MEMORY",
    )
    if code in memory_codes or any(word.upper() in blob for word in memory_words):
        return "memory"
    if any(word in blob for word in (
        "SEMICONDUCTOR", "半導體", "晶圓", "IC設計", "台積電", "聯發科",
        "聯電", "世界先進", "日月光", "SOXX", "SMH",
    )):
        return "semiconductor"
    if any(word in blob for word in ("DEFENSE", "DRONE", "AEROSPACE", "國防", "無人機", "軍工")):
        return "defense"
    if any(word in blob for word in ("FINANCIAL", "BANK", "INSURANCE", "金融", "銀行", "保險")):
        return "financial"
    return "broad"


def _tw_fundamental_event(price: PriceFrame) -> Tuple[float, bool, str]:
    block = (price.context or {}).get("fundamental")
    if not isinstance(block, Mapping) or not _accepted(block):
        return 0.0, False, ""
    if not bool(block.get("revenue_model_usable", block.get("revenue_verified", False))):
        return 0.0, False, ""
    if bool(block.get("revenue_month_anchor_risk")):
        return 0.0, False, ""

    age = _business_session_age(block.get("announcement_date"), _reference_date(price))
    # Tino contract: announcement session + next trading session only.
    if age is None or age > 1:
        return 0.0, False, ""
    decay = (1.0, 0.55)[age]

    yoy = _maybe_num(block.get("yoy"))
    mom = _maybe_num(block.get("mom"))
    accum_yoy = _maybe_num(block.get("accum_yoy"))
    parts: List[Tuple[float, float]] = []
    if yoy is not None:
        parts.append((_tanh100(yoy / 30.0), 0.55))
    if mom is not None:
        parts.append((_tanh100(mom / 15.0), 0.25))
    if accum_yoy is not None:
        parts.append((_tanh100(accum_yoy / 28.0), 0.20))
    if not parts:
        return 0.0, False, ""
    score = sum(value * weight for value, weight in parts) / sum(weight for _, weight in parts)

    # Mixed growth is not a one-way catalyst.
    signs = [math.copysign(1, value) for value in (yoy, mom, accum_yoy) if value not in (None, 0)]
    if signs and min(signs) < 0 < max(signs):
        score *= 0.62

    quality = str(block.get("revenue_quality") or "").lower()
    score *= 1.0 if quality in {"official", "cross_checked"} else 0.82

    # Avoid converting already-realised gap/run into a chase signal.
    last = _num(price.last)
    prev = _num(price.previous_close, last)
    atr = max(_num(price.atr14), last * 0.012, 0.01)
    realised_atr = (last - prev) / atr
    if score * realised_atr > 0 and abs(realised_atr) >= 1.2:
        score *= 0.62

    score = _clamp(score * decay, -72.0, 72.0)
    return score, True, f"月營收催化第{age + 1}交易日（只計兩日）"


def _us_fundamental_event(
    price: PriceFrame,
    news_items: Sequence[NewsItem | Mapping[str, Any]],
) -> Tuple[float, bool, str]:
    ref = _reference_date(price)
    keys = (
        "earnings", "revenue", "guidance", "財報", "營收", "財測", "eps",
        "beat", "miss", "raises outlook", "cuts outlook",
    )
    scored: List[float] = []
    youngest: float | None = None
    for item in news_items or []:
        text = f"{_news_title(item)} {_news_tag(item)}".lower()
        if not any(key in text for key in keys):
            continue
        age_h = _news_age_hours(item, ref)
        if age_h is None or age_h > 48:
            continue
        decay = 1.0 if age_h <= 24 else 0.45
        raw = _clamp(_news_score(item) * 260.0, -72.0, 72.0)
        if abs(raw) < 5.0:
            continue
        scored.append(raw * decay)
        youngest = age_h if youngest is None else min(youngest, age_h)
    if scored:
        return (
            _clamp(sum(scored) / len(scored), -72.0, 72.0),
            True,
            f"財報/指引催化 {youngest:.0f}小時（只計兩日）",
        )

    block = (price.context or {}).get("fundamental")
    if not isinstance(block, Mapping) or not _accepted(block):
        return 0.0, False, ""
    age = _business_session_age(block.get("earnings_event_date"), ref)
    if age is None or age > 1:
        return 0.0, False, ""
    qoq = _maybe_num(block.get("qoq"))
    yoy = _maybe_num(block.get("yoy"))
    parts: List[Tuple[float, float]] = []
    if qoq is not None:
        parts.append((_tanh100(qoq / 42.0), 0.52))
    if yoy is not None:
        parts.append((_tanh100(yoy / 34.0), 0.48))
    if not parts:
        return 0.0, False, ""
    score = sum(value * weight for value, weight in parts) / sum(weight for _, weight in parts)
    score *= (1.0, 0.50)[age]
    return _clamp(score, -68.0, 68.0), True, f"美股財報催化第{age + 1}交易日（只計兩日）"


def _group_average(values: Sequence[Tuple[float | None, float]]) -> float | None:
    valid = [(value, weight) for value, weight in values if value is not None and weight > 0]
    if not valid:
        return None
    return sum(float(value) * weight for value, weight in valid) / sum(weight for _, weight in valid)


def _overnight_family(
    price: PriceFrame,
    profile: str,
) -> Tuple[float, bool, str, Dict[str, float]]:
    macro = (price.context or {}).get("macro")
    if not isinstance(macro, Mapping) or not bool(macro.get("accepted")):
        return 0.0, False, "", {}
    ref = _reference_date(price)

    # Correlated symbols form one proxy family each. SOX+SMH and NQ+QQQ can
    # improve robustness but never receive two separate family weights.
    semiconductor = _group_average((
        (_proxy_value(macro, "sox", ref), 0.60),
        (_proxy_value(macro, "smh", ref), 0.40),
    ))
    nasdaq = _group_average((
        (_proxy_value(macro, "nq", ref), 0.62),
        (_proxy_value(macro, "qqq", ref), 0.38),
    ))
    groups = {
        "tx": _proxy_value(macro, "tx_night", ref, max_sessions=2),
        "semi": semiconductor,
        "nasdaq": nasdaq,
        "memory": _proxy_value(macro, "mu", ref),
        "tsm": _proxy_value(macro, "tsm_adr", ref),
        "vix": _proxy_value(macro, "vix_change", ref),
    }

    market = str(price.ticker.market or "").upper()
    if market == "TW":
        if profile == "memory":
            weights = {"tx": 0.22, "memory": 0.30, "semi": 0.25, "nasdaq": 0.13, "tsm": 0.10}
        elif profile == "semiconductor":
            weights = {"tx": 0.25, "semi": 0.32, "tsm": 0.18, "nasdaq": 0.15, "memory": 0.10}
        else:
            weights = {"tx": 0.45, "nasdaq": 0.30, "semi": 0.12, "tsm": 0.08, "vix": 0.05}
    else:
        if profile == "memory":
            weights = {"memory": 0.35, "semi": 0.30, "nasdaq": 0.22, "vix": 0.13}
        elif profile == "semiconductor":
            weights = {"semi": 0.40, "nasdaq": 0.28, "memory": 0.12, "tsm": 0.08, "vix": 0.12}
        else:
            weights = {"nasdaq": 0.55, "semi": 0.20, "vix": 0.25}

    transformed_parts: List[Tuple[str, float, float]] = []
    for name, weight in weights.items():
        value = groups.get(name)
        if value is None:
            continue
        transformed = -_tanh100(value / 8.0) if name == "vix" else _tanh100(value / 2.4)
        transformed_parts.append((name, transformed, weight))
    if not transformed_parts:
        return 0.0, False, "", {}

    total_weight = sum(weight for _, _, weight in transformed_parts)
    components = {
        name: transformed * weight / total_weight
        for name, transformed, weight in transformed_parts
    }
    score = sum(components.values())
    labels = {
        "tx": "台指夜盤", "semi": "費半", "nasdaq": "那指",
        "memory": "MU", "tsm": "TSM ADR", "vix": "VIX",
    }
    labelled_components = {labels[name]: value for name, value in components.items()}
    used = [labels[name] for name, _, _ in transformed_parts]
    return (
        _clamp(score, -100.0, 100.0),
        True,
        "跨市場：" + "/".join(used[:4]),
        labelled_components,
    )

def _tw_leverage_family(
    price: PriceFrame,
    trend: float,
    intraday: float,
    flow: float,
) -> Tuple[float, bool, str]:
    margin = (price.context or {}).get("margin")
    if not isinstance(margin, Mapping) or not _accepted(margin):
        return 0.0, False, ""
    if not _block_is_fresh(margin, _reference_date(price), 2):
        return 0.0, False, ""
    raw, ok = _horizon_flow(margin, "margin", _average_volume_lots(price))
    if not ok:
        return 0.0, False, ""

    structure = trend * 0.45 + intraday * 0.35 + flow * 0.20
    streak_sign, streak_days = _streak(margin, "margin")
    streak_amp = 1.0 + min(max(streak_days - 1, 0), 4) * 0.12

    if raw > 0:
        # Financing accumulation is most dangerous when price and institutions
        # are weak. In a confirmed strong trend it is only a caution flag.
        factor = 1.0 if structure < -12 else 0.62 if structure < 12 else 0.25
        if streak_sign > 0:
            factor *= streak_amp
        score = -abs(raw) * factor
        label = f"融資連增{streak_days}日風險" if streak_sign > 0 else "融資增加風險"
    else:
        # Deleveraging is healthy only when structure is not collapsing.
        factor = 0.55 if structure >= -5 else -0.18
        if streak_sign < 0 and factor > 0:
            factor *= min(streak_amp, 1.35)
        score = abs(raw) * factor
        label = f"融資連減{streak_days}日清洗" if factor > 0 and streak_sign < 0 else (
            "融資下降清洗" if factor > 0 else "融資下降去槓桿"
        )
    return _clamp(score, -82.0, 62.0), True, label


def _tw_market_heat_family(
    price: PriceFrame,
    trend: float,
    intraday: float,
    flow: float,
) -> Tuple[float, bool, str]:
    heat = (price.context or {}).get("market_heat")
    if not isinstance(heat, Mapping) or not bool(heat.get("accepted")):
        return 0.0, False, ""
    if not _block_is_fresh(heat, _reference_date(price), 2):
        return 0.0, False, ""
    risk = _num(heat.get("risk_score"), 0.0)
    change = _num(heat.get("change_yi"), 0.0)
    structure = trend * 0.40 + intraday * 0.30 + flow * 0.30
    base = -_tanh100(max(risk - 45.0, 0.0) / 32.0)
    change_component = -_tanh100(change / 95.0)
    score = base * 0.62 + change_component * 0.38
    if structure > 25:
        score *= 0.42
    elif structure < -20:
        score *= 1.15
    return _clamp(score, -72.0, 25.0), True, str(heat.get("level") or "市場融資")


def _tw_futures_family(price: PriceFrame) -> Tuple[float, bool, str]:
    futures = (price.context or {}).get("futures")
    if not isinstance(futures, Mapping) or not _accepted(futures):
        return 0.0, False, ""
    if not _block_is_fresh(futures, _reference_date(price), 2):
        return 0.0, False, ""
    net = _maybe_num(futures.get("net_oi"))
    delta = _maybe_num(futures.get("delta"))
    parts: List[Tuple[float, float]] = []
    if net is not None:
        parts.append((_tanh100(net / 80000.0), 0.68))
    if delta is not None:
        parts.append((_tanh100(delta / 4500.0), 0.32))
    if not parts:
        return 0.0, False, ""
    score = sum(value * weight for value, weight in parts) / sum(weight for _, weight in parts)
    return _clamp(score, -68.0, 68.0), True, "外資期貨淨部位"


def _tw_foreign_pressure_family(price: PriceFrame) -> Tuple[float, bool, str]:
    """Use the unique FX/macro part of TV foreign pressure, not its VWAP/futures copy."""
    block = (price.context or {}).get("foreign_flow_v2")
    if not isinstance(block, Mapping) or not bool(block.get("accepted")):
        return 0.0, False, ""
    if not _block_is_fresh(block, _reference_date(price), 1):
        return 0.0, False, ""
    fx = _maybe_num(block.get("fx_score"))
    macro = _maybe_num(block.get("macro_score"))
    parts: List[Tuple[float, float]] = []
    if fx is not None:
        parts.append((fx, 0.70))
    if macro is not None:
        parts.append((macro, 0.30))
    if not parts:
        return 0.0, False, ""
    score = sum(value * weight for value, weight in parts) / sum(weight for _, weight in parts)
    return _clamp(score, -45.0, 45.0), True, "外資匯率壓力"


def _geo_policy_family(
    price: PriceFrame,
    signals: Sequence[SignalPacket],
    news_items: Sequence[NewsItem | Mapping[str, Any]],
    profile: str,
    overnight: float,
    overnight_ok: bool,
) -> Tuple[float, bool, float, str]:
    signal = next(
        (row for row in signals or [] if row.accepted and row.module == "Quantum Macro"),
        None,
    )
    if signal is None or abs(_num(signal.score)) < 4.0:
        return 0.0, False, 0.0, ""

    ref = _reference_date(price)
    geo_keys = (
        "地緣", "台海", "軍演", "戰爭", "烏克蘭", "俄羅斯", "伊朗", "以色列",
        "紅海", "tariff", "關稅", "sanction", "制裁", "export control", "出口管制",
        "chip ban", "taiwan strait", "war", "iran", "israel", "russia", "ukraine",
    )
    ages: List[float] = []
    for item in news_items or []:
        text = f"{_news_title(item)} {_news_tag(item)}".lower()
        if not any(key.lower() in text for key in geo_keys):
            continue
        age = _news_age_hours(item, ref)
        if age is not None and age <= 72:
            ages.append(age)
    if not ages:
        return 0.0, False, 0.0, ""

    age_h = min(ages)
    decay = 1.0 if age_h <= 24 else 0.55 if age_h <= 48 else 0.20
    base = _clamp(_num(signal.score), -48.0, 48.0)
    sensitivity = 1.20 if profile == "memory" else 1.12 if profile == "semiconductor" else 0.75
    if profile == "defense" and base < 0:
        base = abs(base) * 0.55

    confirmed = overnight_ok and base * overnight > 0
    conflicted = overnight_ok and base * overnight < 0
    multiplier = 1.15 if confirmed else 0.25 if conflicted else 0.55
    score = _clamp(base * decay * sensitivity * multiplier, -60.0, 60.0)
    uncertainty = 0.0 if confirmed else 0.10 if conflicted else 0.06
    state = "夜盤/海外確認" if confirmed else "海外反向，降權" if conflicted else "等待夜盤確認"
    return score, True, uncertainty, f"政策/地緣 {age_h:.0f}小時｜{state}"


def _macro_event_risk(price: PriceFrame) -> Tuple[float, List[str]]:
    macro = (price.context or {}).get("macro")
    earnings_days = None
    if str(price.ticker.market or "").upper() == "US":
        fundamental = (price.context or {}).get("fundamental")
        if isinstance(fundamental, Mapping):
            earnings_days = _maybe_num(fundamental.get("earnings_days"))
    return event_risk_from_context(macro if isinstance(macro, Mapping) else None, earnings_days=earnings_days)

def build_quantum_evidence(
    price: PriceFrame,
    signals: Sequence[SignalPacket] | None,
    news_items: Sequence[NewsItem | Mapping[str, Any]] | None,
    *,
    trend_score: float,
    intraday_score: float,
    flow_score: float = 0.0,
) -> QuantumEvidence:
    signals = list(signals or [])
    news_items = list(news_items or [])
    market = str(price.ticker.market or "").upper()
    profile = sector_profile(price)
    overnight, overnight_ok, overnight_reason, overnight_components = _overnight_family(price, profile)

    if market == "TW":
        fundamental, fundamental_ok, fundamental_reason = _tw_fundamental_event(price)
        leverage, leverage_ok, leverage_reason = _tw_leverage_family(
            price, trend_score, intraday_score, flow_score
        )
        heat, heat_ok, heat_reason = _tw_market_heat_family(
            price, trend_score, intraday_score, flow_score
        )
        futures, futures_ok, futures_reason = _tw_futures_family(price)
        foreign, foreign_ok, foreign_reason = _tw_foreign_pressure_family(price)
        families: Dict[str, Tuple[float, float, bool]] = {
            "fundamental_event": (fundamental, 0.105, fundamental_ok),
            "overnight": (overnight, 0.145, overnight_ok),
            "leverage": (leverage, 0.080, leverage_ok),
            "market_heat": (heat, 0.050, heat_ok),
            "futures": (futures, 0.050, futures_ok),
            "foreign_pressure": (foreign, 0.035, foreign_ok),
        }
        reasons = [
            value for value in (
                fundamental_reason, overnight_reason, leverage_reason,
                heat_reason, futures_reason, foreign_reason,
            ) if value
        ]
    else:
        fundamental, fundamental_ok, fundamental_reason = _us_fundamental_event(price, news_items)
        families = {
            "fundamental_event": (fundamental, 0.145, fundamental_ok),
            "overnight": (overnight, 0.185, overnight_ok),
        }
        reasons = [value for value in (fundamental_reason, overnight_reason) if value]

    geo, geo_ok, geo_uncertainty, geo_reason = _geo_policy_family(
        price, signals, news_items, profile, overnight, overnight_ok
    )
    families["geo_policy"] = (geo, 0.060, geo_ok)
    if geo_reason:
        reasons.append(geo_reason)

    macro_uncertainty, risk_factors = _macro_event_risk(price)
    uncertainty = _clamp(macro_uncertainty + geo_uncertainty, 0.0, 0.30)
    family_components: Dict[str, Dict[str, float]] = {}
    if overnight_components:
        family_components["overnight"] = overnight_components
    return QuantumEvidence(
        profile=profile,
        families=families,
        uncertainty=uncertainty,
        reasons=reasons,
        family_components=family_components,
        risk_factors=risk_factors,
    )


def dynamic_family_multiplier(
    family: str,
    *,
    market_status: str,
    profile: str,
    fundamental_event_available: bool,
    geo_available: bool,
) -> float:
    status = str(market_status or "")
    multiplier = 1.0
    if status in {"pre_market", "after_hours", "closed_reference", "after_close"}:
        if family == "overnight":
            multiplier *= 1.35
        if family == "intraday":
            multiplier *= 0.70
    elif status in {"intraday", "close_confirm"}:
        if family == "intraday":
            multiplier *= 1.20
        if family == "overnight":
            multiplier *= 0.82
    if profile in {"memory", "semiconductor"} and family == "overnight":
        multiplier *= 1.18
    if fundamental_event_available:
        if family == "fundamental_event":
            multiplier *= 1.28
        if family == "news":
            multiplier *= 0.55
    if geo_available and family == "news":
        multiplier *= 0.65
    return multiplier


def entanglement_adjustment(scores: Mapping[str, float], market: str) -> Tuple[float, List[str]]:
    """Add bounded same-direction confirmations; never clone a single family."""
    pairs = (
        (
            ("fundamental_event", "overnight", 7.0),
            ("flow", "trend", 5.0),
            ("leverage", "intraday", 4.0),
            ("geo_policy", "overnight", 4.5),
            ("foreign_pressure", "flow", 2.5),
        )
        if str(market).upper() == "TW"
        else (
            ("fundamental_event", "overnight", 7.5),
            ("short", "trend", 3.0),
            ("geo_policy", "overnight", 4.5),
        )
    )
    total = 0.0
    reasons: List[str] = []
    for left, right, cap in pairs:
        a = _num(scores.get(left), 0.0)
        b = _num(scores.get(right), 0.0)
        if abs(a) < 10.0 or abs(b) < 10.0 or a * b <= 0:
            continue
        strength = min(abs(a), abs(b)) / 100.0
        total += math.copysign(cap * strength, a)
        reasons.append(f"{left}×{right}")
    return _clamp(total, -12.0, 12.0), reasons
