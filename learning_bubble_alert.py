# -*- coding: utf-8 -*-
"""Small, crash-isolated Bubble Radar reader for Learning Center.

The module reads only already-persisted prediction rows.  It never fetches
market data, creates a DataFrame, or changes any forecast/learning weight.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Mapping, Sequence


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _direct_snapshot(row: Mapping[str, Any]) -> Dict[str, Any]:
    value = row.get("bubble_radar")
    return dict(value) if isinstance(value, dict) else {}


def _legacy_snapshot(row: Mapping[str, Any]) -> Dict[str, Any]:
    radar = row.get("radar")
    text = (
        "\n".join(str(value or "") for value in radar.values())
        if isinstance(radar, dict)
        else str(radar or "")
    )
    if "AI泡沫雷達" not in text:
        return {}
    line = next((part for part in text.splitlines() if "AI泡沫雷達" in part), text)
    temp_match = re.search(r"(\d+(?:\.\d+)?)\s*℃", line)
    if not temp_match:
        return {}
    temperature = int(round(float(temp_match.group(1))))
    decision_match = re.search(r"Decision\s*([+-]?\d+(?:\.\d+)?)", line)
    quality_match = re.search(r"資料\s*(\d+(?:\.\d+)?)%", line)
    level_match = re.search(r"\d+(?:\.\d+)?\s*℃\s*([^｜\n]+)", line)
    parts = [part.strip() for part in line.split("｜") if part.strip()]
    ineligible = any(
        token in line
        for token in (
            "價格熱度觀察", "成長/價格觀察", "資料待補", "資料不足",
            "估值待確認", "不做泡沫結論", "不做完整泡沫結論",
        )
    )
    eligible = not ineligible
    return {
        "accepted": eligible,
        "bubble_conclusion_eligible": eligible,
        "temperature": temperature,
        "level": level_match.group(1).strip() if level_match else "",
        "alert": bool(eligible and temperature >= 60),
        "decision_adjustment": _safe_float(decision_match.group(1)) if decision_match else None,
        "quality": ((_safe_float(quality_match.group(1)) or 0.0) / 100.0) if quality_match else None,
        "reason": parts[-1] if len(parts) >= 2 else "",
        "legacy_parsed": True,
    }


def _snapshot(row: Mapping[str, Any]) -> Dict[str, Any]:
    return _direct_snapshot(row) or _legacy_snapshot(row)


def bubble_alert_rows(rows: Sequence[Dict[str, Any]], limit: int = 20) -> list[Dict[str, Any]]:
    """Return the latest accepted >=60°C state for each ticker."""
    latest: Dict[str, Dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: str((item or {}).get("run_time_tw") or "")):
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            latest[ticker] = row

    output: list[Dict[str, Any]] = []
    for ticker, row in latest.items():
        bubble = _snapshot(row)
        temperature = _safe_float(bubble.get("temperature", bubble.get("score")))
        eligible = bool(bubble.get("bubble_conclusion_eligible", bubble.get("accepted", False)))
        alert = bool(bubble.get("alert")) or bool(
            eligible and temperature is not None and temperature >= 60.0
        )
        if not alert or temperature is None:
            continue
        quality = _safe_float(bubble.get("quality"))
        output.append({
            "警示": "🚨 泡沫警戒" if temperature >= 75.0 else "🔴 高風險",
            "ticker": ticker,
            "market": row.get("market"),
            "temperature": int(round(temperature)),
            "level": bubble.get("level"),
            "decision": bubble.get("decision_adjustment"),
            "data_quality": None if quality is None else f"{quality * 100:.0f}%",
            "target_date": row.get("target_trade_date"),
            "reason": bubble.get("reason"),
            "run_time_tw": str(row.get("run_time_tw") or "")[:16].replace("T", " "),
        })
    output.sort(
        key=lambda row: (
            _safe_float(row.get("temperature")) or 0.0,
            str(row.get("run_time_tw") or ""),
        ),
        reverse=True,
    )
    return output[: max(1, int(limit))]
