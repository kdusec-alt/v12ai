# -*- coding: utf-8 -*-
"""TINO V1062 ticker-level event exposure DNA.

This module is pure and network-free. It maps a ticker to a stable event
sensitivity profile so the same global event can affect airlines, energy,
semiconductors, shipping and biotech differently. The profile is evidence
metadata only; price reality and the existing Direction/Orchestrator gates
remain the final veto.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

from models import NewsItem, TickerInfo


_RULE_KEYS = (
    "hormuz", "iran_us", "middle_east", "taiwan_strait",
    "chip_controls", "tariff", "rare_earth", "sanctions", "deescalation",
)

_PROFILE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "broad": {
        "label": "大盤/一般產業",
        "market_beta": 1.00,
        "oil_direction_scale": -1.00,
        "rule_multipliers": {},
        "direction_scales": {},
    },
    "broad_market": {
        "label": "大盤ETF/市場Beta",
        "market_beta": 1.00,
        "oil_direction_scale": -0.95,
        "rule_multipliers": {"taiwan_strait": 1.20, "tariff": 1.10},
        "direction_scales": {},
    },
    "memory": {
        "label": "記憶體/半導體",
        "market_beta": 1.28,
        "oil_direction_scale": -1.15,
        "rule_multipliers": {
            "hormuz": 1.12, "iran_us": 1.10, "middle_east": 1.06,
            "taiwan_strait": 1.42, "chip_controls": 1.48,
            "tariff": 1.35, "rare_earth": 1.32,
        },
        "direction_scales": {},
    },
    "semiconductor": {
        "label": "半導體/AI供應鏈",
        "market_beta": 1.22,
        "oil_direction_scale": -1.08,
        "rule_multipliers": {
            "hormuz": 1.08, "iran_us": 1.06, "middle_east": 1.03,
            "taiwan_strait": 1.38, "chip_controls": 1.45,
            "tariff": 1.30, "rare_earth": 1.28,
        },
        "direction_scales": {},
    },
    "ai_power": {
        "label": "AI伺服器/電源供應鏈",
        "market_beta": 1.16,
        "oil_direction_scale": -0.92,
        "rule_multipliers": {
            "taiwan_strait": 1.28, "chip_controls": 1.22,
            "tariff": 1.34, "rare_earth": 1.22, "middle_east": 1.04,
        },
        "direction_scales": {},
    },
    "airline": {
        "label": "航空/燃油成本",
        "market_beta": 1.18,
        "oil_direction_scale": -1.65,
        "rule_multipliers": {
            "hormuz": 1.62, "iran_us": 1.48, "middle_east": 1.52,
            "tariff": 0.72, "sanctions": 1.10,
        },
        "direction_scales": {},
    },
    "shipping": {
        "label": "航運/運價與燃油混合",
        "market_beta": 1.10,
        "oil_direction_scale": 0.12,
        "rule_multipliers": {
            "hormuz": 1.48, "iran_us": 1.25, "middle_east": 1.45,
            "tariff": 1.18, "sanctions": 1.12,
        },
        "direction_scales": {
            "hormuz": -0.18, "iran_us": -0.10, "middle_east": -0.20,
        },
    },
    "energy": {
        "label": "能源/油價受惠",
        "market_beta": 0.92,
        "oil_direction_scale": 0.88,
        "rule_multipliers": {
            "hormuz": 1.35, "iran_us": 1.20, "middle_east": 1.25,
            "tariff": 0.82, "sanctions": 1.15,
        },
        "direction_scales": {
            "hormuz": -0.45, "iran_us": -0.28, "middle_east": -0.30,
            "sanctions": -0.12,
        },
    },
    "defense": {
        "label": "國防/軍工",
        "market_beta": 0.88,
        "oil_direction_scale": -0.30,
        "rule_multipliers": {
            "hormuz": 1.05, "iran_us": 1.35, "middle_east": 1.25,
            "taiwan_strait": 1.45, "tariff": 0.78,
        },
        "direction_scales": {
            "iran_us": -0.38, "middle_east": -0.28, "taiwan_strait": -0.32,
        },
    },
    "biotech": {
        "label": "生技/高波動資金面",
        "market_beta": 1.18,
        "oil_direction_scale": -0.38,
        "rule_multipliers": {
            "hormuz": 0.58, "iran_us": 0.62, "middle_east": 0.58,
            "taiwan_strait": 0.95, "chip_controls": 0.42,
            "tariff": 0.55, "rare_earth": 0.38,
        },
        "direction_scales": {},
    },
    "financial": {
        "label": "金融/利率與風險偏好",
        "market_beta": 0.92,
        "oil_direction_scale": -0.55,
        "rule_multipliers": {
            "hormuz": 0.85, "iran_us": 0.82, "middle_east": 0.75,
            "taiwan_strait": 1.20, "tariff": 0.90, "sanctions": 1.20,
        },
        "direction_scales": {},
    },
    "industrial_export": {
        "label": "工業/出口供應鏈",
        "market_beta": 1.05,
        "oil_direction_scale": -0.82,
        "rule_multipliers": {
            "hormuz": 0.95, "middle_east": 1.02, "taiwan_strait": 1.18,
            "tariff": 1.42, "rare_earth": 1.15, "sanctions": 1.05,
        },
        "direction_scales": {},
    },
    "consumer": {
        "label": "消費/成本轉嫁",
        "market_beta": 0.96,
        "oil_direction_scale": -0.78,
        "rule_multipliers": {
            "hormuz": 0.92, "middle_east": 0.90,
            "tariff": 1.22, "sanctions": 0.92,
        },
        "direction_scales": {},
    },
}

_MEMORY_CODES = {"2408", "2344", "2337", "8299", "3260", "5351", "4967", "2451", "6770", "2349"}
_MEMORY_US = {"MU", "SKHY"}
_SEMI_CODES = {"2330", "2454", "3034", "2379", "3661", "3443", "5274", "3529", "6488"}
_SEMI_US = {"TSM", "NVDA", "AMD", "AVGO", "MRVL", "ASML", "AMAT", "LRCX"}
_AI_POWER_CODES = {"2308", "3017", "3653", "3324", "2382", "3231", "6669", "2368", "2317"}
_AIRLINE_CODES = {"2610", "2618"}
_SHIPPING_CODES = {"2603", "2609", "2615"}
_ENERGY_CODES = {"6505", "9937"}
_DEFENSE_CODES = {"2634", "8033", "5371", "8222"}
_BIOTECH_CODES = {"6586"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _code(symbol: str) -> str:
    return _clean(symbol).upper().split(".")[0]


def _blob(ticker: TickerInfo | Mapping[str, Any] | Any) -> tuple[str, str, str, str]:
    if isinstance(ticker, Mapping):
        symbol = _clean(ticker.get("resolved_symbol") or ticker.get("symbol"))
        name = _clean(ticker.get("name"))
        market = _clean(ticker.get("market")).upper()
        asset_type = _clean(ticker.get("asset_type")).lower()
    else:
        symbol = _clean(getattr(ticker, "resolved_symbol", "") or getattr(ticker, "symbol", ""))
        name = _clean(getattr(ticker, "name", ""))
        market = _clean(getattr(ticker, "market", "")).upper()
        asset_type = _clean(getattr(ticker, "asset_type", "")).lower()
    return _code(symbol), name.upper(), market, asset_type


def classify_ticker_profile(ticker: TickerInfo | Mapping[str, Any] | Any) -> str:
    code, name, market, asset_type = _blob(ticker)
    if asset_type == "etf":
        return "broad_market"
    if code in _MEMORY_CODES or code in _MEMORY_US or any(k in name for k in ("記憶體", "DRAM", "NAND", "HBM", "MICRON", "SK HYNIX", "旺宏", "華邦")):
        return "memory"
    if code in _SEMI_CODES or code in _SEMI_US or any(k in name for k in ("半導體", "晶圓", "IC設計", "TSMC", "NVIDIA", "MEDIATEK")):
        return "semiconductor"
    if code in _AI_POWER_CODES or any(k in name for k in ("電源", "散熱", "伺服器", "SERVER", "AI供應鏈")):
        return "ai_power"
    if code in _AIRLINE_CODES or any(k in name for k in ("航空", "AIRLINES", "AIRWAYS")):
        return "airline"
    if code in _SHIPPING_CODES or any(k in name for k in ("海運", "航運", "SHIPPING", "MARINE")):
        return "shipping"
    if code in _ENERGY_CODES or any(k in name for k in ("石油", "能源", "油氣", "PETROLEUM", "ENERGY")):
        return "energy"
    if code in _DEFENSE_CODES or any(k in name for k in ("軍工", "國防", "航太", "無人機", "DEFENSE", "AEROSPACE")):
        return "defense"
    if code in _BIOTECH_CODES or any(k in name for k in ("生技", "生醫", "醫療", "新藥", "醣基", "BIOTECH", "PHARMA")):
        return "biotech"
    if any(k in name for k in ("金控", "銀行", "保險", "證券", "FINANCIAL", "BANK")):
        return "financial"
    if any(k in name for k in ("工業", "工具機", "機械", "汽車零組件", "INDUSTRIAL", "MACHINERY")):
        return "industrial_export"
    if any(k in name for k in ("零售", "食品", "消費", "RETAIL", "CONSUMER")):
        return "consumer"
    return "broad"


def exposure_for_profile(profile: str) -> Dict[str, Any]:
    key = str(profile or "broad").strip().lower()
    base = dict(_PROFILE_DEFAULTS.get("broad", {}))
    chosen = _PROFILE_DEFAULTS.get(key, _PROFILE_DEFAULTS["broad"])
    base.update({k: v for k, v in chosen.items() if k not in {"rule_multipliers", "direction_scales"}})
    multipliers = {rule: 1.0 for rule in _RULE_KEYS}
    multipliers.update(dict(chosen.get("rule_multipliers") or {}))
    directions = {rule: 1.0 for rule in _RULE_KEYS}
    directions.update(dict(chosen.get("direction_scales") or {}))
    base["profile"] = key if key in _PROFILE_DEFAULTS else "broad"
    base["rule_multipliers"] = multipliers
    base["direction_scales"] = directions
    return base


def build_ticker_event_exposure(ticker: TickerInfo | Mapping[str, Any] | Any) -> Dict[str, Any]:
    code, name, market, asset_type = _blob(ticker)
    profile = classify_ticker_profile(ticker)
    out = exposure_for_profile(profile)
    out.update({
        "ticker_code": code,
        "ticker_name": name,
        "market": market,
        "asset_type": asset_type,
        "source": "TINO_V1062_TICKER_EVENT_EXPOSURE",
        "decision_influence": "direction_and_risk_only",
        "price_veto": True,
    })
    return out


def _meta_tag(exposure: Mapping[str, Any]) -> str:
    return "|".join([
        f"ticker_profile={exposure.get('profile', 'broad')}",
        f"ticker_label={exposure.get('label', '大盤/一般產業')}",
        f"ticker_beta={float(exposure.get('market_beta') or 1.0):.2f}",
        f"ticker_code={exposure.get('ticker_code', '')}",
    ])


def annotate_global_event_news(
    ticker: TickerInfo | Mapping[str, Any] | Any,
    rows: Iterable[NewsItem] | None,
) -> List[NewsItem]:
    exposure = build_ticker_event_exposure(ticker)
    meta = _meta_tag(exposure)
    out: List[NewsItem] = []
    for item in rows or []:
        tag = str(getattr(item, "tag", "") or "")
        if "global_event_core" in tag and "ticker_profile=" not in tag:
            tag = f"{tag}|{meta}"
        out.append(NewsItem(
            str(getattr(item, "source", "") or ""),
            str(getattr(item, "time", "") or ""),
            float(getattr(item, "score", 0.0) or 0.0),
            tag,
            str(getattr(item, "title", "") or ""),
            str(getattr(item, "link", "") or ""),
        ))
    return out
