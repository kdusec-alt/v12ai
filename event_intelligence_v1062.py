# -*- coding: utf-8 -*-
"""V1062 ticker-aware wrapper for the existing Event Intelligence engine.

The original event_intelligence module remains the authoritative parser for
headline age, source quality, event labels and cross-market confirmation. This
wrapper applies ticker-specific exposure DNA and a strong market-shock indicator
so the same oil, tariff or war event does not affect every stock identically.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Sequence

import event_intelligence as _legacy
from ticker_event_exposure import exposure_for_profile
from market_shock_indicator import assess_market_shock

_LEGACY_ASSESS = _legacy.assess_policy_geo

_RULE_META: Dict[str, Dict[str, float]] = {
    "hormuz": {"direction": -1.0, "severity": 4.8},
    "iran_us": {"direction": -1.0, "severity": 4.1},
    "middle_east": {"direction": -1.0, "severity": 3.1},
    "taiwan_strait": {"direction": -1.0, "severity": 4.3},
    "chip_controls": {"direction": -1.0, "severity": 3.8},
    "tariff": {"direction": -1.0, "severity": 3.0},
    "rare_earth": {"direction": -1.0, "severity": 3.3},
    "sanctions": {"direction": -1.0, "severity": 2.7},
    "deescalation": {"direction": 1.0, "severity": 2.8},
}

_LABEL_TO_KEY = (
    ("荷姆茲", "hormuz"), ("霍爾木茲", "hormuz"),
    ("美伊", "iran_us"), ("以伊", "iran_us"),
    ("中東", "middle_east"), ("紅海", "middle_east"),
    ("台海", "taiwan_strait"), ("區域軍事", "taiwan_strait"),
    ("晶片", "chip_controls"), ("出口管制", "chip_controls"),
    ("關稅", "tariff"), ("貿易政策", "tariff"),
    ("稀土", "rare_earth"), ("關鍵材料", "rare_earth"),
    ("制裁", "sanctions"), ("金融限制", "sanctions"),
    ("停火", "deescalation"), ("風險降溫", "deescalation"),
)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _tag(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("tag") or "")
    return str(getattr(item, "tag", "") or "")


def _tag_value(tag: str, key: str) -> str:
    match = re.search(rf"(?:^|\|){re.escape(key)}=([^|]+)", str(tag or ""), flags=re.I)
    return str(match.group(1)).strip() if match else ""


def _profile_from_items(items: Sequence[Any] | None, fallback: str) -> str:
    for item in items or []:
        value = _tag_value(_tag(item), "ticker_profile").lower()
        if value:
            return value
    return str(fallback or "broad").lower()


def _label_key(label: str) -> str:
    text = str(label or "")
    for token, key in _LABEL_TO_KEY:
        if token in text:
            return key
    return ""


def _confirmation_multiplier(base: Mapping[str, Any]) -> float:
    state = str(base.get("confirmation") or "")
    if "跨市場確認" in state:
        return 1.10
    if "海外反向" in state:
        return 0.28
    return 0.55


def _risk_level(risk: float) -> tuple[str, str]:
    if risk >= 16.0:
        return "極高", "市場衝擊已進入多層傳導，優先降部位並等待價格止穩"
    if risk >= 13.0:
        return "高", "個股事件曝險偏高，降低部位並等待價格止穩"
    if risk >= 8.0:
        return "中高", "事件風險升溫，追價降級並等待回測"
    if risk >= 4.0:
        return "中", "維持條件式進場，控制部位"
    return "觀察", "事件影響有限，仍由價格與籌碼裁決"


def _dominant_shock(items: Sequence[Any]) -> Dict[str, Any]:
    rows = [assess_market_shock(item) for item in items if "global_event_core" in _tag(item)]
    if not rows:
        return {
            "level": 0, "label": "觀察", "score": 0.0, "depth": 1,
            "drivers": [], "transmission": [], "color": "green", "price_veto": True,
        }
    return max(rows, key=lambda row: (int(row.get("level") or 0), float(row.get("score") or 0.0)))


def assess_policy_geo_v1062(
    news_items,
    *,
    market: str = "",
    profile: str = "general",
    overnight_score: float = 0.0,
    overnight_ok: bool = False,
    now=None,
):
    items = list(news_items or [])
    resolved_profile = _profile_from_items(items, profile)
    exposure = exposure_for_profile(resolved_profile)
    shock = _dominant_shock(items)
    legacy_profile = {
        "memory": "memory",
        "semiconductor": "semiconductor",
        "ai_power": "semiconductor",
        "defense": "defense",
        "biotech": "biotech",
    }.get(resolved_profile, "general")

    base = dict(_LEGACY_ASSESS(
        items,
        market=market,
        profile=legacy_profile,
        overnight_score=overnight_score,
        overnight_ok=overnight_ok,
        now=now,
    ) or {})

    labels = list(base.get("labels") or [])
    multipliers = dict(exposure.get("rule_multipliers") or {})
    direction_scales = dict(exposure.get("direction_scales") or {})

    weighted_base = 0.0
    weighted_ticker = 0.0
    risk_weights = []
    impacts = []
    for label in labels:
        key = _label_key(label)
        if not key or key not in _RULE_META:
            continue
        meta = _RULE_META[key]
        multiplier = float(multipliers.get(key, 1.0) or 1.0)
        direction_scale = float(direction_scales.get(key, 1.0) or 1.0)
        base_signed = float(meta["direction"]) * float(meta["severity"])
        ticker_signed = base_signed * multiplier * direction_scale
        weighted_base += base_signed
        weighted_ticker += ticker_signed
        risk_weights.append(multiplier)
        impacts.append({
            "key": key,
            "label": label,
            "multiplier": round(multiplier, 3),
            "direction_scale": round(direction_scale, 3),
            "signed_impact": round(ticker_signed, 3),
        })

    score = float(base.get("score") or 0.0)
    if abs(weighted_base) > 0.01:
        score *= _clamp(weighted_ticker / weighted_base, -1.80, 1.80)

    if bool(base.get("oil_up")):
        oil_scale = float(exposure.get("oil_direction_scale") or -1.0)
        score += (oil_scale + 1.0) * 4.4 * _confirmation_multiplier(base)

    # Shock level strengthens uncertainty/risk much more than an ordinary
    # headline. Direction still comes from ticker exposure and price validation.
    shock_level = int(shock.get("level") or 0)
    shock_score = float(shock.get("score") or 0.0)
    shock_risk_boost = {0: 0.0, 1: 0.8, 2: 2.0, 3: 4.0, 4: 6.5, 5: 9.0}.get(shock_level, 0.0)

    score = _clamp(score, -52.0, 42.0)
    average_multiplier = sum(risk_weights) / len(risk_weights) if risk_weights else 1.0
    beta = float(exposure.get("market_beta") or 1.0)
    risk_scale = _clamp(average_multiplier * (0.86 + beta * 0.14), 0.50, 1.70)
    risk = _clamp(float(base.get("risk") or 0.0) * risk_scale + shock_risk_boost, 0.0, 24.0)
    uncertainty = _clamp(risk / 150.0, 0.0, 0.16)
    level, strategy = _risk_level(risk)

    if shock_level >= 5:
        strategy = "極端市場衝擊，先防守；事件方向仍由個股價格與籌碼否決或確認"
    elif shock_level >= 4:
        strategy = "系統性壓力升高，縮小部位並等待海外與價格止穩"
    elif score >= 7.0:
        strategy = "事件結構相對受惠，但仍須由價格與籌碼確認"
    elif score <= -12.0:
        strategy = "個股事件曝險偏空，縮小部位並等待跨市場止穩"

    impact_text = "、".join(
        f"{row['label']} {row['signed_impact']:+.1f}"
        for row in impacts[:3]
    )
    label_text = "+".join(labels[:3]) or "近端事件觀察"
    channels = list(base.get("channels") or [])
    path_text = "→".join(channels[:3]) or "等待市場傳導確認"
    confirm = str(base.get("confirmation") or "等待跨市場確認")
    profile_label = str(exposure.get("label") or "大盤/一般產業")
    shock_drivers = "+".join(shock.get("drivers") or []) or "事件觀察"
    shock_text = (
        f"市場衝擊 L{shock_level} {shock.get('label') or '觀察'} "
        f"{shock_score:.0f}/100｜傳導深度 {int(shock.get('depth') or 1)}層｜{shock_drivers}"
    )
    line = (
        f"Policy/Geo｜{level}｜{shock_text}｜個股曝險 {profile_label}｜{label_text}｜"
        f"{path_text}｜影響 {impact_text or f'{score:+.1f}'}｜{strategy}｜{confirm}"
    )

    base.update({
        "line": line,
        "score": round(score, 4),
        "risk": round(risk, 4),
        "bias": round(_clamp(score / 120.0, -0.34, 0.34), 4),
        "confidence": round(_clamp(abs(score) / 14.0, 0.0, 3.0), 4),
        "uncertainty": round(uncertainty, 4),
        "level": level,
        "reason": (
            f"{base.get('reason') or ''}; ticker_profile={resolved_profile}; "
            f"ticker_event_score={score:+.2f}; ticker_event_risk={risk:.2f}; "
            f"market_shock=L{shock_level}/{shock_score:.1f}"
        ).strip("; "),
        "ticker_profile": resolved_profile,
        "ticker_profile_label": profile_label,
        "ticker_market_beta": round(beta, 3),
        "ticker_impact_rows": impacts,
        "ticker_exposure_source": "TINO_V1062_TICKER_EVENT_EXPOSURE",
        "market_shock": shock,
        "market_shock_level": shock_level,
        "market_shock_score": round(shock_score, 1),
        "market_shock_text": shock_text,
        "price_veto": True,
    })
    return base


def install_event_intelligence_v1062() -> None:
    current = getattr(_legacy, "assess_policy_geo", None)
    if current is assess_policy_geo_v1062:
        return
    _legacy.assess_policy_geo = assess_policy_geo_v1062
