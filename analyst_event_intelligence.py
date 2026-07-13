# -*- coding: utf-8 -*-
"""TINO RC4.6 analyst-event / target-price divergence intelligence.

A broker upgrade or higher price target is *not* treated as an automatic bullish
signal.  The module asks a narrower question:

    Did price / volume / institutional flow confirm the report, or did the
    report arrive together with distribution-like price action?

This implements Tino's observed pattern without claiming that a broker itself
is the seller.  The output is bounded, decays quickly, and is separated into
short-horizon direction, risk and confidence uncertainty.
"""
from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any, Dict, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from models import NewsItem, PriceFrame

_TAIPEI = ZoneInfo("Asia/Taipei")

# House names are kept explicit so generic words such as "target" do not create
# false positives.  The two firms called out by Tino are first-class entries.
_HOUSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("大摩", ("大摩", "摩根士丹利", "morgan stanley")),
    ("小摩", ("小摩", "摩根大通", "jpmorgan", "j.p. morgan", "jp morgan")),
    ("高盛", ("高盛", "goldman sachs")),
    ("花旗", ("花旗", "citigroup", "citi ", "citi:")),
    ("美銀", ("美銀", "bank of america", "bofa")),
    ("瑞銀", ("瑞銀", "ubs")),
    ("麥格理", ("麥格理", "macquarie")),
    ("巴克萊", ("巴克萊", "barclays")),
    ("野村", ("野村", "nomura")),
)

_RAISE_TERMS = (
    "上調目標價", "調高目標價", "提高目標價", "目標價上調", "目標價調升",
    "目標價升至", "目標價喊到", "目標價上看", "升評", "評等升至",
    "調升評等", "重申買進", "重申優於大盤", "重申加碼", "喊買",
    "raises price target", "raised price target", "price target raised",
    "hikes price target", "boosts price target", "lifts price target",
    "target raised", "target lifted", "upgrades to buy", "upgraded to buy",
    "upgrades to overweight", "upgraded to overweight", "upgrades to outperform",
    "upgraded to outperform", "reiterates buy", "reiterates overweight",
)

_CUT_TERMS = (
    "下調目標價", "調降目標價", "降低目標價", "目標價下修", "目標價調降",
    "降評", "評等降至", "調降評等", "downgrades", "downgraded",
    "cuts price target", "cut price target", "price target cut",
    "lowers price target", "target lowered", "reduces price target",
)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _text(item: NewsItem | Mapping[str, Any]) -> str:
    if isinstance(item, Mapping):
        title = item.get("title")
        tag = item.get("tag")
    else:
        title = getattr(item, "title", "")
        tag = getattr(item, "tag", "")
    return re.sub(r"\s+", " ", f"{title or ''} {tag or ''}").strip().lower()


def _title(item: NewsItem | Mapping[str, Any]) -> str:
    raw = item.get("title") if isinstance(item, Mapping) else getattr(item, "title", "")
    return re.sub(r"\s+", " ", str(raw or "")).strip()


def _time(item: NewsItem | Mapping[str, Any]) -> str:
    raw = item.get("time") if isinstance(item, Mapping) else getattr(item, "time", "")
    return str(raw or "").strip()


def _age_hours(item: NewsItem | Mapping[str, Any], now: datetime | None = None) -> float | None:
    text = _time(item)
    if not text or text.lower() in {"latest", "sample", "待同步", "觀察"}:
        return None
    ref = now or datetime.now(_TAIPEI)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=_TAIPEI)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TAIPEI)
        return max(0.0, (ref - dt.astimezone(_TAIPEI)).total_seconds() / 3600.0)
    except Exception:
        try:
            dt = datetime.fromisoformat(text[:10]).replace(tzinfo=_TAIPEI)
            return max(0.0, (ref - dt).total_seconds() / 3600.0)
        except Exception:
            return None


