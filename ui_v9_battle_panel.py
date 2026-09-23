# -*- coding: utf-8 -*-
from __future__ import annotations

from ui_html import fmt, html_block, safe
from decision_brief_v1101 import build_decision_brief
from stock_analysis_narrative_v1108 import build_stock_analysis


def _decision_snapshot_payload(forecast):
    """Read the Orchestrator-owned snapshot; never arbitrate in the UI."""
    snapshot = getattr(forecast, "decision_snapshot", None)
    if snapshot is not None and callable(getattr(snapshot, "to_dict", None)):
        return dict(snapshot.to_dict())
    card = getattr(forecast, "decision_card", {}) or {}
    stored = card.get("_decision_snapshot_v1096") if isinstance(card, dict) else None
    if isinstance(stored, dict):
        return dict(stored)
    reason = "正式決策快照未完成；UI禁止重新仲裁"
    return {
        "schema": "TINO_DECISION_SNAPSHOT_V1096_BLOCKED",
        "action_code": "BLOCK", "label": "禁止進場", "icon": "🔴", "color": "red",
        "instruction": "禁止進場｜不建立新部位", "reason": reason,
        "entry": {"entry_state_label": "決策快照待確認", "missing_conditions": [reason]},
        "reasoning": {
            "headline": "資料待確認", "decision_message": reason,
            "one_line_conclusion": reason, "top_drivers": [],
            "top_driver_summary": "有效證據不足", "price_acceptance": {},
            "cross_module_gate": {"label": "資料未驗證", "reasons": [reason]},
            "abc_context": {}, "quantum_context": {},
        },
        "funnel": [{"stage": "truth", "status": "FAIL", "reason": reason}],
    }


def _title_price(v):
    try:
        x = float(v)
    except Exception:
        return "--"
    if abs(x) >= 1000:
        return f"{x:,.0f}"
    if abs(x) >= 100:
        return f"{x:,.1f}"
    return f"{x:,.2f}"


def _title_pct(v):
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "--"


def _ma_title_piece(label: str, value, gap) -> str:
    if value in (None, "", "--") or gap in (None, "", "--"):
        return f"{label}尚未形成"
    try:
        float(gap)
    except Exception:
        return f"{label}尚未形成"
    return f"{label} {_title_price(value)}｜距離 {_title_pct(gap)}"


def _strip_compare_prefix(text: object, *prefixes: str) -> str:
    value = str(text or "").strip()
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def _header_trend_line(forecast) -> str:
    tags = list(getattr(forecast, "tags", []) or [])
    streak_raw = str(tags[0]) if tags else "盤勢觀察"
    mode = ""
    for item in ("盤中參考", "盤前參考", "盤後參考", "休市參考"):
        if item in streak_raw:
            mode = item
            streak_raw = streak_raw.replace(f"｜{item}", "").replace(item, "")
            break
    snap = {}
    try:
        snap = ((forecast.decision_card or {}).get("_trend_snapshot") or {})
    except Exception:
        snap = {}
    parts = [
        streak_raw.strip("｜ ") or "盤勢觀察",
        _ma_title_piece("MA20", snap.get("ma20"), snap.get("ma20_gap_pct")),
        _ma_title_piece("MA60", snap.get("ma60"), snap.get("ma60_gap_pct")),
    ]
    if mode:
        parts.append(mode)
    return " │ ".join(parts)


def _compact_evidence_piece(text: object, *, segments: int = 2, max_chars: int = 82) -> str:
    """Return a readable evidence headline without destroying the full payload."""
    raw = " ".join(str(text or "").replace("\n", " ").split())
    if not raw:
        return ""
    output = []
    seen = set()
    for part in raw.replace(" | ", "｜").split("｜"):
        value = part.strip(" ｜")
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
        if len(output) >= max(1, int(segments)):
            break
    summary = "｜".join(output) or raw
    return summary if len(summary) <= max_chars else summary[: max_chars - 1].rstrip() + "…"


