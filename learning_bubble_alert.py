# -*- coding: utf-8 -*-
"""Crash-isolated Bubble Monitor for Learning Center.

Reads only persisted prediction rows.  It never fetches market data, creates a
DataFrame, mutates model weights, or writes new predictions.  All calculations
are bounded scalar/list operations so a malformed row cannot affect the app.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any, Dict, Mapping, Sequence

_TW = timezone(timedelta(hours=8))


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TW)
        return dt.astimezone(_TW)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt).replace(tzinfo=_TW)
        except Exception:
            continue
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
    ineligible = any(token in line for token in (
        "價格熱度觀察", "成長/價格觀察", "資料待補", "資料不足",
        "估值待確認", "不做泡沫結論", "不做完整泡沫結論",
    ))
    eligible = not ineligible
    return {
        "accepted": eligible,
        "bubble_conclusion_eligible": eligible,
        "temperature": temperature,
        "score": temperature,
        "level": level_match.group(1).strip() if level_match else "",
        "alert": bool(eligible and temperature >= 60),
        "decision_adjustment": _safe_float(decision_match.group(1)) if decision_match else None,
        "quality": ((_safe_float(quality_match.group(1)) or 0.0) / 100.0) if quality_match else None,
        "reason": parts[-1] if len(parts) >= 2 else "",
        "legacy_parsed": True,
    }


def _snapshot(row: Mapping[str, Any]) -> Dict[str, Any]:
    return _direct_snapshot(row) or _legacy_snapshot(row)


def _eligible_snapshot(row: Mapping[str, Any]) -> Dict[str, Any] | None:
    bubble = _snapshot(row)
    temperature = _safe_float(bubble.get("temperature", bubble.get("score")))
    eligible = bool(bubble.get("bubble_conclusion_eligible", bubble.get("accepted", False)))
    if not eligible or temperature is None:
        return None
    out = dict(bubble)
    out["temperature"] = float(temperature)
    return out


def _history(rows: Sequence[Dict[str, Any]], max_rows: int = 1200) -> Dict[str, list[Dict[str, Any]]]:
    result: Dict[str, list[Dict[str, Any]]] = {}
    bounded = [row for row in list(rows or [])[-max(1, int(max_rows)):] if isinstance(row, dict)]
    for row in bounded:
        ticker = str(row.get("ticker") or "").strip().upper()
        stamp = _parse_time(row.get("run_time_tw") or row.get("run_date_tw"))
        bubble = _eligible_snapshot(row)
        if not ticker or stamp is None or bubble is None:
            continue
        result.setdefault(ticker, []).append({"row": row, "bubble": bubble, "time": stamp})
    for items in result.values():
        items.sort(key=lambda item: item["time"])
        # Keep one final state per ticker per calendar date.  This removes rerun
        # noise but preserves a true daily series for 7d/30d trend calculations.
        daily: Dict[str, Dict[str, Any]] = {}
        for item in items:
            daily[item["time"].date().isoformat()] = item
        items[:] = list(daily.values())[-45:]
    return result


def _baseline(items: Sequence[Dict[str, Any]], latest_time: datetime, days: int) -> Dict[str, Any] | None:
    if len(items) < 2:
        return None
    target = latest_time - timedelta(days=max(1, int(days)))
    candidates = list(items[:-1])
    if not candidates:
        return None
    # Use the historical sample nearest the requested 7d/30d anchor.  This is
    # more faithful than always selecting an older row when the market was not
    # analysed on the exact calendar day.
    return min(candidates, key=lambda item: abs((item["time"] - target).total_seconds()))


def _delta(items: Sequence[Dict[str, Any]], days: int) -> float | None:
    if len(items) < 2:
        return None
    latest = items[-1]
    base = _baseline(items, latest["time"], days)
    if base is None:
        return None
    return round(float(latest["bubble"]["temperature"]) - float(base["bubble"]["temperature"]), 1)


def _fmt_delta(value: float | None) -> str:
    return "--" if value is None else f"{value:+.0f}℃"


def _latest_row(ticker: str, item: Dict[str, Any], d7: float | None, d30: float | None) -> Dict[str, Any]:
    row = item["row"]
    bubble = item["bubble"]
    quality = _safe_float(bubble.get("quality"))
    temp = int(round(float(bubble["temperature"])))
    return {
        "rank": 0,
        "ticker": ticker,
        "market": row.get("market"),
        "temperature": temp,
        "level": bubble.get("level"),
        "trend_7d": _fmt_delta(d7),
        "trend_30d": _fmt_delta(d30),
        "decision": bubble.get("decision_adjustment"),
        "data_quality": None if quality is None else f"{quality * 100:.0f}%",
        "reason": bubble.get("reason"),
        "run_time_tw": item["time"].strftime("%Y-%m-%d %H:%M"),
    }


def bubble_monitor(rows: Sequence[Dict[str, Any]], rank_limit: int = 20) -> Dict[str, list[Dict[str, Any]]]:
    """Build ranking, trends, threshold-crossing alerts and cooling events."""
    histories = _history(rows)
    ranking: list[Dict[str, Any]] = []
    trend_rows: list[Dict[str, Any]] = []
    new_alerts: list[Dict[str, Any]] = []
    cooling: list[Dict[str, Any]] = []

    for ticker, items in histories.items():
        if not items:
            continue
        latest = items[-1]
        d7 = _delta(items, 7)
        d30 = _delta(items, 30)
        current = _latest_row(ticker, latest, d7, d30)
        ranking.append(current)

        if d7 is not None or d30 is not None:
            trend_rows.append(dict(current))

        if len(items) >= 2:
            previous = items[-2]
            current_temp = float(latest["bubble"]["temperature"])
            previous_temp = float(previous["bubble"]["temperature"])
            if current_temp >= 60.0 and previous_temp < 60.0:
                event = dict(current)
                event["event"] = "🔥 首次突破60℃"
                event["from_to"] = f"{previous_temp:.0f}℃ → {current_temp:.0f}℃"
                new_alerts.append(event)
            if previous_temp >= 60.0 and current_temp <= 40.0:
                event = dict(current)
                event["event"] = "❄ 降溫解除"
                event["from_to"] = f"{previous_temp:.0f}℃ → {current_temp:.0f}℃"
                cooling.append(event)

    ranking.sort(key=lambda row: (row["temperature"], row["run_time_tw"]), reverse=True)
    for idx, row in enumerate(ranking[: max(1, int(rank_limit))], 1):
        row["rank"] = idx

    trend_rows.sort(
        key=lambda row: (
            _safe_float(str(row.get("trend_7d") or "").replace("℃", "")) or -999.0,
            _safe_float(str(row.get("trend_30d") or "").replace("℃", "")) or -999.0,
            row.get("temperature") or 0,
        ),
        reverse=True,
    )
    new_alerts.sort(key=lambda row: row.get("temperature") or 0, reverse=True)
    cooling.sort(key=lambda row: row.get("run_time_tw") or "", reverse=True)

    return {
        "ranking": ranking[: max(1, int(rank_limit))],
        "trends": trend_rows[: max(1, int(rank_limit))],
        "new_alerts": new_alerts[:20],
        "cooling": cooling[:20],
    }


def bubble_alert_rows(rows: Sequence[Dict[str, Any]], limit: int = 20) -> list[Dict[str, Any]]:
    """Compatibility helper: latest accepted >=60°C state for each ticker."""
    output = []
    for row in bubble_monitor(rows, max(limit, 20))["ranking"]:
        temperature = _safe_float(row.get("temperature"))
        if temperature is None or temperature < 60:
            continue
        alert = dict(row)
        alert["警示"] = "🚨 泡沫警戒" if temperature >= 75 else "🔴 高風險"
        alert["target_date"] = ""
        output.append(alert)
    return output[: max(1, int(limit))]