def _decay(age_hours: float | None) -> float:
    # Undated rows are context only.  Target-price events should not remain an
    # active directional force for weeks.
    if age_hours is None:
        return 0.22
    if age_hours <= 12:
        return 1.00
    if age_hours <= 24:
        return 0.88
    if age_hours <= 48:
        return 0.67
    if age_hours <= 72:
        return 0.45
    if age_hours <= 120:
        return 0.20
    return 0.0


def _house(text: str) -> str:
    for label, terms in _HOUSES:
        if any(term in text for term in terms):
            return label
    return ""


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


_RAISE_PATTERNS = (
    r"(?:上調|調高|提高|調升|看高|喊高).{0,16}目標價",
    r"目標價.{0,16}(?:上調|調升|升至|上看|喊到|看至|提高|調高)",
    r"(?:升評|調升評等|重申買進|重申加碼|喊買)",
    r"(?:raise[sd]?|hike[sd]?|boost(?:ed|s)?|lift(?:ed|s)?).{0,45}price target",
    r"price target.{0,45}(?:raise[sd]?|hike[sd]?|boost(?:ed|s)?|lift(?:ed|s)?)",
    r"upgrade[sd]?.{0,35}(?:buy|overweight|outperform)",
    r"reiterate[sd]?.{0,35}(?:buy|overweight|outperform)",
)
_CUT_PATTERNS = (
    r"(?:下調|調降|降低|下修).{0,16}目標價",
    r"目標價.{0,16}(?:下調|調降|下修|降低)",
    r"(?:降評|調降評等)",
    r"(?:cut|cuts|lower(?:ed|s)?|reduce[sd]?).{0,45}price target",
    r"price target.{0,45}(?:cut|lower(?:ed|s)?|reduce[sd]?)",
    r"downgrade[sd]?.{0,35}(?:sell|underweight|underperform|neutral)",
)


