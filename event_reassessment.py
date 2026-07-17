# -*- coding: utf-8 -*-
"""Bounded event-driven forecast reassessment for TINO V12.

This module is deliberately pure and network-free.  It compares a forecast's
already-seen headlines with a newly-fetched set and decides whether the active
Streamlit session should rebuild the forecast.  It never changes Direction,
Quantum, price targets, Memory, or V13 Research by itself.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any, Dict, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


_TAIPEI = ZoneInfo("Asia/Taipei")
_NEW_YORK = ZoneInfo("America/New_York")

_COMPANY_HARD_NEGATIVE = (
    "財測下修", "下修財測", "展望下修", "下修展望", "營收預警", "獲利預警",
    "低於預期", "未達預期", "需求放緩", "訂單取消", "延後拉貨", "庫存升高",
    "毛利率下滑", "季減", "衰退", "降評", "下修目標價", "減碼", "賣出評等",
    "增資", "現金增資", "稀釋", "監管調查", "司法調查", "召回",
    "guidance cut", "cuts guidance", "lowered guidance", "weak outlook",
    "profit warning", "revenue warning", "misses estimates", "below estimates",
    "demand slowdown", "order cancellation", "inventory build", "margin decline",
    "downgrade", "downgraded", "price target cut", "offering", "dilution",
    "investigation", "probe", "recall",
)

_COMPANY_FORWARD_CHANGE = (
    "財報", "法說", "展望", "財測", "下一季", "下季", "毛利率", "資本支出",
    "earnings", "guidance", "outlook", "next quarter", "gross margin", "capex",
)

_GEO_ESCALATION = (
    "攻擊伊朗", "空襲", "開戰", "宣戰", "戰爭", "軍事行動", "封鎖海峽",
    "飛彈攻擊", "出口管制", "制裁", "台海軍演", "封鎖台灣",
    "attack iran", "airstrike", "military action", "war", "missile strike",
    "blockade", "export control", "sanctions", "taiwan drills",
)

_MARKET_SHOCK = (
    "緊急降息", "緊急升息", "流動性危機", "信用危機", "銀行倒閉", "違約",
    "恐慌性賣壓", "融資斷頭", "margin call", "liquidity crisis", "credit crisis",
    "bank failure", "default", "emergency rate",
)

_DEESCALATION = (
    "停火", "撤銷制裁", "解除管制", "達成協議", "ceasefire", "lift sanctions",
    "reopen", "de-escalation", "agreement reached",
)


def _value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _event_text(item: Any) -> str:
    return _clean_text(f"{_value(item, 'title')} {_value(item, 'tag')}").lower()


def _published_epoch(item: Any) -> float | None:
    raw = _clean_text(_value(item, "time"))
    if not raw or raw.lower() in {"latest", "sample", "待同步", "觀察"}:
        return None
    for candidate in (raw, raw[:19], raw[:16], raw[:10]):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_TAIPEI)
            return parsed.timestamp()
        except Exception:
            continue
    return None


def event_fingerprint(item: Any) -> str:
    """Stable headline identity independent of list order and Google redirect."""
    title = _clean_text(_value(item, "title")).lower()
    # Google RSS appends the publisher after a dash.  Keep the actual headline
    # stable when the same story is syndicated by more than one route.
    title = re.sub(r"\s+[-–—]\s+[^-–—]{2,60}$", "", title)
    title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", title).strip()
    if not title:
        return ""
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]


def news_fingerprints(items: Iterable[Any] | None) -> list[str]:
    return sorted({fp for fp in (event_fingerprint(row) for row in (items or [])) if fp})


def _contains(text: str, terms: Sequence[str]) -> bool:
    return any(term.lower() in text for term in terms)


def classify_event(item: Any) -> Dict[str, Any]:
    """Classify one headline without making a price-direction decision."""
    text = _event_text(item)
    score = float(_value(item, "score", 0.0) or 0.0)
    tag = _clean_text(_value(item, "tag")).lower()
    category = "headline"
    severity = 0
    risk_sign = -1 if score < 0 else 1 if score > 0 else 0
    reason = "一般新聞，不觸發正式重估"

    if _contains(text, _DEESCALATION):
        category, severity, risk_sign = "geo_deescalation", 2, 1
        reason = "地緣風險降溫，需要更新事件風險"
    elif _contains(text, _GEO_ESCALATION) or "policy_geo" in tag:
        category, severity, risk_sign = "geo_policy_escalation", 3, -1
        reason = "政策／地緣事件升級，需要跨市場重新仲裁"
    elif _contains(text, _MARKET_SHOCK):
        category, severity, risk_sign = "systemic_market_shock", 4, -1
        reason = "系統性市場事件，需要立即重估"
    elif _contains(text, _COMPANY_HARD_NEGATIVE):
        category, severity, risk_sign = "company_hard_negative", 3, -1
        reason = "公司硬事件或Forward風險，需要重新估計"
    elif _contains(text, _COMPANY_FORWARD_CHANGE):
        category, severity = "company_forward_update", 2
        reason = "財報／法說／Forward更新，需要重讀最新預期"
    elif abs(score) >= 0.16:
        category, severity = "high_impact_headline", 2
        reason = "高影響新聞分數，需要重新檢查"
    elif abs(score) >= 0.08:
        category, severity = "material_headline", 1
        reason = "具影響力新聞，先累積觀察"

    return {
        "fingerprint": event_fingerprint(item),
        "title": _clean_text(_value(item, "title")),
        "published_at": _clean_text(_value(item, "time")),
        "source": _clean_text(_value(item, "source")),
        "tag": tag,
        "score": round(score, 4),
        "risk_sign": risk_sign,
        "category": category,
        "severity": severity,
        "reason": reason,
    }


def _revision_type(market: str, now: datetime) -> str:
    market = str(market or "").upper()
    if market == "US":
        local = now.astimezone(_NEW_YORK)
        minute = local.hour * 60 + local.minute
        if local.weekday() >= 5:
            return "WEEKEND_EVENT"
        if minute < 570:
            return "PREMARKET_EVENT"
        if minute < 960:
            return "INTRADAY_EVENT"
        return "AFTER_CLOSE_EVENT"
    local = now.astimezone(_TAIPEI)
    minute = local.hour * 60 + local.minute
    if local.weekday() >= 5:
        return "WEEKEND_EVENT"
    if minute < 540:
        return "PREMARKET_EVENT"
    if minute <= 810:
        return "INTRADAY_EVENT"
    return "AFTER_CLOSE_EVENT"


def assess_event_delta(
    previous_news: Iterable[Any] | None,
    latest_news: Iterable[Any] | None,
    *,
    ticker: str = "",
    market: str = "",
    revision_of: str = "",
    not_before_epoch: float | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Return a bounded reassessment plan for genuinely new material events."""
    reference = now or datetime.now(_TAIPEI)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=_TAIPEI)
    seen = set(news_fingerprints(previous_news))
    new_events = []
    for row in (latest_news or []):
        fp = event_fingerprint(row)
        if not fp or fp in seen:
            continue
        if not_before_epoch is not None:
            published = _published_epoch(row)
            # A newly rotated search result is not necessarily a new event.
            # Only rows published after the forecast baseline (with a small
            # clock/feed grace) may invalidate that forecast.
            if published is None or published < float(not_before_epoch) - 300.0:
                continue
        new_events.append(classify_event(row))
    # Deduplicate the same syndicated headline before severity aggregation.
    unique: Dict[str, Dict[str, Any]] = {}
    for row in new_events:
        fp = str(row.get("fingerprint") or "")
        old = unique.get(fp)
        if old is None or int(row.get("severity") or 0) > int(old.get("severity") or 0):
            unique[fp] = row
    rows = sorted(
        unique.values(),
        key=lambda row: (int(row.get("severity") or 0), abs(float(row.get("score") or 0.0))),
        reverse=True,
    )
    material = [row for row in rows if int(row.get("severity") or 0) >= 1]
    critical = [row for row in rows if int(row.get("severity") or 0) >= 3]
    signed_score = sum(float(row.get("score") or 0.0) for row in material)
    max_severity = max((int(row.get("severity") or 0) for row in rows), default=0)
    needs_reassessment = bool(critical or max_severity >= 2 or abs(signed_score) >= 0.14)
    bundle_payload = [str(row.get("fingerprint") or "") for row in material]
    event_bundle_id = hashlib.sha1(
        json.dumps(bundle_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16] if bundle_payload else ""
    lead = material[0] if material else {}
    return {
        "status": "reassess" if needs_reassessment else "unchanged",
        "needs_reassessment": needs_reassessment,
        "ticker": str(ticker or "").upper(),
        "market": str(market or "").upper(),
        "revision_type": _revision_type(market, reference),
        "revision_of": str(revision_of or ""),
        "event_bundle_id": event_bundle_id,
        "event_fingerprint": str(lead.get("fingerprint") or ""),
        "event_published_at": str(lead.get("published_at") or ""),
        "event_detected_at": reference.astimezone(_TAIPEI).isoformat(timespec="seconds"),
        "event_severity": max_severity,
        "event_category": str(lead.get("category") or ""),
        "reassessment_reason": str(lead.get("reason") or "沒有新的重大事件"),
        "event_title": str(lead.get("title") or ""),
        "new_event_count": len(rows),
        "material_event_count": len(material),
        "signed_news_score": round(signed_score, 4),
        "events": material[:8],
        "research_only": False,
        "decision_influence": "full_reanalysis_only",
    }