def _compact_evidence_summary(evidence: object, market: object, chip: object, market_code: str) -> str:
    parts = []
    evidence_piece = _compact_evidence_piece(evidence, segments=2, max_chars=88)
    market_piece = _compact_evidence_piece(market, segments=1, max_chars=54)
    chip_piece = _compact_evidence_piece(chip, segments=1, max_chars=48)
    if evidence_piece:
        parts.append(evidence_piece)
    if market_piece:
        parts.append(f"市場 {market_piece}")
    if chip_piece:
        parts.append(f"{'Short' if market_code == 'US' else '籌碼'} {chip_piece}")
    return "｜".join(parts) or "等待價格、海外市場與籌碼確認"


def _entry_range_text(lower, upper) -> str:
    if lower in (None, "") and upper in (None, ""):
        return "--"
    if lower in (None, ""):
        return _title_price(upper)
    if upper in (None, ""):
        return _title_price(lower)
    try:
        lo, hi = sorted((float(lower), float(upper)))
        if abs(hi - lo) <= max(abs(lo) * 0.0002, 0.01):
            return _title_price(lo)
        return f"{_title_price(lo)}～{_title_price(hi)}"
    except Exception:
        return "--"


def _entry_map_tiles(plan, fallback_tiles):
    """Render V1083.1 as one consistent five-column execution map."""
    if not isinstance(plan, dict) or not plan:
        return list(fallback_tiles or [])

    zone = plan.get("low_entry_zone") if isinstance(plan.get("low_entry_zone"), dict) else {}
    low_condition = str(plan.get("low_entry_condition") or zone.get("text") or "")
    low_value = _entry_range_text(zone.get("lower"), zone.get("upper"))
    if low_value == "--" and "禁止" in low_condition:
        low_value = "禁止"
    elif low_value == "--" and low_condition:
        low_value = "等待"

    current_price = _title_price(plan.get("current_price"))
    current_location = str(plan.get("current_location") or "位置待確認")
    current_action = str(plan.get("current_action") or "等待同源價格")
    confirmation = _title_price(plan.get("confirmation_price"))
    add_price = _title_price(plan.get("add_price"))
    invalid = _title_price(plan.get("invalidation_price"))

    return [
        {
            "label": "現在",
            "value": f"{current_price}｜{current_location}",
            "title": current_action,
        },
        {
            "label": "低接",
            "value": low_value,
            "title": low_condition or "目前不建立左側低接價",
        },
        {
            "label": "確認",
            "value": confirmation,
            "title": str(plan.get("confirmation_text") or "本日無回測買點"),
        },
        {
            "label": "加碼",
            "value": add_price,
            "title": str(plan.get("breakout_text") or plan.get("add_text") or "本日無突破買點"),
        },
        {
            "label": "失效",
            "value": f"{invalid}跌破" if invalid != "--" else "條件失效",
            "title": str(plan.get("invalidation_text") or plan.get("invalidation") or "條件失效即取消"),
        },
    ]


