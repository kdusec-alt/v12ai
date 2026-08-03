# -*- coding: utf-8 -*-
"""V1083.1 evidence arbitration + dual-path recommended entry map.

Narrative/execution only: reuses V1081 entry state and verified session prices.
It never mutates formal forecasts, learning samples, weights, calibration state,
or the V1081 entry-state engine.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Mapping

from evidence_reasoning_v1082 import build_evidence_reasoning as build_v1082

SCHEMA = "TINO_EVIDENCE_ARBITRATION_V1083_1"


def _text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _map(v: Any) -> Dict[str, Any]:
    return dict(v) if isinstance(v, Mapping) else {}


def _num(v: Any) -> float | None:
    try:
        if v in (None, "", "--", "NA"):
            return None
        n = float(str(v).replace(",", "").replace("%", "").strip())
        return n if math.isfinite(n) else None
    except Exception:
        return None


def _first_num(v: Any) -> float | None:
    m = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", _text(v))
    return _num(m.group(0)) if m else None


def _price(v: Any) -> str:
    n = _num(v)
    if n is None:
        return "--"
    if abs(n) >= 1000:
        return f"{n:,.0f}"
    if abs(n) >= 100:
        return f"{n:,.2f}".rstrip("0").rstrip(".")
    return f"{n:,.2f}"


def _range_text(lower: float | None, upper: float | None) -> str:
    if lower is None and upper is None:
        return "--"
    if lower is None:
        return _price(upper)
    if upper is None or abs(upper - lower) <= max(abs(lower) * 0.0002, 0.01):
        return _price(lower)
    lo, hi = sorted((lower, upper))
    return f"{_price(lo)}～{_price(hi)}"


def _short(v: Any, limit: int = 56) -> str:
    s = _text(v).strip("｜ ")
    return s if len(s) <= limit else s[: limit - 1].rstrip("，；｜ ") + "…"


def _stars(score: int) -> str:
    c = 5 if score >= 88 else 4 if score >= 76 else 3 if score >= 62 else 2 if score >= 48 else 1
    return "★" * c + "☆" * (5 - c)


def _radar(radar: Mapping[str, Any], key: str) -> str:
    v = radar.get(key)
    if isinstance(v, Mapping):
        return "｜".join(f"{k}:{x}" for k, x in v.items() if not str(k).startswith("_"))
    return _text(v)


def _named_pct(text: str, label: str) -> float | None:
    m = re.search(rf"{re.escape(label)}\s*[:：]?\s*([+-]?\d+(?:\.\d+)?)\s*%?", text, re.I)
    return _num(m.group(1)) if m else None


def _abc(radar: Mapping[str, Any]) -> Dict[str, Any] | None:
    text = _radar(radar, "ABC 多空情境")
    if not text:
        return None
    a, b, c = (_named_pct(text, x) for x in ("A突破", "B回測", "C防守"))
    if a is None and b is None and c is None:
        return None
    av, bv, cv = float(a or 0), float(b or 0), float(c or 0)
    spread = av - bv
    stance = -1 if cv >= 20 or spread <= -12 else 1 if av >= 45 and spread >= 8 else 0
    score = int(max(48, min(92, 58 + abs(spread) + cv * .35)))
    verdict = "突破占優" if stance > 0 else "回測／防守優先" if stance < 0 else "突破與回測接近"
    return {
        "category": "abc", "label": "ABC情境", "stance_value": stance,
        "stance": "偏多" if stance > 0 else "偏空" if stance < 0 else "中性",
        "strength": score, "stars": _stars(score), "verified": True,
        "text": f"A突破 {av:.0f}%｜B回測 {bv:.0f}%｜C防守 {cv:.0f}%｜{verdict}",
        "a": a, "b": b, "c": c,
    }


def _quantum(radar: Mapping[str, Any]) -> Dict[str, Any] | None:
    text = _radar(radar, "Quantum 貢獻")
    if not text:
        return None
    m = re.search(r"(?:方向總分|趨勢)\s*[:：]?\s*([+-]?\d+(?:\.\d+)?)", text, re.I)
    if not m:
        return None
    q = float(_num(m.group(1)) or 0)
    stance = 1 if q >= 2 else -1 if q <= -2 else 0
    score = int(max(48, min(93, 58 + abs(q) * 1.3)))
    parts = [p.strip() for p in text.replace(" | ", "｜").split("｜") if p.strip()]
    parts = [p for p in parts if any(k in p for k in ("方向總分", "趨勢", "價格結構", "收盤位置", "籌碼", "法人"))][:4]
    return {
        "category": "quantum", "label": "Quantum模型", "stance_value": stance,
        "stance": "偏多" if stance > 0 else "偏空" if stance < 0 else "中性",
        "strength": score, "stars": _stars(score), "verified": True,
        "text": _short("｜".join(parts) or f"方向總分 {q:+.1f}", 80), "direction": q,
    }


def _base_packet(row: Mapping[str, Any]) -> Dict[str, Any]:
    stance = _text(row.get("stance")) or "中性"
    value = 1 if stance == "偏多" else -1 if stance == "偏空" else 0
    category = _text(row.get("category")) or "other"
    score = int(_num(row.get("strength")) or 0)
    verified = bool(row.get("verified"))
    if category == "price" and value == 0:
        score = min(score, 68)
    if not verified and category != "price":
        score = max(0, score - 14)
    return {
        "category": category, "label": _text(row.get("label")) or category,
        "stance_value": value, "stance": stance, "strength": score,
        "stars": _stars(score), "verified": verified,
        "text": _text(row.get("text")),
    }


def _rank(rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    priority = {"event": 8, "fundamental": 7, "chip": 6, "quantum": 5, "abc": 4, "price": 3, "market": 2}
    unique, seen = [], set()
    for row in rows:
        key = (row.get("category"), row.get("stance_value"), _text(row.get("text"))[:70].lower())
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return sorted(
        unique,
        key=lambda r: (int(r.get("strength") or 0), bool(r.get("verified")), priority.get(_text(r.get("category")), 0)),
        reverse=True,
    )


def _acceptance(entry: Mapping[str, Any], base: Mapping[str, Any], abc: Mapping[str, Any] | None, quantum: Mapping[str, Any] | None) -> Dict[str, Any]:
    score, pos, neg = 50, [], []
    state, vp = _text(entry.get("state")), _text(entry.get("vwap_position"))
    day = _num(entry.get("operative_return_pct")) or 0
    shape, market = _map(entry.get("price_shape")), _map(entry.get("market_context"))
    gap = _num(market.get("relative_gap_pct"))
    if vp == "above":
        score += 18; pos.append("VWAP上方")
    elif vp == "at":
        score += 6; pos.append("VWAP多空邊界")
    elif vp == "below":
        score -= 18; neg.append("VWAP下方")
    delta = {"BUY_TODAY_CONFIRM":15,"WAIT_VWAP_PULLBACK":8,"WAIT_VWAP_RECLAIM":-8,"WAIT_RECLAIM_HOLD":2,"LIMIT_LIQUIDITY_WAIT":8,"OVERHEATED_NO_CHASE":4,"SELLING_EXPANSION_BLOCK":-22,"FAILED_BREAKOUT_EXIT":-28,"DATA_WAIT":-18}.get(state, 0)
    score += delta
    if delta:
        (pos if delta > 0 else neg).append(_text(entry.get("label")) or state)
    if day >= 3:
        score += 5; pos.append(f"價格 {day:+.2f}%")
    elif day <= -3:
        score -= 5; neg.append(f"價格 {day:+.2f}%")
    if shape.get("near_high"):
        score += 6; pos.append("接近日內高檔")
    if shape.get("near_low"):
        score -= 6; neg.append("接近日內低檔")
    if shape.get("opening_selloff"):
        score -= 8; neg.append("開盤後賣壓")
    if gap is not None and gap >= 1.5:
        score += 8; pos.append(f"相對市場 {gap:+.2f}%")
    elif gap is not None and gap <= -4:
        score -= 12; neg.append(f"相對市場 {gap:+.2f}%")
    code = _text(base.get("code"))
    catalyst_delta = {"POSITIVE_CONFIRMED":10,"NEGATIVE_ABSORBED":6,"POSITIVE_REJECTED":-12,"NEGATIVE_CONFIRMED":-16}.get(code, 0)
    score += catalyst_delta
    if catalyst_delta:
        (pos if catalyst_delta > 0 else neg).append(_text(base.get("label")))
    if abc and abc.get("a") is not None and abc.get("b") is not None:
        spread = float(abc["a"]) - float(abc["b"])
        if spread >= 8:
            score += 5; pos.append(f"ABC突破 +{spread:.0f}pp")
        elif spread <= -8:
            score -= 4; neg.append(f"ABC回測 +{abs(spread):.0f}pp")
    if quantum and quantum.get("stance_value"):
        score += 5 if quantum["stance_value"] > 0 else -5
        (pos if quantum["stance_value"] > 0 else neg).append("Quantum偏多" if quantum["stance_value"] > 0 else "Quantum偏空")
    score = max(0, min(100, int(round(score))))
    title = "利空吸收度" if code == "NEGATIVE_ABSORBED" else "利多價格接受度" if code in {"POSITIVE_CONFIRMED","POSITIVE_REJECTED"} else "利空價格確認度" if code == "NEGATIVE_CONFIRMED" else "價格結構確認度"
    grade = "已形成" if score >= 72 else "待加強" if score >= 55 else "尚未完成" if score >= 40 else "明顯不足"
    return {"code": code or "STRUCTURE_ONLY", "title": title, "score": score, "grade": grade, "label": f"{title} {score}%｜{grade}", "positives": pos[:4], "negatives": neg[:4]}


def _pullback_band(*, low: float | None, high: float | None, vwap: float | None, last: float | None) -> tuple[float | None, float | None]:
    """Execution band from verified session geometry, not a new forecast."""
    if vwap is None:
        return (low, low)
    day_range = (high - low) if high is not None and low is not None and high > low else 0.0
    buffer = max(abs(vwap) * 0.0025, day_range * 0.10, 0.01)
    lower = max(low, vwap - buffer) if low is not None else vwap - buffer
    upper = vwap + buffer * 0.35
    if last is not None and last < lower:
        lower = last
    return (lower, upper)


def _reclaim_low_zone(*, low: float | None, vwap: float | None, last: float | None) -> tuple[float | None, float | None]:
    """Low-entry observation zone between verified session low and VWAP."""
    if low is None:
        return (None, None)
    if vwap is None or vwap <= low:
        width = max(abs(low) * 0.004, 0.01)
        return (low, low + width)
    upper = low + (vwap - low) * 0.40
    if last is not None and last < low:
        return (last, low)
    return (low, upper)


def _current_location(*, state: str, last: float | None, low_zone: tuple[float | None, float | None], confirmation: float | None, add_price: float | None, invalid_price: float | None) -> tuple[str, str]:
    if state in {"SELLING_EXPANSION_BLOCK", "FAILED_BREAKOUT_EXIT"}:
        return ("禁止區", "現在不買")
    if state in {"WAIT_NEXT_SESSION", "DATA_WAIT"}:
        return ("不可驗證時段", "等待下一正式Session")
    if state == "LIMIT_LIQUIDITY_WAIT":
        return ("漲停／流動性區", "只可限價小量排隊")
    if state == "OVERHEATED_NO_CHASE":
        return ("過熱區", "現在不追，等回測")
    if last is None:
        return ("位置待確認", "等待同源價格")
    lo, hi = low_zone
    if invalid_price is not None and last < invalid_price:
        return ("失效價下方", "取消進場")
    if lo is not None and hi is not None and lo <= last <= hi:
        return ("低接觀察區", "等止跌量縮後才小量")
    if hi is not None and confirmation is not None and hi < last < confirmation:
        return ("低接與確認價中間", "先等，不在中間價追")
    if confirmation is not None and last >= confirmation:
        if add_price is not None and last < add_price:
            return ("右側確認區", "等站穩或回踩不破")
        if add_price is not None and last >= add_price:
            return ("加碼／突破區", "確認不過熱才加碼")
        return ("右側確認區", "等站穩或回踩不破")
    if lo is not None and last < lo:
        return ("低接區下方", "等重新止跌，不攤平")
    return ("等待區", "等低接或右側確認")


def _entry_plan(forecast: Any, entry: Mapping[str, Any]) -> Dict[str, Any]:
    d = _map(getattr(forecast, "decision_card", {}))
    state = _text(entry.get("state")) or "DATA_WAIT"
    last = _num(entry.get("operative_price")) or _num(d.get("現價"))
    vwap = _num(entry.get("vwap"))
    low = _num(d.get("最低") or d.get("今日低"))
    high = _num(d.get("最高") or d.get("今日高"))
    legacy_confirm = _first_num(d.get("轉強")) or _first_num(d.get("攻擊"))
    stop = _num(d.get("防守"))
    no_chase = _num(d.get("不追"))
    add_price = legacy_confirm
    if vwap is not None and add_price is not None and add_price <= vwap * 1.001:
        add_price = high if high is not None and high > vwap else None
    if add_price is None and high is not None and (vwap is None or high > vwap):
        add_price = high
    low_zone = (None, None)
    confirmation_price = None
    invalid_price = None
    low_entry_condition = ""
    confirmation_condition = ""
    add_condition = ""
    actionable = False
    if state == "BUY_TODAY_CONFIRM":
        low_zone = _pullback_band(low=low, high=high, vwap=vwap, last=last)
        confirmation_price = last
        invalid_price = vwap or stop or low
        low_entry_condition = "第二筆等VWAP回測不破"
        confirmation_condition = "現價附近只做小量確認單"
        add_condition = f"站穩 {_price(add_price)}再加碼" if add_price else "突破平台後再加碼"
        actionable = True
    elif state == "WAIT_VWAP_PULLBACK":
        low_zone = _pullback_band(low=low, high=high, vwap=vwap, last=last)
        confirmation_price = vwap
        invalid_price = low or stop or vwap
        low_entry_condition = "回測不破且賣壓量縮才參與"
        confirmation_condition = "VWAP承接完成後小量"
        add_condition = f"站穩 {_price(add_price)}再加碼" if add_price else "重新放量再加碼"
        actionable = True
    elif state == "WAIT_VWAP_RECLAIM":
        low_zone = _reclaim_low_zone(low=low, vwap=vwap, last=last)
        confirmation_price = vwap
        invalid_price = low or stop
        low_entry_condition = "低點不再下移且賣壓量縮才低接"
        confirmation_condition = f"站回 {_price(vwap)} 並回踩不破" if vwap else "收復關鍵均價並回踩不破"
        add_condition = f"突破 {_price(add_price)}再加碼" if add_price else "突破盤中高點再加碼"
        actionable = True
    elif state == "WAIT_RECLAIM_HOLD":
        low_zone = _pullback_band(low=low, high=high, vwap=vwap, last=last)
        confirmation_price = vwap
        invalid_price = low or vwap or stop
        low_entry_condition = "VWAP附近維持5–15分鐘或回踩不破"
        confirmation_condition = "確認承接後才小量"
        add_condition = f"站穩 {_price(add_price)}再加碼" if add_price else "高點抬升再加碼"
        actionable = True
    elif state == "LIMIT_LIQUIDITY_WAIT":
        confirmation_price = last
        invalid_price = vwap or low
        confirmation_condition = "只可限價小量排隊，成交不確定"
        add_condition = "開板後重新驗證，不得加價追單"
    elif state == "OVERHEATED_NO_CHASE":
        low_zone = _pullback_band(low=low, high=high, vwap=vwap, last=last)
        confirmation_price = vwap
        invalid_price = low or stop
        low_entry_condition = "等待量縮回測才重新評估"
        confirmation_condition = "現在沒有追價買點"
        add_condition = f"不追 {_price(no_chase)}以上" if no_chase else "第三段加速不追"
    elif state == "SELLING_EXPANSION_BLOCK":
        confirmation_price = vwap
        invalid_price = stop or low
        low_entry_condition = "禁止接刀，不提供低接價"
        confirmation_condition = f"至少先收復 {_price(vwap)}" if vwap else "至少先止跌並收復均價"
        add_condition = "低點停止下移後才重算"
    elif state == "FAILED_BREAKOUT_EXIT":
        confirmation_price = vwap
        invalid_price = stop or low
        low_entry_condition = "取消原進場計畫"
        confirmation_condition = f"收復 {_price(vwap)}後再評估" if vwap else "重回突破平台後再評估"
        add_condition = "未修復前不新增"
    elif state == "WAIT_NEXT_SESSION":
        invalid_price = low
        low_entry_condition = "不沿用舊低接價"
        confirmation_condition = "下一正式盤以新VWAP重算"
        add_condition = "先驗證缺口與量能"
    else:
        low_entry_condition = "不建立買點"
        confirmation_condition = "等待同源價格與Session"
        add_condition = "驗證完成後重算"
    location, current_action = _current_location(state=state, last=last, low_zone=low_zone, confirmation=confirmation_price, add_price=add_price, invalid_price=invalid_price)
    low_entry_text = f"{_range_text(*low_zone)}（{low_entry_condition}）" if any(v is not None for v in low_zone) else low_entry_condition or "--"
    confirmation_text = f"{_price(confirmation_price)}（{confirmation_condition}）" if confirmation_price is not None else confirmation_condition or "--"
    add_text = f"{_price(add_price)}（{add_condition}）" if add_price is not None else add_condition or "--"
    invalid_text = f"{_price(invalid_price)}跌破" if invalid_price is not None else "條件失效即取消"
    current_text = f"{_price(last)}｜{location}，{current_action}" if last is not None else f"--｜{location}"
    display_line = f"現在 {current_text}｜低接 {low_entry_text}｜確認 {confirmation_text}｜加碼 {add_text}｜失效 {invalid_text}"
    return {
        "state": state,
        "label": "進場地圖",
        "headline": f"現在 {current_text}",
        "condition": f"低接 {low_entry_text}",
        "secondary": f"確認 {confirmation_text}｜加碼 {add_text}",
        "invalidation": invalid_text,
        "display_line": display_line,
        "actionable": actionable,
        "current_price": last,
        "current_location": location,
        "current_action": current_action,
        "low_entry_zone": {"lower": low_zone[0], "upper": low_zone[1], "text": low_entry_text},
        "low_entry_condition": low_entry_condition,
        "confirmation_price": confirmation_price,
        "confirmation_text": confirmation_text,
        "add_price": add_price,
        "add_text": add_text,
        "invalidation_price": invalid_price,
        "invalidation_text": invalid_text,
        "stop_price": stop,
        "no_chase_price": no_chase,
        "source": "V1081 Entry Opportunity + verified session OHLC/VWAP geometry",
        "formal_price_model_unchanged": True,
    }


def _decisive_action(
    entry: Mapping[str, Any],
    plan: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    top: list[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return exactly one executable public action.

    The V1081 state remains available for Audit compatibility.  This layer is the
    final entry/position decision shown to the user and may veto a permissive VWAP
    state when the ranked evidence conflicts.
    """
    state = _text(entry.get("state")) or "DATA_WAIT"
    score = int(_num(acceptance.get("score")) or 0)
    bullish = sum(int(_num(x.get("strength")) or 0) for x in top if int(x.get("stance_value") or 0) > 0)
    bearish = sum(int(_num(x.get("strength")) or 0) for x in top if int(x.get("stance_value") or 0) < 0)
    dominance = bullish - bearish
    invalid = _price(plan.get("invalidation_price"))
    confirm = _price(plan.get("confirmation_price"))
    current = _price(plan.get("current_price"))

    code = "BLOCK"
    reason = "證據不足以建立新部位"
    if state in {"SELLING_EXPANSION_BLOCK", "FAILED_BREAKOUT_EXIT"}:
        code = "SELL"
        reason = "賣壓擴張或原突破結構已失效"
    elif state == "OVERHEATED_NO_CHASE":
        code = "REDUCE"
        reason = "價格過熱，新增部位的風險報酬不合格"
    elif state == "BUY_TODAY_CONFIRM":
        if score >= 55 and dominance >= -20:
            code = "BUY"
            reason = "價格結構與有效證據已達買進門檻"
        else:
            reason = "VWAP偏多但跨證據衝突，否決買進"
    elif state == "WAIT_VWAP_PULLBACK":
        if score >= 65 and dominance >= 0:
            code = "HOLD"
            reason = "方向仍偏多，但現價沒有新增部位優勢"
        else:
            reason = "VWAP上方不足以抵銷ABC、法人或其他偏空證據"
    elif state == "WAIT_RECLAIM_HOLD":
        if score >= 62 and dominance >= 0:
            code = "HOLD"
            reason = "既有多方結構尚未失效，但不核准加碼"
        elif bearish > bullish:
            code = "REDUCE"
            reason = "多空交界且偏空證據占優"
    elif state == "WAIT_VWAP_RECLAIM":
        if bearish > bullish or score < 50:
            code = "REDUCE"
            reason = "價格未收復VWAP且偏空證據占優"
        else:
            reason = "價格尚未完成收復，不核准新部位"
    elif state == "LIMIT_LIQUIDITY_WAIT":
        if score >= 60 and dominance >= 0:
            code = "HOLD"
            reason = "趨勢仍強，但流動性不足以核准追價"
        else:
            reason = "成交與流動性未通過買進門檻"

    meta = {
        "BUY": ("買進", "🟢", "green"),
        "HOLD": ("續抱", "🟢", "green"),
        "REDUCE": ("減碼", "🟠", "yellow"),
        "SELL": ("賣出", "🔴", "red"),
        "BLOCK": ("禁止進場", "🔴", "red"),
    }
    label, icon, color = meta[code]
    if code == "BUY":
        instruction = f"買進｜{current}附近小量｜跌破 {invalid} 停損"
    elif code == "HOLD":
        instruction = f"續抱｜空手不買｜跌破 {invalid} 減碼"
    elif code == "REDUCE":
        instruction = f"減碼｜空手禁止進場｜跌破 {invalid} 全出"
    elif code == "SELL":
        instruction = f"賣出｜取消低接｜未收復 {confirm} 不重進"
    else:
        instruction = "禁止進場｜不建立新部位"

    return {
        "code": code,
        "label": label,
        "icon": icon,
        "color": color,
        "instruction": instruction,
        "reason": reason,
        "acceptance_score": score,
        "bullish_evidence": bullish,
        "bearish_evidence": bearish,
        "evidence_dominance": dominance,
        "source_state": state,
        "single_action": True,
    }


