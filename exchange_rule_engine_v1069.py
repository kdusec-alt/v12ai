# -*- coding: utf-8 -*-
"""V1069 exchange-rule and intraday reachability guard.

Safety contract
---------------
- Never changes Direction, T0, T1, High, Low, Fair Value or Prediction DNA.
- `.TWO` is only a Yahoo/TPEx route marker; it never means unlimited moves.
- TWSE and TPEx ordinary shares share the same default ±10% principle.
- Emerging shares, overseas-underlying ETFs and US equities have no Taiwan-style
  fixed daily limit.
- Taiwan bounds use the current-session auction reference and directional tick
  rounding.  Corporate actions are not guessed from yesterday's close.
- When official product/reference metadata is unavailable, the result is marked
  pending and operational prices are not silently rewritten.
"""
from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any, Dict, Mapping, Optional

from models import TickerInfo


# Small, explicit high-confidence overrides.  Quote identity and future official
# product-master metadata are allowed to supersede these defaults.
_DOMESTIC_LEVERAGED_ETF = {
    "00631L": 0.20,  # 元大台灣50正2
}
_DOMESTIC_INVERSE_ETF = {
    "00632R": 0.10,  # 元大台灣50反1
}
_FOREIGN_NO_STATIC_LIMIT_ETF = {
    "00662",  # 富邦NASDAQ
    "00646",  # 元大S&P500
    "00668",  # 國泰美國道瓊
}

_FOREIGN_NAME_TOKENS = (
    "NASDAQ", "S&P", "道瓊", "費城", "SOX", "美國", "美股", "日本", "日經",
    "印度", "越南", "香港", "港股", "中國", "滬深", "上證", "國企", "全球",
    "原油", "黃金", "白銀", "美元", "美債", "海外", "歐洲", "歐元", "VIX",
)


def _code(value: Any) -> str:
    return str(value or "").strip().upper().split(".")[0]


def _text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _num(value: Any) -> Optional[float]:
    try:
        x = float(value)
        return x if math.isfinite(x) and x > 0 else None
    except Exception:
        return None


def tw_stock_tick(price: float) -> float:
    p = abs(float(price))
    if p < 10:
        return 0.01
    if p < 50:
        return 0.05
    if p < 100:
        return 0.1
    if p < 500:
        return 0.5
    if p < 1000:
        return 1.0
    return 5.0


def tw_etf_etn_tick(price: float) -> float:
    """TW ETF/ETN tick: below NT$50 = 0.01; NT$50 and above = 0.05."""
    return 0.01 if abs(float(price)) < 50 else 0.05


def tw_tick(price: float, product_family: str = "COMMON_STOCK") -> float:
    family = str(product_family or "").upper()
    if "ETF" in family or "ETN" in family:
        return tw_etf_etn_tick(price)
    return tw_stock_tick(price)


def _floor_to_tick(value: float, product_family: str) -> float:
    tick = tw_tick(value, product_family)
    return round(math.floor((float(value) + 1e-10) / tick) * tick, 2)


def _ceil_to_tick(value: float, product_family: str) -> float:
    tick = tw_tick(value, product_family)
    return round(math.ceil((float(value) - 1e-10) / tick) * tick, 2)


def tw_daily_price_bounds(
    reference_price: float,
    limit_pct: float,
    product_family: str = "COMMON_STOCK",
) -> tuple[float, float]:
    """Return (lower, upper) using Taiwan directional tick rounding.

    Lower limit is the nearest valid tick not below reference*(1-limit), while
    upper is the nearest valid tick not above reference*(1+limit).
    """
    reference = _num(reference_price)
    pct = _num(limit_pct)
    if reference is None or pct is None:
        raise ValueError("valid reference_price and limit_pct are required")
    lower = _ceil_to_tick(reference * (1.0 - pct), product_family)
    upper = _floor_to_tick(reference * (1.0 + pct), product_family)
    return lower, upper


