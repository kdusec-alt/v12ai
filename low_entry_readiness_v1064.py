# -*- coding: utf-8 -*-
"""Narrative-only AI low-entry readiness gate for TINO V12.

The engine answers one bounded question: is the current tape mature enough to
execute the already-calculated low-entry plan? It never changes Direction,
T0/T1/High/Low, Confidence, Prediction DNA, Audit, or Event Lifecycle.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, Mapping, Sequence


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, "", "--", "暫停", "NA", "待同步"):
            return default
        return float(str(value).replace(",", "").replace("%", "").strip())
    except Exception:
        return default


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _tag_value(tag: str, name: str) -> str:
    match = re.search(rf"(?:^|\|){re.escape(name)}=([^|]+)", str(tag or ""), flags=re.I)
    return _text(match.group(1)) if match else ""


def _parse_time(value: Any) -> float:
    raw = _text(value)
    if not raw:
        return 0.0
    for candidate in (raw, raw[:25], raw[:19], raw[:16], raw[:10]):
        try:
            return datetime.fromisoformat(candidate.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
    return 0.0


def _item_value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _family(item: Any) -> str:
    tag = _text(_item_value(item, "tag")).lower()
    family = _tag_value(tag, "family").lower()
    if family:
        return family
    title = _text(_item_value(item, "title")).lower()
    if any(x in title for x in ("wti", "brent", "oil price", "crude", "油價", "原油")):
        return "energy"
    if any(x in title for x in ("tariff", "section 301", "關稅")):
        return "trade_tariff"
    if "pmi" in title or "採購經理人" in title:
        return "macro_pmi"
    return "other"


def _event_priority(item: Any) -> int:
    source = _text(_item_value(item, "source")).lower()
    title = _text(_item_value(item, "title")).lower()
    tag = _text(_item_value(item, "tag")).lower()
    if "tino_globaleventcore_yahoofinance" in source or (
        title.startswith("global event core") and "oil_supply_shock" in tag
    ):
        return 50
    if any(x in source for x in ("ustr", "bls", "federal reserve", "eia", "official")):
        return 40
    if "global_event_core" in tag:
        return 20
    return 10


def resolve_current_event_families(news_items: Sequence[Any] | None) -> Dict[str, Dict[str, Any]]:
    """Keep one current reality anchor per event family.

    A current market snapshot beats older narrative headlines in the same
    family. This prevents an old 'oil rises' article from overriding a newer
    WTI/Brent price reversal.
    """
    grouped: Dict[str, list[Any]] = {}
    for item in news_items or []:
        tag = _text(_item_value(item, "tag")).lower()
        if "global_event_core" not in tag:
            continue
        grouped.setdefault(_family(item), []).append(item)

    resolved: Dict[str, Dict[str, Any]] = {}
    for family, rows in grouped.items():
        latest_ts = max((_parse_time(_item_value(x, "time")) for x in rows), default=0.0)
        current = [
            x for x in rows
            if latest_ts <= 0 or _parse_time(_item_value(x, "time")) >= latest_ts - 6 * 3600
        ] or rows
        lead = max(
            current,
            key=lambda x: (
                _event_priority(x),
                _parse_time(_item_value(x, "time")),
                abs(_num(_item_value(x, "score"), 0.0) or 0.0),
            ),
        )
        tag = _text(_item_value(lead, "tag")).lower()
        direction = (
            "down" if "oil_price_down" in tag
            else "up" if "oil_price_up" in tag
            else "neutral"
        )
        shock_level = int(_num(_tag_value(tag, "shock_level"), 0.0) or 0)
        severity = int(_num(_tag_value(tag, "severity"), 0.0) or 0)
        conflicts = 0
        for row in rows:
            row_tag = _text(_item_value(row, "tag")).lower()
            row_dir = "down" if "oil_price_down" in row_tag else "up" if "oil_price_up" in row_tag else "neutral"
            if direction != "neutral" and row_dir not in ("neutral", direction):
                conflicts += 1
        resolved[family] = {
            "family": family,
            "direction": direction,
            "shock_level": shock_level,
            "severity": severity,
            "title": _text(_item_value(lead, "title")),
            "source": _text(_item_value(lead, "source")),
            "time": _text(_item_value(lead, "time")),
            "tag": tag,
            "conflicting_older_rows": conflicts,
        }
    return resolved


def _first_number(text: Any) -> float | None:
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", _text(text))
    return _num(match.group(0)) if match else None


def _fair_atr_estimate(radar: Mapping[str, Any], last: float) -> float:
    fair = _text(radar.get("Fair Value"))
    values = [_num(x) for x in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", fair)]
    values = [float(x) for x in values if x is not None]
    if len(values) >= 2:
        return max(abs(values[1] - values[0]), last * 0.012, 0.01)
    return max(last * 0.02, 0.01)


def _condition(ok: bool, good: str, bad: str) -> Dict[str, Any]:
    return {"ok": bool(ok), "text": good if ok else bad}


def assess_low_entry_readiness(forecast: Any) -> Dict[str, Any]:
    d = dict(getattr(forecast, "decision_card", {}) or {})
    radar = dict(getattr(forecast, "radar", {}) or {})
    news_items = list(getattr(forecast, "news_items", []) or [])
    ticker = getattr(forecast, "ticker", None)
    market = _text(getattr(ticker, "market", "")).upper()

    last = float(_num(d.get("現價"), 0.0) or 0.0)
    first = _num(d.get("低接第一批"))
    second = _num(d.get("低接第二批"))
    stop = _num(d.get("防守"))
    no_chase = _num(d.get("不追"), _num(getattr(forecast, "no_chase", None)))
    attack = _first_number(d.get("攻擊"))
    atr = _fair_atr_estimate(radar, last) if last > 0 else 0.01
    hard_blockers: list[str] = []
    caps: list[int] = []

    if last <= 0 or bool((d.get("_price_meta") or {}).get("decision_blocked")):
        price_score = 0.0
        hard_blockers.append("價格尚未通過即時驗證")
        price_note = "價格待確認"
    elif stop is not None and last < stop:
        price_score = 0.0
        hard_blockers.append("已跌破防守價，等待重新築底")
        price_note = "跌破防守"
    elif no_chase is not None and last >= no_chase:
        price_score = 0.0
        hard_blockers.append("已進入不追區，不屬於低接")
        price_note = "進入不追區"
    elif second is not None and last <= second + atr * 0.18:
        price_score = 30.0
        price_note = "已到第二批極限區"
    elif first is not None and last <= first + atr * 0.18:
        price_score = 27.0
        price_note = "已到第一批低接區"
    elif first is not None and last <= first + atr * 0.75:
        price_score = 20.0
        price_note = "接近第一批低接區"
    elif attack is not None and last < attack:
        price_score = 13.0
        price_note = "尚未到理想低接區"
        caps.append(74)
    else:
        price_score = 7.0
        price_note = "價格仍偏高，等回測"
        caps.append(69)

    stabilization = 0.0
    vwap_above = "上方" in _text(d.get("VWAP位置"))
    stabilization += 10.0 if vwap_above else 2.0
    chgp = float(_num(d.get("漲跌幅"), 0.0) or 0.0)
    stabilization += 5.0 if chgp >= 0 else 3.0 if chgp >= -2 else 1.0 if chgp >= -4 else 0.0
    direction = dict(d.get("_direction_engine") or {})
    gate = _text(direction.get("gate_state"))
    direction_score = float(_num(direction.get("score"), _num(d.get("決策分"), 0.0)) or 0.0)
    stabilization += 7.0 if "A突破" in gate else 5.0 if "B回測" in gate else 1.0 if gate else 2.0
    trend = dict(d.get("_trend_snapshot") or {})
    ma20_gap = _num(trend.get("ma20_gap_pct"))
    stabilization += 3.0 if ma20_gap is not None and ma20_gap >= 0 else 2.0 if ma20_gap is not None and ma20_gap >= -5 else 0.0
    stabilization = min(25.0, stabilization)

    chip_text = "｜".join(_text(radar.get(k)) for k in ("左側籌碼摘要", "三大法人", "資券 / 融資融券", "空方成本 / 回補"))
    chip_score = 10.0
    positive_terms = (
        ("法人同步偏多", 5.0), ("法人偏多", 3.0), ("外資連買", 4.0),
        ("投信連買", 2.0), ("融資連減", 3.0), ("回補啟動", 2.0),
    )
    negative_terms = (
        ("法人同步偏空", 6.0), ("法人偏空", 4.0), ("外資連賣", 4.0),
        ("投信連賣", 2.0), ("融資連增", 3.0), ("借券賣壓", 2.0),
    )
    for term, value in positive_terms:
        if term in chip_text:
            chip_score += value
    for term, value in negative_terms:
        if term in chip_text:
            chip_score -= value
    chip_score = max(0.0, min(20.0, chip_score))
    foreign_long_sell = bool(re.search(r"外資[^｜\n]{0,16}連賣(?:[5-9]|\d{2,})天", chip_text))
    if market == "TW" and foreign_long_sell and not vwap_above:
        caps.append(54)

    families = resolve_current_event_families(news_items)
    active_levels = [int(row.get("shock_level") or 0) for row in families.values()]
    max_shock = max(active_levels, default=0)
    event_score = 15.0
    if max_shock >= 5:
        event_score = 0.0
        hard_blockers.append("仍有 L5 極端市場事件")
    elif max_shock == 4:
        event_score = 3.0
        caps.append(59)
    elif max_shock == 3:
        event_score = 8.0
    elif max_shock == 2:
        event_score = 12.0

    energy = families.get("energy") or {}
    energy_profile = _tag_value(_text(energy.get("tag")), "ticker_profile")
    if energy.get("direction") == "down" and energy_profile in {
        "airline", "ai_power", "semiconductor", "memory", "industrial", "consumer", "broad",
    }:
        event_score = min(15.0, event_score + 3.0)
    event_caution = "事件卡" in _text(d.get("標題")) or "公布前" in _text(d.get("主訊息"))
    if event_caution:
        caps.append(69)

    title = _text(d.get("標題"))
    ai_score = 5.0
    if "攻擊卡" in title:
        ai_score = 8.0
    elif "觀望卡" in title or "事件卡" in title:
        ai_score = 5.0
    elif "防守卡" in title:
        ai_score = 3.0
    elif "風險共振" in title:
        ai_score = 1.0
    elif "價格待確認" in title:
        ai_score = 0.0
        hard_blockers.append("AI 價格閘門尚未開啟")
    if direction_score >= 12:
        ai_score += 2.0
    elif direction_score > 0:
        ai_score += 1.0
    ai_score = min(10.0, ai_score)

    total = price_score + stabilization + chip_score + event_score + ai_score
    if caps:
        total = min(total, float(min(caps)))
    if hard_blockers:
        total = min(total, 39.0)
    total = int(round(max(0.0, min(100.0, total))))

    if total >= 75 and not hard_blockers:
        color, icon, label = "green", "🟢", "低接成熟"
    elif total >= 50 and not hard_blockers:
        color, icon, label = "yellow", "🟡", "再等等"
    else:
        color, icon, label = "red", "🔴", "暫不低接"

    conditions = [
        _condition(price_score >= 25, price_note, price_note),
        _condition(vwap_above, "價格站在 VWAP 上方", "價格仍在 VWAP 下方"),
        _condition(chip_score >= 12, "法人／籌碼開始改善", "法人／籌碼尚未確認"),
        _condition(max_shock <= 2, "重大事件已降溫", f"市場事件仍為 L{max_shock}" if max_shock else "事件方向待確認"),
        _condition(ai_score >= 7, "AI 允許進入低接觀察", "AI 尚未開啟低接閘門"),
    ]
    if energy.get("direction") == "down":
        conditions.insert(3, _condition(True, "最新油價快速回落，舊上漲新聞已降權", ""))
    summary_parts = [row["text"] for row in conditions if row["ok"]][:2]
    wait_parts = [row["text"] for row in conditions if not row["ok"]][:2]
    summary = "；".join(summary_parts + wait_parts)
    if hard_blockers:
        summary = "；".join(hard_blockers[:2])

    return {
        "score": total,
        "label": label,
        "color": color,
        "icon": icon,
        "summary": summary or "等待更多價格與籌碼證據",
        "conditions": conditions,
        "components": {
            "價格區位": round(price_score, 1),
            "止穩確認": round(stabilization, 1),
            "法人籌碼": round(chip_score, 1),
            "事件海外": round(event_score, 1),
            "AI同意": round(ai_score, 1),
        },
        "hard_blockers": hard_blockers,
        "caps": caps,
        "current_event_families": families,
        "max_shock_level": max_shock,
        "decision_only": True,
    }