def _conclusion(base: Mapping[str, Any], entry: Mapping[str, Any], plan: Mapping[str, Any], acceptance: Mapping[str, Any], top: list[Dict[str, Any]], action: Mapping[str, Any]) -> str:
    label = _text(action.get("label")) or "禁止進場"
    instruction = _text(action.get("instruction")) or label
    reason = _text(action.get("reason")) or "有效證據不足"
    winner = _text(top[0].get("label")) if top else "有效證據"
    return f"最終決策：{label}｜{instruction}。主因：{reason}；{winner}主導；{acceptance.get('label') or '價格接受度待確認'}。"


def build_evidence_arbitration(forecast: Any, entry: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    entry = dict(entry or {})
    base = dict(build_v1082(forecast, entry) or {})
    radar = _map(getattr(forecast, "radar", {}))
    rows = [_base_packet(x) for x in list(base.get("top_drivers") or []) if isinstance(x, Mapping)]
    abc, quantum = _abc(radar), _quantum(radar)
    rows.extend(x for x in (abc, quantum) if x)
    ranked = _rank(rows)
    top = ranked[:3]
    acceptance = _acceptance(entry, _map(base.get("price_acceptance")), abc, quantum)
    plan = _entry_plan(forecast, entry)
    action = _decisive_action(entry, plan, acceptance, top)
    conclusion = _conclusion(base, entry, plan, acceptance, top, action)
    top_rows = []
    for i, row in enumerate(top, 1):
        row = dict(row)
        row.update({"rank": i, "reason": _short(row.get("text"))})
        top_rows.append(row)
    summary = "｜".join(f"{x['rank']} {x['label']} {x['stars']} {x['stance']}：{x['reason']}" for x in top_rows) or "有效證據不足"
    winner = top_rows[0] if top_rows else {}
    out = dict(base)
    out.update({
        "schema": SCHEMA,
        "headline": f"{base.get('short_term_bias') or '待確認'}｜主導：{winner.get('label') or '有效證據不足'}",
        "decision_message": conclusion,
        "one_line_conclusion": conclusion,
        "evidence_winner": {k: winner.get(k) for k in ("category", "label", "stance", "reason")},
        "price_acceptance": acceptance,
        "recommended_entry": plan,
        "action_decision": action,
        "top_drivers": top_rows,
        "top_driver_summary": summary,
        "abc_context": abc or {},
        "quantum_context": quantum or {},
        "narrative_only": False,
        "decision_influence": True,
        "entry_decision_influence": True,
        "formal_forecast_unchanged": True,
        "formal_price_model_unchanged": True,
        "learning_sample_unchanged": True,
    })
    return out


build_evidence_reasoning = build_evidence_arbitration
reason_about_forecast = build_evidence_arbitration
