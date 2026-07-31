# -*- coding: utf-8 -*-
"""V1083 evidence arbitration + recommended entry price explanation.

Narrative/execution only: reuses V1081 entry state and prices, never mutates
formal forecasts, learning samples, weights, or calibration state.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Mapping

from evidence_reasoning_v1082 import build_evidence_reasoning as build_v1082

SCHEMA = "TINO_EVIDENCE_ARBITRATION_V1083"


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
    return sorted(unique, key=lambda r: (int(r.get("strength") or 0), bool(r.get("verified")), priority.get(_text(r.get("category")), 0)), reverse=True)


def _acceptance(entry: Mapping[str, Any], base: Mapping[str, Any], abc: Mapping[str, Any] | None, quantum: Mapping[str, Any] | None) -> Dict[str, Any]:
    score, pos, neg = 50, [], []
    state, vp = _text(entry.get("state")), _text(entry.get("vwap_position"))
    day = _num(entry.get("operative_return_pct")) or 0
    shape, market = _map(entry.get("price_shape")), _map(entry.get("market_context"))
    gap = _num(market.get("relative_gap_pct"))
    if vp == "above": score += 18; pos.append("VWAP上方")
    elif vp == "at": score += 6; pos.append("VWAP多空邊界")
    elif vp == "below": score -= 18; neg.append("VWAP下方")
    delta = {"BUY_TODAY_CONFIRM":15,"WAIT_VWAP_PULLBACK":8,"WAIT_VWAP_RECLAIM":-8,"WAIT_RECLAIM_HOLD":2,"LIMIT_LIQUIDITY_WAIT":8,"OVERHEATED_NO_CHASE":4,"SELLING_EXPANSION_BLOCK":-22,"FAILED_BREAKOUT_EXIT":-28,"DATA_WAIT":-18}.get(state,0)
    score += delta
    (pos if delta > 0 else neg).append(_text(entry.get("label")) or state) if delta else None
    if day >= 3: score += 5; pos.append(f"價格 {day:+.2f}%")
    elif day <= -3: score -= 5; neg.append(f"價格 {day:+.2f}%")
    if shape.get("near_high"): score += 6; pos.append("接近日內高檔")
    if shape.get("near_low"): score -= 6; neg.append("接近日內低檔")
    if shape.get("opening_selloff"): score -= 8; neg.append("開盤後賣壓")
    if gap is not None and gap >= 1.5: score += 8; pos.append(f"相對市場 {gap:+.2f}%")
    elif gap is not None and gap <= -4: score -= 12; neg.append(f"相對市場 {gap:+.2f}%")
    code = _text(base.get("code"))
    d = {"POSITIVE_CONFIRMED":10,"NEGATIVE_ABSORBED":6,"POSITIVE_REJECTED":-12,"NEGATIVE_CONFIRMED":-16}.get(code,0)
    score += d
    if d: (pos if d > 0 else neg).append(_text(base.get("label")))
    if abc and abc.get("a") is not None and abc.get("b") is not None:
        spread = float(abc["a"]) - float(abc["b"])
        if spread >= 8: score += 5; pos.append(f"ABC突破 +{spread:.0f}pp")
        elif spread <= -8: score -= 4; neg.append(f"ABC回測 +{abs(spread):.0f}pp")
    if quantum and quantum.get("stance_value"):
        score += 5 if quantum["stance_value"] > 0 else -5
        (pos if quantum["stance_value"] > 0 else neg).append("Quantum偏多" if quantum["stance_value"] > 0 else "Quantum偏空")
    score = max(0, min(100, int(round(score))))
    title = "利空吸收度" if code == "NEGATIVE_ABSORBED" else "利多價格接受度" if code in {"POSITIVE_CONFIRMED","POSITIVE_REJECTED"} else "利空價格確認度" if code == "NEGATIVE_CONFIRMED" else "價格結構確認度"
    grade = "已形成" if score >= 72 else "待加強" if score >= 55 else "尚未完成" if score >= 40 else "明顯不足"
    return {"code": code or "STRUCTURE_ONLY", "title": title, "score": score, "grade": grade, "label": f"{title} {score}%｜{grade}", "positives": pos[:4], "negatives": neg[:4]}


def _entry_plan(forecast: Any, entry: Mapping[str, Any]) -> Dict[str, Any]:
    d, state = _map(getattr(forecast, "decision_card", {})), _text(entry.get("state")) or "DATA_WAIT"
    last, vwap = _num(entry.get("operative_price")) or _num(d.get("現價")), _num(entry.get("vwap"))
    low = _num(d.get("最低") or d.get("今日低")); confirm = _first_num(d.get("轉強")) or _first_num(d.get("攻擊"))
    stop, no_chase = _num(d.get("防守")), _num(d.get("不追"))
    label, headline, condition, second, invalid, actionable = "建議進場", "等待同源價格與Session", "未驗證前不建立部位", "", "", False
    if state == "BUY_TODAY_CONFIRM":
        label, headline, condition, second, invalid, actionable = "建議首筆", f"現價 {_price(last)} 附近小量", f"站穩 {_price(confirm)}再加碼" if confirm else "突破平台後再加碼", f"第二筆等VWAP {_price(vwap)}回測不破" if vwap else "第二筆等量縮回測", f"跌破VWAP {_price(vwap)}未收回" if vwap else "突破失守", True
    elif state == "WAIT_VWAP_PULLBACK":
        label, headline, condition, second, invalid, actionable = "建議承接區", f"VWAP {_price(vwap)} 附近" if vwap else "首次量縮回測區", "回測不破且賣壓量縮才參與", f"站穩 {_price(confirm)}再加碼" if confirm else "重新放量再加碼", f"跌破VWAP {_price(vwap)}無法收回" if vwap else "回測失敗", True
    elif state == "WAIT_VWAP_RECLAIM":
        label, headline, condition, second, invalid = "確認型進場", f"收復VWAP {_price(vwap)}後回踩不破才買" if vwap else "收復關鍵均價後再買", "VWAP是確認門檻，不是直接追價", f"低點 {_price(low)}需守住" if low else "低點不得下移", f"跌破 {_price(low)}" if low else "再創新低"
    elif state == "WAIT_RECLAIM_HOLD":
        label, headline, condition, second, invalid, actionable = "建議確認區", f"VWAP {_price(vwap)} 附近維持5–15分鐘" if vwap else "多空交界維持5–15分鐘", "或回踩不破後小量參與", f"站穩 {_price(confirm)}轉強" if confirm else "高點抬升再加碼", f"跌回VWAP {_price(vwap)}下方" if vwap else "多空交界失守", True
    elif state == "LIMIT_LIQUIDITY_WAIT":
        label, headline, condition, second, invalid = "限價排隊", f"僅掛 {_price(last)} 小量，成交不確定", "開板後驗證賣壓、VWAP與回封", "不得加價追單", f"開板跌破 {_price(vwap or low)}" if (vwap or low) else "開板賣壓擴張"
    elif state == "OVERHEATED_NO_CHASE":
        label, headline, condition, second, invalid = "暫無進場價", f"不追現價 {_price(last)}", f"等待VWAP {_price(vwap)}或平台回測" if vwap else "等待量縮回測", f"不追 {_price(no_chase)}以上" if no_chase else "第三段加速不追", f"跌破 {_price(low)}" if low else "承接失敗"
    elif state == "SELLING_EXPANSION_BLOCK":
        label, headline, condition, second, invalid = "禁止進場", "賣壓未止，不提供低接價", f"至少先收復VWAP {_price(vwap)}" if vwap else "至少先止跌並收復均價", f"低點 {_price(low)}不得再破" if low else "低點不得下移", f"跌破 {_price(stop or low)}" if (stop or low) else "再創新低"
    elif state == "FAILED_BREAKOUT_EXIT":
        label, headline, condition, second, invalid = "取消進場", "原突破計畫失效", f"收復VWAP {_price(vwap)}後再評估" if vwap else "重回突破平台後再評估", "未修復前不新增", f"防守 {_price(stop)}" if stop else "原結構失守"
    elif state == "WAIT_NEXT_SESSION":
        label, headline, condition, second, invalid = "下一時段重算", "目前不沿用舊進場價追價", "下一正式盤驗證缺口、新VWAP與量能", f"前一VWAP {_price(vwap)}僅參考" if vwap else "等待新價格中樞", f"前低 {_price(low)}失守" if low else "缺口回補且賣壓擴張"
    return {"state": state, "label": label, "headline": headline, "condition": condition, "secondary": second, "invalidation": invalid, "actionable": actionable, "primary_price": last if state in {"BUY_TODAY_CONFIRM","LIMIT_LIQUIDITY_WAIT"} else vwap, "confirmation_price": confirm, "stop_price": stop, "no_chase_price": no_chase, "source": "V1081 Entry Opportunity / Price Tiles", "formal_price_model_unchanged": True}


def _conclusion(base: Mapping[str, Any], entry: Mapping[str, Any], plan: Mapping[str, Any], acceptance: Mapping[str, Any], top: list[Dict[str, Any]]) -> str:
    state, short, medium = _text(entry.get("state")), _text(base.get("short_term_bias")) or "待確認", _text(base.get("medium_term_bias")) or "待確認"
    winner = _text(top[0].get("label")) if top else "有效證據"
    h, c, invalid = _text(plan.get("headline")), _text(plan.get("condition")), _text(plan.get("invalidation"))
    if state == "BUY_TODAY_CONFIRM": return f"{winner}勝出，短線{short}；{h}，{c}，若{invalid}即取消。"
    if state == "WAIT_VWAP_PULLBACK": return f"方向仍為{short}但追價優勢不足；{h}，{c}。"
    if state == "WAIT_VWAP_RECLAIM": return f"目前不是低接點，短線{short}；{h}。{c}，若{invalid}維持觀望。"
    if state == "WAIT_RECLAIM_HOLD": return f"{acceptance['label']}，短線{short}、中線{medium}；{h}，{c}。"
    if state == "LIMIT_LIQUIDITY_WAIT": return f"價格強勢但流動性未驗證；{h}，{c}，不可加價追單。"
    if state == "OVERHEATED_NO_CHASE": return f"方向可能偏多但價格過熱；{h}，{c}。"
    if state == "SELLING_EXPANSION_BLOCK": return f"{winner}偏空證據勝出，{h}；{c}，反彈先視為修復。"
    if state == "FAILED_BREAKOUT_EXIT": return f"原突破條件失效；{h}，{c}。"
    if state == "WAIT_NEXT_SESSION": return f"目前Session不適合追價；{h}，{c}。"
    return f"{acceptance['label']}；{h}，{c}。"


def build_evidence_arbitration(forecast: Any, entry: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    entry = dict(entry or {})
    base = dict(build_v1082(forecast, entry) or {})
    radar = _map(getattr(forecast, "radar", {}))
    rows = [_base_packet(x) for x in list(base.get("top_drivers") or []) if isinstance(x, Mapping)]
    abc, quantum = _abc(radar), _quantum(radar)
    rows.extend(x for x in (abc, quantum) if x)
    ranked = _rank(rows); top = ranked[:3]
    acceptance = _acceptance(entry, _map(base.get("price_acceptance")), abc, quantum)
    plan = _entry_plan(forecast, entry)
    conclusion = _conclusion(base, entry, plan, acceptance, top)
    top_rows = []
    for i, row in enumerate(top, 1):
        row = dict(row); row.update({"rank": i, "reason": _short(row.get("text"))}); top_rows.append(row)
    summary = "｜".join(f"{x['rank']} {x['label']} {x['stars']} {x['stance']}：{x['reason']}" for x in top_rows) or "有效證據不足"
    winner = top_rows[0] if top_rows else {}
    out = dict(base)
    out.update({
        "schema": SCHEMA,
        "headline": f"{base.get('short_term_bias') or '待確認'}｜主導：{winner.get('label') or '有效證據不足'}",
        "decision_message": conclusion,
        "one_line_conclusion": conclusion,
        "evidence_winner": {k: winner.get(k) for k in ("category","label","stance","reason")},
        "price_acceptance": acceptance,
        "recommended_entry": plan,
        "top_drivers": top_rows,
        "top_driver_summary": summary,
        "abc_context": abc or {}, "quantum_context": quantum or {},
        "narrative_only": True, "decision_influence": False,
        "formal_forecast_unchanged": True, "formal_price_model_unchanged": True,
        "learning_sample_unchanged": True,
    })
    return out


build_evidence_reasoning = build_evidence_arbitration
reason_about_forecast = build_evidence_arbitration
