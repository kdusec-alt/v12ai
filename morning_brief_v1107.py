# -*- coding: utf-8 -*-
"""Compact, cross-market, per-stock morning brief built from TINO snapshots.

This display layer preserves the user's symbol order and formal model values.
It does not rank stocks, re-arbitrate decisions, or scan the whole market.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from decision_brief_v1101 import build_decision_brief

_TW_INDUSTRIES = {
    "3702": "電子通路", "3045": "電信", "2303": "半導體",
    "6446": "生技製藥", "2542": "營建", "1303": "塑化材料",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    try:
        match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(value or ""))
        return float(match.group(0).replace(",", "")) if match else None
    except Exception:
        return None


def _industry(price: Any) -> str:
    context = _mapping(getattr(price, "context", {}))
    fundamental = _mapping(context.get("fundamental"))
    persona = _mapping(context.get("persona"))
    for key in ("industry", "sector", "industry_name", "category"):
        if fundamental.get(key):
            return str(fundamental[key])
    if persona.get("label"):
        return str(persona["label"])
    ticker = getattr(price, "ticker", None)
    if str(getattr(ticker, "market", "")) == "TW":
        code = str(getattr(ticker, "resolved_symbol", "")).split(".", 1)[0]
        if code in _TW_INDUSTRIES:
            return _TW_INDUSTRIES[code]
    return "產業資料未同步"


def _evidence(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        dict(item) for item in snapshot.get("evidence", [])
        if isinstance(item, Mapping) and item.get("accepted") and item.get("verified")
    ]
    # One representative per independent evidence family avoids rewarding
    # repeated/correlated fields as if they were separate confirmations.
    families: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row.get("correlation_group") or row.get("family") or row.get("label") or "unknown")
        old = families.get(family)
        if old is None or float(row.get("strength") or 0) > float(old.get("strength") or 0):
            families[family] = row
    selected = sorted(
        families.values(),
        key=lambda row: (float(row.get("strength") or 0), float(row.get("confidence") or 0)),
        reverse=True,
    )
    return selected[:3]


def build_morning_brief_row(forecast: Any) -> dict[str, Any]:
    """Project one formal analysis into the user's per-stock briefing format."""
    ticker = getattr(forecast, "ticker", None)
    price = getattr(forecast, "price_frame", None)
    symbol = str(getattr(ticker, "resolved_symbol", "") or "")
    code = symbol.split(".", 1)[0] if str(getattr(ticker, "market", "")) == "TW" else symbol
    snapshot_obj = getattr(forecast, "decision_snapshot", None)
    snapshot = snapshot_obj.to_dict() if callable(getattr(snapshot_obj, "to_dict", None)) else {}
    if not snapshot:
        card = _mapping(getattr(forecast, "decision_card", {}))
        snapshot = _mapping(card.get("_decision_snapshot_v1096"))
    radar = _mapping(getattr(forecast, "radar", {}))
    brief = build_decision_brief(snapshot, radar=radar)
    entry = _mapping(snapshot.get("entry"))
    reasoning = _mapping(snapshot.get("reasoning"))
    execution = _mapping(reasoning.get("recommended_entry")) or entry
    if brief.get("candidate_mode"):
        execution = _mapping(entry.get("conditional_next_session")) or execution

    last = _number(getattr(price, "last", None)) if price is not None else None
    previous_close = _number(getattr(price, "previous_close", None)) if price is not None else None
    stop = _number(execution.get("invalidation_price") or execution.get("stop_price"))
    evidence = _evidence(snapshot)
    confidence = max(0.0, min(100.0, float(getattr(forecast, "confidence", 0.0) or 0.0)))
    truth = getattr(price, "truth", None) if price is not None else None
    accepted_truth = bool(getattr(truth, "accepted", False)) and not bool(getattr(truth, "fallback", True))
    try:
        from stock_analysis_narrative_v1108 import build_stock_analysis
        narrative = build_stock_analysis(forecast, brief)
    except Exception:
        narrative = {}

    evidence_text = "；".join(
        f"{row.get('label') or row.get('family')}:{'偏多' if int(row.get('direction') or 0) > 0 else '偏空' if int(row.get('direction') or 0) < 0 else '中性'}"
        for row in evidence[:2]
    ) or "有效證據不足"
    source_text = "、".join(dict.fromkeys(str(row.get("source") or "") for row in evidence[:2] if row.get("source"))) or "來源待確認"

    return {
        "symbol": code,
        "name": str(getattr(ticker, "name", "") or code),
        "market": str(getattr(ticker, "market", "") or ""),
        "industry": narrative.get("industry") or (_industry(price) if price is not None else "產業資料未同步"),
        "price_date": str(getattr(price, "price_date", "") or "") if price is not None else "",
        "previous_close": previous_close,
        "last": last,
        "price_status": (narrative.get("price_status") or "價格／日期未驗證，不列參考") if accepted_truth else "價格／日期未驗證，不列參考",
        "action": str(brief.get("verdict") or snapshot.get("label") or "禁止進場"),
        "entry": str(narrative.get("entry") or brief.get("staged_entry") or brief.get("entry_instruction") or "條件未形成，等待確認"),
        "invalidation": str(brief.get("invalidation") or "待確認"),
        "risk": str(narrative.get("risk") or brief.get("primary_risk") or snapshot.get("reason") or "主要風險資料不足"),
        "risk_cell": str(narrative.get("risk") or "主要風險資料不足"),
        "narrative_evidence": str(narrative.get("evidence") or "有效證據不足；來源待確認"),
        "evidence": evidence_text,
        "sources": source_text,
        "confidence": round(confidence),
        "accepted_truth": accepted_truth,
        "candidate_mode": bool(brief.get("candidate_mode")),
        "action_code": str(snapshot.get("action_code") or "BLOCK").upper(),
    }


def analyze_candidate_symbols(symbols: list[str], *, price_fetcher, news_fetcher, orchestrator) -> list[dict[str, Any]]:
    """Run the established per-symbol pipeline for an explicit bounded list."""
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            price = price_fetcher(symbol)
            news = news_fetcher(symbol)
            forecast = orchestrator(price, news_items=news)
            rows.append(build_morning_brief_row(forecast))
        except Exception as exc:
            rows.append({
                "symbol": str(symbol), "name": str(symbol), "market": "",
                "industry": "產業資料未同步", "price_date": "", "price_status": "價格／日期未驗證，不列參考",
                "previous_close": None, "last": None,
                "action": "分析未完成", "entry": "不建立新部位；先確認資料源",
                "invalidation": "待確認", "risk": f"本檔分析失敗：{type(exc).__name__}",
                "risk_cell": f"本檔分析失敗：{type(exc).__name__}",
                "narrative_evidence": "有效證據不足；來源待確認",
                "evidence": "有效證據不足", "sources": "來源待確認",
                "confidence": 0,
                "accepted_truth": False, "candidate_mode": False,
                "action_code": "BLOCK",
            })
    return rows