def classify_static_rule(
    *,
    market: str,
    exchange: str,
    asset_type: str,
    symbol: str,
    name: str = "",
) -> Dict[str, Any]:
    """Classify the default fixed-limit regime without guessing exceptions."""
    market_u = str(market or "").upper()
    exchange_u = str(exchange or "").upper()
    asset_u = str(asset_type or "stock").lower()
    code = _code(symbol)
    name_u = _text(name)

    if market_u == "US":
        return {
            "market_family": "US",
            "product_family": "US_EQUITY",
            "fixed_daily_limit": False,
            "price_limit_pct": None,
            "rule_code": "US_LULD_DYNAMIC_BANDS",
            "rule_label": "美股無台式固定日漲跌停；採 LULD 動態價格帶／暫停機制",
            "rule_confidence": "high",
        }

    if market_u != "TW":
        return {
            "market_family": market_u or "UNKNOWN",
            "product_family": "UNKNOWN",
            "fixed_daily_limit": False,
            "price_limit_pct": None,
            "rule_code": "UNKNOWN_MARKET",
            "rule_label": "市場制度待確認",
            "rule_confidence": "low",
        }

    if exchange_u == "TPEX_EMERGING":
        return {
            "market_family": "TW",
            "product_family": "EMERGING_STOCK",
            "fixed_daily_limit": False,
            "price_limit_pct": None,
            "rule_code": "TW_EMERGING_NO_STATIC_LIMIT",
            "rule_label": "興櫃股票無固定漲跌幅限制",
            "rule_confidence": "high",
        }

    if asset_u in {"etf", "etn"} or code.startswith(("00", "02")):
        foreign_underlying = code in _FOREIGN_NO_STATIC_LIMIT_ETF or any(token in name_u for token in _FOREIGN_NAME_TOKENS)
        if foreign_underlying:
            return {
                "market_family": "TW",
                "product_family": "FOREIGN_UNDERLYING_ETF" if asset_u != "etn" else "FOREIGN_UNDERLYING_ETN",
                "fixed_daily_limit": False,
                "price_limit_pct": None,
                "rule_code": "TW_FOREIGN_PRODUCT_NO_STATIC_LIMIT",
                "rule_label": "海外成分／海外標的 ETF/ETN 無固定漲跌幅限制",
                "rule_confidence": "high" if name_u or code in _FOREIGN_NO_STATIC_LIMIT_ETF else "medium",
            }
        if code in _DOMESTIC_LEVERAGED_ETF or (code.endswith("L") and "正2" in name_u):
            pct = float(_DOMESTIC_LEVERAGED_ETF.get(code, 0.20))
            return {
                "market_family": "TW",
                "product_family": "DOMESTIC_LEVERAGED_ETF",
                "fixed_daily_limit": True,
                "price_limit_pct": pct,
                "rule_code": "TW_DOMESTIC_LEVERAGED_ETF",
                "rule_label": f"國內標的槓桿 ETF 固定漲跌幅 ±{pct*100:.0f}%",
                "rule_confidence": "high" if code in _DOMESTIC_LEVERAGED_ETF or name_u else "medium",
            }
        if code in _DOMESTIC_INVERSE_ETF or code.endswith("R"):
            pct = float(_DOMESTIC_INVERSE_ETF.get(code, 0.10))
            return {
                "market_family": "TW",
                "product_family": "DOMESTIC_INVERSE_ETF",
                "fixed_daily_limit": True,
                "price_limit_pct": pct,
                "rule_code": "TW_DOMESTIC_INVERSE_ETF",
                "rule_label": f"國內標的反向 ETF 固定漲跌幅 ±{pct*100:.0f}%",
                "rule_confidence": "high" if code in _DOMESTIC_INVERSE_ETF or name_u else "medium",
            }
        family = "DOMESTIC_ETN_OR_PENDING" if asset_u == "etn" or code.startswith("02") else "DOMESTIC_ETF_OR_PENDING"
        return {
            "market_family": "TW",
            "product_family": family,
            "fixed_daily_limit": True,
            "price_limit_pct": 0.10,
            "rule_code": "TW_DOMESTIC_PRODUCT_CONSERVATIVE_10",
            "rule_label": "國內成分 ETF/ETN 原則 ±10%；商品主檔待確認時採保守限制",
            "rule_confidence": "medium",
        }

    # TWSE and TPEx ordinary common stocks share the same default ±10% rule.
    # Initial-listing first-five-day exceptions require an official listing flag
    # and are never inferred from `.TW` / `.TWO` alone.
    return {
        "market_family": "TW",
        "product_family": "COMMON_STOCK",
        "fixed_daily_limit": True,
        "price_limit_pct": 0.10,
        "rule_code": "TW_COMMON_STOCK_10",
        "rule_label": "上市／上櫃普通股原則以當日開盤競價基準 ±10%",
        "rule_confidence": "high" if exchange_u in {"TWSE", "TPEX"} else "medium",
    }


