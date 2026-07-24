# -*- coding: utf-8 -*-
"""Bounded event-driven forecast reassessment for TINO V12.

The module compares already-seen evidence with newly fetched company and Global
Event Core rows. It does not mutate Direction, Quantum, price targets, Memory,
or V13 Research by itself; a material event only requests the established full
V12 reanalysis path.
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
_ENERGY_TERMS = (
    "油價", "原油", "wti", "brent", "crude oil", "oil prices", "能源成本",
)
_ENERGY_UP = (
    "上漲", "走高", "飆升", "大漲", "跳升", "climb", "rise", "rises", "surge", "jumps", "spike",
)
_TARIFF_TERMS = (
    "關稅", "tariff", "section 301", "trade war", "貿易戰", "對等關稅", "trade controls",
)
_PMI_TERMS = (
    "pmi", "採購經理人指數", "purchasing managers", "s&p global",
)


def event_watch_display(
    report: Mapping[str, Any] | None,
    *,
    notice: str = "",
    ticker: str = "",
    interval_label: str = "5m",
) -> Dict[str, str]:
    """Build a small, testable UI message without importing Streamlit."""
    row = dict(report or {})
    symbol = _clean_text(ticker or row.get("ticker") or "目前標的").upper()
    checked = _clean_text(row.get("checked_at_tw") or "等待首次檢查")
    status = _clean_text(row.get("status") or "baseline").lower()
    severity = int(row.get("event_severity") or 0)
    headline = _clean_text(notice or row.get("event_title") or row.get("reassessment_reason"))
    if notice:
        if severity >= 3:
            return {"level": "error", "text": f"🚨 重大事件重新評估｜Severity {severity}｜{headline}"}
        if severity >= 2:
            return {"level": "warning", "text": f"⚠️ 事件重新評估｜Severity {severity}｜{headline}"}
        return {"level": "info", "text": f"事件重新評估｜{headline}"}
    if status == "degraded":
        reason = _clean_text(row.get("reason") or "新聞來源暫時無法更新")
        return {"level": "warning", "text": f"🟠 事件監測降級｜{symbol}｜上次檢查 {checked}｜{reason}"}
    result = "等待首次輪詢" if status == "baseline" else "無新重大事件"
    return {
        "level": "caption",
        "text": f"🟢 Admin 事件監測｜{symbol}｜每 {interval_label}｜上次檢查 {checked}｜{result}",
    }


def _value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _event_text(item: Any) -> str:
    return _clean_text(f"{_value(item, 'title')} {_value(item, 'tag')}").lower()


def _tag_value(tag: str, name: str) -> str:
    match = re.search(rf"(?:^|\|){re.escape(name)}=([^|]+)", str(tag or ""), flags=re.I)
    return _clean_text(match.group(1)) if match else ""


def _tag_severity(tag: str, default: int = 0) -> int:
    try:
        return max(0, min(4, int(_tag_value(tag, "severity") or default)))
    except Exception:
        return default


def _published_epoch(item: Any) -> float | None:
    raw = _clean_text(_value(item, "time"))
    if not raw or raw.lower() in {"latest", "sample", "待同步", "觀察"}:
        return None
    for candidate in (raw, raw[:25], raw[:19], raw[:16], raw[:10]):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_TAIPEI)
            return parsed.timestamp()
        except Exception:
            continue
    return None


def event_fingerprint(item: Any) -> str:
    """Stable identity; Global Event Core may change live prices in its title."""
    tag = _clean_text(_value(item, "tag")).lower()
    global_id = _tag_value(tag, "eventid").lower()
    if "global_event_core" in tag and global_id:
        severity = _tag_severity(tag, 0)
        return hashlib.sha1(f"global:{global_id}:s{severity}".encode("utf-8")).hexdigest()[:16]
    title = _clean_text(_value(item, "title")).lower()
    title = re.sub(r"\s+[-–—]\s+[^-–—]{2,60}$", "", title)
    title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", title).strip()
    if not title:
        return ""
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]


def news_fingerprints(items: Iterable[Any] | None) -> list[str]:
    return sorted({fp for fp in (event_fingerprint(row) for row in (items or [])) if fp})


def _contains(text: str, terms: Sequence[str]) -> bool:
    return any(term.lower() in text for term in terms)


def _base_event(item: Any, *, score: float, tag: str) -> Dict[str, Any]:
    return {
        "fingerprint": event_fingerprint(item),
        "title": _clean_text(_value(item, "title")),
        "published_at": _clean_text(_value(item, "time")),
        "source": _clean_text(_value(item, "source")),
        "tag": tag,
        "score": round(score, 4),
        "risk_sign": -1 if score < 0 else 1 if score > 0 else 0,
        "category": "headline",
        "severity": 0,
        "reason": "一般新聞，不觸發正式重估",
        "family": _tag_value(tag, "family"),
        "transmission": "",
        "affected_assets": [],
        "duration_hours": 0,
        "confidence": 0.0,
    }


def classify_event(item: Any) -> Dict[str, Any]:
    """Classify one headline and attach its economic transmission path."""
    text = _event_text(item)
    try:
        score = float(_value(item, "score", 0.0) or 0.0)
    except Exception:
        score = 0.0
    tag = _clean_text(_value(item, "tag")).lower()
    row = _base_event(item, score=score, tag=tag)
    family = row["family"]
    severity_hint = _tag_severity(tag, 0)

    if _contains(text, _DEESCALATION):
        row.update(
            category="geo_deescalation", severity=max(2, severity_hint), risk_sign=1,
            reason="地緣／能源風險降溫，需要更新事件風險",
            transmission="風險溢價↓→油價/供應鏈壓力緩和→等待殖利率與價格確認",
            affected_assets=["原油", "美債", "美元", "科技股", "半導體"],
            duration_hours=36, confidence=0.78,
        )
    elif family == "energy" or (_contains(text, _ENERGY_TERMS) and _contains(text, _ENERGY_UP)):
        sev = max(2, severity_hint or (3 if abs(score) >= 0.16 else 2))
        row.update(
            category="geo_energy_escalation", severity=sev, risk_sign=-1,
            reason="油價供應衝擊是市場級事件，需要重跑跨資產與產業傳導",
            transmission="油價↑→通膨預期↑→降息空間↓→殖利率/美元壓力↑→成長股、半導體與記憶體估值承壓",
            affected_assets=["WTI", "Brent", "US10Y", "DXY", "NQ", "SOX", "半導體", "記憶體"],
            duration_hours=120, confidence=0.90,
        )
    elif family == "trade_tariff" or _contains(text, _TARIFF_TERMS):
        row.update(
            category="geo_trade_controls", severity=max(3, severity_hint), risk_sign=-1,
            reason="關稅／貿易政策會經由成本、毛利與訂單移轉影響台灣供應鏈",
            transmission="關稅→出口成本/轉嫁能力→毛利率→訂單移轉→電子、半導體與工業供應鏈差異化",
            affected_assets=["台股", "TSM ADR", "電子", "半導體", "工業", "美元/台幣"],
            duration_hours=24 * 30, confidence=0.88,
        )
    elif family == "macro_pmi" or _contains(text, _PMI_TERMS):
        row.update(
            category="macro_release_pending", severity=max(2, severity_hint),
            risk_sign=-1 if score < 0 else 1 if score > 0 else 0,
            reason="PMI公布前只提高不確定性；公布後須用預期差、殖利率、美元與價格反應確認",
            transmission="PMI預期差→成長/衰退預期→美元與殖利率→NQ/SOX→個股價格驗證",
            affected_assets=["US10Y", "DXY", "NQ", "SOX", "台指夜盤"],
            duration_hours=8, confidence=0.82,
        )
    elif _contains(text, _GEO_ESCALATION) or "policy_geo" in tag:
        row.update(
            category="geo_policy_escalation", severity=max(3, severity_hint), risk_sign=-1,
            reason="政策／地緣事件升級，需要跨市場重新仲裁",
            transmission="地緣風險→商品/運價/避險→通膨與風險溢價→股價驗證",
            affected_assets=["原油", "黃金", "美元", "美債", "全球股市"],
            duration_hours=96, confidence=0.82,
        )
    elif _contains(text, _MARKET_SHOCK):
        row.update(
            category="systemic_market_shock", severity=4, risk_sign=-1,
            reason="系統性市場事件，需要立即重估",
            transmission="流動性/信用壓力→強制去槓桿→跨資產賣壓",
            affected_assets=["全球股市", "信用市場", "美元", "VIX"],
            duration_hours=72, confidence=0.92,
        )
    elif _contains(text, _COMPANY_HARD_NEGATIVE):
        row.update(
            category="company_hard_negative", severity=3, risk_sign=-1,
            reason="公司硬事件或Forward風險，需要重新估計",
            transmission="公司展望/需求/毛利變化→盈利預期→估值與籌碼確認",
            affected_assets=[], duration_hours=72, confidence=0.82,
        )
    elif _contains(text, _COMPANY_FORWARD_CHANGE):
        row.update(
            category="company_forward_update", severity=2,
            reason="財報／法說／Forward更新，需要重讀最新預期",
            transmission="Forward更新→盈利預期→價格與籌碼確認",
            duration_hours=48, confidence=0.72,
        )
    elif abs(score) >= 0.16:
        row.update(
            category="high_impact_headline", severity=max(2, severity_hint),
            reason="高影響新聞分數，需要重新檢查", duration_hours=24, confidence=0.65,
        )
    elif abs(score) >= 0.08:
        row.update(
            category="material_headline", severity=1,
            reason="具影響力新聞，先累積觀察", duration_hours=12, confidence=0.55,
        )
    return row


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
    for item in (latest_news or []):
        fp = event_fingerprint(item)
        if not fp or fp in seen:
            continue
        if not_before_epoch is not None:
            published = _published_epoch(item)
            if published is None or published < float(not_before_epoch) - 300.0:
                continue
        new_events.append(classify_event(item))

    unique: Dict[str, Dict[str, Any]] = {}
    for row in new_events:
        fp = str(row.get("fingerprint") or "")
        old = unique.get(fp)
        if old is None or int(row.get("severity") or 0) > int(old.get("severity") or 0):
            unique[fp] = row
    rows = sorted(
        unique.values(),
        key=lambda row: (
            int(row.get("severity") or 0),
            float(row.get("confidence") or 0.0),
            abs(float(row.get("score") or 0.0)),
        ),
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
        "event_family": str(lead.get("family") or ""),
        "event_transmission": str(lead.get("transmission") or ""),
        "event_affected_assets": list(lead.get("affected_assets") or []),
        "event_duration_hours": int(lead.get("duration_hours") or 0),
        "event_confidence": float(lead.get("confidence") or 0.0),
        "reassessment_reason": str(lead.get("reason") or "沒有新的重大事件"),
        "event_title": str(lead.get("title") or ""),
        "new_event_count": len(rows),
        "material_event_count": len(material),
        "signed_news_score": round(signed_score, 4),
        "events": material[:8],
        "research_only": False,
        "decision_influence": "full_reanalysis_only",
    }
