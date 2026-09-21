# -*- coding: utf-8 -*-
"""V1101 concise, evidence-aware public decision brief.

This module is deliberately presentation-only.  It ranks already-arbitrated
evidence, removes correlated repetition and turns one immutable decision
snapshot into an executive trading brief.  It never invents a price or changes
the formal forecast, weights, audit trail or entry state.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _num(value: Any):
    try:
        if value in (None, "", "--", "NA"):
            return None
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _price(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "--"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    if abs(number) >= 100:
        return f"{number:,.1f}"
    return f"{number:,.2f}"


def _compact(text: Any, limit: int = 58) -> str:
    value = " ".join(str(text or "").replace("｜", " ").split())
    value = re.sub(r"^(主因|原因|目前動作|最終決策)[:：\s]*", "", value)
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip("，；、 ") + "…"


def _semantic_key(text: Any) -> str:
    value = re.sub(r"[\W_]+", "", str(text or "").lower())
    for token in ("等待", "確認", "目前", "今日", "資料", "價格", "主因", "條件"):
        value = value.replace(token, "")
    return value[:28]


def _direction_match(action_code: str, stance_value: int) -> int:
    if action_code == "BUY":
        return 2 if stance_value > 0 else 0 if stance_value == 0 else -2
    if action_code in {"SELL", "REDUCE", "BLOCK"}:
        return 2 if stance_value < 0 else 0 if stance_value == 0 else -1
    return 1 if stance_value != 0 else 0


def _ranked_reasons(snapshot: Mapping[str, Any], limit: int = 3) -> list[str]:
    reasoning = _mapping(snapshot.get("reasoning"))
    action_code = str(snapshot.get("action_code") or "HOLD").upper()
    rows = []
    for index, raw in enumerate(list(snapshot.get("evidence") or [])):
        row = _mapping(raw)
        if not row or not bool(row.get("accepted")):
            continue
        strength = int(_num(row.get("strength")) or 0)
        confidence = float(_num(row.get("confidence")) or 0)
        direction = int(_num(row.get("direction")) or 0)
        score = strength + confidence * 0.12 + _direction_match(action_code, direction) * 8
        rows.append((score, index, row))
    if not rows:
        rows = [
            (float(_num(row.get("strength")) or 0), index, _mapping(row))
            for index, row in enumerate(list(reasoning.get("top_drivers") or []))
            if isinstance(row, Mapping)
        ]

    selected: list[str] = []
    groups: set[str] = set()
    meanings: set[str] = set()
    for _, _, row in sorted(rows, key=lambda item: (item[0], -item[1]), reverse=True):
        group = str(row.get("correlation_group") or row.get("category") or row.get("label") or "")
        if group and group in groups:
            continue
        label = _compact(row.get("label") or row.get("category") or "證據", 16)
        reason = _compact(row.get("reason") or row.get("text") or "", 45)
        stance_value = int(_num(row.get("direction") if "direction" in row else row.get("stance_value")) or 0)
        stance = "偏多" if stance_value > 0 else "偏空" if stance_value < 0 else "中性"
        sentence = f"{label}{stance}：{reason}" if reason else f"{label}{stance}"
        key = _semantic_key(sentence)
        if not key or key in meanings:
            continue
        selected.append(sentence)
        meanings.add(key)
        if group:
            groups.add(group)
        if len(selected) >= limit:
            break
    return selected or ["有效證據不足，等待下一交易時段重新計算"]


def _actions(snapshot: Mapping[str, Any], plan: Mapping[str, Any]) -> tuple[str, str]:
    code = str(snapshot.get("action_code") or "BLOCK").upper()
    instruction = _compact(snapshot.get("instruction"), 74)
    invalid = _price(plan.get("invalidation_price"))
    confirm = _price(plan.get("confirmation_price"))
    breakout = _price(plan.get("add_price") or plan.get("breakout_price"))

    if code == "BUY":
        flat = instruction or "條件成立，小量分批建立首倉"
        holding = f"續抱；跌破 {invalid} 依紀律減碼" if invalid != "--" else "續抱並依失效條件管理"
    elif code == "SELL":
        flat = "空手，不接刀"
        holding = instruction or (f"跌破 {invalid} 執行退場" if invalid != "--" else "依失效條件退場")
    elif code == "REDUCE":
        flat = "空手不進場，等待結構修復"
        holding = instruction or (f"降低部位；守 {invalid}" if invalid != "--" else "降低部位風險")
    else:
        trigger = confirm if confirm != "--" else breakout
        flat = f"不進場；站回 {trigger} 後重評" if trigger != "--" else "不進場；等待資料與結構完成"
        holding = f"守 {invalid}；跌破執行風險管理" if invalid != "--" else "依既有失效條件管理"
    return flat, holding


def _candidate(plan: Mapping[str, Any]) -> Mapping[str, Any]:
    candidate = _mapping(plan.get("conditional_next_session"))
    if bool(candidate.get("eligible")):
        return candidate
    # The formal plan already contains a verified price ladder during premarket
    # and wait-for-confirmation states.  Present it as a conditional order,
    # rather than hiding it behind generic HOLD wording.
    zone = _mapping(plan.get("low_entry_zone"))
    if (
        _num(zone.get("lower")) is not None
        and _num(zone.get("upper")) is not None
        and _num(plan.get("confirmation_price")) is not None
        and _num(plan.get("invalidation_price")) is not None
    ):
        return {
            "eligible": True,
            "kind": "FORMAL_CONDITIONAL",
            "label": "條件式候選｜等待觸發",
            "entry_zone": zone,
            "entry_text": str(plan.get("low_entry_condition") or "回測區量縮止穩，先建立 1/3；未觸發不追價"),
            "confirmation_price": plan.get("confirmation_price"),
            "confirmation_text": str(plan.get("confirmation_text") or "站回確認價後再補 1/3"),
            "breakout_price": plan.get("add_price") or plan.get("breakout_price"),
            "breakout_text": str(plan.get("add_text") or plan.get("breakout_text") or "放量站穩後才考慮最後 1/3"),
            "invalidation_price": plan.get("invalidation_price"),
            "invalidation_text": str(plan.get("invalidation_text") or "跌破失效價取消條件單，不攤平"),
            "risk": "正式進場條件尚未觸發；僅在價格與量能同時確認後執行",
            "formal_gate_preserved": True,
        }
    return {}


def _zone_text(plan: Mapping[str, Any]) -> str:
    zone = _mapping(plan.get("entry_zone") or plan.get("low_entry_zone"))
    lower, upper = _num(zone.get("lower")), _num(zone.get("upper"))
    if lower is None or upper is None:
        return "等待"
    lo, hi = sorted((lower, upper))
    return f"{_price(lo)}～{_price(hi)}"


def build_decision_brief(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Build one concise public brief from the immutable decision snapshot."""
    snap = _mapping(snapshot)
    reasoning = _mapping(snap.get("reasoning"))
    plan = _mapping(snap.get("entry")) or _mapping(reasoning.get("recommended_entry"))
    action = _mapping(reasoning.get("action_decision"))
    label = str(action.get("label") or snap.get("label") or "禁止進場")
    reason = _compact(action.get("reason") or snap.get("reason"), 70)
    flat_action, holding_action = _actions(snap, plan)
    candidate = _candidate(plan)
    execution = candidate or plan
    candidate_mode = bool(candidate)
    if candidate_mode:
        label = str(candidate.get("label") or "條件式候選｜等待觸發")
        reason = _compact(candidate.get("risk") or reason, 70)
        flat_action = (
            f"{_zone_text(candidate)} 守穩先 1/3；站回 "
            f"{_price(candidate.get('confirmation_price'))} 再補 1/3"
        )
        holding_action = (
            f"守 {_price(candidate.get('invalidation_price'))}；未站回 "
            f"{_price(candidate.get('confirmation_price'))} 不加碼"
        )

    return {
        "schema": "TINO_DECISION_BRIEF_V1102",
        "verdict": label,
        "summary": reason or "跨模組尚未形成可執行共識",
        "reasons": _ranked_reasons(snap),
        "flat_action": flat_action,
        "holding_action": holding_action,
        "entry_zone": _zone_text(execution),
        "confirmation": _price(execution.get("confirmation_price")),
        "breakout": _price(execution.get("add_price") or execution.get("breakout_price")),
        "invalidation": _price(execution.get("invalidation_price")),
        "entry_instruction": str(execution.get("entry_text") or execution.get("low_entry_condition") or "等待結構完成"),
        "confirmation_instruction": str(execution.get("confirmation_text") or "確認後才加碼"),
        "breakout_instruction": str(execution.get("breakout_text") or "突破後才加碼"),
        "invalidation_instruction": str(execution.get("invalidation_text") or "條件失效即取消"),
        "candidate_mode": candidate_mode,
        "audit_preserved": True,
        "formal_model_unchanged": True,
    }