def render_battle_panel(st, forecast):
    if forecast.stopped:
        st.error(forecast.stop_reason)
        return
    p = forecast
    t = p.ticker
    d = p.decision_card or {}
    data_title = str(d.get("資料標題", ""))
    is_intraday = data_title.startswith("盤中")
    is_closed = data_title.startswith("收盤")
    close_reference = is_closed or data_title.startswith("盤後") or data_title.startswith("休市")
    t0_line = f"<br><span class='label'>今日收盤預估：</span>{fmt(p.final_t0)}" if is_intraday else ""
    compare_line = ""
    if close_reference:
        try:
            from learning import t1_prediction_vs_actual, today_prediction_vs_actual
            cmp = t1_prediction_vs_actual(p, d.get("現價"))
            text = _strip_compare_prefix(cmp.get("display", ""), "昨測今收：", "昨測今收預覽：")
            if cmp.get("status") in {"audited", "preview"} and text and "尚無昨日" not in text:
                compare_line = f"<br><span class='label'>昨測今收：</span>{safe(text)}"
            elif is_closed:
                alt_cmp = today_prediction_vs_actual(p, d.get("現價"))
                text = _strip_compare_prefix(alt_cmp.get("display", ""), "今日預測VS實際：", "今日預測VS實際預覽：")
                if alt_cmp.get("status") in {"audited", "preview"} and text and "尚無" not in text:
                    compare_line = f"<br><span class='label'>今日預測VS實際：</span>{safe(text)}"
        except Exception as exc:
            compare_line = f"<br><span class='label'>昨測今收：</span>暫無可用比對（{safe(type(exc).__name__)}）"

    fair = safe(p.radar.get("Fair Value", ""))
    persona_badge = safe(p.radar.get("US Persona", "") or "")
    persona_html = f"<div class='persona'>{persona_badge}</div>" if persona_badge else ""
    header_trend = _header_trend_line(p)
    header_streak_positive = "+" in header_trend.split("│", 1)[0]

    public_snapshot = _decision_snapshot_payload(p)
    reasoning = public_snapshot.get("reasoning") if isinstance(public_snapshot.get("reasoning"), dict) else {}
    entry_plan = public_snapshot.get("entry") if isinstance(public_snapshot.get("entry"), dict) else {}
    action_decision = reasoning.get("action_decision") if isinstance(reasoning.get("action_decision"), dict) else {
        "code": public_snapshot.get("action_code"), "label": public_snapshot.get("label"),
        "icon": public_snapshot.get("icon"), "color": public_snapshot.get("color"),
        "instruction": public_snapshot.get("instruction"), "reason": public_snapshot.get("reason"),
    }
    decision_brief = build_decision_brief(public_snapshot, radar=p.radar)
    analysis_row = build_stock_analysis(p, decision_brief)
    conditional_plan = entry_plan.get("conditional_next_session") if isinstance(entry_plan.get("conditional_next_session"), dict) else {}
    display_plan = conditional_plan if bool(decision_brief.get("candidate_mode")) else entry_plan
    entry = {
        "state": action_decision.get("source_state") or entry_plan.get("state") or "DATA_WAIT",
        "color": public_snapshot.get("color") or "red",
        "icon": public_snapshot.get("icon") or "🔴",
        "conditions": [], "price_tiles": [], "show_score": False,
    }

    entry_color = str(action_decision.get("color") or entry.get("color") or "red")
    entry_icon = safe(action_decision.get("icon") or entry.get("icon") or "🔴")
    entry_label = safe(decision_brief.get("verdict") or action_decision.get("label") or "禁止進場")
    entry_summary = safe(decision_brief.get("summary") or action_decision.get("reason") or "禁止進場｜不建立新部位")
    intelligence_thesis = safe(decision_brief.get("thesis") or entry_summary)
    intelligence_risk = safe(decision_brief.get("primary_risk") or "風險條件待確認")
    intelligence_confidence = safe(decision_brief.get("confidence_label") or "低")
    entry_score_html = ""
    if bool(entry.get("show_score")):
        entry_score_html = f"<span class='score'>{int(entry.get('score') or 0)}%</span>"

    entry_price_strategy_raw = str(entry.get("price_strategy_text") or "等待價格與時段確認")
    legacy_price_tiles = list(entry.get("price_tiles") or [])
    if len(legacy_price_tiles) < 5:
        legacy_price_tiles = [
            {"label": "進場", "value": entry_price_strategy_raw},
            {"label": "攻擊", "value": d.get("攻擊")},
            {"label": "轉強", "value": d.get("轉強")},
            {"label": "停手", "value": fmt(d.get("防守"))},
            {"label": "不追", "value": fmt(d.get("不追"))},
        ]
    raw_price_tiles = _entry_map_tiles(display_plan, legacy_price_tiles)
    if bool(decision_brief.get("candidate_mode")):
        raw_price_tiles = [
            {"label": "低接", "value": decision_brief.get("entry_zone"), "title": decision_brief.get("entry_instruction")},
            {"label": "確認", "value": decision_brief.get("confirmation"), "title": decision_brief.get("confirmation_instruction")},
            {"label": "加碼", "value": decision_brief.get("breakout"), "title": decision_brief.get("breakout_instruction")},
            {"label": "失效", "value": f"{decision_brief.get('invalidation')}跌破", "title": decision_brief.get("invalidation_instruction")},
        ]
    price_tiles_html = "".join(
        "<div class='priceitem' title='"
        + safe(row.get("title") or row.get("value") or "")
        + "'><b>" + safe(row.get("label") or "條件") + "</b>"
        + safe(row.get("value") or "--") + "</div>"
        for row in raw_price_tiles[:5]
    )

    flat_action = str(decision_brief.get("flat_action") or "等待價格確認")
    holding_action = str(decision_brief.get("holding_action") or "依失效條件管理")
    current_action_raw = f"空手｜{flat_action}　　持股｜{holding_action}"
    current_action_text = safe(current_action_raw)

    decision_title_raw = str(reasoning.get("headline") or "").strip()
    if not decision_title_raw:
        decision_title_raw = _strip_compare_prefix(
            d.get("標題", "AI決策"), "AI進場決策卡｜", "AI進場決策卡 |"
        )
    decision_title = safe(decision_title_raw or "AI決策")
    evidence_raw = str(d.get("證據鏈", "") or "")
    market_raw = str(p.radar.get("市場風控", "") or "")
    chip_raw = str(p.radar.get("左側籌碼摘要", "") or "")
    fallback_summary = _compact_evidence_summary(evidence_raw, market_raw, chip_raw, str(t.market or ""))
    brief_reasons = list(decision_brief.get("reasons") or [])[:3]
    evidence_summary_raw = "｜".join(f"{index}. {value}" for index, value in enumerate(brief_reasons, 1)) or fallback_summary
    evidence_summary = safe(evidence_summary_raw)
    evidence = safe(evidence_raw)
    market = safe(market_raw)
    chip = safe(chip_raw)
    cross_gate = (reasoning.get("cross_module_gate") or {})
    gate_reasons = "、".join(str(x) for x in list(cross_gate.get("reasons") or [])[:3])
    gate_detail_raw = f"{cross_gate.get('label') or '跨模組待確認'}"
    if gate_reasons:
        gate_detail_raw += f"｜{gate_reasons}"
    entry_state_detail_raw = str(entry_plan.get("entry_state_label") or "進場狀態待確認")
    missing_conditions = "、".join(str(x) for x in list(entry_plan.get("missing_conditions") or [])[:3])
    if missing_conditions:
        entry_state_detail_raw += f"｜尚缺：{missing_conditions}"
    funnel_missing = [
        str(row.get("reason") or row.get("stage") or "")
        for row in list(public_snapshot.get("funnel") or [])
        if isinstance(row, dict) and row.get("status") in {"FAIL", "UNKNOWN"}
    ]
    funnel_missing = [item for item in funnel_missing if item][:3]
    if funnel_missing:
        entry_state_detail_raw += f"｜買進漏斗：{'、'.join(funnel_missing)}"
    gate_detail = safe(gate_detail_raw)
    entry_state_detail = safe(entry_state_detail_raw)
    reasoning_conflict = safe(reasoning.get("conflict") or "有效證據尚未形成單一主導方向")
    entry_plan_raw = str(entry_plan.get("display_line") or "").strip()
    if not entry_plan_raw:
        entry_plan_raw = "｜".join(
            item for item in (
                f"{entry_plan.get('label') or '建議進場'} {entry_plan.get('headline') or '等待同源價格'}",
                str(entry_plan.get("condition") or ""),
                str(entry_plan.get("secondary") or ""),
                f"失效：{entry_plan.get('invalidation')}" if entry_plan.get("invalidation") else "",
            ) if item
        )
    entry_plan_text = safe(entry_plan_raw)
    abc_detail = safe((reasoning.get("abc_context") or {}).get("text") or "未形成")
    quantum_detail = safe((reasoning.get("quantum_context") or {}).get("text") or "未形成")
    executive_price_line = safe(
        f"低接 {decision_brief.get('entry_zone')}｜確認 {decision_brief.get('confirmation')}｜"
        f"加碼 {decision_brief.get('breakout')}｜失效 {decision_brief.get('invalidation')}"
    )
    staged_entry_line = safe(decision_brief.get("staged_entry") or "等待價格結構完成")
    analysis_industry = safe(analysis_row.get("industry"))
    analysis_price = safe(analysis_row.get("price_status"))
    analysis_model_low = safe(analysis_row.get("model_low"))
    analysis_entry = safe(analysis_row.get("entry"))
    analysis_risk = safe(analysis_row.get("risk"))
    analysis_evidence = safe(analysis_row.get("evidence"))
    analysis_confidence = safe(analysis_row.get("confidence"))

    html = f"""
    <!doctype html><html><head><meta charset='utf-8'>
    <style>
    *{{box-sizing:border-box}}body{{margin:0;background:transparent;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft JhengHei',Arial,sans-serif;color:#edf7ff}}
    .panel{{background:linear-gradient(180deg,#041321 0%,#02080d 100%);border-left:5px solid #37e6ff;min-height:612px;max-height:634px;padding:4px 8px 5px;border-right:1px solid rgba(55,230,255,.16);overflow-x:hidden;overflow-y:auto;scrollbar-width:thin}}
    .head{{border-bottom:1px solid rgba(55,230,255,.22);padding-bottom:5px;display:grid;grid-template-columns:minmax(0,1fr) minmax(220px,318px);gap:8px;align-items:start}}
    h1{{margin:0;color:#fff;font-size:20px;font-weight:900;letter-spacing:.01em}}.streak{{margin-top:1px;color:{'#6dffb1' if header_streak_positive else '#ff6f8e'};font-weight:800;font-size:11px}}
    .fvleft{{border:1px solid rgba(45,212,191,.28);background:linear-gradient(135deg,rgba(6,78,59,.18),rgba(2,18,30,.55));border-radius:11px;padding:5px 8px;color:#ecfeff;font-size:10.3px;line-height:1.12;font-weight:650}}
    .fvleft b{{display:block;color:#a7f3d0;font-size:9.2px;letter-spacing:.35px;margin-bottom:1px;font-weight:800}}.fvnote{{display:block;color:#93c5fd;font-size:8.7px;margin-top:1px}}
    .persona{{display:inline-block;margin-top:4px;border:1px solid rgba(255,215,82,.55);border-radius:13px;color:#fff6c8;background:rgba(18,49,37,.55);padding:2px 7px;font-size:10.6px;font-weight:800;white-space:nowrap}}
    .info{{margin-top:5px;border:1px solid rgba(85,200,255,.22);border-radius:10px;background:#071727;padding:5px 8px;font-weight:650;line-height:1.12;font-size:11.2px}}.ptime{{display:block;margin-top:1px;color:#a7f3d0;font-size:9.1px;font-weight:750}}.label{{color:#9bdcff;font-weight:800}}
    .entrylamp{{margin-top:5px;border-radius:11px;padding:6px 9px;border:1px solid;box-shadow:inset 0 0 22px rgba(255,255,255,.025)}}
    .entrylamp.green{{background:linear-gradient(90deg,rgba(0,90,55,.54),rgba(4,24,28,.92));border-color:#37f59a}}.entrylamp.yellow{{background:linear-gradient(90deg,rgba(104,75,0,.48),rgba(20,20,24,.94));border-color:#ffd35a}}.entrylamp.red{{background:linear-gradient(90deg,rgba(105,17,31,.54),rgba(25,10,16,.94));border-color:#ff5574}}
    .entrytop{{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;font-weight:950;color:#fff}}.entrytop .name{{font-size:12.5px}}.entrytop .score{{font-size:18px}}.entrytop .state{{font-size:11.5px;color:#fff5b8}}
    .entrysummary{{margin-top:1px;color:#eaf7ff;font-size:10.2px;font-weight:780;line-height:1.12}}
    .decision{{margin-top:5px;border:1px solid rgba(255,211,78,.48);border-radius:12px;background:linear-gradient(180deg,rgba(28,26,34,.96),rgba(13,13,20,.96));padding:5px 7px}}
    .analysis-row{{margin-top:5px;border:1px solid rgba(85,200,255,.34);border-radius:10px;background:#071727;overflow:hidden;display:grid;grid-template-columns:1.05fr 1.35fr 1.05fr 1.4fr}}
    .analysis-cell{{min-width:0;padding:6px 7px;border-right:1px solid rgba(85,200,255,.2);color:#e6f5ff;font-size:10px;line-height:1.27;overflow-wrap:anywhere}}
    .analysis-cell:last-child{{border-right:0}}.analysis-cell b{{display:block;color:#8fd7ff;font-size:9.5px;margin-bottom:3px;letter-spacing:.02em}}
    .analysis-cell .sub{{display:block;color:#bfe8ff;margin-top:2px}}.analysis-cell .confidence{{display:block;color:#ffe28a;margin-top:3px;font-weight:850}}
    .dt{{font-size:11px;font-weight:850;color:#fff;margin-bottom:3px}}
    .action-now{{border:1px solid rgba(95,244,255,.42);border-left:4px solid #5ff4ff;border-radius:8px;background:linear-gradient(90deg,rgba(0,78,102,.48),rgba(4,17,25,.88));color:#eaffff;font-size:11.5px;line-height:1.12;font-weight:950;padding:5px 8px;margin-bottom:3px}}
    .thesis{{border-left:4px solid #ffd35a;background:rgba(80,59,0,.22);border-radius:0 8px 8px 0;color:#fff7ce;font-size:10.7px;line-height:1.18;font-weight:850;padding:5px 8px;margin-bottom:3px}}
    .risk{{border-left:3px solid #ff6f8e;background:rgba(70,10,25,.22);border-radius:0 7px 7px 0;color:#ffe1e8;font-size:9.7px;line-height:1.15;font-weight:760;padding:4px 7px;margin-bottom:3px}}
    .price-command{{background:rgba(0,0,0,.24);border-radius:8px;color:#fff9c9;font-size:10.7px;line-height:1.08;font-weight:820;padding:4px 8px;margin-bottom:2px}}
    .reasoning-line{{padding:2px 6px;border-left:3px solid #74f4c3;background:rgba(3,31,33,.62);color:#d9fff1;font-size:8.7px;line-height:1.14;border-radius:0 6px 6px 0;margin-bottom:2px}}
    .reasoning-line b{{color:#74f4c3;margin-right:4px}}.reasoning-conflict{{display:block;color:#cfe7f7;margin-top:1px}}.reasoning-price{{display:block;color:#fff1a8;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.reasoning-price b{{color:#ffd96a}}
    .evidence-summary{{border-left:3px solid #ff6f8e;padding:3px 6px 3px 7px;color:#dff2ff;background:rgba(4,18,30,.72);font-size:9.1px;font-weight:700;line-height:1.16;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-radius:0 6px 6px 0}}
    .evidence-summary b{{color:#8fd7ff;margin-right:4px}}
    .evidence-details{{margin:2px 0 3px 3px;color:#bfe8ff;font-size:8.7px}}
    .evidence-details summary{{cursor:pointer;color:#8fd7ff;font-weight:850;list-style:none;user-select:none}}
    .evidence-details summary::-webkit-details-marker{{display:none}}
    .evidence-details summary::before{{content:'＋ ';color:#ffd96a}}.evidence-details[open] summary::before{{content:'－ '}}
    .evidence-full{{margin-top:3px;padding:6px 8px;border:1px solid rgba(85,170,255,.28);border-radius:8px;background:#06111d;color:#e9f6ff;line-height:1.35;font-size:9.2px;max-height:150px;overflow:auto}}
    .evidence-full b{{color:#9bdcff}}
    .pricebar{{margin-top:0;border:1px solid rgba(85,170,255,.28);background:#071727;border-radius:9px;display:grid;grid-template-columns:1.38fr 1.1fr .92fr .92fr 1fr;overflow:hidden;clear:both}}
    .priceitem{{min-width:0;padding:4px 6px;border-right:1px solid rgba(85,170,255,.18);font-size:9.7px;font-weight:760;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.priceitem:last-child{{border-right:0}}.priceitem b{{color:#9bdcff;margin-right:3px;font-size:9.2px}}
    .t1{{margin-top:5px;border-top:1px solid rgba(55,230,255,.18);padding-top:4px}}.tl{{font-size:10.8px;color:#9bdcff;font-weight:800}}.tm{{font-size:15.8px;line-height:1.0;color:#5ff4ff;font-weight:900}}.ts{{color:#d8f2ff;font-weight:650;font-size:10.3px}}
    @media(max-width:1020px) and (min-width:721px){{
      .panel{{padding:3px 7px 4px}}.head{{grid-template-columns:minmax(0,1fr) minmax(205px,285px);gap:6px;padding-bottom:4px}}
      h1{{font-size:18.2px}}.streak{{font-size:10px}}.fvleft{{padding:4px 7px;font-size:9.5px;line-height:1.08}}.fvleft b{{font-size:8.7px}}.fvnote{{font-size:8.1px}}
      .info{{margin-top:4px;padding:4px 7px;font-size:10.4px;line-height:1.08}}.ptime{{font-size:8.6px}}
      .entrylamp{{margin-top:4px;padding:5px 7px}}.entrytop{{gap:6px}}.entrytop .name{{font-size:11.5px}}.entrytop .score{{font-size:16.5px}}.entrytop .state{{font-size:10.5px}}.entrysummary{{font-size:9.4px}}
      .decision{{margin-top:4px;padding:4px 6px}}.dt{{font-size:9.9px;margin-bottom:2px}}.action-now{{font-size:10.3px;padding:4px 7px}}.price-command{{font-size:9.8px;padding:3px 7px;margin-bottom:2px;line-height:1.05}}
      .analysis-cell{{padding:5px;font-size:9.2px;line-height:1.2}}.analysis-cell b{{font-size:8.8px}}
      .reasoning-line{{font-size:8px;padding:2px 5px}}.evidence-summary{{font-size:8.3px;padding:2px 5px 2px 6px}}.evidence-details{{font-size:8px;margin-bottom:2px}}.evidence-full{{font-size:8.5px;max-height:130px}}
      .priceitem{{padding:3px 4px;font-size:8.7px}}.priceitem b{{font-size:8.2px;margin-right:2px}}
      .t1{{margin-top:4px;padding-top:3px}}.tl{{font-size:9.9px}}.tm{{font-size:14.6px}}.ts{{font-size:9.3px}}
    }}
    @media(max-width:720px){{
      .head{{grid-template-columns:1fr}}.analysis-row{{grid-template-columns:repeat(2,minmax(0,1fr))}}.analysis-cell:nth-child(2){{border-right:0}}.analysis-cell:nth-child(-n+2){{border-bottom:1px solid rgba(85,200,255,.2)}}.pricebar{{grid-template-columns:repeat(2,minmax(0,1fr))}}.priceitem{{border-bottom:1px solid rgba(85,170,255,.18)}}.priceitem:last-child{{grid-column:1 / -1}}.panel{{overflow:visible}}
    }}
    </style></head><body><div class='panel'>
      <div class='head'><div><h1>{safe(t.resolved_symbol)}｜{safe(t.name)}</h1><div class='streak'>{safe(header_trend)}</div>{persona_html}</div><div class='fvleft'><b>技術情境價格帶 / TECHNICAL RANGE</b>{fair}<span class='fvnote'>現價 ± ATR 技術情境｜不是基本面估值</span></div></div>
      <div class='info'><span class='label'>{safe(d.get('資料標題','資料狀態'))}</span><br>開盤：{fmt(d.get('開盤'))}｜現價：{fmt(d.get('現價'))}｜{safe(d.get('漲跌標籤','漲跌'))}：{fmt(d.get('漲跌'))} / {fmt(d.get('漲跌幅'))}%<br>{safe(d.get('價格範圍標籤','今日'))}高：{fmt(d.get('最高'))}｜{safe(d.get('價格範圍標籤','今日'))}低：{fmt(d.get('最低'))}｜{safe(d.get('VWAP位置', p.tags[1] if len(p.tags)>1 else ''))}<span class='ptime'>{safe(d.get('價格時間',''))}</span>{t0_line}{compare_line}</div>
      <div class='entrylamp {entry_color}'><div class='entrytop'><span class='name'>{entry_icon} AI交易決策</span>{entry_score_html}<span class='state'>{entry_label}</span></div><div class='entrysummary'>{entry_summary}</div></div>
      <div class='analysis-row' aria-label='單股智能分析'>
        <div class='analysis-cell'><b>產業／價格狀態</b>{analysis_industry}<span class='sub'>{analysis_price}</span><span class='sub'>{analysis_model_low}</span></div>
        <div class='analysis-cell'><b>條件式低接／分批方式</b>{analysis_entry}</div>
        <div class='analysis-cell'><b>失效條件／主要風險</b>{analysis_risk}</div>
        <div class='analysis-cell'><b>證據與聯動</b>{analysis_evidence}<span class='confidence'>模型信心：{analysis_confidence}</span></div>
      </div>
      <div class='decision'>
        <div class='dt'>AI執行計畫｜AI策略判斷｜信心 {intelligence_confidence}｜{'條件單' if decision_brief.get('candidate_mode') else decision_title}</div>
        <div class='thesis'>結論｜{intelligence_thesis}</div>
        <div class='action-now'>目前動作｜{current_action_text}</div>
        <div class='reasoning-price'><b>模型價位</b>{executive_price_line}</div>
        <div class='price-command'>進場與分批｜{staged_entry_line}</div>
        <div class='risk'>失效／主要風險｜{intelligence_risk}</div>
        <div class='evidence-summary' title='{safe(evidence_summary_raw)}'><b>決策依據</b>{evidence_summary}</div>
        <details class='evidence-details'><summary>展開完整 AI 證據</summary><div class='evidence-full'><b>推理仲裁：</b>{reasoning_conflict}<br><b>跨模組門檻：</b>{gate_detail}<br><b>進場狀態：</b>{entry_state_detail}<br><b>AI執行價格：</b>{entry_plan_text}<br><b>ABC情境：</b>{abc_detail}<br><b>Quantum：</b>{quantum_detail}<br><b>AI 證據：</b>{evidence}<br><b>市場：</b>{market}<br><b>{'Short' if t.market == 'US' else '籌碼'}：</b>{chip}</div></details>
        <div class='pricebar'>{price_tiles_html}</div>
      </div>
      <div class='t1'><div class='tl'>下一交易日參考預測</div><div class='tm'>下一交易日收盤預估：{fmt(p.final_t1)}</div><div class='ts'>下一交易日路徑上緣：{fmt(p.final_t1_high)}｜下一交易日風險低點：{fmt(p.final_t1_low)}</div></div>
    </div></body></html>
    """
    html_block(html, height=642, scrolling=False)