def static_price_limit_pct(ticker: TickerInfo, *, quote_name: str = "") -> Optional[float]:
    return classify_static_rule(
        market=ticker.market,
        exchange=ticker.exchange,
        asset_type=ticker.asset_type,
        symbol=ticker.resolved_symbol,
        name=quote_name or ticker.name,
    ).get("price_limit_pct")


def ticker_with_exchange_rule(ticker: TickerInfo, *, quote_name: str = "") -> TickerInfo:
    pct = static_price_limit_pct(ticker, quote_name=quote_name)
    return ticker if ticker.price_limit_pct == pct else replace(ticker, price_limit_pct=pct)


def _apply_official_override(
    rule: Dict[str, Any],
    *,
    official_no_static_limit: bool = False,
    official_price_limit_pct: Any = None,
    official_rule_code: str = "",
) -> Dict[str, Any]:
    out = dict(rule)
    if official_no_static_limit:
        out.update({
            "fixed_daily_limit": False,
            "price_limit_pct": None,
            "rule_code": str(official_rule_code or "TW_OFFICIAL_NO_STATIC_LIMIT"),
            "rule_label": "官方商品／掛牌資料確認：本交易日無固定漲跌幅限制",
            "rule_confidence": "official",
        })
        return out
    pct = _num(official_price_limit_pct)
    if pct is not None:
        out.update({
            "fixed_daily_limit": True,
            "price_limit_pct": pct,
            "rule_code": str(official_rule_code or "TW_OFFICIAL_PRICE_LIMIT"),
            "rule_label": f"官方商品資料確認：固定漲跌幅 ±{pct*100:.0f}%",
            "rule_confidence": "official",
        })
    return out


