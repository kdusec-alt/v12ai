# -*- coding: utf-8 -*-
"""TINO RC4.2 event-intelligence layer.

This module is deliberately network-free and lightweight.  It converts recent
policy/geopolitical headlines into three separate outputs:

1. directional contribution (small until cross-market confirmation exists)
2. risk / uncertainty contribution
3. transmission path (energy, yields, inflation, shipping, semiconductors...)

The split prevents a headline from being treated as a permanent bullish or
bearish call and keeps the V9 frontend language stable.
"""
from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence
from zoneinfo import ZoneInfo

from models import NewsItem

_TAIPEI = ZoneInfo("Asia/Taipei")


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


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


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(str(term).lower() in text for term in terms)


# Each rule describes the economic transmission, not only a literal keyword.
_RULES: Sequence[Dict[str, Any]] = (
    {
        "key": "hormuz",
        "label": "荷姆茲海峽/原油通道",
        "terms": (
            "荷姆茲", "霍爾木茲", "hormuz", "strait of hormuz", "海峽控制權",
            "封鎖海峽", "關閉海峽", "blockade the strait",
        ),
        "direction": -1.0,
        "severity": 4.8,
        "channels": ("原油供給", "航運保險", "通膨", "美債殖利率"),
        "sectors": ("成長股", "半導體", "航空", "航運", "能源"),
    },
    {
        "key": "iran_us",
        "label": "美伊/以伊衝突",
        "terms": (
            "美伊", "美國 伊朗", "伊朗 美國", "us-iran", "u.s.-iran", "iran war",
            "以伊", "以色列 伊朗", "israel iran", "iran israel", "伊朗戰爭",
        ),
        "direction": -1.0,
        "severity": 4.1,
        "channels": ("原油", "避險", "通膨預期", "殖利率"),
        "sectors": ("科技", "半導體", "航空", "國防", "能源"),
    },
    {
        "key": "middle_east",
        "label": "中東/紅海航運",
        "terms": (
            "中東", "紅海", "胡塞", "houthi", "red sea", "葉門", "yemen",
            "oil tanker", "油輪", "航運中斷", "shipping disruption",
        ),
        "direction": -1.0,
        "severity": 3.1,
        "channels": ("運價", "原油", "供應鏈", "通膨"),
        "sectors": ("航運", "航空", "零售", "科技"),
    },
    {
        "key": "taiwan_strait",
        "label": "台海/區域軍事風險",
        "terms": (
            "台海", "台灣海峽", "taiwan strait", "軍演", "共軍", "解放軍",
            "封鎖台灣", "blockade taiwan", "south china sea", "南海",
        ),
        "direction": -1.0,
        "severity": 4.3,
        "channels": ("供應鏈", "風險溢價", "外資部位", "航運"),
        "sectors": ("台股", "半導體", "電子", "航運"),
    },
    {
        "key": "chip_controls",
        "label": "晶片/出口管制",
        "terms": (
            "出口管制", "export control", "export controls", "晶片禁售", "chip ban",
            "半導體限制", "semiconductor restriction", "entity list", "實體清單",
            "ai chip restriction", "先進製程限制",
        ),
        "direction": -1.0,
        "severity": 3.8,
        "channels": ("中國營收", "供應鏈", "資本支出", "估值"),
        "sectors": ("半導體", "記憶體", "AI伺服器", "設備"),
    },
    {
        "key": "tariff",
        "label": "關稅/貿易政策",
        "terms": ("關稅", "tariff", "tariffs", "trade war", "貿易戰", "對等關稅"),
        "direction": -1.0,
        "severity": 3.0,
        "channels": ("進口成本", "企業毛利", "通膨", "需求"),
        "sectors": ("電子", "零售", "汽車", "工業"),
    },
    {
        "key": "rare_earth",
        "label": "稀土/關鍵材料管制",
        "terms": (
            "稀土", "rare earth", "鎵", "gallium", "鍺", "germanium", "銻", "antimony",
            "關鍵礦物", "critical minerals",
        ),
        "direction": -1.0,
        "severity": 3.3,
        "channels": ("材料供給", "成本", "交期", "產能"),
        "sectors": ("半導體", "電動車", "機器人", "國防"),
    },
    {
        "key": "sanctions",
        "label": "制裁/金融限制",
        "terms": ("制裁", "sanction", "sanctions", "金融封鎖", "asset freeze"),
        "direction": -1.0,
        "severity": 2.7,
        "channels": ("資金流", "商品供給", "匯率", "風險溢價"),
        "sectors": ("金融", "能源", "原物料", "科技"),
    },
    {
        "key": "deescalation",
        "label": "停火/風險降溫",
        "terms": (
            "停火", "ceasefire", "達成協議", "和平協議", "de-escalation", "deescalation",
            "重開海峽", "reopen the strait", "撤銷制裁", "lift sanctions",
        ),
        "direction": 1.0,
        "severity": 2.8,
        "channels": ("風險溢價下降", "油價壓力緩和", "供應鏈恢復"),
        "sectors": ("成長股", "半導體", "航空", "消費"),
    },
)

