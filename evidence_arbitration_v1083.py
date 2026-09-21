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

try:
    from decision_language_v1087 import compose_action_language
except Exception:  # deployment fallback keeps the panel alive during rolling update
    compose_action_language = None

SCHEMA = "TINO_EVIDENCE_ARBITRATION_V1092"


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
    if cv >= 35:
        path_code, label, stance = "DEFENSE", "ABC防守風險", -1
        verdict = "防守情境主導"
        score = int(max(68, min(92, 70 + (cv - 35))))
    elif bv >= 45 and bv >= max(av, cv):
        path_code, label, stance = "PULLBACK", "ABC回測情境", 0
        verdict = "回測情境主導"
        score = int(max(48, min(92, bv)))
    elif av >= 45 and spread >= 8:
        path_code, label, stance = "BREAKOUT", "ABC突破情境", 1
        verdict = "突破情境主導"
        score = int(max(48, min(92, av)))
    else:
        path_code, label, stance = "MIXED", "ABC情境", 0
        verdict = "突破與回測接近"
        score = int(max(48, min(76, max(av, bv, cv))))
    return {
        "category": "abc", "label": label, "stance_value": stance,
        "stance": "偏多" if stance > 0 else "偏空" if stance < 0 else "中性",
        "strength": score, "stars": _stars(score), "verified": True,
        "text": f"A突破 {av:.0f}%｜B回測 {bv:.0f}%｜C防守 {cv:.0f}%｜{verdict}",
        "a": a, "b": b, "c": c, "path_code": path_code,
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
        path_code = _text(abc.get("path_code"))
        if path_code == "BREAKOUT" and spread >= 8:
            score += 5; pos.append(f"ABC突破 +{spread:.0f}pp")
        elif path_code == "DEFENSE":
            score -= 4; neg.append(f"ABC防守 {float(abc.get('c') or 0):.0f}%")
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


def _cross_module_gate(
    forecast: Any,
    entry: Mapping[str, Any],
    abc: Mapping[str, Any] | None,
    top: list[Dict[str, Any]],
) -> Dict[str, Any]:
    """Arbitrate entry qualification across T1, ABC, chips, price and events."""
    d = _map(getattr(forecast, "decision_card", {}))
    state = _text(entry.get("state")) or "DATA_WAIT"
    last = _num(entry.get("operative_price")) or _num(d.get("現價"))
    t1 = _num(getattr(forecast, "final_t1", None))
    if t1 is None:
        t1 = _num(d.get("下一交易日收盤預估") or d.get("T1_CLOSE"))
    t1_return = ((t1 / last) - 1.0) * 100.0 if t1 is not None and last not in (None, 0) else None
    a = _num((abc or {}).get("a"))
    b = _num((abc or {}).get("b"))
    c = _num((abc or {}).get("c"))
    chip_bear = sum(
        int(_num(row.get("strength")) or 0)
        for row in top if _text(row.get("category")) == "chip" and int(row.get("stance_value") or 0) < 0
    )
    chip_bull = sum(
        int(_num(row.get("strength")) or 0)
        for row in top if _text(row.get("category")) == "chip" and int(row.get("stance_value") or 0) > 0
    )
    event_bear = any(
        _text(row.get("category")) == "event" and int(row.get("stance_value") or 0) < 0
        and int(_num(row.get("strength")) or 0) >= 78 for row in top
    )
    evidence_blob = "｜".join(
        _text(row.get("label")) + "｜" + _text(row.get("text")) + "｜" + _text(row.get("source"))
        for row in top
    ).lower()
    deleveraging_evidence = any(term in evidence_blob for term in (
        "融資下降", "融資減少", "融資減", "融資清洗", "健康去槓桿", "去槓桿",
        "margin decline", "margin reduced", "deleveraging",
    ))
    positive_event = any(
        _text(row.get("category")) in {"event", "fundamental"}
        and int(row.get("stance_value") or 0) > 0
        and bool(row.get("verified"))
        and int(_num(row.get("strength")) or 0) >= 55 for row in top
    )
    positive_market = any(
        _text(row.get("category")) == "market"
        and int(row.get("stance_value") or 0) > 0
        and bool(row.get("verified")) for row in top
    )
    controlled_low_trade = bool(
        t1_return is not None and -0.8 < t1_return <= 0
        and b is not None and b >= 45
        and (c is None or c <= 30)
        and chip_bear < 78 and not event_bear
        and state in {"BUY_TODAY_CONFIRM", "WAIT_VWAP_PULLBACK", "WAIT_VWAP_RECLAIM", "WAIT_RECLAIM_HOLD"}
    )
    day_return = _num(entry.get("operative_return_pct")) or 0.0
    vwap = _num(entry.get("vwap"))
    vwap_position = _text(entry.get("vwap_position")).lower()
    price_above_vwap = bool(
        vwap_position == "above"
        or (last is not None and vwap is not None and last >= vwap)
    )
    recovery_confirmation_count = sum(bool(x) for x in (
        deleveraging_evidence, positive_event, positive_market, chip_bull >= 60,
    ))
    recovery_setup = bool(
        t1_return is not None and -2.5 <= t1_return <= 0.8
        and day_return >= 1.0 and price_above_vwap
        and b is not None and b >= 45 and (c is None or c <= 30)
        and chip_bear < 78 and not event_bear
        and recovery_confirmation_count >= 2
        and (deleveraging_evidence or positive_event or positive_market)
        and state in {"BUY_TODAY_CONFIRM", "WAIT_VWAP_PULLBACK", "WAIT_VWAP_RECLAIM", "WAIT_RECLAIM_HOLD"}
    )
    rebound_monitor = bool(
        t1_return is not None and t1_return <= 0
        and day_return >= 3.0
        and price_above_vwap
        and state not in {"SELLING_EXPANSION_BLOCK", "FAILED_BREAKOUT_EXIT", "DATA_WAIT", "WAIT_NEXT_SESSION"}
    )

    reasons: list[str] = []
    allow_immediate = True
    allow_pullback = True
    allow_breakout = True
    code = "ENTRY_QUALIFIED"

    if state in {"DATA_WAIT", "WAIT_NEXT_SESSION"}:
        code = "DATA_BLOCK"
        allow_immediate = allow_pullback = allow_breakout = False
        reasons.append("Session或同源價格未完成驗證")
    else:
        if t1_return is not None and t1_return <= 0:
            allow_immediate = False
            reasons.append(f"T1預期報酬 {t1_return:+.2f}% 未轉正")
        if a is not None and b is not None and a < 20 and b >= 50:
            allow_immediate = False
            allow_breakout = False
            reasons.append(f"ABC突破僅 {a:.0f}%、回測 {b:.0f}%")
        if c is not None and c >= 35:
            allow_immediate = False
            allow_pullback = False
            allow_breakout = False
            reasons.append(f"ABC防守情境 {c:.0f}% 過高")
        if event_bear:
            allow_immediate = False
            reasons.append("重大負面事件尚未被價格吸收")
        if chip_bear >= 78 and chip_bull == 0:
            allow_immediate = False
            reasons.append("法人／籌碼偏空尚未改善")
        if not recovery_setup and t1_return is not None and t1_return <= -0.8 and (
            (c is not None and c >= 25) or chip_bear >= 70
        ):
            allow_pullback = allow_breakout = False
            reasons.append("T1負報酬與防守／籌碼風險形成共振")
        elif controlled_low_trade:
            allow_pullback = True
            allow_breakout = False
            reasons.append("T1已收斂、回測情境主導且無強空共振，可保留受控試單")
        elif recovery_setup:
            allow_pullback = True
            allow_breakout = False
            reasons.append("去槓桿／事件／市場證據與價格修復共振，開放修復型首倉")
        if state in {"OVERHEATED_NO_CHASE", "LIMIT_LIQUIDITY_WAIT"}:
            allow_immediate = False
            allow_pullback = allow_breakout = False
            reasons.append("過熱或流動性限制，當日不追價")

        if not (allow_immediate or allow_pullback or allow_breakout):
            if state in {"OVERHEATED_NO_CHASE", "LIMIT_LIQUIDITY_WAIT"}:
                code = "RECHECK_NEXT_SESSION"
            elif t1_return is not None and t1_return <= 0:
                code = "NO_ENTRY_T1"
            elif c is not None and c >= 35:
                code = "NO_ENTRY_ABC"
            else:
                code = "NO_ENTRY_CONFLICT"
        elif controlled_low_trade:
            code = "CONTROLLED_LOW_TRADE"
        elif recovery_setup:
            code = "RECOVERY_SETUP"
        elif not allow_immediate:
            code = "CONDITIONAL_ONLY"

    entry_qualified = bool(allow_immediate or allow_pullback or allow_breakout)
    label_map = {
        "ENTRY_QUALIFIED": "買進資格通過",
        "CONDITIONAL_ONLY": "僅條件式買進",
        "CONTROLLED_LOW_TRADE": "可以交易｜小倉試單",
        "RECOVERY_SETUP": "修復布局｜回測確認首倉",
        "NO_ENTRY_T1": "T1未轉正，本日無買點",
        "NO_ENTRY_ABC": "ABC防守過高，本日無買點",
        "NO_ENTRY_CONFLICT": "跨模組衝突，本日無買點",
        "RECHECK_NEXT_SESSION": "不追價，下一Session重算",
        "DATA_BLOCK": "資料未驗證，禁止進場",
    }
    return {
        "code": code, "label": label_map[code], "entry_qualified": entry_qualified,
        "allow_immediate_buy": allow_immediate, "allow_pullback": allow_pullback,
        "allow_breakout": allow_breakout, "reasons": reasons,
        "t1_price": t1, "t1_return_pct": t1_return,
        "abc_a": a, "abc_b": b, "abc_c": c,
        "chip_bear_strength": chip_bear, "chip_bull_strength": chip_bull,
        "event_bearish_veto": event_bear,
        "controlled_low_trade": controlled_low_trade,
        "recovery_setup": recovery_setup,
        "recovery_confirmation_count": recovery_confirmation_count,
        "deleveraging_evidence": deleveraging_evidence,
        "positive_event": positive_event,
        "positive_market": positive_market,
        "rebound_monitor": rebound_monitor,
        "day_return_pct": day_return,
        "price_above_vwap": price_above_vwap,
        "trade_level": (
            "TRADEABLE" if controlled_low_trade else
            "RECOVERY_SETUP" if recovery_setup else
            "REBOUND_MONITOR" if rebound_monitor else
            "LOW_MONITOR" if t1_return is not None and t1_return <= 0 else
            "TREND_CONFIRMED"
        ),
        "source": "V1092 Deleveraging Recovery Entry Gate",
    }


def _entry_plan(forecast: Any, entry: Mapping[str, Any], gate: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Build an executable entry plan from verified session geometry.

    V1086 guarantees that a pullback confirmation is above the low-entry zone
    and that a breakout trigger is above both the confirmation and current price.
    These are execution triggers only; the formal T0/T1 price model is unchanged.
    """
    d = _map(getattr(forecast, "decision_card", {}))
    state = _text(entry.get("state")) or "DATA_WAIT"
    last = _num(entry.get("operative_price")) or _num(d.get("現價"))
    vwap = _num(entry.get("vwap"))
    low = _num(d.get("最低") or d.get("今日低"))
    high = _num(d.get("最高") or d.get("今日高"))
    legacy_confirm = _first_num(d.get("轉強")) or _first_num(d.get("攻擊"))
    stop = _num(d.get("防守"))
    no_chase = _num(d.get("不追"))
    ticker = getattr(forecast, "ticker", None)
    market = _text(getattr(ticker, "market", "")).upper()
    asset_type = _text(getattr(ticker, "asset_type", "stock")).lower()
    is_tw_etf = market == "TW" and asset_type in {"etf", "etn"}

    def tick(price: float | None) -> float:
        if price is None or price <= 0:
            return 0.01
        if market == "TW":
            if asset_type in {"etf", "etn"}:
                return 0.01 if price < 50 else 0.05
            if price < 10:
                return 0.01
            if price < 50:
                return 0.05
            if price < 100:
                return 0.10
            if price < 500:
                return 0.50
            if price < 1000:
                return 1.00
            return 5.00
        return 0.01

    low_zone = (None, None)
    invalid_price = None
    low_entry_condition = ""
    confirmation_condition = ""
    breakout_condition = ""
    actionable = False
    trigger_mode = "NONE"

    if state in {"BUY_TODAY_CONFIRM", "WAIT_VWAP_PULLBACK", "WAIT_RECLAIM_HOLD", "OVERHEATED_NO_CHASE"}:
        low_zone = _pullback_band(low=low, high=high, vwap=vwap, last=last)
    elif state == "WAIT_VWAP_RECLAIM":
        low_zone = _reclaim_low_zone(low=low, vwap=vwap, last=last)

    if state in {"BUY_TODAY_CONFIRM", "WAIT_VWAP_PULLBACK", "WAIT_RECLAIM_HOLD"}:
        invalid_price = low or stop or vwap
        actionable = True
        trigger_mode = "PULLBACK_OR_BREAKOUT"
        low_entry_condition = "量縮守穩後，重新站回回測確認價才買進"
    elif state == "WAIT_VWAP_RECLAIM":
        invalid_price = low or stop
        actionable = True
        trigger_mode = "RECLAIM_OR_BREAKOUT"
        low_entry_condition = "低點不再下移且賣壓量縮，重新站回確認價才買進"
    elif state == "OVERHEATED_NO_CHASE":
        invalid_price = low or stop
        actionable = True
        trigger_mode = "PULLBACK_OR_BREAKOUT"
        low_entry_condition = "量縮回測守穩後，重新站回確認價才買進"
    elif state == "LIMIT_LIQUIDITY_WAIT":
        invalid_price = vwap or low or stop
        low_entry_condition = "開板並恢復可成交後才重算買點"
    elif state in {"SELLING_EXPANSION_BLOCK", "FAILED_BREAKOUT_EXIT"}:
        invalid_price = stop or low
        low_entry_condition = "原結構失效，本日無買點"
    elif state == "WAIT_NEXT_SESSION":
        invalid_price = low
        low_entry_condition = "下一正式Session以新OHLC與VWAP重算"
    else:
        low_entry_condition = "資料不足，本日無買點"

    gate = _map(gate)
    if gate and not bool(gate.get("entry_qualified")):
        actionable = False
        trigger_mode = "NONE"
        low_entry_condition = "；".join(list(gate.get("reasons") or [])[:2]) or "跨模組未通過，本日無買點"

    zone_lo, zone_hi = low_zone
    step = tick(last or vwap or zone_hi)
    confirmation_price = None
    breakout_price = None
    if actionable and zone_hi is not None:
        # Pullback confirmation must sit strictly above the whole low-entry zone.
        if gate.get("left_low_candidate") and bool(gate.get("allow_pullback", True)):
            # Left-side lane confirms a bounce out of the verified low zone;
            # requiring a full VWAP reclaim would turn it back into right-side
            # momentum buying and miss the intended low-risk entry.
            confirmation_price = zone_hi + step
        else:
            confirmation_price = max(zone_hi + step, vwap + step if vwap is not None else zone_hi + step) if not gate or bool(gate.get("allow_pullback", True)) else None
        if is_tw_etf and confirmation_price is not None:
            confirmation_price = round(math.ceil((confirmation_price - 1e-9) / step) * step, 2)
        # If price is already above the zone, require a reclaim of the current
        # reference after the pullback instead of reusing VWAP as a fake trigger.
        if confirmation_price is not None and last is not None and last > confirmation_price and not gate.get("left_low_candidate"):
            confirmation_price = last
        if confirmation_price is not None:
            confirmation_condition = "回測量縮守穩後重新站回，觸發小量買進"

        candidates = [x for x in (high, legacy_confirm, no_chase) if x is not None]
        breakout_base = max(candidates) if candidates else confirmation_price
        if last is not None:
            breakout_base = max(breakout_base, last)
        if not gate or bool(gate.get("allow_breakout", True)):
            confirm_floor = confirmation_price if confirmation_price is not None else zone_hi
            breakout_price = max(breakout_base + tick(breakout_base), confirm_floor + step)
            if is_tw_etf:
                breakout_step = tick(breakout_price)
                breakout_price = round(math.ceil((breakout_price - 1e-9) / breakout_step) * breakout_step, 2)
            breakout_condition = "放量站穩後觸發突破買進"
    elif actionable:
        actionable = False
        trigger_mode = "NONE"
        low_entry_condition = "缺少可驗證區間，本日無買點"

    session_touched_zone = bool(
        zone_lo is not None and zone_hi is not None and low is not None and high is not None
        and low <= zone_hi and high >= zone_lo
    )
    entry_sequence_verified = not session_touched_zone
    if not actionable:
        entry_state_code, entry_state_label, missing = "NO_ENTRY", "本日無買進資格", list(gate.get("reasons") or [])[:3]
    elif state == "BUY_TODAY_CONFIRM" and (
        not gate or bool(gate.get("allow_immediate_buy", True)) or bool(gate.get("controlled_low_trade"))
        or bool(gate.get("recovery_setup"))
    ):
        entry_state_code, entry_state_label, missing = "TRIGGERED", "正式觸發", []
    elif zone_lo is not None and zone_hi is not None and last is not None and zone_lo <= last <= zone_hi:
        entry_state_code, entry_state_label, missing = "IN_PULLBACK", "已進入回測區", ["量縮守穩", "重新站回確認價"]
    elif (
        gate.get("left_low_candidate") and session_touched_zone
        and confirmation_price is not None and last is not None and last >= confirmation_price
    ):
        entry_state_code, entry_state_label, missing = "TRIGGERED", "左側止跌觸發", []
        entry_sequence_verified = True
    elif zone_hi is not None and last is not None and last > zone_hi:
        if session_touched_zone:
            entry_state_code, entry_state_label, missing = (
                "WAIT_PULLBACK_RECLAIM", "等待再次回測後站回",
                ["再次回測進入價格區", "量縮守穩", "重新站回確認價"],
            )
        else:
            entry_state_code, entry_state_label, missing = (
                "WAIT_PULLBACK", "尚未進入回測區",
                ["回測進入價格區", "量縮守穩", "重新站回確認價"],
            )
    else:
        entry_state_code, entry_state_label, missing = "WAIT_STABILIZE", "等待止跌結構", ["低點不再下移", "賣壓量縮", "重新站回"]

    location, current_action = _current_location(
        state=state, last=last, low_zone=low_zone, confirmation=confirmation_price,
        add_price=breakout_price, invalid_price=invalid_price,
    )
    if entry_state_code == "TRIGGERED" and actionable:
        current_action = "買進條件已成立，小量執行"
    elif actionable:
        current_action = f"尚未買進；{entry_state_label}"
    elif state not in {"SELLING_EXPANSION_BLOCK", "FAILED_BREAKOUT_EXIT"}:
        current_action = "本日無買點"

    low_entry_text = f"{_range_text(*low_zone)}（{low_entry_condition}）" if any(v is not None for v in low_zone) else low_entry_condition
    confirmation_text = f"{_price(confirmation_price)}（{confirmation_condition}）" if confirmation_price is not None else "本日無回測買點"
    if breakout_price is not None:
        breakout_text = f"{_price(breakout_price)}（{breakout_condition}）"
    elif gate and not bool(gate.get("allow_breakout", True)):
        abc_reason = next((str(x) for x in list(gate.get("reasons") or []) if str(x).startswith("ABC突破")), "跨模組未通過")
        breakout_text = f"突破資格關閉（{abc_reason}）"
    else:
        breakout_text = "本日無突破買點"
    invalid_text = f"{_price(invalid_price)}跌破，取消買進計畫" if invalid_price is not None else "條件失效即取消"
    current_text = f"{_price(last)}｜{location}，{current_action}" if last is not None else f"--｜{location}"
    display_line = (
        f"現在 {current_text}｜回測區 {low_entry_text}｜"
        f"買進觸發 {confirmation_text}｜突破觸發 {breakout_text}｜失效 {invalid_text}"
    )
    pullback_valid = confirmation_price is not None and zone_hi is not None and zone_hi < confirmation_price
    breakout_valid = breakout_price is not None and zone_hi is not None and zone_hi < breakout_price and (confirmation_price is None or confirmation_price < breakout_price)
    price_order_valid = bool(actionable and (pullback_valid or breakout_valid) and (invalid_price is None or invalid_price < (confirmation_price or breakout_price)))
    if actionable and not price_order_valid:
        actionable = False
        trigger_mode = "NONE"
        confirmation_text = "價格階層未通過，本日無買點"
        breakout_text = "價格階層未通過，本日無買點"
        display_line = f"現在 {current_text}｜本日無買點｜失效 {invalid_text}"

    return {
        "state": state,
        "label": "進場觸發",
        "headline": f"現在 {current_text}",
        "condition": f"回測區 {low_entry_text}",
        "secondary": f"買進觸發 {confirmation_text}｜突破觸發 {breakout_text}",
        "invalidation": invalid_text,
        "display_line": display_line,
        "actionable": actionable,
        "trigger_mode": trigger_mode,
        "trigger_status": "TRIGGERED" if entry_state_code == "TRIGGERED" and actionable else "PENDING" if actionable else "NO_ENTRY",
        "entry_state_code": entry_state_code,
        "entry_state_label": entry_state_label,
        "missing_conditions": missing,
        "entry_sequence_verified": entry_sequence_verified,
        "session_touched_entry_zone": session_touched_zone,
        "entry_sequence_note": (
            "OHLC顯示價格曾進入回測區，但無法驗證盤中先後順序；等待再次回測後站回"
            if session_touched_zone else "當日價格尚未觸及回測區"
        ),
        "qualification_gate": dict(gate),
        "current_price": last,
        "session_low": low,
        "current_location": location,
        "current_action": current_action,
        "low_entry_zone": {"lower": zone_lo, "upper": zone_hi, "text": low_entry_text},
        "low_entry_condition": low_entry_condition,
        "confirmation_price": confirmation_price,
        "confirmation_text": confirmation_text,
        "add_price": breakout_price,
        "add_text": breakout_text,
        "breakout_price": breakout_price,
        "breakout_text": breakout_text,
        "invalidation_price": invalid_price,
        "invalidation_text": invalid_text,
        "stop_price": stop,
        "no_chase_price": no_chase,
        "price_order_valid": price_order_valid,
        "trade_level": (
            "RECOVERY_ENTRY_READY"
            if gate.get("recovery_setup") and entry_state_code == "TRIGGERED" and actionable
            else "LOW_ENTRY_READY"
            if gate.get("controlled_low_trade") and entry_state_code == "TRIGGERED" and actionable
            else gate.get("trade_level") or "LOW_MONITOR"
        ),
        "trade_level_label": {
            "LOW_ENTRY_READY": "可以低接｜回測確認買進",
            "RECOVERY_ENTRY_READY": "修復布局｜回測確認首倉",
            "RECOVERY_SETUP": "修復布局｜等待回測確認",
            "TRADEABLE": "可以交易｜小倉試單",
            "REBOUND_MONITOR": "反彈監控｜已止跌反彈，尚未確認轉強",
            "LOW_MONITOR": "低檔監控｜尚未止跌",
            "TREND_CONFIRMED": "正式轉強",
        }.get(
            "RECOVERY_ENTRY_READY"
            if gate.get("recovery_setup") and entry_state_code == "TRIGGERED" and actionable
            else "LOW_ENTRY_READY"
            if gate.get("controlled_low_trade") and entry_state_code == "TRIGGERED" and actionable
            else gate.get("trade_level") or "LOW_MONITOR",
            "低檔監控｜尚未止跌",
        ),
        "source": "V1092 Deleveraging Recovery Entry + verified session OHLC/VWAP geometry",
        "formal_price_model_unchanged": True,
    }
def _decisive_action(
    entry: Mapping[str, Any],
    plan: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    top: list[Dict[str, Any]],
    gate: Mapping[str, Any] | None = None,
    forecast: Any = None,
) -> Dict[str, Any]:
    """Return one public action plus an executable next-entry trigger."""
    state = _text(entry.get("state")) or "DATA_WAIT"
    score = int(_num(acceptance.get("score")) or 0)
    bullish = sum(int(_num(x.get("strength")) or 0) for x in top if int(x.get("stance_value") or 0) > 0)
    bearish = sum(int(_num(x.get("strength")) or 0) for x in top if int(x.get("stance_value") or 0) < 0)
    dominance = bullish - bearish
    invalid_n = _num(plan.get("invalidation_price"))
    current_n = _num(plan.get("current_price"))
    session_low_n = _num(plan.get("session_low"))
    invalid = _price(invalid_n)
    confirm = _price(plan.get("confirmation_price"))
    breakout = _price(plan.get("breakout_price"))
    current = _price(current_n)
    trigger_status = _text(plan.get("trigger_status")) or "NO_ENTRY"
    price_order_valid = bool(plan.get("price_order_valid"))

    gate = _map(gate)
    code = "HOLD"
    reason = "方向尚未失效；依明確買進觸發與失效價執行"
    hard_bearish = score < 42 and dominance <= -120

    situation = "HOLD_TRIGGER_PENDING"
    exit_basis = "NONE"
    current_below_invalid = bool(
        current_n is not None and invalid_n is not None and current_n < invalid_n
    )
    intraday_breach_recovered = bool(
        current_n is not None and session_low_n is not None and invalid_n is not None
        and session_low_n < invalid_n <= current_n
    )
    strong_reclaim_reset = bool(
        intraday_breach_recovered
        and gate.get("price_above_vwap")
        and (_num(gate.get("day_return_pct")) or 0) >= 3.0
        and (_num(gate.get("abc_c")) is None or (_num(gate.get("abc_c")) or 0) <= 30)
        and (gate.get("rebound_monitor") or gate.get("recovery_setup"))
    )
    if current_below_invalid:
        code, reason = "SELL", "現價已跌破失效價，原交易結構失效"
        situation, exit_basis = "SELL_PRICE_INVALID", "CURRENT_PRICE_INVALIDATION"
    elif intraday_breach_recovered and not strong_reclaim_reset:
        code, reason = "HOLD", "盤中曾跌破失效價但現價已收回；視為假跌破收復觀察，不提前判定減碼"
        situation, exit_basis = "HOLD_RECLAIM_WATCH", "INTRADAY_BREACH_RECLAIMED"
    elif state == "FAILED_BREAKOUT_EXIT":
        code, reason = "REDUCE", "原突破條件失敗，但現價並未跌破本卡失效價"
        situation, exit_basis = "REDUCE_FAILED_BREAKOUT", "FAILED_BREAKOUT"
    elif state == "SELLING_EXPANSION_BLOCK":
        code, reason = "REDUCE", "賣壓擴張，先降低曝險；不得改寫成跌破失效價"
        situation, exit_basis = "REDUCE_SELLING_EXPANSION", "SELLING_EXPANSION"
    elif state in {"DATA_WAIT", "WAIT_NEXT_SESSION"}:
        code, reason = "BLOCK", "Session或同源價格無法驗證，禁止以失真資料下單"
        situation = "BLOCK_DATA"
    elif (
        trigger_status == "TRIGGERED" and price_order_valid
        and (state == "BUY_TODAY_CONFIRM" or gate.get("left_low_candidate"))
    ):
        if gate.get("recovery_setup") and score >= 55 and dominance >= -20:
            code, reason = "BUY", "融資／事件／市場與價格修復形成共振，回測確認後開放第一層布局"
            situation = "BUY_RECOVERY_ENTRY"
        elif gate.get("controlled_low_trade") and score >= (48 if gate.get("left_low_candidate") else 55) and dominance >= -20:
            code, reason = "BUY", "低檔回測已確認，T1風險收斂且無強空共振，只開放小倉試單"
            situation = "BUY_LOW_ENTRY"
        elif score >= 55 and dominance >= -20:
            code, reason = "BUY", "價格結構、有效證據與買進觸發均已成立"
            situation = "BUY_READY"
        elif hard_bearish:
            code, reason = "BLOCK", "價格接受度不足且多項強空證據共振"
            situation = "BLOCK_RISK"
        else:
            code, reason = "HOLD", "價格觸發已到，但有效證據尚未通過買進門檻"
            situation = "HOLD_NO_ENTRY"
    elif state == "WAIT_VWAP_RECLAIM" and not gate.get("left_low_candidate") and (score < 50 or dominance <= -60):
        code, reason = "REDUCE", "價格未收復VWAP且偏空證據明顯占優"
        situation = "REDUCE_WEAKNESS"
    elif hard_bearish:
        code, reason = "BLOCK", "價格接受度不足且多項強空證據共振"
        situation = "BLOCK_RISK"
    elif gate and not bool(gate.get("entry_qualified")):
        code = "HOLD"
        if state == "WAIT_VWAP_RECLAIM" and ((_num(gate.get("t1_return_pct")) or 0) < 0 or dominance <= -60):
            code, situation, reason = "REDUCE", "REDUCE_WEAKNESS", "價格弱勢與跨模組負向預期共振"
        elif gate.get("code") == "RECHECK_NEXT_SESSION":
            situation, reason = "HOLD_OVERHEATED", "過熱或流動性限制，本日不追價"
        elif gate.get("code") == "NO_ENTRY_T1":
            situation, reason = "HOLD_T1_NEGATIVE", "T1未轉正，本日撤銷買進價格"
        elif gate.get("code") == "NO_ENTRY_ABC":
            situation, reason = "HOLD_ABC_DEFENSIVE", "ABC防守情境過高，本日撤銷買進價格"
        else:
            situation, reason = "HOLD_NO_ENTRY", "跨模組未形成合格買進組合"
    elif not bool(plan.get("actionable")):
        code, reason = "HOLD", "目前無法建立合格風險價格階層，本日無買點"
        situation = "HOLD_NO_ENTRY"
    elif state in {"OVERHEATED_NO_CHASE", "LIMIT_LIQUIDITY_WAIT"}:
        code, reason = "HOLD", "趨勢未失效但現在不追；依回測或突破觸發重新進場"
        situation = "HOLD_OVERHEATED"
    else:
        code, reason = "HOLD", "買進觸發尚未成立；空手等待觸發，持股續抱"
        situation = "HOLD_TRIGGER_PENDING"

    meta = {
        "BUY": ("買進", "🟢", "green"),
        "HOLD": ("空手等觸發｜持股續抱", "🟡", "yellow"),
        "REDUCE": ("減碼", "🟠", "yellow"),
        "SELL": ("賣出", "🔴", "red"),
        "BLOCK": ("禁止進場", "🔴", "red"),
    }
    label, icon, color = meta[code]
    if code == "BUY":
        instruction = (
            f"修復布局｜{current}附近15%～20%首倉｜跌破 {invalid} 停損｜確認轉強後才加碼"
            if gate.get("recovery_setup")
            else f"可以低接｜{current}附近20%～30%試單｜跌破 {invalid} 停損"
            if gate.get("controlled_low_trade")
            else f"買進｜{current}附近小量｜跌破 {invalid} 停損"
        )
    elif code == "HOLD" and bool(plan.get("actionable")):
        instruction = f"尚未買進｜回測站回 {confirm} 買；或放量突破 {breakout} 買｜跌破 {invalid} 取消"
    elif code == "HOLD":
        instruction = f"本日無買點｜持股守 {invalid}"
    elif code == "REDUCE":
        instruction = f"減碼｜空手不買｜跌破 {invalid} 全出"
    elif code == "SELL":
        instruction = f"賣出｜取消低接｜未收復 {confirm} 不重進"
    else:
        instruction = "禁止進場｜不建立新部位"

    if callable(compose_action_language):
        ticker = getattr(forecast, "ticker", None)
        symbol = _text(getattr(ticker, "resolved_symbol", None) or getattr(ticker, "symbol", None))
        leading = _text(top[0].get("label")) if top else "價格結構"
        language = compose_action_language(
            situation=situation, code=code, symbol=symbol, current=current,
            confirmation=confirm, breakout=breakout, invalid=invalid,
            metrics=gate, entry_state_label=_text(plan.get("entry_state_label")),
            leading_driver=leading,
        )
        label = language.get("label") or label
        instruction = language.get("instruction") or instruction
        reason = language.get("reason") or reason

    return {
        "code": code, "label": label, "icon": icon, "color": color,
        "instruction": instruction, "reason": reason,
        "acceptance_score": score, "bullish_evidence": bullish,
        "bearish_evidence": bearish, "evidence_dominance": dominance,
        "source_state": state, "hard_risk_veto": code == "BLOCK",
        "single_action": True, "entry_trigger_attached": bool(plan.get("actionable")),
        "trigger_status": trigger_status,
        "situation_code": situation,
        "language_schema": "V1087_EVIDENCE_LANGUAGE",
        "exit_reason_schema": "V1088_SEPARATED_EXIT_REASON",
        "exit_basis": exit_basis,
        "current_vs_invalidation": (
            "BELOW" if current_below_invalid else
            "ABOVE_OR_EQUAL" if current_n is not None and invalid_n is not None else
            "UNKNOWN"
        ),
        "intraday_breach_recovered": intraday_breach_recovered,
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
    gate = _cross_module_gate(forecast, entry, abc, top)
    plan = _entry_plan(forecast, entry, gate)
    action = _decisive_action(entry, plan, acceptance, top, gate, forecast)
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
        "cross_module_gate": gate,
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