def classify_analyst_headline(value: str) -> tuple[str, str]:
    """Return (house, raise/cut) for a major-broker target/rating headline."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    house = _house(text)
    if not house:
        return "", ""
    raise_hit = _contains(text, _RAISE_TERMS) or any(re.search(pattern, text) for pattern in _RAISE_PATTERNS)
    cut_hit = _contains(text, _CUT_TERMS) or any(re.search(pattern, text) for pattern in _CUT_PATTERNS)
    if raise_hit and not cut_hit:
        return house, "raise"
    if cut_hit:
        return house, "cut"
    return "", ""


def _average_volume(price: PriceFrame) -> float:
    values = [_num(v) for v in (price.recent_volumes or []) if _num(v) > 0]
    if not values and _num(price.volume) > 0:
        values = [_num(price.volume)]
    if not values:
        return 0.0
    return sum(values[-20:]) / len(values[-20:])


def _average_volume_lots(price: PriceFrame) -> float:
    avg = _average_volume(price)
    if avg >= 100_000:
        avg /= 1000.0
    return max(avg, 1.0)


def _institution_confirmation(price: PriceFrame) -> tuple[float, bool]:
    """Return -1..+1 institutional confirmation for Taiwan.

    Negative means foreign flow confirms distribution.  It is intentionally
    unavailable for US names rather than replaced with a proxy.
    """
    if str(price.ticker.market or "").upper() != "TW":
        return 0.0, False
    inst = (price.context or {}).get("inst")
    if not isinstance(inst, Mapping) or not bool(inst.get("accepted")):
        return 0.0, False
    source = str(inst.get("source") or "").upper()
    if any(token in source for token in ("SAMPLE", "MOCK", "FALLBACK", "PENDING")):
        return 0.0, False

    avg_lots = _average_volume_lots(price)
    rows: list[tuple[float, float]] = []
    for key, days, weight in (("foreign", 1, 0.54), ("foreign_3", 3, 0.28), ("foreign_5", 5, 0.18)):
        if inst.get(key) is None:
            continue
        per_day = _num(inst.get(key)) / float(days)
        ratio = per_day / avg_lots
        rows.append((math.tanh(ratio / 0.035), weight))
    total = sum(weight for _, weight in rows)
    if total <= 0:
        return 0.0, False
    return _clamp(sum(value * weight for value, weight in rows) / total, -1.0, 1.0), True


def _recent_run(price: PriceFrame) -> float:
    closes = [_num(v) for v in (price.recent_closes or []) if _num(v) > 0]
    if len(closes) < 6:
        return 0.0
    anchor = closes[-6]
    return ((closes[-1] - anchor) / anchor * 100.0) if anchor > 0 else 0.0


def _empty_result(reason: str = "no_recent_analyst_target_event") -> Dict[str, Any]:
    return {
        "score": 0.0, "risk": 0.0, "uncertainty": 0.0, "ok": False,
        "label": "", "reason": reason, "houses": [], "action": "",
        "components": {}, "top_title": "",
    }


def assess_analyst_event(
    price: PriceFrame,
    news_items: Sequence[NewsItem | Mapping[str, Any]] | None,
    *,
    trend_score: float = 0.0,
    intraday_score: float = 0.0,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Assess broker target/rating events using price-and-flow confirmation.

    Positive target revisions are direction-neutral until the market confirms
    them.  A negative day, loss of VWAP and foreign selling after the revision
    create a short-lived distribution-risk contribution.  This is a pattern
    detector, not an allegation that the broker sold shares.
    """
    if str(getattr(price.ticker, "asset_type", "stock") or "stock").lower() != "stock":
        return _empty_result("analyst_target_not_applicable_to_etf")
    matched: list[Dict[str, Any]] = []
    for item in list(news_items or [])[:40]:
        text = _text(item)
        house, action = classify_analyst_headline(text)
        if not house or not action:
            continue
        age = _age_hours(item, now)
        decay = _decay(age)
        if decay <= 0:
            continue
        matched.append({
            "house": house,
            "action": action,
            "decay": decay,
            "age_hours": age,
            "title": _title(item),
        })

    if not matched:
        return _empty_result()

    matched.sort(key=lambda row: float(row["decay"]), reverse=True)
    raises = [row for row in matched if row["action"] == "raise"]
    cuts = [row for row in matched if row["action"] == "cut"]
    event_decay = max(float(row["decay"]) for row in matched)
    houses: list[str] = []
    for row in matched:
        if row["house"] not in houses:
            houses.append(str(row["house"]))

    last = _num(price.last)
    prev = _num(price.previous_close, last) or last
    vwap = _num(price.vwap, last) or last
    atr = max(_num(price.atr14), last * 0.012, 0.01)
    day_atr = (last - prev) / atr
    vwap_atr = (last - vwap) / atr
    avg_volume = _average_volume(price)
    volume_ratio = (_num(price.volume) / avg_volume) if avg_volume > 0 else 1.0
    run5 = _recent_run(price)
    inst, inst_ok = _institution_confirmation(price)

    below_vwap = _clamp(-vwap_atr / 0.80, 0.0, 1.0)
    down_day = _clamp(-day_atr / 1.10, 0.0, 1.0)
    high_volume = _clamp((volume_ratio - 1.05) / 1.10, 0.0, 1.0)
    prior_run = _clamp(max(run5, 0.0) / 12.0, 0.0, 1.0)
    trend_extended = _clamp(max(_num(trend_score), 0.0) / 70.0, 0.0, 1.0)
    foreign_sell = _clamp(-inst, 0.0, 1.0) if inst_ok else 0.0

    weights = {
        "跌破VWAP": 0.29,
        "當日轉弱": 0.27,
        "量能放大": 0.13,
        "前波漲幅": 0.10,
        "趨勢延伸": 0.07,
        "外資賣壓": 0.14 if inst_ok else 0.0,
    }
    weight_total = sum(weights.values()) or 1.0
    distribution = (
        below_vwap * weights["跌破VWAP"]
        + down_day * weights["當日轉弱"]
        + high_volume * weights["量能放大"]
        + prior_run * weights["前波漲幅"]
        + trend_extended * weights["趨勢延伸"]
        + foreign_sell * weights["外資賣壓"]
    ) / weight_total

    above_vwap = _clamp(vwap_atr / 0.80, 0.0, 1.0)
    up_day = _clamp(day_atr / 1.10, 0.0, 1.0)
    foreign_buy = _clamp(inst, 0.0, 1.0) if inst_ok else 0.0
    hold_weights = (0.46, 0.38, 0.16 if inst_ok else 0.0)
    hold_total = sum(hold_weights) or 1.0
    positive_hold = (
        above_vwap * hold_weights[0]
        + up_day * hold_weights[1]
        + foreign_buy * hold_weights[2]
    ) / hold_total

    # A downgrade / target cut remains a conventional negative event, but it is
    # still bounded and decays.  A target raise is contrarian only when price
    # and flow reject the report.
    if cuts and (not raises or float(cuts[0]["decay"]) >= float(raises[0]["decay"])):
        confirm = max(down_day, below_vwap, distribution)
        score = -(16.0 + 28.0 * confirm) * event_decay
        risk = (3.0 + 5.0 * confirm) * event_decay
        uncertainty = _clamp(risk / 160.0, 0.0, 0.07)
        label = f"{'+'.join(houses[:2])}降評/下修目標價"
        action = "cut"
        reason = f"{label}；價格確認 {confirm:.2f}；短線偏空但採時間衰減"
    else:
        action = "raise"
        if distribution >= 0.48:
            score = -(10.0 + 42.0 * distribution) * event_decay
            risk = (2.5 + 6.5 * distribution) * event_decay
            label = f"{'+'.join(houses[:2])}升評後價量背離"
            reason = (
                f"{label}；不假設券商賣出，僅辨識目標價上調後的VWAP/價格/法人背離；"
                f"派發風險 {distribution:.2f}"
            )
        elif positive_hold >= 0.58 and distribution <= 0.25:
            # Confirmation is deliberately small: a target raise must never be
            # allowed to dominate price, flow or the event gate.
            score = (4.0 + 10.0 * positive_hold) * event_decay
            risk = 1.2 * event_decay
            label = f"{'+'.join(houses[:2])}升評獲價量確認"
            reason = f"{label}；僅給小幅確認，不直接追價"
        else:
            score = -3.0 * prior_run * event_decay
            risk = (2.0 + 2.0 * max(distribution, prior_run)) * event_decay
            label = f"{'+'.join(houses[:2])}目標價上調待驗證"
            reason = f"{label}；先看VWAP、量能及法人是否承接"
        uncertainty = _clamp(risk / 150.0, 0.0, 0.07)

    micro = (price.context or {}).get("market_microstructure")
    if isinstance(micro, Mapping):
        if bool(micro.get("is_emerging")):
            score *= 0.55
            risk *= 0.85
            uncertainty = _clamp(uncertainty + 0.015, 0.0, 0.08)
            reason += "；興櫃/延遲報價降權"
        elif str(micro.get("liquidity") or "") == "薄量":
            score *= 0.75
            reason += "；薄量股降權"
    score = _clamp(score, -58.0, 20.0)
    risk = _clamp(risk, 0.0, 10.0)
    components = {
        "跌破VWAP": round(below_vwap, 4),
        "當日轉弱": round(down_day, 4),
        "量能放大": round(high_volume, 4),
        "前波漲幅": round(prior_run, 4),
        "外資賣壓": round(foreign_sell, 4) if inst_ok else 0.0,
        "價量承接": round(positive_hold, 4),
    }
    return {
        "score": round(score, 4),
        "risk": round(risk, 4),
        "uncertainty": round(uncertainty, 4),
        "ok": True,
        "label": label,
        "reason": reason,
        "houses": houses,
        "action": action,
        "components": components,
        "distribution": round(distribution, 4),
        "positive_hold": round(positive_hold, 4),
        "top_title": str(matched[0].get("title") or ""),
        "matched_count": len(matched),
        "event_decay": round(event_decay, 4),
        "intraday_score": round(_num(intraday_score), 4),
    }