_OIL_UP_TERMS = ("油價上漲", "油價飆", "原油上漲", "crude oil rises", "oil prices rise", "brent rises", "wti rises")
_YIELD_UP_TERMS = ("殖利率上漲", "美債殖利率上升", "treasury yields rise", "bond yields rise", "10-year yield rises")
_GOLD_DOWN_TERMS = ("金價走跌", "黃金下跌", "gold falls", "gold drops")


def _decay(age_hours: float | None) -> float:
    # Undated rows are retained only as weak context.
    if age_hours is None:
        return 0.35
    if age_hours <= 12:
        return 1.00
    if age_hours <= 24:
        return 0.88
    if age_hours <= 48:
        return 0.62
    if age_hours <= 72:
        return 0.35
    if age_hours <= 96:
        return 0.15
    return 0.0


def _profile_sensitivity(profile: str, rule_key: str) -> float:
    p = str(profile or "general").lower()
    if p in {"memory", "semiconductor"}:
        if rule_key in {"chip_controls", "taiwan_strait", "rare_earth", "tariff"}:
            return 1.22
        if rule_key in {"hormuz", "iran_us", "middle_east"}:
            return 1.08
    if p == "defense":
        if rule_key in {"iran_us", "middle_east", "taiwan_strait"}:
            return 0.35
    return 1.0


