# -*- coding: utf-8 -*-
"""TINO V1082 ticker-independent evidence reasoning layer.

The layer explains why a V1081 execution state exists. It consumes only the
already-built forecast, Entry Opportunity result and verified evidence. It is
narrative/execution-only and may never mutate Direction, T0/T1/High/Low,
confidence, Prediction DNA, Auto Audit, Genome, Research weights, calibration
schedules or formal learning samples.

No ticker code, company name, sector whitelist or one-off exception is allowed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Dict, Iterable, Mapping, Sequence

try:
    from decision_architecture_v1081 import assess_entry_opportunity
except Exception:
    assess_entry_opportunity = None


SCHEMA = "TINO_EVIDENCE_REASONING_V1082"

_POSITIVE = (
    "優於預期", "高於預期", "擊敗預期", "上修", "轉盈", "獲利成長",
    "買超", "連買", "回補", "承接", "吸收", "站穩", "突破", "創高",
    "beats", "raised guidance", "strong outlook", "upgrade", "buyback",
)
_NEGATIVE = (
    "低於預期", "不如預期", "未達預期", "下修", "衰退", "虧損", "財報差",
    "財報不佳", "賣超", "連賣", "稀釋", "增資", "折價", "跌破", "失守",
    "misses", "guidance cut", "weak outlook", "downgrade", "profit warning",
)
_VERIFIED = (
    "正式財報", "公司公告", "公開資訊觀測站", "mops", "twse", "tpex",
    "finmind", "yahooinstitutional", "yahooquotesummary", "yahoo法人",
    "sec filing", "sec.gov", "edgar", "investor relations", "company ir",
    "event_verified=1", "source_verified=1", "content_verified=1",
    "timestamp_verified=1", "model_eligible=1", "已驗證", "官方",
)
_UNVERIFIED = (
    "未驗證", "待驗證", "傳聞", "市場傳聞", "社群轉述", "來源待確認",
    "event_verified=0", "source_verified=0", "content_verified=0",
)
_STALE = ("stale_reindexed", "舊聞重新收錄", "old_reindexed")
_RESEARCH_ONLY = ("研究模式，不介入決策", "研究模式", "不介入決策")

_RADAR_GROUPS = {
    "event": ("事件/Macro", "Policy/Geo", "Company News", "Daily Headline"),
    "fundamental": ("基本面",),
    "chip": ("三大法人", "外資期貨", "資券 / 融資融券", "BSI 借券空方", "空方成本 / 回補"),
    "market": ("市場風控", "市場熱度", "Quantum 貢獻"),
}

_STATE_ACTION = {
    "BUY_TODAY_CONFIRM": "可用小部位確認，但第二筆仍須等待回測與承接",
    "WAIT_VWAP_PULLBACK": "方向未被否定，但先等量縮回測，不在延伸段追價",
    "WAIT_VWAP_RECLAIM": "VWAP是收復門檻，不是直接買價；收復並回踩不破後才重新評估",
    "WAIT_RECLAIM_HOLD": "價格位於多空交界，先確認維持與回踩承接",
    "LIMIT_LIQUIDITY_WAIT": "方向偏強但成交條件未證實，只能限價小量排隊並監看開板品質",
    "OVERHEATED_NO_CHASE": "方向可能正確但價格過熱，等待量縮與價格中樞重新建立",
    "SELLING_EXPANSION_BLOCK": "賣壓或個股反證仍在，禁止把跌深當成便宜",
    "FAILED_BREAKOUT_EXIT": "原突破條件已失效，先取消進場計畫",
    "WAIT_NEXT_SESSION": "目前不建立新追價部位，下一正式時段重新驗證缺口、量能與VWAP",
    "DATA_WAIT": "資料或Session尚未完成驗證，不建立操作結論",
}


@dataclass(frozen=True)
class Driver:
    category: str
    label: str
    stance: int
    strength: int
    verified: bool
    horizon: str
    text: str
    source: str

    @property
    def score(self) -> int:
        verification = 8 if self.verified else -12
        return max(0, min(100, self.strength + verification))


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "--", "NA"):
            return None
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _contains(text: str, terms: Iterable[str]) -> bool:
    low = text.lower()
    return any(term.lower() in low for term in terms)


def _lexical_stance(text: str) -> int:
    low = text.lower()
    positive = sum(1 for term in _POSITIVE if term.lower() in low)
    negative = sum(1 for term in _NEGATIVE if term.lower() in low)
    if positive > negative:
        return 1
    if negative > positive:
        return -1
    return 0


def _numeric_fundamental_stance(text: str) -> int:
    values: list[float] = []
    patterns = (
        r"(?:成長支撐|基本面(?:分數)?|財報(?:分數)?)\s*[:：]?\s*([+-]?\d+(?:\.\d+)?)",
        r"(?:EPS\s*YoY|EPS年增|獲利年增|淨利年增)\s*[:：]?\s*([+-]?\d+(?:\.\d+)?)\s*%?",
        r"(?:毛利率變化|營益率變化)\s*[:：]?\s*([+-]?\d+(?:\.\d+)?)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            value = _num(match.group(1))
            if value is not None:
                values.append(value)
    if not values:
        return 0
    aggregate = sum(values)
    if aggregate > 0:
        return 1
    if aggregate < 0:
        return -1
    return 0


def _numeric_chip_stance(text: str) -> int:
    values: list[float] = []
    pattern = (
        r"(?:外資|投信|自營(?:商)?|法人|主力|大戶)[^｜\n]{0,22}?"
        r"(?:今日|淨量|淨買賣)?\s*[:：]?\s*([+-]\d[\d,]*)\s*(?:張|口|股)?"
    )
    for match in re.finditer(pattern, text, flags=re.I):
        value = _num(match.group(1))
        if value is not None:
            values.append(value)
    if not values:
        return 0
    aggregate = sum(values)
    if aggregate > 0:
        return 1
    if aggregate < 0:
        return -1
    return 0


def _verified(text: str, structured: Mapping[str, Any] | None = None) -> bool:
    row = dict(structured or {})
    for key in ("verified", "accepted", "model_eligible", "source_verified", "content_verified"):
        if key in row:
            return row.get(key) is True
    low = text.lower()
    if _contains(low, _STALE) or _contains(low, _UNVERIFIED):
        return False
    return _contains(low, _VERIFIED)


def _compact(text: str, limit: int = 105) -> str:
    value = _text(text).strip("｜ ")
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip("，；｜ ") + "…"


def _radar_text(radar: Mapping[str, Any], keys: Sequence[str]) -> str:
    rows: list[str] = []
    for key in keys:
        value = radar.get(key)
        if isinstance(value, Mapping):
            rows.append("｜".join(
                f"{k}:{v}" for k, v in value.items() if not str(k).startswith("_")
            ))
        elif value not in (None, ""):
            rows.append(_text(value))
    return "｜".join(row for row in rows if row)


def _driver(
    category: str,
    label: str,
    text: str,
    *,
    stance: int,
    strength: int,
    verified: bool,
    horizon: str,
    source: str,
) -> Driver | None:
    content = _compact(text)
    if not content:
        return None
    return Driver(
        category=category,
        label=label,
        stance=int(max(-1, min(1, stance))),
        strength=int(max(0, min(100, strength))),
        verified=bool(verified),
        horizon=horizon,
        text=content,
        source=source,
    )


def _price_driver(entry: Mapping[str, Any]) -> Driver:
    state = _text(entry.get("state")) or "DATA_WAIT"
    day_pct = _num(entry.get("operative_return_pct")) or 0.0
    vwap_position = _text(entry.get("vwap_position"))
    shape = _mapping(entry.get("price_shape"))
    market_ctx = _mapping(entry.get("market_context"))
    if state in {"SELLING_EXPANSION_BLOCK", "FAILED_BREAKOUT_EXIT"}:
        stance = -1
    elif state in {"BUY_TODAY_CONFIRM", "LIMIT_LIQUIDITY_WAIT"}:
        stance = 1
    elif vwap_position == "above" and day_pct > 0:
        stance = 1
    elif vwap_position == "below" or day_pct < 0:
        stance = -1
    else:
        stance = 0
    pieces = [f"當日 {day_pct:+.2f}%", f"VWAP {vwap_position or 'unknown'}"]
    if shape.get("opening_selloff"):
        pieces.append(f"開盤後回落 {abs(float(shape.get('open_to_last_pct') or 0)):.2f}%")
    if shape.get("near_low"):
        pieces.append("接近盤中低點")
    relative = _num(market_ctx.get("relative_gap_pct"))
    if relative is not None:
        pieces.append(f"相對市場 {relative:+.2f}%")
    strength = 50 if state == "DATA_WAIT" else 90
    return Driver(
        "price", "價格結構", stance, strength, True, "short",
        "｜".join(pieces), "V1081 Entry/Price Truth",
    )


def _fundamental_driver(entry: Mapping[str, Any], radar: Mapping[str, Any]) -> Driver | None:
    context = _mapping(entry.get("fundamental"))
    state = _text(context.get("state"))
    text = _text(context.get("text")) or _radar_text(radar, _RADAR_GROUPS["fundamental"])
    if not text:
        return None
    if state == "negative":
        stance = -1
    elif state == "positive":
        stance = 1
    elif state == "mixed":
        stance = 0
    else:
        stance = _numeric_fundamental_stance(text) or _lexical_stance(text)
    research_only = _contains(text, _RESEARCH_ONLY)
    verified = (bool(context.get("verified")) or _verified(text, context)) and not research_only
    source = "Fundamental Research Context" if research_only else "Fundamental Intelligence"
    return _driver(
        "fundamental", "基本面／財報", text,
        stance=stance,
        strength=84 if verified and stance else 62 if stance else 52,
        verified=verified,
        horizon="medium",
        source=source,
    )


def _event_driver(entry: Mapping[str, Any], radar: Mapping[str, Any]) -> Driver | None:
    context = _mapping(entry.get("event_context"))
    text = _radar_text(radar, _RADAR_GROUPS["event"])
    if not text:
        return None
    stale = bool(context.get("stale")) or _contains(text, _STALE)
    verified = bool(context.get("verified")) or _verified(text, context)
    if stale:
        verified = False
    severity = int(_num(context.get("severity")) or 0)
    return _driver(
        "event", "事件／新聞", text,
        stance=_lexical_stance(text),
        strength=48 + severity * 9,
        verified=verified,
        horizon="short",
        source="News/Event Truth Guard",
    )


def _chip_driver(radar: Mapping[str, Any]) -> Driver | None:
    text = _radar_text(radar, _RADAR_GROUPS["chip"])
    if not text:
        return None
    stance = _numeric_chip_stance(text) or _lexical_stance(text)
    verified = _verified(text)
    return _driver(
        "chip", "籌碼／法人", text,
        stance=stance,
        strength=74 if verified and stance else 58,
        verified=verified,
        horizon="short",
        source="Institution/Short Evidence",
    )


def _market_driver(entry: Mapping[str, Any], radar: Mapping[str, Any]) -> Driver | None:
    context = _mapping(entry.get("market_context"))
    text = _radar_text(radar, _RADAR_GROUPS["market"])
    positive = len(_mapping(context.get("positive_proxies")))
    negative = len(_mapping(context.get("negative_proxies")))
    stance = 1 if positive >= 2 and positive > negative else -1 if negative >= 2 and negative > positive else 0
    if context.get("severe_relative_weakness"):
        stance = -1
    relative = _num(context.get("relative_gap_pct"))
    if not text:
        if relative is None and positive == 0 and negative == 0:
            return None
        text = f"同Session正向代理 {positive}｜負向代理 {negative}"
        if relative is not None:
            text += f"｜個股相對市場 {relative:+.2f}%"
    verified = bool(context.get("session_verified"))
    return _driver(
        "market", "市場／產業", text,
        stance=stance,
        strength=74 if verified and stance else 52,
        verified=verified,
        horizon="short",
        source="Session Truth/Market Context",
    )


def _dedupe(drivers: Sequence[Driver]) -> list[Driver]:
    output: list[Driver] = []
    seen = set()
    for row in drivers:
        key = (row.category, row.stance, row.text.lower()[:80])
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _rank(drivers: Sequence[Driver]) -> list[Driver]:
    category_priority = {"price": 5, "fundamental": 4, "event": 3, "chip": 2, "market": 1}
    return sorted(
        drivers,
        key=lambda row: (row.score, category_priority.get(row.category, 0), abs(row.stance)),
        reverse=True,
    )


def _stars(score: int) -> str:
    count = 5 if score >= 88 else 4 if score >= 76 else 3 if score >= 62 else 2 if score >= 48 else 1
    return "★" * count + "☆" * (5 - count)


def _bias(score: int) -> str:
    if score >= 45:
        return "偏多"
    if score >= 15:
        return "中性偏多"
    if score <= -45:
        return "偏空"
    if score <= -15:
        return "中性偏空"
    return "中性／待確認"


def _weighted_bias(drivers: Sequence[Driver], horizon: str) -> tuple[int, str]:
    if horizon == "short":
        relevant = [row for row in drivers if row.category in {"price", "event", "chip", "market"}]
        category_weight = {"price": 1.20, "event": 1.0, "chip": 0.9, "market": 0.75}
    else:
        relevant = [row for row in drivers if row.category == "fundamental" and row.verified]
        category_weight = {"fundamental": 1.0}
    if not relevant:
        return 0, "中性／待確認"
    numerator = 0.0
    denominator = 0.0
    for row in relevant:
        confidence_weight = 1.0 if row.verified or row.category == "price" else 0.30
        weight = row.score * category_weight.get(row.category, 1.0) * confidence_weight
        numerator += row.stance * weight
        denominator += weight
    if denominator <= 0:
        return 0, "中性／待確認"
    value = int(round(100 * numerator / denominator))
    return value, _bias(value)


def _price_acceptance(entry: Mapping[str, Any], drivers: Sequence[Driver]) -> Dict[str, str]:
    price = next((row for row in drivers if row.category == "price"), None)
    fundamental = next((row for row in drivers if row.category == "fundamental" and row.verified), None)
    event = next((row for row in drivers if row.category == "event" and row.verified), None)
    catalyst = fundamental or event
    if price is None or catalyst is None or catalyst.stance == 0 or price.stance == 0:
        return {
            "code": "UNRESOLVED",
            "label": "價格接受度待確認",
            "detail": "已驗證催化劑與價格尚未形成可判讀的同向或反向關係",
        }
    if catalyst.stance < 0 and price.stance > 0:
        return {
            "code": "NEGATIVE_ABSORBED",
            "label": "利空被價格吸收",
            "detail": "負面基本面／事件存在，但價格結構未跟隨轉弱；短線由承接或籌碼主導，中線仍保留反證",
        }
    if catalyst.stance > 0 and price.stance < 0:
        return {
            "code": "POSITIVE_REJECTED",
            "label": "利多未被價格接受",
            "detail": "正面基本面／事件存在，但價格仍弱；先尊重價格，不以新聞直接宣告轉強",
        }
    if catalyst.stance > 0 and price.stance > 0:
        return {
            "code": "POSITIVE_CONFIRMED",
            "label": "利多獲價格確認",
            "detail": "催化劑與價格同向，但仍須服從目前進場狀態與風險邊界",
        }
    return {
        "code": "NEGATIVE_CONFIRMED",
        "label": "利空獲價格確認",
        "detail": "負面催化與價格弱勢同向，風險優先於折價想像",
    }


def _conflict(drivers: Sequence[Driver], acceptance: Mapping[str, str]) -> str:
    eligible = [row for row in drivers if row.category == "price" or row.verified]
    positive = [row.label for row in eligible if row.stance > 0 and row.score >= 50]
    negative = [row.label for row in eligible if row.stance < 0 and row.score >= 50]
    if positive and negative:
        return (
            f"證據衝突：{'、'.join(positive[:2])}偏多，但{'、'.join(negative[:2])}偏空；"
            f"目前以價格接受度「{acceptance.get('label')}」仲裁"
        )
    if positive:
        return f"主要證據偏多：{'、'.join(positive[:3])}；仍須服從進場與失效條件"
    if negative:
        return f"主要證據偏空：{'、'.join(negative[:3])}；反彈先視為修復而非反轉"
    return "有效證據尚未形成單一主導方向"


def _primary_short_driver(drivers: Sequence[Driver], short_score: int) -> Driver | None:
    short_rows = [row for row in drivers if row.category in {"price", "event", "chip", "market"}]
    if not short_rows:
        return None
    desired = 1 if short_score > 0 else -1 if short_score < 0 else 0
    aligned = [row for row in short_rows if row.stance == desired]
    return (aligned or short_rows)[0]


def build_evidence_reasoning(forecast: Any, entry: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    entry_row = dict(entry or {})
    if not entry_row and callable(assess_entry_opportunity):
        try:
            entry_row = dict(assess_entry_opportunity(forecast) or {})
        except Exception:
            entry_row = {}
    radar = _mapping(getattr(forecast, "radar", {}))

    drivers = [_price_driver(entry_row)]
    for candidate in (
        _fundamental_driver(entry_row, radar),
        _event_driver(entry_row, radar),
        _chip_driver(radar),
        _market_driver(entry_row, radar),
    ):
        if candidate is not None:
            drivers.append(candidate)
    drivers = _rank(_dedupe(drivers))
    top = drivers[:3]

    short_score, short_bias = _weighted_bias(drivers, "short")
    medium_score, medium_bias = _weighted_bias(drivers, "medium")
    acceptance = _price_acceptance(entry_row, drivers)
    conflict = _conflict(drivers, acceptance)
    state = _text(entry_row.get("state")) or "DATA_WAIT"
    action = _STATE_ACTION.get(state, _STATE_ACTION["DATA_WAIT"])

    primary = _primary_short_driver(drivers, short_score)
    headline = "證據不足｜等待驗證" if primary is None else f"{short_bias}｜主導：{primary.label}"
    decision_message = (
        f"{acceptance['label']}。短線{short_bias}、中線{medium_bias}；{conflict}。"
        f"操作上，{action}。"
    )
    top_rows = [
        {
            "rank": index + 1,
            "category": row.category,
            "label": row.label,
            "stance": "偏多" if row.stance > 0 else "偏空" if row.stance < 0 else "中性",
            "strength": row.score,
            "stars": _stars(row.score),
            "verified": row.verified,
            "horizon": row.horizon,
            "text": row.text,
            "source": row.source,
        }
        for index, row in enumerate(top)
    ]
    top_summary = "｜".join(
        f"{row['rank']} {row['label']} {row['stars']} {row['stance']}"
        for row in top_rows
    ) or "有效證據不足"

    return {
        "schema": SCHEMA,
        "headline": headline,
        "decision_message": decision_message,
        "short_term_bias": short_bias,
        "short_term_score": short_score,
        "medium_term_bias": medium_bias,
        "medium_term_score": medium_score,
        "price_acceptance": acceptance,
        "conflict": conflict,
        "dominant_category": primary.category if primary else "none",
        "top_drivers": top_rows,
        "top_driver_summary": top_summary,
        "entry_state": state,
        "action_boundary": action,
        "all_driver_count": len(drivers),
        "narrative_only": True,
        "decision_influence": False,
        "formal_forecast_unchanged": True,
        "learning_sample_unchanged": True,
    }


reason_about_forecast = build_evidence_reasoning
