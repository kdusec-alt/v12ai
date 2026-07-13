# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Dict

from models import TickerInfo


def ticker_with_quote_identity(ticker: TickerInfo, quote: Dict[str, object] | None) -> TickerInfo:
    """Use official quote name for unknown numeric symbols without changing routing."""
    if not isinstance(quote, dict):
        return ticker
    quote_name = re.sub(r"\s+", " ", str(quote.get("quote_name") or "")).strip()
    code = str(ticker.resolved_symbol or "").split(".")[0]
    if not quote_name or str(ticker.name or "").strip() not in {"", code}:
        return ticker
    return TickerInfo(
        raw=ticker.raw,
        resolved_symbol=ticker.resolved_symbol,
        name=quote_name,
        market=ticker.market,
        asset_type=ticker.asset_type,
        exchange=ticker.exchange,
        currency=ticker.currency,
        price_limit_pct=ticker.price_limit_pct,
    )


def attach_market_microstructure(
    context: Dict[str, object],
    ticker: TickerInfo,
    *,
    history_count: int,
    volume: float,
    is_emerging: bool,
) -> Dict[str, object]:
    """Attach a compact SSOT describing TWSE/TPEx/emerging data coverage."""
    price_meta = context.get("price_meta") if isinstance(context.get("price_meta"), dict) else {}
    inst = context.get("inst") if isinstance(context.get("inst"), dict) else {}
    margin = context.get("margin") if isinstance(context.get("margin"), dict) else {}
    is_tpex = str(ticker.resolved_symbol).upper().endswith(".TWO")
    mode = "興櫃資訊模式" if is_emerging else "上櫃資訊模式" if is_tpex else "上市資訊模式"
    coverage = (2 if bool(price_meta.get("price_verified")) else 1 if not bool(price_meta.get("decision_blocked")) else 0)
    coverage += 1 if history_count >= 20 else 0
    coverage += 1 if bool(inst.get("accepted")) else 0
    coverage += 1 if bool(margin.get("accepted")) else 0
    liquidity = "薄量" if float(volume or 0) < 100_000 else "中量" if float(volume or 0) < 1_000_000 else "正常"
    context["market_microstructure"] = {
        "mode": mode,
        "exchange": ticker.exchange,
        "is_tpex": is_tpex,
        "is_emerging": bool(is_emerging),
        "history_count": int(max(history_count, 0)),
        "price_verified": bool(price_meta.get("price_verified")),
        "limited_price_mode": bool(price_meta.get("limited_price_mode")),
        "institution_available": bool(inst.get("accepted")),
        "margin_available": bool(margin.get("accepted")),
        "liquidity": liquidity,
        "coverage_score": coverage,
        "missing_is_not_zero": True,
    }
    return context
