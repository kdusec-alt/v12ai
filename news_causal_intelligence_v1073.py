# -*- coding: utf-8 -*-
"""Cross-market news causality for the TINO V1073 decision layer.

The news fetchers answer *what was published*.  This module answers the harder
questions that must be resolved before a headline is allowed to explain price:

1. Is the row company, industry or global evidence?
2. Is it a result, forward guidance, a scheduled event or another catalyst?
3. Is it the newest independent event family, or another publisher repeating
   the same story?
4. Was the price snapshot formed before or after the event was published?

It is deliberately ticker-agnostic.  No symbol receives a special branch; the
market, exchange, asset type, company name, event semantics and timestamps drive
the result.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import email.utils
import hashlib
import math
import re
from typing import Any, Dict, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from earnings_intelligence_v1072 import assess_earnings_evidence
from price_truth_v1072 import price_truth


_TAIPEI = ZoneInfo("Asia/Taipei")
_NEW_YORK = ZoneInfo("America/New_York")

_GLOBAL_TAGS = (
    "daily_headline", "tw_daily_", "policy_geo", "macro_event",
    "global_event_core", "market_shock",
)
_INDUSTRY_TAGS = ("industry", "sector", "supply_chain", "供應鏈", "產業")
_PLACEHOLDER_TEXT = ("待同步", "syncing", "新聞查詢中", "news pending")

_SCHEDULED_TERMS = (
    "will report", "to report", "set to report", "ahead of earnings",
    "earnings preview", "earnings expected", "reports after the bell",
    "財報將公布", "即將公布財報", "法說會將於", "將召開法說", "財報前瞻",
)
_FORWARD_POSITIVE = (
    "raises guidance", "raised guidance", "guidance raised", "boosts outlook",
    "strong outlook", "guides above", "above consensus", "upbeat outlook",
    "上調財測", "上調展望", "財測優於預期", "展望優於預期", "提高財測",
)
_FORWARD_NEGATIVE = (
    "cuts guidance", "cut guidance", "guidance cut", "lowered guidance",
    "weak guidance", "soft guidance", "underwhelming guidance",
    "guidance falls short", "guidance disappoints", "weak outlook",
    "soft outlook", "outlook below", "forecast below", "demand slowdown",
    "demand slows", "margin decline", "財測下修", "展望下修", "下修財測",
    "下修展望", "財測疲軟", "財測未達預期", "展望未達預期",
    "需求放緩", "毛利率下滑",
)
_HIGH_BAR_TERMS = (
    "not enough", "tepid guidance", "lack of a stronger outlook",
    "high expectations", "lofty expectations", "priced for perfection",
    "valuation reset", "expectation reset", "in line with expectations",
    "matches expectations", "matches consensus", "market high bar",
    "未達市場高標", "市場期待過高", "估值修正", "預期修正",
)
_EARNINGS_TERMS = (
    "earnings", "quarterly results", "quarter results", "revenue", "eps",
    "q1", "q2", "q3", "q4", "guidance", "outlook",
    "財報", "法說", "營收", "獲利", "每股盈餘", "財測", "展望",
)
_EARNINGS_POSITIVE = (
    "beat estimates", "beats estimates", "beat expectations", "beats expectations",
    "tops estimates", "better than expected", "record revenue", "record sales",
    "優於預期", "擊敗預期", "超越預期", "營收創高", "獲利創高",
)
_EARNINGS_NEGATIVE = (
    "missed estimates", "misses estimates", "below estimates", "revenue miss",
    "earnings miss", "低於預期", "未達預期", "虧損擴大",
)
_ANALYST_TERMS = (
    "price target", "target price", "upgrade", "downgrade", "rating",
    "overweight", "underweight", "目標價", "升評", "降評", "評等", "買進評等",
)
_ORDER_POSITIVE = (
    "wins contract", "contract win", "new contract", "secures order",
    "new order", "backlog rises", "demand surge", "訂單大增", "取得訂單",
    "拿下訂單", "新增訂單", "在手訂單", "需求強勁", "擴大合作",
)
_ORDER_NEGATIVE = (
    "order cancellation", "orders cancelled", "loses contract", "demand weakens",
    "order delay", "訂單取消", "延後拉貨", "砍單", "掉單", "需求轉弱",
)
_FINANCING_POSITIVE = (
    "share buyback", "stock buyback", "repurchase", "special dividend",
    "庫藏股", "股票回購", "特別股利", "提高股利",
)
_FINANCING_NEGATIVE = (
    "stock offering", "share offering", "secondary offering", "dilution",
    "convertible notes", "capital raise", "現金增資", "增資", "私募",
    "可轉債", "稀釋",
)
_LEGAL_POSITIVE = (
    "approval granted", "wins approval", "cleared by regulator", "lawsuit dismissed",
    "核准上市", "取得藥證", "通過審查", "訴訟駁回",
)
_LEGAL_NEGATIVE = (
    "investigation", "probe", "lawsuit", "recall", "fraud", "antitrust",
    "監管調查", "司法調查", "訴訟", "召回", "裁罰", "違規",
)
_MNA_TERMS = (
    "acquire", "acquisition", "merger", "takeover", "strategic investment",
    "收購", "併購", "合併", "策略投資", "出售子公司",
)
_PRODUCT_POSITIVE = (
    "launches", "new product", "mass production", "production ramp",
    "qualification complete", "design win", "量產", "新品", "驗證通過",
    "導入供應鏈", "獲認證",
)
_PRODUCT_NEGATIVE = (
    "product delay", "launch delay", "production issue", "yield issue",
    "delays launch", "延後量產", "產品延遲", "良率不佳", "生產中斷",
)
_MACRO_TERMS = (
    "fomc", "fed", "cpi", "ppi", "pce", "nfp", "payroll", "pmi", "ism",
    "treasury yield", "inflation", "rate hike", "rate cut",
    "聯準會", "利率決議", "通膨", "非農", "殖利率", "採購經理人",
)
_POLICY_TERMS = (
    "tariff", "export control", "sanction", "trade war", "section 301",
    "關稅", "出口管制", "制裁", "貿易戰", "禁令",
)
_GEO_TERMS = (
    "war", "airstrike", "missile", "iran", "israel", "hormuz", "taiwan strait",
    "red sea", "戰爭", "空襲", "飛彈", "伊朗", "以色列", "荷姆茲",
    "霍爾木茲", "台灣海峽", "軍演", "紅海",
)
_ENERGY_TERMS = ("oil", "crude", "wti", "brent", "opec", "原油", "油價")

_FAMILY_LABELS = {
    "earnings_package": "財報／前瞻",
    "scheduled_event": "待公布事件",
    "analyst_action": "評等／目標價",
    "orders_demand": "訂單／需求",
    "capital_financing": "資本／籌資",
    "regulatory_legal": "監管／法律",
    "merger_acquisition": "併購／策略投資",
    "product_execution": "產品／量產",
    "company_event": "公司事件",
    "industry_event": "產業事件",
    "macro_release": "宏觀數據",
    "trade_policy": "關稅／政策",
    "geopolitical": "地緣風險",
    "energy_market": "能源市場",
    "global_market": "全球市場",
}

_MATERIALITY = {
    "earnings_package": 1.00,
    "scheduled_event": 0.72,
    "capital_financing": 0.92,
    "regulatory_legal": 0.90,
    "orders_demand": 0.84,
    "merger_acquisition": 0.82,
    "analyst_action": 0.64,
    "product_execution": 0.68,
    "company_event": 0.58,
    "industry_event": 0.56,
    "trade_policy": 0.82,
    "geopolitical": 0.82,
    "energy_market": 0.74,
    "macro_release": 0.70,
    "global_market": 0.52,
}


def _value(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clip(value: Any, limit: int = 66) -> str:
    text = _clean(value)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _has(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _parse_timestamp(
    value: Any,
    *,
    reference_date: str = "",
    now: datetime | None = None,
) -> datetime | None:
    """Parse source labels, which are normalized to Taipei by both news routes."""
    raw = _clean(value)
    if not raw or raw.lower() in {
        "latest", "sample", "待同步", "觀察", "時間待同步", "none",
    }:
        return None
    reference = now or datetime.now(_TAIPEI)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=_TAIPEI)
    text = raw.replace("台灣", "").strip()
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_TAIPEI)
            return parsed.astimezone(_TAIPEI)
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_TAIPEI)
        return parsed.astimezone(_TAIPEI)
    except Exception:
        pass
    formats = (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=_TAIPEI)
        except Exception:
            continue
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if match:
        try:
            return datetime(
                reference.year,
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5) or 0),
                tzinfo=_TAIPEI,
            )
        except Exception:
            return None
    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if match:
        try:
            day = date.fromisoformat(str(reference_date)[:10])
            return datetime.combine(
                day,
                time(int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)),
                tzinfo=_TAIPEI,
            )
        except Exception:
            return None
    return None


def _event_key(item: Any) -> str:
    title = _clean(_value(item, "title")).lower()
    title = re.sub(r"\s+[-–—]\s+[^-–—]{2,70}$", "", title)
    title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", title).strip()
    if not title:
        return ""
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:18]


def _scope(tag: str) -> str:
    lowered = str(tag or "").lower()
    # The query route owns scope.  A company-specific export-control headline
    # is still company evidence; the shared daily route supplies the global
    # policy backdrop separately.
    if any(token in lowered for token in (
        "tw_company_", "us_company", "bullish_us_company",
        "bearish_us_company", "company_event",
    )):
        return "company"
    if any(token in lowered for token in _INDUSTRY_TAGS):
        return "industry"
    if any(token in lowered for token in _GLOBAL_TAGS):
        return "global"
    return "company"


def _family(text: str, tag: str, scope: str) -> str:
    if scope == "global":
        if _has(text, _ENERGY_TERMS):
            return "energy_market"
        if _has(text, _POLICY_TERMS):
            return "trade_policy"
        if _has(text, _GEO_TERMS):
            return "geopolitical"
        if _has(text, _MACRO_TERMS):
            return "macro_release"
        return "global_market"
    if _has(text, _SCHEDULED_TERMS):
        return "scheduled_event"
    if _has(text, _EARNINGS_TERMS) or "earnings" in tag:
        return "earnings_package"
    if _has(text, _ANALYST_TERMS) or "analyst_target" in tag:
        return "analyst_action"
    if _has(text, (*_ORDER_POSITIVE, *_ORDER_NEGATIVE)):
        return "orders_demand"
    if _has(text, (*_FINANCING_POSITIVE, *_FINANCING_NEGATIVE)):
        return "capital_financing"
    if _has(text, (*_LEGAL_POSITIVE, *_LEGAL_NEGATIVE)):
        return "regulatory_legal"
    if _has(text, _MNA_TERMS):
        return "merger_acquisition"
    if _has(text, (*_PRODUCT_POSITIVE, *_PRODUCT_NEGATIVE)):
        return "product_execution"
    if _has(text, _POLICY_TERMS):
        return "regulatory_legal" if scope == "company" else "industry_event"
    return "industry_event" if scope == "industry" else "company_event"


def _semantic_score(text: str, tag: str, raw_score: float, family: str) -> float:
    if family == "scheduled_event":
        return 0.0
    score = _clamp(raw_score, -0.32, 0.32)
    if _has(text, _FORWARD_NEGATIVE):
        return min(score, -0.18)
    if _has(text, _FORWARD_POSITIVE):
        return max(score, 0.18)
    if family == "earnings_package" and _has(text, _HIGH_BAR_TERMS):
        score = min(score, -0.14)
    if family == "earnings_package":
        if _has(text, _EARNINGS_NEGATIVE):
            score = min(score, -0.12)
        if _has(text, _EARNINGS_POSITIVE):
            score = max(score, 0.12)
    if family == "orders_demand":
        if _has(text, _ORDER_NEGATIVE):
            score = min(score, -0.14)
        if _has(text, _ORDER_POSITIVE):
            score = max(score, 0.14)
    if family == "capital_financing":
        if _has(text, _FINANCING_NEGATIVE):
            score = min(score, -0.16)
        if _has(text, _FINANCING_POSITIVE):
            score = max(score, 0.14)
    if family == "regulatory_legal":
        if _has(text, _LEGAL_NEGATIVE):
            score = min(score, -0.16)
        if _has(text, _LEGAL_POSITIVE):
            score = max(score, 0.14)
    if family == "product_execution":
        if _has(text, _PRODUCT_NEGATIVE):
            score = min(score, -0.12)
        if _has(text, _PRODUCT_POSITIVE):
            score = max(score, 0.10)
    if "bearish_" in tag:
        score = min(score, -0.08)
    elif "bullish_" in tag:
        score = max(score, 0.08)
    return _clamp(score, -0.32, 0.32)


def _freshness_weight(published: datetime | None, reference: datetime) -> tuple[float, float | None]:
    if published is None:
        return 0.22, None
    age_hours = max(0.0, (reference - published).total_seconds() / 3600.0)
    if age_hours <= 6:
        return 1.00, age_hours
    if age_hours <= 24:
        return 0.96, age_hours
    if age_hours <= 72:
        return 0.82, age_hours
    if age_hours <= 24 * 7:
        return 0.64, age_hours
    if age_hours <= 24 * 30:
        return 0.36, age_hours
    return 0.16, age_hours


def _formal_close(price: Any, truth: Mapping[str, Any]) -> datetime | None:
    raw_date = str(truth.get("formal_date") or getattr(price, "price_date", "") or "")[:10]
    try:
        formal_day = date.fromisoformat(raw_date)
    except Exception:
        return None
    market = str(truth.get("market") or getattr(getattr(price, "ticker", None), "market", "") or "").upper()
    if market == "US":
        return datetime.combine(formal_day, time(16, 0), tzinfo=_NEW_YORK).astimezone(_TAIPEI)
    exchange = str(getattr(getattr(price, "ticker", None), "exchange", "") or "").upper()
    micro = (getattr(price, "context", {}) or {}).get("market_microstructure")
    emerging = bool(
        exchange == "TPEX_EMERGING"
        or (isinstance(micro, Mapping) and micro.get("is_emerging"))
    )
    return datetime.combine(
        formal_day,
        time(15, 0) if emerging else time(13, 30),
        tzinfo=_TAIPEI,
    )


def _price_observation(
    price: Any,
    truth: Mapping[str, Any],
    *,
    now: datetime,
) -> tuple[datetime | None, datetime | None, bool]:
    formal = _formal_close(price, truth)
    status = str(truth.get("session") or "")
    market = str(truth.get("market") or "").upper()
    current_date = str(truth.get("current_trade_date") or truth.get("formal_date") or "")[:10]
    context = getattr(price, "context", {}) or {}
    meta = context.get("price_meta") if isinstance(context, Mapping) else {}
    snap = context.get("price_snapshot") if isinstance(context, Mapping) else {}
    us_session = context.get("us_session") if isinstance(context, Mapping) else {}
    meta = meta if isinstance(meta, Mapping) else {}
    snap = snap if isinstance(snap, Mapping) else {}
    us_session = us_session if isinstance(us_session, Mapping) else {}
    stamp = None
    for candidate in (
        meta.get("source_time"),
        snap.get("time"),
        us_session.get("timestamp"),
        truth.get("timestamp"),
        meta.get("source_time_hm"),
    ):
        parsed = _parse_timestamp(candidate, reference_date=current_date, now=now)
        if parsed is not None and parsed <= now + timedelta(minutes=10):
            stamp = parsed
            break

    live = bool(truth.get("live_session_quote"))
    if live and stamp is not None:
        return stamp, formal, True
    if market == "TW" and status in {"intraday", "close_confirm"} and stamp is not None:
        return stamp, formal, True
    # Never use a future scheduled close as if it were an observed price.
    if formal is not None and formal <= now + timedelta(minutes=2):
        return formal, formal, True
    return None, formal, False


def _reaction_state(
    published: datetime | None,
    *,
    observation: datetime | None,
    formal_close: datetime | None,
    timestamp_verified: bool,
) -> Dict[str, Any]:
    if published is None:
        return {
            "state": "time_unverified",
            "price_has_seen_event": False,
            "can_compare_to_price": False,
            "minutes_of_price_reaction": None,
        }
    if observation is None:
        return {
            "state": "price_time_unverified",
            "price_has_seen_event": False,
            "can_compare_to_price": False,
            "minutes_of_price_reaction": None,
        }
    if published > observation + timedelta(minutes=2):
        return {
            "state": "awaiting_market_reaction",
            "price_has_seen_event": False,
            "can_compare_to_price": False,
            "minutes_of_price_reaction": 0,
        }
    observed_minutes = max(0.0, (observation - published).total_seconds() / 60.0)
    if (
        formal_close is not None
        and formal_close <= observation + timedelta(minutes=2)
        and formal_close >= published
    ):
        formal_minutes = max(0.0, (formal_close - published).total_seconds() / 60.0)
        if formal_minutes >= 30:
            return {
                "state": "verified_session_complete",
                "price_has_seen_event": True,
                "can_compare_to_price": True,
                "minutes_of_price_reaction": round(formal_minutes, 1),
            }
    return {
        "state": "reaction_in_progress",
        "price_has_seen_event": bool(timestamp_verified or observed_minutes >= 2),
        "can_compare_to_price": bool(timestamp_verified and observed_minutes >= 2),
        "minutes_of_price_reaction": round(observed_minutes, 1),
    }


def _classify_rows(
    news_items: Iterable[Any] | None,
    *,
    reference: datetime,
) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for index, item in enumerate(news_items or []):
        title = _clean(_value(item, "title"))
        if not title or any(token.lower() in title.lower() for token in _PLACEHOLDER_TEXT):
            continue
        tag = _clean(_value(item, "tag")).lower()
        scope = _scope(tag)
        text = f"{title} {tag}".lower()
        family = _family(text, tag, scope)
        published = _parse_timestamp(_value(item, "time"), now=reference)
        # A source clock in the future must not become trading evidence.
        if published is not None and published > reference + timedelta(minutes=10):
            continue
        freshness, age_hours = _freshness_weight(published, reference)
        raw_score = _num(_value(item, "score"), 0.0)
        semantic_score = _semantic_score(text, tag, raw_score, family)
        # A preview without a machine-readable event date cannot remain
        # "pending" weeks after publication.
        if family == "scheduled_event" and age_hours is not None and age_hours > 72.0:
            continue
        materiality = _MATERIALITY.get(family, 0.50)
        effective_score = semantic_score * freshness
        rows.append({
            "index": index,
            "key": _event_key(item),
            "title": title,
            "headline": _clip(title),
            "tag": tag,
            "source": _clean(_value(item, "source")),
            "published_at": published.isoformat(timespec="minutes") if published else "",
            "_published_dt": published,
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "scope": scope,
            "family": family,
            "family_label": _FAMILY_LABELS.get(family, family),
            "raw_score": round(raw_score, 4),
            "semantic_score": round(semantic_score, 4),
            "effective_score": round(effective_score, 4),
            "freshness_weight": round(freshness, 4),
            "materiality": materiality,
            "scheduled": family == "scheduled_event",
        })
    return rows


def _row_rank(row: Mapping[str, Any]) -> tuple[float, float, float, float]:
    published = row.get("_published_dt")
    epoch = published.timestamp() if isinstance(published, datetime) else 0.0
    dated = 1.0 if isinstance(published, datetime) else 0.0
    return (
        dated,
        epoch,
        float(row.get("materiality") or 0.0),
        abs(float(row.get("effective_score") or 0.0)),
    )


def _select_families(rows: Sequence[Mapping[str, Any]]) -> tuple[list[Dict[str, Any]], list[str]]:
    selected: list[Dict[str, Any]] = []
    selected_keys: list[str] = []
    groups: Dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get("family") or "company_event"), []).append(row)
    for family, members in groups.items():
        ordered = sorted(members, key=_row_rank, reverse=True)
        if family == "earnings_package":
            newest_dt = ordered[0].get("_published_dt") if ordered else None
            cluster = [
                row for row in ordered
                if newest_dt is None
                or row.get("_published_dt") is None
                or abs((newest_dt - row.get("_published_dt")).total_seconds()) <= 72 * 3600
            ]
            # Preserve at most one positive, one negative and one neutral row so
            # historical results and forward guidance can be arbitrated together.
            buckets: Dict[int, Mapping[str, Any]] = {}
            for row in cluster:
                sign = 1 if float(row.get("semantic_score") or 0.0) > 0 else -1 if float(row.get("semantic_score") or 0.0) < 0 else 0
                old = buckets.get(sign)
                if old is None or (
                    abs(float(row.get("semantic_score") or 0.0)),
                    _row_rank(row),
                ) > (
                    abs(float(old.get("semantic_score") or 0.0)),
                    _row_rank(old),
                ):
                    buckets[sign] = row
            chosen = sorted(buckets.values(), key=_row_rank, reverse=True)[:3]
        else:
            chosen = ordered[:1]
        for row in chosen:
            clean = {k: v for k, v in dict(row).items() if not k.startswith("_")}
            selected.append(clean)
            key = str(row.get("key") or "")
            if key:
                selected_keys.append(key)
    selected.sort(
        key=lambda row: (
            float(row.get("materiality") or 0.0) * float(row.get("freshness_weight") or 0.0),
            abs(float(row.get("effective_score") or 0.0)),
            str(row.get("published_at") or ""),
        ),
        reverse=True,
    )
    return selected, list(dict.fromkeys(selected_keys))


def _earnings_from_rows(selected_company: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = [
        row for row in selected_company
        if str(row.get("family") or "") == "earnings_package"
    ]
    return assess_earnings_evidence(rows)


def _family_contributions(
    selected: Sequence[Mapping[str, Any]],
    *,
    earnings: Mapping[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    contributions: list[Dict[str, Any]] = []
    earnings_done = False
    earnings_signal = {
        "backward_beat_forward_miss": -0.18,
        "backward_beat_high_bar_reset": -0.16,
        "forward_miss": -0.18,
        "backward_miss": -0.12,
        "beat_and_raise": 0.18,
        "forward_raise": 0.16,
        "backward_beat_only": 0.10,
        "earnings_unresolved": 0.0,
    }
    for row in selected:
        family = str(row.get("family") or "")
        if family == "earnings_package":
            if earnings_done:
                continue
            earnings_done = True
            state = str((earnings or {}).get("state") or "")
            score = earnings_signal.get(state, float(row.get("effective_score") or 0.0))
            score *= float(row.get("freshness_weight") or 0.0)
            title = str((earnings or {}).get("headline") or row.get("headline") or "")
        else:
            score = float(row.get("effective_score") or 0.0)
            title = str(row.get("headline") or "")
        contributions.append({
            "family": family,
            "family_label": str(row.get("family_label") or family),
            "score": round(_clamp(score, -0.20, 0.20), 4),
            "headline": title,
            "published_at": str(row.get("published_at") or ""),
            "materiality": float(row.get("materiality") or 0.0),
            "counted_once": True,
        })
    return contributions


def _summed_score(contributions: Sequence[Mapping[str, Any]], cap: float) -> float:
    return round(
        _clamp(sum(float(row.get("score") or 0.0) for row in contributions), -cap, cap),
        4,
    )


def _sign(score: float, threshold: float = 0.055) -> int:
    return 1 if score >= threshold else -1 if score <= -threshold else 0


def _dominant_event(
    selected_company: Sequence[Mapping[str, Any]],
    *,
    earnings: Mapping[str, Any],
) -> Dict[str, Any]:
    if not selected_company:
        return {}
    rows = list(selected_company)
    if earnings.get("accepted"):
        earnings_rows = [row for row in rows if row.get("family") == "earnings_package"]
        if earnings_rows:
            target = str(earnings.get("headline") or "").rstrip("…").strip()
            exact_rows = [
                row for row in earnings_rows
                if target and (
                    str(row.get("title") or "").startswith(target)
                    or target.startswith(str(row.get("headline") or "").rstrip("…"))
                )
            ]
            candidates = exact_rows or earnings_rows
            chosen = max(candidates, key=lambda row: (
                abs(float(row.get("semantic_score") or 0.0)),
                float(row.get("freshness_weight") or 0.0),
                str(row.get("published_at") or ""),
            ))
            out = dict(chosen)
            out["headline"] = str(earnings.get("headline") or chosen.get("headline") or "")
            out["semantic_score"] = {
                "backward_beat_forward_miss": -0.18,
                "backward_beat_high_bar_reset": -0.16,
                "forward_miss": -0.18,
                "backward_miss": -0.12,
                "beat_and_raise": 0.18,
                "forward_raise": 0.16,
                "backward_beat_only": 0.10,
            }.get(str(earnings.get("state") or ""), float(chosen.get("semantic_score") or 0.0))
            return out
    return dict(max(
        rows,
        key=lambda row: (
            float(row.get("materiality") or 0.0) * float(row.get("freshness_weight") or 0.0),
            abs(float(row.get("effective_score") or 0.0)),
            str(row.get("published_at") or ""),
        ),
    ))


def analyze_news_causality(
    price: Any,
    news_items: Iterable[Any] | None,
    *,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Build one all-stock news/time/price arbitration snapshot."""
    news_list = list(news_items or [])
    reference = now or datetime.now(_TAIPEI)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=_TAIPEI)
    reference = reference.astimezone(_TAIPEI)
    truth = price_truth(price)
    rows = _classify_rows(news_list, reference=reference)
    company_rows = [row for row in rows if row.get("scope") in {"company", "industry"}]
    global_rows = [row for row in rows if row.get("scope") == "global"]
    selected_company, company_keys = _select_families(company_rows)
    selected_global, global_keys = _select_families(global_rows)
    earnings = _earnings_from_rows(selected_company)
    company_contributions = _family_contributions(selected_company, earnings=earnings)
    global_contributions = _family_contributions(selected_global)
    company_score = _summed_score(company_contributions, 0.32)
    global_score = _summed_score(global_contributions, 0.24)
    company_sign = _sign(company_score)
    global_sign = _sign(global_score)
    dominant = _dominant_event(selected_company, earnings=earnings)

    observation, formal, timestamp_verified = _price_observation(
        price,
        truth,
        now=reference,
    )
    published = _parse_timestamp(
        dominant.get("published_at"),
        reference_date=str(truth.get("current_trade_date") or ""),
        now=reference,
    )
    reaction = _reaction_state(
        published,
        observation=observation,
        formal_close=formal,
        timestamp_verified=timestamp_verified,
    ) if dominant else {
        "state": "no_company_event",
        "price_has_seen_event": False,
        "can_compare_to_price": False,
        "minutes_of_price_reaction": None,
    }
    reaction_age_hours = (
        max(0.0, (observation - published).total_seconds() / 3600.0)
        if published is not None and observation is not None and observation >= published
        else None
    )
    # A historical filing can remain useful context, but it cannot explain
    # today's move forever. Seven calendar days covers ordinary long weekends
    # while preventing a month-old earnings item from being called today's
    # price confirmation or rejection.
    within_current_window = bool(
        reaction_age_hours is not None and reaction_age_hours <= 24.0 * 7.0
    )
    if (
        dominant
        and not bool(dominant.get("scheduled"))
        and bool(reaction.get("price_has_seen_event"))
        and not within_current_window
    ):
        reaction = {
            **reaction,
            "state": "historical_context",
            "can_compare_to_price": False,
        }

    scheduled = bool(dominant.get("scheduled"))
    reaction_state = str(reaction.get("state") or "")
    current_pct = _num(truth.get("current_return_pct"), 0.0)
    formal_pct = _num(truth.get("formal_return_pct"), current_pct)
    live = bool(truth.get("live_session_quote"))
    price_pct = current_pct if live else formal_pct
    price_sign = 1 if price_pct >= 0.6 else -1 if price_pct <= -0.6 else 0
    event_sign = _sign(float(dominant.get("semantic_score") or company_score))
    can_compare = bool(reaction.get("can_compare_to_price"))

    if not dominant:
        causal_state = "no_fresh_company_event"
        alignment = "not_applicable"
    elif scheduled:
        causal_state = "scheduled_event_pending"
        alignment = "event_not_published"
    elif reaction_state == "awaiting_market_reaction":
        causal_state = "event_awaiting_market_reaction"
        alignment = "price_precedes_event"
    elif reaction_state == "historical_context":
        causal_state = "event_context_only"
        alignment = "outside_current_reaction_window"
    elif not can_compare:
        causal_state = "event_time_unverified"
        alignment = "time_unverified"
    elif event_sign > 0 and price_sign < 0:
        causal_state = "positive_event_rejected"
        alignment = "price_rejects_event"
    elif event_sign < 0 and price_sign > 0:
        causal_state = "negative_event_absorbed"
        alignment = "price_absorbs_event"
    elif event_sign and event_sign == price_sign:
        causal_state = "event_price_confirming"
        alignment = "price_confirms_event"
    elif reaction_state == "reaction_in_progress":
        causal_state = "event_reaction_in_progress"
        alignment = "reaction_incomplete"
    else:
        causal_state = "event_price_mixed"
        alignment = "price_mixed"

    event_materiality = float(dominant.get("materiality") or 0.0)
    if causal_state == "event_awaiting_market_reaction" and event_materiality >= 0.68:
        entry_gate = "block_until_first_reaction"
    elif causal_state == "scheduled_event_pending":
        entry_gate = "event_caution"
    elif reaction_state == "reaction_in_progress" and (abs(price_pct) >= 3.0 or event_materiality >= 0.85):
        entry_gate = "wait_15_30m"
    else:
        entry_gate = "normal"

    headline = str(dominant.get("headline") or "")
    if causal_state == "event_awaiting_market_reaction":
        causal_text = "事件已公布，但現有價格形成於事件之前；尚未經市場驗證，等待下一交易時段首次反應"
    elif causal_state == "scheduled_event_pending":
        causal_text = "事件尚未正式公布；不預設利多或利空，公布後重新查詢並等待價格確認"
    elif causal_state == "positive_event_rejected":
        causal_text = "公司利多已進入可交易時段，但價格未確認；價格否決優先"
    elif causal_state == "negative_event_absorbed":
        causal_text = "公司利空已進入可交易時段，但價格未下跌；屬利空吸收"
    elif causal_state == "event_price_confirming":
        causal_text = "公司事件與事件後價格同向；仍以關鍵價與量價延續確認"
    elif causal_state == "event_reaction_in_progress":
        causal_text = "事件後首輪價格反應進行中；尚未完成一個正式交易時段"
    elif causal_state == "event_time_unverified":
        causal_text = "事件時間或價格時間未完整標示；可作方向證據，但不宣稱價格已接受或否決"
    elif causal_state == "event_context_only":
        causal_text = "公司事件已超出本輪價格驗證窗口；只作背景，不把今日漲跌歸因於舊新聞"
    elif causal_state == "event_price_mixed":
        causal_text = "事件後價格反應分歧；公司、產業與籌碼需再交叉確認"
    else:
        causal_text = "未找到足以主導個股判斷的新公司事件；價格、產業與籌碼優先"

    if headline:
        company_text = f"{causal_text}｜主事件《{_clip(headline, 48)}》"
    else:
        company_text = causal_text
    global_headline = str(selected_global[0].get("headline") or "") if selected_global else ""
    if global_headline:
        global_text = (
            f"共同背景《{_clip(global_headline, 46)}》｜"
            f"{'偏多' if global_sign > 0 else '偏空' if global_sign < 0 else '待跨資產確認'}"
        )
    else:
        global_text = "宏觀／政策背景無新主導事件"

    market = str(truth.get("market") or "").upper()
    ticker = getattr(price, "ticker", None)
    return {
        "schema": "TINO_NEWS_CAUSAL_INTELLIGENCE_V1073",
        "accepted": bool(rows),
        "ticker": str(getattr(ticker, "resolved_symbol", "") or ""),
        "market": market,
        "asset_type": str(getattr(ticker, "asset_type", "") or ""),
        "raw_count": len(news_list),
        "classified_count": len(rows),
        "selected_count": len(selected_company) + len(selected_global),
        "company_family_count": len(company_contributions),
        "global_family_count": len(global_contributions),
        "company_score": company_score,
        "global_score": global_score,
        "combined_score": round(_clamp(company_score + global_score * 0.35, -0.36, 0.36), 4),
        "company_sign": company_sign,
        "global_sign": global_sign,
        "cause_priority": (
            "company"
            if dominant
            and event_materiality >= 0.68
            and causal_state not in {"event_context_only", "event_time_unverified"}
            else "global_context"
            if selected_global
            else "price"
        ),
        "causal_state": causal_state,
        "reaction_state": reaction_state,
        "alignment": alignment,
        "entry_gate": entry_gate,
        "price_has_seen_event": bool(reaction.get("price_has_seen_event")),
        "can_compare_to_price": can_compare,
        "minutes_of_price_reaction": reaction.get("minutes_of_price_reaction"),
        "reaction_age_hours": round(reaction_age_hours, 2) if reaction_age_hours is not None else None,
        "within_current_reaction_window": within_current_window,
        "price_reaction_pct": round(price_pct, 4),
        "price_observed_at": observation.isoformat(timespec="minutes") if observation else "",
        "formal_close_at": formal.isoformat(timespec="minutes") if formal else "",
        "price_timestamp_verified": timestamp_verified,
        "dominant_event": {
            key: value
            for key, value in dominant.items()
            if key not in {"_published_dt", "index"}
        },
        "dominant_headline": headline,
        "dominant_family": str(dominant.get("family") or ""),
        "dominant_family_label": str(dominant.get("family_label") or ""),
        "dominant_published_at": str(dominant.get("published_at") or ""),
        "dominant_materiality": event_materiality,
        "causal_text": causal_text,
        "company_text": company_text,
        "global_text": global_text,
        "earnings": earnings,
        "company_contributions": company_contributions,
        "global_contributions": global_contributions,
        "selected_company_events": selected_company,
        "selected_global_events": selected_global,
        "selected_keys": list(dict.fromkeys([*company_keys, *global_keys])),
        "family_deduplicated": True,
        "price_veto": True,
        "narrative_gate_only": False,
    }


def select_effective_news_items(
    news_items: Iterable[Any] | None,
    assessment: Mapping[str, Any] | None,
) -> list[Any]:
    """Return newest independent families while preserving original objects."""
    rows = list(news_items or [])
    selected = set(str(key) for key in (assessment or {}).get("selected_keys", []) if key)
    if not selected:
        return rows
    out = [item for item in rows if _event_key(item) in selected]
    return out or rows


def news_causal_line(assessment: Mapping[str, Any] | None) -> str:
    row = dict(assessment or {})
    if not row.get("accepted"):
        return "News Causal｜新聞資料不足，價格與籌碼優先"
    return (
        f"News Causal｜{row.get('causal_text') or '等待價格確認'}"
        f"｜公司事件族 {int(row.get('company_family_count') or 0)}"
        f"｜全球事件族 {int(row.get('global_family_count') or 0)}"
    )
