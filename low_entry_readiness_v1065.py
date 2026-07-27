# -*- coding: utf-8 -*-
"""V1065 execution-price explanation layered on top of V1064 readiness.

This module does not recalculate any forecast price.  It only translates the
existing first/second batch, confirmation, defence and no-chase prices into a
clear answer to: what exactly are we waiting for?
"""
from __future__ import annotations

import re
from typing import Any, Dict

from low_entry_readiness_v1064 import assess_low_entry_readiness as _assess_v1064


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "--", "NA", "待同步", "暫停"):
            return None
        return float(str(value).replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def _first_number(value: Any) -> float | None:
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", str(value or ""))
    return _num(match.group(0)) if match else None


def _price(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:,.2f}"


def _wait_plan(forecast: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(getattr(forecast, "decision_card", {}) or {})
    last = _num(d.get("現價"))
    first = _num(d.get("低接第一批"))
    second = _num(d.get("低接第二批"))
    stop = _num(d.get("防守"))
    no_chase = _num(d.get("不追"))
    confirmation = _first_number(d.get("轉強")) or _first_number(d.get("攻擊"))
    color = str(result.get("color") or "yellow")
    blockers = list(result.get("hard_blockers") or [])

    invalid_text = f"跌破 {_price(stop)}，低接計畫失效" if stop is not None else "跌破防守條件，低接計畫失效"

    if color == "green":
        action = (
            f"可執行：{_price(first)} 附近分批，仍須確認止穩；{invalid_text}。"
            if first is not None
            else f"可執行低接，但仍須確認止穩；{invalid_text}。"
        )
        return {
            "mode": "ready",
            "text": action,
            "pullback": first,
            "confirmation": confirmation,
            "second": second,
            "invalid": stop,
        }

    if blockers:
        if stop is not None and last is not None and last < stop:
            text = f"暫停低接：先重新站回 {_price(stop)} 並完成築底，再重新評估第一批 {_price(first)}。"
        elif no_chase is not None and last is not None and last >= no_chase:
            text = f"等待回測 {_price(first)} 附近，不在 {_price(no_chase)} 以上追價；{invalid_text}。"
        else:
            text = f"暫停低接：等待價格重新驗證；{invalid_text}。"
        return {
            "mode": "blocked",
            "text": text,
            "pullback": first,
            "confirmation": confirmation,
            "second": second,
            "invalid": stop,
        }

    if first is not None and last is not None and last > first:
        routes = [f"A 回測 {_price(first)} 附近止穩"]
        if confirmation is not None:
            routes.append(f"B 站穩 {_price(confirmation)} 轉強確認")
        text = "等待：" + "；".join(routes) + f"；{invalid_text}。"
    elif second is not None and last is not None and last <= second:
        text = f"等待：已到第二批極限區 {_price(second)}，只在止穩與籌碼改善後執行；{invalid_text}。"
    elif first is not None:
        text = f"等待：第一批區 {_price(first)} 完成止穩；若續探 {_price(second)}，只在止穩時執行第二批；{invalid_text}。"
    elif confirmation is not None:
        text = f"等待：站穩 {_price(confirmation)} 完成轉強確認；{invalid_text}。"
    else:
        text = f"等待價格止穩與法人／籌碼確認；{invalid_text}。"

    return {
        "mode": "wait",
        "text": text,
        "pullback": first,
        "confirmation": confirmation,
        "second": second,
        "invalid": stop,
    }


def assess_low_entry_readiness(forecast: Any) -> Dict[str, Any]:
    result = dict(_assess_v1064(forecast) or {})
    plan = _wait_plan(forecast, result)
    result["context_summary"] = str(result.get("summary") or "")
    result["wait_plan"] = plan
    result["wait_text"] = str(plan.get("text") or "")
    # The condition chips below the headline already explain why.  The headline
    # must answer what price/action the user is waiting for.
    result["summary"] = result["wait_text"] or result["context_summary"]
    return result