def build_exchange_rule_snapshot(
    ticker: TickerInfo,
    *,
    reference_price: Any,
    current_price: Any = None,
    reference_source: str = "",
    historical_previous_close: Any = None,
    market_status: str = "",
    price_date: str = "",
    quote_name: str = "",
    official_no_static_limit: bool = False,
    official_price_limit_pct: Any = None,
    official_rule_code: str = "",
) -> Dict[str, Any]:
    rule = classify_static_rule(
        market=ticker.market,
        exchange=ticker.exchange,
        asset_type=ticker.asset_type,
        symbol=ticker.resolved_symbol,
        name=quote_name or ticker.name,
    )
    rule = _apply_official_override(
        rule,
        official_no_static_limit=official_no_static_limit,
        official_price_limit_pct=official_price_limit_pct,
        official_rule_code=official_rule_code,
    )
    reference = _num(reference_price)
    current = _num(current_price)
    historical = _num(historical_previous_close)
    source = str(reference_source or "")
    source_u = source.upper()
    official_reference = any(token in source_u for token in ("TWSE_MIS", "TPEX_MIS", "TWSE_OPENAPI", "TPEX_OPENAPI", "OFFICIAL_REFERENCE"))

    snapshot: Dict[str, Any] = {
        **rule,
        "symbol": ticker.resolved_symbol,
        "exchange": ticker.exchange,
        "reference_price": reference,
        "reference_source": source,
        "reference_verified": official_reference if ticker.market == "TW" else True,
        "market_status": str(market_status or ""),
        "price_date": str(price_date or ""),
        "historical_previous_close": historical,
        "reference_adjusted": False,
        "reference_adjustment_pct": None,
        "daily_lower": None,
        "daily_upper": None,
        "official_change_pct": None,
    }

    if reference and historical:
        adjustment_pct = (reference / historical - 1.0) * 100.0
        snapshot["reference_adjustment_pct"] = round(adjustment_pct, 4)
        # Large gaps can be ex-rights/dividend, capital reduction, split or a
        # source mismatch. Never name the corporate action without official data.
        snapshot["reference_adjusted"] = abs(adjustment_pct) >= 3.0

    if current and reference:
        snapshot["official_change_pct"] = round((current / reference - 1.0) * 100.0, 4)

    pct = rule.get("price_limit_pct")
    if bool(rule.get("fixed_daily_limit")) and reference and pct:
        lower, upper = tw_daily_price_bounds(reference, float(pct), str(rule.get("product_family") or "COMMON_STOCK"))
        snapshot["daily_lower"] = lower
        snapshot["daily_upper"] = upper

    if ticker.market == "TW" and bool(rule.get("fixed_daily_limit")) and not official_reference:
        snapshot["rule_confidence"] = "medium"
        snapshot["reference_note"] = "交易基準來自非官方即時來源；只做保守檢查，不硬改操作價"
    elif snapshot.get("reference_adjusted"):
        snapshot["reference_note"] = "當日基準與歷史前收差異明顯；疑似公司行動或基準調整，以官方基準為準"
    else:
        snapshot["reference_note"] = ""
    return snapshot


def attach_exchange_rule_context(
    context: Mapping[str, Any] | None,
    ticker: TickerInfo,
    **kwargs: Any,
) -> Dict[str, Any]:
    out = dict(context or {})
    out["exchange_rule"] = build_exchange_rule_snapshot(ticker, **kwargs)
    return out


def assess_today_reachability(entry_price: Any, rule_snapshot: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Check today's static exchange range; never invent a replacement entry."""
    entry = _num(entry_price)
    rule = dict(rule_snapshot or {})
    if entry is None:
        return {"status": "unknown", "reachable": None, "reason": "entry_missing"}
    if not rule:
        return {"status": "unknown", "reachable": None, "entry": entry, "reason": "rule_missing"}
    if not bool(rule.get("fixed_daily_limit")):
        return {
            "status": "no_static_daily_limit",
            "reachable": None,
            "entry": entry,
            "reason": str(rule.get("rule_code") or "dynamic_or_unlimited"),
        }
    lower = _num(rule.get("daily_lower"))
    upper = _num(rule.get("daily_upper"))
    if lower is None or upper is None or not bool(rule.get("reference_verified")):
        return {
            "status": "reference_pending",
            "reachable": None,
            "entry": entry,
            "lower": lower,
            "upper": upper,
            "reason": "official_reference_unverified",
        }
    if entry < lower - 1e-9:
        return {
            "status": "below_daily_lower",
            "reachable": False,
            "entry": entry,
            "lower": lower,
            "upper": upper,
            "reason": "entry_below_official_daily_limit",
        }
    if entry > upper + 1e-9:
        return {
            "status": "above_daily_upper",
            "reachable": False,
            "entry": entry,
            "lower": lower,
            "upper": upper,
            "reason": "entry_above_official_daily_limit",
        }
    return {
        "status": "reachable_today",
        "reachable": True,
        "entry": entry,
        "lower": lower,
        "upper": upper,
        "reason": "within_official_daily_range",
    }