def assess_policy_geo(
    news_items: Sequence[NewsItem | Mapping[str, Any]] | None,
    *,
    market: str = "",
    profile: str = "general",
    overnight_score: float = 0.0,
    overnight_ok: bool = False,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Return one bounded policy/geopolitical assessment.

    Direction is deliberately restrained until overseas/overnight price action
    confirms the same sign.  Risk remains visible even when direction is zero.
    """
    items = list(news_items or [])[:36]
    matches: Dict[str, Dict[str, Any]] = {}
    oil_up = False
    yield_up = False
    gold_down = False

    for item in items:
        text = _text(item)
        if not text:
            continue
        rise_word = _contains(text, ("上漲", "走高", "飆升", "大漲", "rises", "rise", "surges", "jumps"))
        fall_word = _contains(text, ("下跌", "走跌", "回落", "falls", "drops", "declines"))
        oil_up = oil_up or _contains(text, _OIL_UP_TERMS) or (
            _contains(text, ("油價", "原油", "wti", "brent", "crude oil")) and rise_word
        )
        yield_up = yield_up or _contains(text, _YIELD_UP_TERMS) or (
            _contains(text, ("殖利率", "treasury yield", "bond yield", "10-year yield")) and rise_word
        )
        gold_down = gold_down or _contains(text, _GOLD_DOWN_TERMS) or (
            _contains(text, ("金價", "黃金", "gold")) and fall_word
        )
        age = _age_hours(item, now)
        decay = _decay(age)
        if decay <= 0:
            continue
        for rule in _RULES:
            if not _contains(text, rule["terms"]):
                continue
            strength = float(rule["severity"]) * decay * _profile_sensitivity(profile, str(rule["key"]))
            existing = matches.get(str(rule["key"]))
            row = {
                "key": rule["key"],
                "label": rule["label"],
                "direction": float(rule["direction"]),
                "strength": strength,
                "age_hours": age,
                "channels": tuple(rule["channels"]),
                "sectors": tuple(rule["sectors"]),
                "title": _title(item),
            }
            if existing is None or strength > float(existing.get("strength") or 0.0):
                matches[str(rule["key"])] = row

    rows = list(matches.values())
    if not rows:
        return {
            "line": "Policy/Geo｜觀察｜近端政策/地緣事件未形成方向",
            "score": 0.0,
            "risk": 0.0,
            "bias": 0.0,
            "confidence": 0.0,
            "uncertainty": 0.0,
            "reason": "no_recent_policy_geo_transmission",
            "level": "觀察",
            "labels": [],
            "channels": [],
            "sectors": [],
            "top_title": "",
            "matched_count": 0,
        }

    signed = sum(float(row["direction"]) * float(row["strength"]) for row in rows)
    gross = sum(abs(float(row["strength"])) for row in rows)

    # Oil + yields rising together is a direct inflation/valuation transmission.
    transmission_bonus = 0.0
    transmission_labels: List[str] = []
    if oil_up:
        transmission_bonus -= 1.0
        transmission_labels.append("油價上行")
    if yield_up:
        transmission_bonus -= 1.2
        transmission_labels.append("殖利率上行")
    if oil_up and yield_up:
        transmission_bonus -= 1.1
        transmission_labels.append("通膨/估值共振")
    if gold_down and yield_up:
        # Gold weakness with rising yields is not a risk-off confirmation by itself;
        # it reinforces the rates channel rather than cancelling geopolitical risk.
        transmission_bonus -= 0.25

    signed += transmission_bonus
    provisional = _clamp(signed * 4.4, -48.0, 36.0)
    if overnight_ok and abs(float(overnight_score)) >= 5.0:
        same_sign = provisional * float(overnight_score) > 0
        opposite = provisional * float(overnight_score) < 0
        direction_multiplier = 1.10 if same_sign else 0.28 if opposite else 0.55
        confirm_state = "跨市場確認" if same_sign else "海外反向，方向降權" if opposite else "等待確認"
    else:
        direction_multiplier = 0.55
        confirm_state = "等待跨市場確認"

    direction_score = _clamp(provisional * direction_multiplier, -48.0, 36.0)
    risk_points = _clamp(gross * 1.85 + (2.5 if oil_up and yield_up else 0.0), 0.0, 18.0)
    uncertainty = _clamp(risk_points / 150.0, 0.0, 0.12)

    if risk_points >= 13.0:
        level = "高"
        strategy = "事件風險偏高，降低部位，等待跨市場止穩"
    elif risk_points >= 8.0:
        level = "中高"
        strategy = "風險升溫，追價降級，先等回測確認"
    elif risk_points >= 4.0:
        level = "中"
        strategy = "維持條件式進場，控制部位"
    else:
        level = "觀察"
        strategy = "事件觀察，等待確認"

    rows.sort(key=lambda row: abs(float(row["strength"])), reverse=True)
    labels = [str(row["label"]) for row in rows]
    channels: List[str] = []
    sectors: List[str] = []
    for row in rows:
        for channel in row["channels"]:
            if channel not in channels:
                channels.append(channel)
        for sector in row["sectors"]:
            if sector not in sectors:
                sectors.append(sector)
    for label in transmission_labels:
        if label not in channels:
            channels.append(label)

    top_title = str(rows[0].get("title") or "")
    short_title = top_title[:30] + "…" if len(top_title) > 30 else top_title
    label_text = "+".join(labels[:3])
    path_text = "→".join(channels[:3])
    title_text = f"｜{short_title}" if short_title else ""
    line = f"Policy/Geo｜{level}｜{label_text}｜{path_text}｜{strategy}｜{confirm_state}{title_text}"

    return {
        "line": line,
        "score": round(direction_score, 4),
        "risk": round(risk_points, 4),
        "bias": round(_clamp(direction_score / 120.0, -0.30, 0.30), 4),
        "confidence": round(_clamp(abs(direction_score) / 14.0, 0.0, 3.0), 4),
        "uncertainty": round(uncertainty, 4),
        "reason": f"policy_geo={label_text}; path={path_text}; direction={direction_score:+.2f}; risk={risk_points:.2f}; {confirm_state}",
        "level": level,
        "labels": labels,
        "channels": channels,
        "sectors": sectors,
        "top_title": top_title,
        "matched_count": len(rows),
        "confirmation": confirm_state,
        "oil_up": oil_up,
        "yield_up": yield_up,
        "gold_down": gold_down,
    }
