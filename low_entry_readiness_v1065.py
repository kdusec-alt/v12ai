# -*- coding: utf-8 -*-
"""V1065 execution-price explanation and narrative consistency guard.

This module never recalculates forecast prices.  The existing decision-card
fields (first/second batch, transition confirmation, defence and no-chase) are
the single source of truth.  It answers what price/action the user is waiting
for and normalises any stale prices left in the AI narrative before rendering.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping

from low_entry_readiness_v1064 import assess_low_entry_readiness as _assess_v1064


_NUMBER = r"-?\d+(?:,\d{3})*(?:\.\d+)?"


def _num(value: Any) -> float | None:
    try:
        if value in (None, "", "--", "NA", "待同步", "暫停"):
            return None
        return float(str(value).replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def _first_number(value: Any) -> float | None:
    match = re.search(_NUMBER, str(value or ""))
    return _num(match.group(0)) if match else None


def _price(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:,.2f}"


def _canonical_prices(decision: Mapping[str, Any]) -> Dict[str, float | None]:
    """Read, but never recalculate, the prices already produced by the engine."""
    return {
        "last": _num(decision.get("現價")),
        "first": _num(decision.get("低接第一批")),
        "second": _num(decision.get("低接第二批")),
        "confirmation": _first_number(decision.get("轉強")) or _first_number(decision.get("攻擊")),
        "stop": _num(decision.get("防守")),
        "no_chase": _num(decision.get("不追")),
    }


def _replace_price_pattern(text: str, pattern: str, value: float | None, replacement) -> tuple[str, bool]:
    if value is None:
        return text, False
    changed = False

    def repl(match: re.Match[str]) -> str:
        nonlocal changed
        old_number = _num(match.group("price"))
        if old_number is None or abs(old_number - value) > max(0.005, abs(value) * 0.00001):
            changed = True
        return replacement(match, _price(value))

    return re.sub(pattern, repl, text, flags=re.I), changed


def normalise_price_narrative(message: Any, decision: Mapping[str, Any], readiness_color: str) -> Dict[str, Any]:
    """Align operational prices and action semantics with the five tactical cards.

    Only numbers next to explicit operational verbs are touched.  Event countdowns,
    percentages and macro figures remain unchanged.
    """
    original = str(message or "").strip()
    text = original
    prices = _canonical_prices(decision)
    corrections: list[str] = []

    rules = (
        (
            rf"(?P<prefix>(?:未)?站(?:穩|回)\s*)(?P<price>{_NUMBER})",
            prices["confirmation"],
            lambda m, p: f"{m.group('prefix')}{p}",
            "轉強確認價",
        ),
        (
            rf"(?P<prefix>回測\s*)(?P<price>{_NUMBER})",
            prices["first"],
            lambda m, p: f"{m.group('prefix')}{p}",
            "第一批低接價",
        ),
        (
            rf"(?P<prefix>第一批(?:低接)?(?:區)?\s*)(?P<price>{_NUMBER})",
            prices["first"],
            lambda m, p: f"{m.group('prefix')}{p}",
            "第一批低接價",
        ),
        (
            rf"(?P<prefix>第二批(?:低接)?(?:區)?\s*)(?P<price>{_NUMBER})",
            prices["second"],
            lambda m, p: f"{m.group('prefix')}{p}",
            "第二批低接價",
        ),
        (
            rf"(?P<prefix>(?:跌)?破\s*)(?P<price>{_NUMBER})(?P<suffix>\s*(?:停|停止|收不回|失效))",
            prices["stop"],
            lambda m, p: f"{m.group('prefix')}{p}{m.group('suffix')}",
            "防守價",
        ),
        (
            rf"(?P<price>{_NUMBER})(?P<suffix>\s*上方(?:急拉)?不追)",
            prices["no_chase"],
            lambda m, p: f"{p}{m.group('suffix')}",
            "不追價",
        ),
    )

    for pattern, value, replacement, label in rules:
        text, changed = _replace_price_pattern(text, pattern, value, replacement)
        if changed and label not in corrections:
            corrections.append(label)

    # A yellow/red readiness gate must never coexist with an aggressive immediate-buy instruction.
    if readiness_color != "green":
        aggressive = r"立即買進|直接買進|立即進場|直接進場|全倉|重倉追價|無條件買進"
        if re.search(aggressive, text):
            text = re.sub(aggressive, "等待條件確認後再分批", text)
            corrections.append("操作語意")

    return {
        "text": text or original,
        "original": original,
        "corrected": bool(corrections),
        "corrections": corrections,
        "prices": prices,
        "consistent": True,
    }


def _wait_plan(forecast: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    decision = dict(getattr(forecast, "decision_card", {}) or {})
    prices = _canonical_prices(decision)
    last = prices["last"]
    first = prices["first"]
    second = prices["second"]
    stop = prices["stop"]
    no_chase = prices["no_chase"]
    confirmation = prices["confirmation"]
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
            "no_chase": no_chase,
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
            "no_chase": no_chase,
        }

    if first is not None and last is not None and last > first:
        routes = [f"A 回測 {_price(first)} 附近止穩"]
        if confirmation is not None:
            routes.append(f"B 站穩 {_price(confirmation)} 轉強確認")
        text = "等待：" + "；".join(routes) + f"；{invalid_text}。"
    elif second is not None and last is not None and last <= second:
        text = f"等待：已到第二批極限區 {_price(second)}，只在止穩與籌碼改善後執行；{invalid_text}。"
    elif first is not None:
        second_text = f"；若續探 {_price(second)}，只在止穩時執行第二批" if second is not None else ""
        text = f"等待：第一批區 {_price(first)} 完成止穩{second_text}；{invalid_text}。"
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
        "no_chase": no_chase,
    }


def assess_low_entry_readiness(forecast: Any) -> Dict[str, Any]:
    result = dict(_assess_v1064(forecast) or {})
    decision = dict(getattr(forecast, "decision_card", {}) or {})
    plan = _wait_plan(forecast, result)
    consistency = normalise_price_narrative(
        decision.get("主訊息"),
        decision,
        str(result.get("color") or "yellow"),
    )
    result["context_summary"] = str(result.get("summary") or "")
    result["wait_plan"] = plan
    result["wait_text"] = str(plan.get("text") or "")
    result["canonical_main_message"] = str(consistency.get("text") or decision.get("主訊息") or "")
    result["price_consistency"] = consistency
    # The condition chips explain why; the headline must answer what price/action
    # is being awaited, using exactly the same values as the tactical cards.
    result["summary"] = result["wait_text"] or result["context_summary"]
    return result
