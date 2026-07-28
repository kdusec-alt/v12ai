# -*- coding: utf-8 -*-
"""Evidence-led Decision Thesis Engine for TINO V1072.

This layer does not invent a forecast.  It arbitrates already verified facts
into one coherent thesis: regime, dominant evidence, counter-evidence, action,
one preferred entry, confirmation and invalidation.  Evidence is registered by
family so the same macro story cannot be counted repeatedly through UI panels.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from price_truth_v1072 import price_truth
from trend_engine import build_trend_snapshot


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _sign(value: Any) -> int:
    number = _num(value)
    return 1 if number > 0 else -1 if number < 0 else 0


def _row(
    family: str,
    label: str,
    sign: int,
    text: str,
    *,
    available: bool = True,
    role: str = "context",
) -> Dict[str, Any]:
    return {
        "family": family,
        "label": label,
        "sign": int(sign),
        "text": str(text or ""),
        "available": bool(available),
        "role": role,
        "counted_once": True,
    }


def _fmt(value: float) -> str:
    return f"{float(value):,.2f}"


def build_decision_thesis(
    price: Any,
    direction: Any,
    reality: Mapping[str, Any],
    overseas: Mapping[str, Any],
    news: Mapping[str, Any],
    positioning: Mapping[str, Any],
    model: Mapping[str, Any],
    *,
    session_prefix: str,
    low1: float,
    low2: float,
    attack: float,
    stop: float,
    no_chase: float,
    hard_defense: bool = False,
    event_caution: bool = False,
    event_name: str = "一級宏觀事件",
    pause_second: bool = False,
    prediction_trust: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    truth = price_truth(price)
    trend = build_trend_snapshot(price)
    trust = dict(prediction_trust or {})
    market = str(getattr(getattr(price, "ticker", None), "market", "") or "").upper()
    session = str(truth.get("session") or "")

    day_pct = _num(reality.get("day_pct"))
    strong_up = bool(reality.get("strong_up"))
    strong_down = bool(reality.get("strong_down"))
    trend_break = bool(reality.get("trend_break"))
    limit_like = bool(reality.get("limit_like"))
    above_vwap = bool(reality.get("above_vwap"))
    vwap_available = bool(reality.get("vwap_available", truth.get("vwap_available")))
    structural_bear = bool(
        (trend.ma20_gap_pct is not None and trend.ma20_gap_pct < -1.5)
        or (trend.ma60_gap_pct is not None and trend.ma60_gap_pct < -8.0)
    )
    structural_bull = bool(
        trend.ma20_gap_pct is not None
        and trend.ma20_gap_pct >= 0
        and (trend.ma60_gap_pct is None or trend.ma60_gap_pct >= -2.5)
    )

    move_label = str(truth.get("return_label") or ("今日漲跌" if market == "TW" else "時段漲跌"))
    vwap_text = str(truth.get("vwap_state") or "VWAP待確認")
    price_text = (
        f"{move_label} {day_pct:+.2f}%｜{vwap_text}｜"
        f"區間位置 {_num(reality.get('close_location')) * 100:.0f}%"
    )
    trend_text = (
        f"MA20 {trend.ma20_gap_pct:+.2f}%｜MA60 {trend.ma60_gap_pct:+.2f}%"
        if trend.ma20_gap_pct is not None and trend.ma60_gap_pct is not None
        else trend.ma_alert
    )

    ledger: Dict[str, Dict[str, Any]] = {}

    def register(row: Dict[str, Any]) -> None:
        family = str(row.get("family") or "")
        if family and family not in ledger:
            ledger[family] = row

    register(_row("price", "價格", _sign(day_pct), price_text, role="primary"))
    register(_row(
        "structural_trend",
        "結構趨勢",
        -1 if structural_bear else 1 if structural_bull else 0,
        trend_text,
        role="primary",
    ))
    register(_row(
        "overseas_proxy",
        "海外代理",
        int(overseas.get("sign") or 0),
        str(overseas.get("text") or ""),
        available=bool(overseas.get("available")),
    ))
    register(_row(
        "company_news",
        "公司／產業新聞",
        int(news.get("company_sign") or 0),
        str(news.get("company_text") or "公司新聞無明確方向"),
        available=bool(news.get("company_available")),
    ))
    register(_row(
        "macro_event",
        "宏觀事件",
        int(news.get("global_sign") or 0),
        str(news.get("global_text") or "宏觀事件無明確方向"),
        available=bool(news.get("global_available")),
    ))
    register(_row(
        "positioning",
        "籌碼／部位",
        int(positioning.get("sign") or 0),
        str(positioning.get("text") or ""),
        available=bool(positioning.get("available")),
    ))
    register(_row(
        "direction_model",
        "方向模型",
        int(model.get("sign") or 0),
        str(model.get("text") or ""),
        role="secondary",
    ))
    if trust.get("accepted"):
        trust_sign = -1 if str(trust.get("severity")) in {"moderate", "high", "severe"} else 0
        register(_row(
            "prediction_trust",
            "昨測校準",
            trust_sign,
            str(trust.get("reason") or ""),
            role="safety",
        ))

    company_sign = int(news.get("company_sign") or 0)
    earnings = dict(news.get("earnings") or {})
    earnings_state = str(earnings.get("state") or "")
    independent_negative = [
        row["label"]
        for row in ledger.values()
        if row.get("available") and row.get("sign", 0) < 0 and row["family"] != "price"
    ]
    independent_positive = [
        row["label"]
        for row in ledger.values()
        if row.get("available") and row.get("sign", 0) > 0 and row["family"] != "price"
    ]

    blocked_by_truth = bool(truth.get("decision_blocked"))
    severe_miss = str(trust.get("severity") or "") == "severe"
    high_miss = str(trust.get("severity") or "") == "high"
    active_us = market == "US" and session in {"pre_market", "intraday", "after_hours"} and bool(
        truth.get("live_session_quote")
    )
    repricing_threshold = -max(4.0, _num(getattr(price, "atr14", 0.0)) / max(_num(getattr(price, "previous_close", 0.0)), 0.01) * 80.0)

    state = "range_wait"
    title = "AI進場決策卡｜盤整等待｜讓價格裁決"
    axis = "盤整等待｜價格確認"
    entry_permission = "conditional"
    buy_now = False
    action_mode = "wait"
    dominant = price_text
    counter = "尚無足以推翻價格狀態的獨立反證"

    if blocked_by_truth:
        state = "price_truth_blocked"
        title = "AI進場決策卡｜價格基準不一致｜暫停判定"
        axis = "資料閘門｜等待同源價格"
        entry_permission = "blocked"
        action_mode = "data_wait"
        dominant = "價格、基準價或時段區間未通過一致性驗證"
    elif severe_miss and (day_pct <= -3.0 or trend_break):
        state = "forecast_cooldown"
        title = "AI進場決策卡｜價格結構破壞＋模型失準冷卻"
        axis = "預測熔斷｜禁止把觀察支撐當買點"
        entry_permission = "blocked"
        action_mode = "cooldown"
        dominant = f"{price_text}；{trust.get('reason')}"
        counter = "即使仍在長期均線上方，也不足以解除短期模型冷卻"
    elif (
        (active_us or market == "TW")
        and day_pct <= -4.0
        and earnings_state in {
            "backward_beat_forward_miss",
            "backward_beat_high_bar_reset",
            "forward_miss",
        }
    ):
        state = "earnings_expectation_reset"
        title = (
            "AI進場決策卡｜財報後預期重定價｜本季佳績不抵銷前瞻落差"
            if earnings.get("backward_positive")
            else "AI進場決策卡｜財報後前瞻重定價｜先等正式盤"
        )
        axis = "Forward Guidance 優先｜估值／預期重定價｜不在急跌中接刀"
        entry_permission = "blocked"
        action_mode = "earnings_repricing"
        dominant = f"{earnings.get('text')}；{price_text}"
        counter = (
            "本季營收／獲利優於預期是長期正面反證，但不能否決當下前瞻重定價"
            if earnings.get("backward_positive")
            else "公司長期題材仍需與正式盤承接交叉確認"
        )
    elif active_us and day_pct <= repricing_threshold:
        state = "session_repricing"
        title = f"AI進場決策卡｜{truth.get('session_scope')}重定價｜先等正式盤驗證"
        axis = "延長盤重定價｜不把盤前低點當低接"
        entry_permission = "blocked"
        action_mode = "session_wait"
        dominant = price_text
        counter = "正式盤尚未形成 15–30 分鐘承接，延長盤 VWAP 不能代表全天"
    elif active_us and day_pct > 0.5 and above_vwap and structural_bear:
        state = "session_countertrend_rebound"
        title = f"AI進場決策卡｜{truth.get('session_scope')}反彈已收復VWAP｜結構尚未翻多"
        axis = "時段反彈｜承認收復VWAP｜等待趨勢確認"
        entry_permission = "conditional"
        action_mode = "confirmation_only"
        dominant = f"{price_text}；但{trend_text}"
        counter = "時段買盤是正面反證，若正式盤續守 VWAP 並站回 MA20，空頭反彈假設失效"
    elif (trend_break or strong_down) and company_sign > 0:
        state = "good_news_rejected"
        title = "AI進場決策卡｜公司利多未獲價格確認｜暫停低接"
        axis = "利多失效｜價格優先｜等待收復"
        entry_permission = "blocked"
        action_mode = "reclaim_only"
        dominant = price_text
        counter = str(news.get("company_text") or "公司新聞偏多")
    elif trend_break or strong_down:
        state = "trend_break"
        title = "AI進場決策卡｜價格結構破壞｜防守優先"
        axis = "結構破壞｜觀察支撐不是買點"
        entry_permission = "blocked" if day_pct <= -6.0 or high_miss else "conditional"
        action_mode = "reclaim_only"
        dominant = price_text
        counter = "必須先停止破低並收復確認價，才有資格談反彈"
    elif strong_up and structural_bear:
        state = "countertrend_breakout"
        title = "AI進場決策卡｜下降趨勢中的強勢反攻｜尚未全面翻多"
        axis = "反趨勢突破｜不追開高｜用確認價裁決"
        entry_permission = "conditional"
        action_mode = "pullback_or_confirmation"
        dominant = price_text
        counter = f"中期結構仍弱：{trend_text}"
    elif strong_up and company_sign < 0:
        state = "bad_news_absorbed"
        title = "AI進場決策卡｜公司利空未壓低價格｜強勢吸收"
        axis = "公司利空吸收｜價格優先｜回測確認"
        entry_permission = "conditional"
        action_mode = "pullback"
        dominant = price_text
        counter = str(news.get("company_text") or "公司新聞偏空")
    elif strong_up and (independent_negative or hard_defense):
        state = "surge_divergence"
        title = "AI進場決策卡｜急漲但獨立證據背離｜不直接判空"
        axis = "價格強｜證據背離｜守支撐"
        entry_permission = "conditional"
        action_mode = "pullback"
        dominant = price_text
        counter = "、".join(independent_negative[:3]) + "尚未同步"
    elif limit_like and len(independent_positive) >= 2:
        state = "limit_breakout"
        title = "AI進場決策卡｜漲停／極強突破｜同向確認"
        axis = "極強突破｜回測守穩再續攻"
        entry_permission = "conditional"
        action_mode = "pullback"
        dominant = f"{price_text}；{'、'.join(independent_positive[:3])}同向"
    elif strong_up:
        state = "strong_continuation"
        title = "AI進場決策卡｜強勢續攻｜回測確認"
        axis = "強勢續攻｜守支撐不追高"
        entry_permission = "conditional"
        action_mode = "pullback"
        dominant = price_text
    elif bool(reality.get("deep_stabilizing")):
        state = "deep_stabilization"
        title = "AI進場決策卡｜跌深出現承接｜只做確認單"
        axis = "跌深止穩｜確認後試單"
        entry_permission = "conditional"
        action_mode = "confirmation_only"
        dominant = price_text
    elif bool(reality.get("weak_rebound")) or (day_pct > 0.5 and structural_bear):
        state = "weak_rebound"
        title = "AI進場決策卡｜弱勢反彈｜尚未翻多"
        axis = "弱勢反彈｜等待站回關鍵價"
        entry_permission = "conditional"
        action_mode = "confirmation_only"
        dominant = f"{price_text}；{trend_text}"
        counter = f"{vwap_text}是短線正面證據，但不能取代 MA20／MA60 結構"
    elif event_caution:
        state = "event_caution"
        title = "AI進場決策卡｜事件前不猜方向｜公布後確認"
        axis = "一級事件前｜縮小部位｜等待價格確認"
        entry_permission = "conditional"
        action_mode = "event_wait"
        dominant = f"{event_name}尚未公布"
    elif _num(model.get("conflict")) >= 0.45:
        state = "evidence_conflict"
        title = "AI進場決策卡｜證據衝突｜等待價格裁決"
        axis = "證據衝突｜只做條件單"
        entry_permission = "conditional"
        action_mode = "pullback_or_confirmation"
    elif int(model.get("sign") or 0) > 0:
        state = "conditional_attack"
        title = "AI進場決策卡｜條件式偏多｜站穩再攻"
        axis = "條件式偏多｜站穩再攻"
        action_mode = "pullback_or_confirmation"
    elif int(model.get("sign") or 0) < 0 or hard_defense:
        state = "conditional_defense"
        title = "AI進場決策卡｜條件式偏弱｜只等止穩"
        axis = "條件式偏弱｜防守低接"
        action_mode = "pullback"

    preferred = float(low1)
    second = float(low2)
    confirmation = float(attack)
    invalid = float(stop)

    if state == "price_truth_blocked":
        message = f"{session_prefix}：價格基準尚未同源，暫停買進；即時價、參考收盤與時段高低同步後重算。"
    elif state == "forecast_cooldown":
        message = (
            f"{session_prefix}：{move_label} {day_pct:+.2f}%，且{trust.get('reason')}；"
            f"{_fmt(preferred)} 只列觀察支撐，不是買點。至少先停止破低並站回 {_fmt(confirmation)}，"
            f"跌破 {_fmt(invalid)} 持續取消計畫。"
        )
    elif state == "session_repricing":
        next_window = "下一個正式盤" if session == "after_hours" else "正式開盤後"
        message = (
            f"{session_prefix}：{move_label} {day_pct:+.2f}% 且位於{truth.get('session_scope')}弱側，市場正在重新定價；"
            f"現在不接。等{next_window} 15–30 分鐘不再破低，並站回 {_fmt(confirmation)} 才小量確認，"
            f"跌破 {_fmt(invalid)} 取消計畫。"
        )
    elif state == "earnings_expectation_reset":
        nuance = (
            "本季實績優於預期，但前瞻財測偏弱"
            if earnings_state == "backward_beat_forward_miss"
            else "本季實績優於預期，但財測未跨過市場隱含高標"
            if earnings_state == "backward_beat_high_bar_reset"
            else "前瞻財測偏弱"
        )
        opening_wait = (
            "下一交易日開盤後"
            if market == "TW" or session == "after_hours"
            else "正式開盤"
        )
        message = (
            f"{session_prefix}：{nuance}，{move_label} {day_pct:+.2f}% 顯示市場正重估未來成長／估值；"
            f"公司長期 AI 題材不能抵銷當下價格否決，且不應讓 FOMC 等共通事件搶走個股主因。"
            f"現在不買；等{opening_wait} 15–30 分鐘不再破低並站回 {_fmt(confirmation)} 才小量確認，"
            f"跌破 {_fmt(invalid)} 取消。"
        )
    elif state in {"trend_break", "good_news_rejected"}:
        rejection = "公司利多出現但價格未買單；" if state == "good_news_rejected" else ""
        message = (
            f"{session_prefix}：{rejection}{move_label} {day_pct:+.2f}% 且價格結構受損；"
            f"{_fmt(preferred)} 只看是否止穩，不直接買。重新站回 {_fmt(confirmation)} 才能小量確認，"
            f"跌破 {_fmt(invalid)} 取消計畫。"
        )
    elif state == "session_countertrend_rebound":
        message = (
            f"{session_prefix}：{move_label} {day_pct:+.2f}% 且已在{vwap_text}，短線反彈成立；"
            f"但仍低於中期均線。正式盤續守 VWAP 並站穩 {_fmt(confirmation)} 才小量買進，"
            f"跌破 {_fmt(invalid)} 取消。"
        )
    elif state == "countertrend_breakout":
        message = (
            f"{session_prefix}：{move_label} {day_pct:+.2f}% 是下降趨勢中的強力反攻，不等於長趨勢翻多；"
            f"首選 {_fmt(preferred)} 止穩後小量，未回測則站穩 {_fmt(confirmation)} 才確認，"
            f"跌破 {_fmt(invalid)} 取消。"
        )
    elif state in {"bad_news_absorbed", "surge_divergence", "limit_breakout", "strong_continuation"}:
        absorption = "負面新聞未能壓低價格；" if state == "bad_news_absorbed" else ""
        message = (
            f"{session_prefix}：{absorption}{dominant}；不追開高。首選 {_fmt(preferred)} 回測止穩後分批，"
            f"第二承接 {_fmt(second)}；若未回測，站穩 {_fmt(confirmation)} 才小量確認，"
            f"{_fmt(no_chase)} 以上不加碼，跌破 {_fmt(invalid)} 取消。"
        )
    elif state == "deep_stabilization":
        message = (
            f"{session_prefix}：價格跌深後出現承接，但尚不是直接買進訊號；"
            f"站穩 {_fmt(confirmation)} 才試小單，回測 {_fmt(preferred)} 不破可分批，"
            f"跌破 {_fmt(invalid)} 取消。"
        )
    elif state in {"weak_rebound", "event_caution"}:
        reason = (
            f"{move_label} {day_pct:+.2f}% 且{vwap_text}，但中期趨勢仍弱"
            if state == "weak_rebound"
            else f"{event_name}公布前不預設方向"
        )
        message = (
            f"{session_prefix}：{reason}；站穩 {_fmt(confirmation)} 才小量確認，"
            f"回測 {_fmt(preferred)} 必須先止穩，跌破 {_fmt(invalid)} 取消。"
        )
    else:
        message = (
            f"{session_prefix}：目前沒有足以直接買進的單一優勢；首選 {_fmt(preferred)} 止穩後小量，"
            f"未回測則站穩 {_fmt(confirmation)} 才確認，跌破 {_fmt(invalid)} 取消。"
        )

    if pause_second:
        message = message.replace(f"第二承接 {_fmt(second)}", "融資降溫前暫停第二批")
        axis += "｜第二批暫停"

    if entry_permission == "blocked":
        attack_text = f"站回 {_fmt(confirmation)} 後重評"
        turn_text = f"收復 {_fmt(confirmation)} 才解除禁買"
    elif action_mode == "pullback":
        attack_text = f"首選 {_fmt(preferred)} 止穩"
        turn_text = f"站穩 {_fmt(confirmation)} 確認"
    else:
        attack_text = f"站穩 {_fmt(confirmation)} 小量"
        turn_text = f"收復 {_fmt(confirmation)} 才轉強"

    evidence_rows = [row for row in ledger.values() if row.get("available")]
    evidence_line = "；".join(f"{row['label']} {row['text']}" for row in evidence_rows)
    return {
        "schema": "TINO_DECISION_THESIS_V1072",
        "state": state,
        "title": title,
        "message": message,
        "axis": axis,
        "action_mode": action_mode,
        "entry_permission": entry_permission,
        "buy_now": buy_now,
        "preferred_entry": round(preferred, 4),
        "second_entry": round(second, 4),
        "confirmation": round(confirmation, 4),
        "invalidation": round(invalid, 4),
        "no_chase": round(float(no_chase), 4),
        "trigger": f"站穩 {_fmt(confirmation)}",
        "dominant_evidence": dominant,
        "counter_evidence": counter,
        "evidence_line": evidence_line,
        "evidence_ledger": evidence_rows,
        "evidence_families_counted": [row["family"] for row in evidence_rows],
        "positive_groups": independent_positive,
        "negative_groups": independent_negative,
        "attack_text": attack_text,
        "turn_text": turn_text,
        "price_truth": truth,
        "trend": trend.to_dict(),
        "prediction_trust": trust,
        "earnings_evidence": earnings,
        # Formal direction probabilities and T0/T1 prices stay immutable.
        "narrative_only": True,
        "decision_gate_only": True,
    }
