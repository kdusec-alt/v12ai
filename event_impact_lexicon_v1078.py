# -*- coding: utf-8 -*-
"""V1078 single source of truth for material-event priority and magnitude.

The database answers four questions before an event may explain a price move:

1. What happened (event family / kind)?
2. Whose event is it (the analysed company, a counterparty, or the market)?
3. How material is it (P1-P5)?
4. Is there a measurable magnitude (oil move, dilution, discount, tariff)?

It is intentionally pure and network-free.  The output is evidence metadata;
price, volume, VWAP and the formal Orchestrator keep final veto authority.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, Mapping, Sequence


PRIORITY_LABELS = {
    0: "未分級",
    1: "P1背景",
    2: "P2一般催化",
    3: "P3重大事件",
    4: "P4結構重估",
    5: "P5生存／系統危機",
}

# Ordered by semantic importance.  This is the maintained major-term database,
# not a direct direction score.  Scope and price confirmation are evaluated
# separately so a customer earnings headline cannot impersonate company results.
EVENT_TERM_DATABASE: tuple[Dict[str, Any], ...] = (
    {
        "kind": "corporate_survival",
        "family": "regulatory_legal",
        "priority": 5,
        "direction": -1,
        "terms": (
            "聲請破產", "破產保護", "債務違約", "無法清償", "停止交易",
            "bankruptcy", "chapter 11", "insolvency", "debt default",
            "trading suspension", "going concern warning",
        ),
    },
    {
        "kind": "geo_blockade_full_war",
        "family": "geopolitical",
        "priority": 5,
        "direction": -1,
        "terms": (
            "正式宣戰", "全面戰爭", "開戰", "封鎖海峽", "關閉海峽",
            "封鎖台灣", "荷姆茲封鎖", "霍爾木茲封鎖",
            "declares war", "full-scale war", "war breaks out",
            "hormuz blockade", "closes the strait", "blockade taiwan",
        ),
    },
    {
        "kind": "capital_raise_dilution",
        "family": "capital_financing",
        "priority": 4,
        "direction": -1,
        "terms": (
            "gds", "gdr", "adr增發", "全球存託憑證", "海外存託憑證",
            "海外存託股份", "發行存託憑證", "發行新股", "新股發行",
            "折價發行", "現金增資", "股本稀釋", "增發", "募資",
            "global depositary shares", "global depositary receipts",
            "depositary share offering", "new share issuance",
            "stock offering", "share offering", "secondary offering",
            "follow-on offering", "capital raise", "dilution",
        ),
    },
    {
        "kind": "geo_direct_attack",
        "family": "geopolitical",
        "priority": 4,
        "direction": -1,
        "terms": (
            "空襲", "飛彈攻擊", "軍事行動", "攻擊伊朗", "戰火重啟",
            "美伊戰爭", "伊朗戰爭", "以伊戰爭", "俄烏戰爭",
            "airstrike", "missile strike", "military action",
            "attacks iran", "war escalates", "conflict resumes",
            "iran war", "israel iran war", "iran israel war",
            "ukraine war", "russia ukraine war",
        ),
    },
    {
        "kind": "major_restriction",
        "family": "trade_policy",
        "priority": 4,
        "direction": -1,
        "terms": (
            "全面禁運", "出口禁令", "晶片禁售", "列入實體清單",
            "全面制裁", "export ban", "chip ban", "entity list",
            "comprehensive sanctions", "embargo",
        ),
    },
    {
        "kind": "forward_reset",
        "family": "earnings_package",
        "priority": 3,
        "direction": -1,
        "terms": (
            "財測下修", "下修財測", "展望下修", "下修展望", "獲利預警",
            "營收預警", "需求放緩", "訂單取消", "延後拉貨",
            "guidance cut", "cuts guidance", "lowered guidance",
            "profit warning", "revenue warning", "weak outlook",
            "demand slowdown", "order cancellation",
        ),
    },
    {
        "kind": "geo_escalation",
        "family": "geopolitical",
        "priority": 3,
        "direction": -1,
        "terms": (
            "衝突升級", "軍演", "戰爭風險", "增兵", "制裁",
            "台海緊張", "中東緊張", "紅海攻擊",
            "conflict escalates", "military drills", "war risk",
            "troop buildup", "sanctions", "taiwan strait tensions",
        ),
    },
    {
        "kind": "regulatory_investigation",
        "family": "regulatory_legal",
        "priority": 3,
        "direction": -1,
        "terms": (
            "監管調查", "司法調查", "重大訴訟", "反壟斷調查", "召回",
            "investigation", "regulatory probe", "antitrust probe",
            "material lawsuit", "recall",
        ),
    },
    {
        "kind": "trade_tariff",
        "family": "trade_policy",
        "priority": 3,
        "direction": -1,
        "terms": (
            "關稅", "對等關稅", "貿易戰", "出口管制",
            "tariff", "section 301", "trade war", "export control",
        ),
    },
    {
        "kind": "earnings_result",
        "family": "earnings_package",
        "priority": 2,
        "direction": 0,
        "terms": (
            "財報", "季報", "自結", "法說", "每股盈餘", "eps",
            "earnings", "quarterly results", "quarter results",
        ),
    },
    {
        "kind": "company_order_product",
        "family": "orders_demand",
        "priority": 2,
        "direction": 0,
        "terms": (
            "取得訂單", "新增訂單", "擴大合作", "量產", "新品",
            "wins contract", "new order", "backlog", "mass production",
            "new product", "design win",
        ),
    },
    {
        "kind": "analyst_action",
        "family": "analyst_action",
        "priority": 1,
        "direction": 0,
        "terms": (
            "目標價", "升評", "降評", "評等",
            "price target", "upgrade", "downgrade", "rating",
        ),
    },
)

_OIL_TERMS = ("wti", "brent", "crude", "oil price", "oil prices", "原油", "油價")
_OIL_UP = (
    "oil_price_up", "上漲", "走高", "大漲", "飆升", "跳升",
    "rises", "rise", "jumps", "jump", "surge", "spike", "climbs",
)
_OIL_DOWN = (
    "oil_price_down", "下跌", "回落", "大跌", "重挫",
    "falls", "fall", "drops", "drop", "plunges", "slides",
)
_DEESCALATION = (
    "停火", "撤軍", "解除封鎖", "撤銷制裁", "風險降溫",
    "ceasefire", "de-escalation", "withdraw troops", "lifts blockade",
    "lift sanctions",
)

# Counterparty names are event-owner aliases, not ticker-specific trading rules.
# They only prevent "Microsoft earnings" from becoming "Quanta earnings".
KNOWN_EVENT_OWNER_ALIASES: Mapping[str, tuple[str, ...]] = {
    "microsoft": ("microsoft", "微軟", "msft"),
    "nvidia": ("nvidia", "輝達", "英偉達", "nvda"),
    "meta": ("meta platforms", "meta", "facebook", "臉書"),
    "amazon": ("amazon", "aws", "亞馬遜"),
    "alphabet": ("alphabet", "google", "谷歌"),
    "apple": ("apple", "蘋果", "aapl"),
    "amd": ("advanced micro devices", "amd", "超微"),
    "tsmc": ("taiwan semiconductor", "tsmc", "台積電"),
    "micron": ("micron technology", "micron", "美光"),
    "tesla": ("tesla", "特斯拉"),
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _has(text: str, terms: Iterable[str]) -> bool:
    return any(str(term).lower() in text for term in terms)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _tag_number(tag: str, key: str) -> float | None:
    match = re.search(
        rf"(?:^|\|){re.escape(key)}=([-+]?\d+(?:\.\d+)?)",
        str(tag or ""),
        flags=re.I,
    )
    if not match:
        return None
    try:
        value = float(match.group(1))
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _percent_values(text: str) -> list[float]:
    out: list[float] = []
    for raw in re.findall(r"([-+]?\d+(?:\.\d+)?)\s*%", str(text or "")):
        try:
            value = float(raw)
        except Exception:
            continue
        if math.isfinite(value):
            out.append(value)
    return out


def _alias_spans(text: str, aliases: Sequence[str]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for alias in aliases:
        value = _clean(alias)
        if len(value) < 2:
            continue
        if re.fullmatch(r"[a-z0-9.\-]+", value):
            pattern = rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])"
        else:
            pattern = re.escape(value)
        for match in re.finditer(pattern, text, flags=re.I):
            spans.append((match.start(), match.end(), value))
    return spans


def _term_spans(text: str, terms: Sequence[str]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for term in terms:
        value = _clean(term)
        if not value:
            continue
        for match in re.finditer(re.escape(value), text, flags=re.I):
            spans.append((match.start(), match.end(), value))
    return spans


def _distance(left: tuple[int, int, str], right: tuple[int, int, str]) -> float:
    return abs(((left[0] + left[1]) / 2.0) - ((right[0] + right[1]) / 2.0))


def resolve_event_subject(
    text: str,
    event_terms: Sequence[str],
    *,
    target_aliases: Sequence[str] = (),
    target_tag_verified: bool = False,
) -> Dict[str, Any]:
    """Resolve which named entity owns a company-level event phrase."""
    lowered = _clean(text)
    term_spans = _term_spans(lowered, event_terms)
    target_spans = _alias_spans(lowered, target_aliases)
    target_alias_set = {_clean(alias) for alias in target_aliases if _clean(alias)}
    external: list[tuple[int, int, str]] = []
    for owner, aliases in KNOWN_EVENT_OWNER_ALIASES.items():
        if any(_clean(alias) in target_alias_set for alias in aliases):
            continue
        for start, end, alias in _alias_spans(lowered, aliases):
            external.append((start, end, owner))

    if not term_spans:
        return {
            "role": "global",
            "owner": "market",
            "distance": None,
            "reason": "no_company_event_term",
        }

    target_distance = min(
        (_distance(entity, term) for entity in target_spans for term in term_spans),
        default=float("inf"),
    )
    external_choice = min(
        (
            (_distance(entity, term), entity[2])
            for entity in external
            for term in term_spans
        ),
        default=(float("inf"), ""),
    )
    external_distance, external_owner = external_choice

    # A clearly closer named counterparty owns the event.  The margin prevents
    # punctuation or word-order noise from flipping near-ties.
    if external_owner and external_distance + 3.0 < target_distance:
        return {
            "role": "counterparty",
            "owner": external_owner,
            "distance": round(external_distance, 2),
            "reason": "counterparty_closest_to_event_term",
        }
    if target_spans or target_tag_verified:
        return {
            "role": "self",
            "owner": "target_company",
            "distance": None if target_distance == float("inf") else round(target_distance, 2),
            "reason": "target_company_owns_event",
        }
    if external_owner:
        return {
            "role": "counterparty",
            "owner": external_owner,
            "distance": round(external_distance, 2),
            "reason": "external_company_owns_event",
        }
    return {
        "role": "unverified",
        "owner": "",
        "distance": None,
        "reason": "event_owner_unverified",
    }


def _oil_impact(text: str, tag: str) -> Dict[str, Any] | None:
    if not _has(text, _OIL_TERMS) and "family=energy" not in tag:
        return None
    tagged = [
        value
        for value in (
            _tag_number(tag, "magnitude_pct"),
            _tag_number(tag, "wti_pct"),
            _tag_number(tag, "brent_pct"),
        )
        if value is not None
    ]
    values = tagged or _percent_values(text)
    lead = max(values, key=abs) if values else None
    falling = "oil_price_down" in tag or (_has(text, _OIL_DOWN) and not _has(text, _OIL_UP))
    rising = "oil_price_up" in tag or (_has(text, _OIL_UP) and not falling)
    if lead is not None:
        if falling and lead > 0:
            lead *= -1.0
        elif rising and lead < 0:
            lead *= -1.0
    magnitude = abs(lead) if lead is not None else 0.0
    if magnitude >= 8.0:
        priority, impact = 5, 100.0
    elif magnitude >= 6.0:
        priority, impact = 4, 82.0 + min(13.0, (magnitude - 6.0) * 5.0)
    elif magnitude >= 3.0:
        priority, impact = 3, 50.0 + (magnitude - 3.0) * (30.0 / 3.0)
    elif magnitude >= 1.5:
        priority, impact = 2, 28.0 + (magnitude - 1.5) * (20.0 / 1.5)
    else:
        severity = int(_tag_number(tag, "severity") or 0)
        priority = 3 if severity >= 3 else 2 if severity >= 2 else 1
        impact = {4: 82.0, 3: 58.0, 2: 36.0}.get(severity, 20.0)
    direction = -1 if rising else 1 if falling else 0
    return {
        "kind": "oil_move",
        "family": "energy_market",
        "priority_tier": priority,
        "priority_label": PRIORITY_LABELS[priority],
        "direction": direction,
        "impact_score": round(_clamp(impact, 0.0, 100.0), 1),
        "magnitude_pct": round(float(lead), 4) if lead is not None else None,
        "magnitude_basis": "market_move_pct" if lead is not None else "headline_or_tag_severity",
        "event_terms": list(_OIL_TERMS),
    }


def assess_major_event(
    text: str,
    tag: str = "",
    *,
    target_aliases: Sequence[str] = (),
    target_tag_verified: bool = False,
) -> Dict[str, Any]:
    """Classify priority, owner and magnitude from one title/tag pair."""
    lowered = _clean(f"{text} {tag}")
    tag_lower = _clean(tag)
    oil = _oil_impact(lowered, tag_lower)

    matches: list[Dict[str, Any]] = []
    for rule in EVENT_TERM_DATABASE:
        terms = tuple(rule.get("terms") or ())
        if _has(lowered, terms):
            row = dict(rule)
            row["event_terms"] = [term for term in terms if _clean(term) in lowered]
            matches.append(row)

    if _has(lowered, _DEESCALATION):
        matches.append({
            "kind": "geo_deescalation",
            "family": "geopolitical",
            "priority": 3,
            "direction": 1,
            "terms": _DEESCALATION,
            "event_terms": [term for term in _DEESCALATION if _clean(term) in lowered],
        })

    if oil is not None:
        matches.append({
            "kind": oil["kind"],
            "family": oil["family"],
            "priority": oil["priority_tier"],
            "direction": oil["direction"],
            "terms": tuple(oil["event_terms"]),
            "event_terms": list(oil["event_terms"]),
            "_oil": oil,
        })

    if not matches:
        return {
            "matched": False,
            "kind": "background",
            "family": "",
            "priority_tier": 1,
            "priority_label": PRIORITY_LABELS[1],
            "direction": 0,
            "impact_score": 12.0,
            "magnitude_pct": None,
            "magnitude_basis": "no_material_term",
            "subject_role": "global",
            "event_owner": "market",
            "subject_reason": "no_material_event",
            "matched_kinds": [],
        }

    # Priority first; at an equal tier prefer structural company events over
    # earnings/result language that may appear in the same headline.
    family_order = {
        "regulatory_legal": 6,
        "capital_financing": 5,
        "geopolitical": 4,
        "trade_policy": 3,
        "earnings_package": 2,
        "orders_demand": 1,
        "analyst_action": 0,
        "energy_market": 4,
    }
    lead = max(
        matches,
        key=lambda row: (
            int(row.get("priority") or 0),
            family_order.get(str(row.get("family") or ""), 0),
        ),
    )
    priority = int(lead.get("priority") or 1)
    base_impact = {1: 18.0, 2: 34.0, 3: 58.0, 4: 82.0, 5: 100.0}[priority]
    magnitude_pct = None
    magnitude_basis = "semantic_priority"
    if lead.get("_oil"):
        oil_row = dict(lead["_oil"])
        base_impact = float(oil_row["impact_score"])
        magnitude_pct = oil_row["magnitude_pct"]
        magnitude_basis = str(oil_row["magnitude_basis"])
    elif str(lead.get("kind")) == "capital_raise_dilution":
        percentages = _percent_values(lowered)
        if percentages:
            magnitude_pct = max(percentages, key=abs)
            base_impact = _clamp(70.0 + abs(magnitude_pct) * 2.0, 70.0, 96.0)
            magnitude_basis = "reported_dilution_or_discount_pct"

    event_terms = tuple(lead.get("event_terms") or lead.get("terms") or ())
    lead_family = str(lead.get("family") or "")
    global_family = (
        lead_family in {"geopolitical", "energy_market"}
        or (
            lead_family == "trade_policy"
            and not target_tag_verified
            and not target_aliases
        )
    )
    if global_family:
        subject = {
            "role": "global",
            "owner": "market",
            "reason": "market_level_event",
        }
    else:
        subject = resolve_event_subject(
            lowered,
            event_terms,
            target_aliases=target_aliases,
            target_tag_verified=target_tag_verified,
        )

    # War plus a verified oil move is one causal chain, not two independent
    # headlines.  Add bounded synergy without double-counting both scores.
    kinds = {str(row.get("kind") or "") for row in matches}
    war_present = bool(kinds & {
        "geo_blockade_full_war", "geo_direct_attack", "geo_escalation",
    })
    if war_present and oil is not None:
        base_impact = min(100.0, max(base_impact, float(oil["impact_score"])) + 8.0)
        priority = max(priority, int(oil["priority_tier"]))
        magnitude_basis = "war_plus_verified_oil_move"

    return {
        "matched": True,
        "kind": str(lead.get("kind") or ""),
        "family": str(lead.get("family") or ""),
        "priority_tier": priority,
        "priority_label": PRIORITY_LABELS[priority],
        "direction": int(lead.get("direction") or 0),
        "impact_score": round(_clamp(base_impact, 0.0, 100.0), 1),
        "magnitude_pct": round(float(magnitude_pct), 4) if magnitude_pct is not None else None,
        "magnitude_basis": magnitude_basis,
        "subject_role": str(subject.get("role") or "unverified"),
        "event_owner": str(subject.get("owner") or ""),
        "subject_reason": str(subject.get("reason") or ""),
        "matched_kinds": sorted(kinds),
    }


def event_metadata_tags(assessment: Mapping[str, Any] | None) -> tuple[str, ...]:
    row = dict(assessment or {})
    tags = [
        f"event_kind={row.get('kind') or 'background'}",
        f"priority_tier={int(row.get('priority_tier') or 1)}",
        f"impact_score={float(row.get('impact_score') or 0.0):.1f}",
    ]
    magnitude = row.get("magnitude_pct")
    if magnitude is not None:
        tags.append(f"magnitude_pct={float(magnitude):+.4f}")
    return tuple(tags)
