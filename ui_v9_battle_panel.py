# -*- coding: utf-8 -*-
from __future__ import annotations

from ui_html import fmt, html_block, safe

try:
    from low_entry_readiness_v1065 import assess_low_entry_readiness
except Exception:
    def assess_low_entry_readiness(_forecast):
        return {
            "score": 0,
            "label": "資料待確認",
            "color": "yellow",
            "icon": "🟡",
            "summary": "低接成熟度模組暫時無法載入",
            "conditions": [],
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
        return f"{label}資料不足"
    try:
        g = float(gap)
    except Exception:
        return f"{label}資料不足"
    # gap 是「現價相對均線的距離」，不是均線本身的漲跌方向。
    return f"{label} {_title_price(value)}｜距離 {_title_pct(g)}"


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
    for m in ("盤中參考", "盤前參考", "盤後參考", "休市參考"):
        if m in streak_raw:
            mode = m
            streak_raw = streak_raw.replace(f"｜{m}", "").replace(m, "")
            break
    snap = {}
    try:
        snap = ((forecast.decision_card or {}).get("_trend_snapshot") or {})
    except Exception:
        snap = {}
    ma20 = _ma_title_piece("MA20", snap.get("ma20"), snap.get("ma20_gap_pct"))
    ma60 = _ma_title_piece("MA60", snap.get("ma60"), snap.get("ma60_gap_pct"))
    parts = [streak_raw.strip("｜ ") or "盤勢觀察", ma20, ma60]
    if mode:
        parts.append(mode)
    return " │ ".join(parts)


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

    readiness = assess_low_entry_readiness(p)
    readiness_color = str(readiness.get("color") or "yellow")
    readiness_icon = safe(readiness.get("icon") or "🟡")
    readiness_score = int(readiness.get("score") or 0)
    readiness_label = safe(readiness.get("label") or "再等等")
    readiness_summary = safe(readiness.get("summary") or "等待價格與籌碼確認")
    main_message = safe(readiness.get("canonical_main_message") or d.get("主訊息"))
    readiness_items = []
    consistency = dict(readiness.get("price_consistency") or {})
    if consistency.get("consistent"):
        readiness_items.append("<span class='ok'>✓ 操作價格已同步</span>")
    # 價格等待路徑已在 summary 說明；下方只保留兩個最重要的條件，避免卡片肥大。
    for row in list(readiness.get("conditions") or [])[:2]:
        ok = bool(row.get("ok"))
        cls = "ok" if ok else "wait"
        symbol = "✓" if ok else "✕"
        readiness_items.append(f"<span class='{cls}'>{symbol} {safe(row.get('text'))}</span>")
    readiness_detail = "".join(readiness_items)

    decision_title_raw = _strip_compare_prefix(
        d.get("標題", "AI決策"), "AI進場決策卡｜", "AI進場決策卡 |"
    )
    decision_title = safe(decision_title_raw or "AI決策")
    evidence_raw = str(d.get("證據鏈", "") or "")
    market_raw = str(p.radar.get("市場風控", "") or "")
    chip_raw = str(p.radar.get("左側籌碼摘要", "") or "")
    evidence = safe(evidence_raw)
    market = safe(market_raw)
    chip = safe(chip_raw)
    evidence_tooltip = safe(f"AI證據：{evidence_raw}｜市場：{market_raw}｜籌碼：{chip_raw}")

    html = f"""
    <!doctype html><html><head><meta charset='utf-8'>
    <style>
    *{{box-sizing:border-box}}body{{margin:0;background:transparent;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft JhengHei',Arial,sans-serif;color:#edf7ff}}
    .panel{{background:linear-gradient(180deg,#041321 0%,#02080d 100%);border-left:5px solid #37e6ff;min-height:612px;padding:4px 8px 5px;border-right:1px solid rgba(55,230,255,.16);overflow:hidden}}
    .head{{border-bottom:1px solid rgba(55,230,255,.22);padding-bottom:5px;display:grid;grid-template-columns:minmax(0,1fr) minmax(220px,318px);gap:8px;align-items:start}}
    h1{{margin:0;color:#fff;font-size:20px;font-weight:900;letter-spacing:.01em}}.streak{{margin-top:1px;color:{'#6dffb1' if header_streak_positive else '#ff6f8e'};font-weight:800;font-size:11px}}
    .fvleft{{border:1px solid rgba(45,212,191,.28);background:linear-gradient(135deg,rgba(6,78,59,.18),rgba(2,18,30,.55));border-radius:11px;padding:5px 8px;color:#ecfeff;font-size:10.3px;line-height:1.12;font-weight:650}}
    .fvleft b{{display:block;color:#a7f3d0;font-size:9.2px;letter-spacing:.35px;margin-bottom:1px;font-weight:800}}.fvnote{{display:block;color:#93c5fd;font-size:8.7px;margin-top:1px}}
    .persona{{display:inline-block;margin-top:4px;border:1px solid rgba(255,215,82,.55);border-radius:13px;color:#fff6c8;background:rgba(18,49,37,.55);padding:2px 7px;font-size:10.6px;font-weight:800;white-space:nowrap}}
    .info{{margin-top:5px;border:1px solid rgba(85,200,255,.22);border-radius:10px;background:#071727;padding:5px 8px;font-weight:650;line-height:1.12;font-size:11.2px}}.ptime{{display:block;margin-top:1px;color:#a7f3d0;font-size:9.1px;font-weight:750}}.label{{color:#9bdcff;font-weight:800}}
    .entrylamp{{margin-top:5px;border-radius:11px;padding:6px 9px;border:1px solid;box-shadow:inset 0 0 22px rgba(255,255,255,.025)}}
    .entrylamp.green{{background:linear-gradient(90deg,rgba(0,90,55,.54),rgba(4,24,28,.92));border-color:#37f59a}}.entrylamp.yellow{{background:linear-gradient(90deg,rgba(104,75,0,.48),rgba(20,20,24,.94));border-color:#ffd35a}}.entrylamp.red{{background:linear-gradient(90deg,rgba(105,17,31,.54),rgba(25,10,16,.94));border-color:#ff5574}}
    .entrytop{{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;font-weight:950;color:#fff}}.entrytop .name{{font-size:12.5px}}.entrytop .score{{font-size:18px}}.entrytop .state{{font-size:11.5px;color:#fff5b8}}
    .entrysummary{{margin-top:1px;color:#eaf7ff;font-size:10.2px;font-weight:780;line-height:1.12}}.entryfacts{{margin-top:2px;display:flex;gap:4px 9px;flex-wrap:wrap;font-size:9px;font-weight:750}}.entryfacts .ok{{color:#7dffbd}}.entryfacts .wait{{color:#ffd27a}}
    .decision{{margin-top:5px;border:1px solid rgba(255,211,78,.48);border-radius:12px;background:linear-gradient(180deg,rgba(28,26,34,.96),rgba(13,13,20,.96));padding:5px 7px}}
    .dt{{font-size:11px;font-weight:850;color:#fff;margin-bottom:3px}}.main{{background:rgba(0,0,0,.24);border-radius:8px;color:#fff9c9;font-size:11.6px;line-height:1.10;font-weight:850;padding:5px 8px;margin-bottom:4px}}
    /* V1067: reserve two complete text lines and a separate gap before the price strip.
       WebKit line-clamp clipped the second line descenders and visually collided with
       the price bar at 100% zoom.  A fixed two-line viewport keeps both TW and US text
       readable while the title attribute still exposes the complete evidence chain. */
    .decision-evidence{{border-left:3px solid #ff6f8e;padding:3px 0 3px 7px;color:#dff2ff;font-size:9.1px;font-weight:650;line-height:1.22;display:block;height:31px;overflow:hidden;cursor:help;margin-bottom:4px}}
    .decision-evidence b{{color:#8fd7ff}}.sep{{color:#6d8ca5;padding:0 3px}}
    .pricebar{{margin-top:0;border:1px solid rgba(85,170,255,.28);background:#071727;border-radius:9px;display:grid;grid-template-columns:1.35fr 1.15fr 1.15fr .95fr .95fr;overflow:hidden;clear:both}}
    .priceitem{{min-width:0;padding:4px 6px;border-right:1px solid rgba(85,170,255,.18);font-size:9.7px;font-weight:760;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.priceitem:last-child{{border-right:0}}.priceitem b{{color:#9bdcff;margin-right:3px;font-size:9.2px}}
    .t1{{margin-top:5px;border-top:1px solid rgba(55,230,255,.18);padding-top:4px}}.tl{{font-size:10.8px;color:#9bdcff;font-weight:800}}.tm{{font-size:15.8px;line-height:1.0;color:#5ff4ff;font-weight:900}}.ts{{color:#d8f2ff;font-weight:650;font-size:10.3px}}
    @media(max-width:1020px) and (min-width:721px){{
      .panel{{padding:3px 7px 4px}}.head{{grid-template-columns:minmax(0,1fr) minmax(205px,285px);gap:6px;padding-bottom:4px}}
      h1{{font-size:18.2px}}.streak{{font-size:10px}}.fvleft{{padding:4px 7px;font-size:9.5px;line-height:1.08}}.fvleft b{{font-size:8.7px}}.fvnote{{font-size:8.1px}}
      .info{{margin-top:4px;padding:4px 7px;font-size:10.4px;line-height:1.08}}.ptime{{font-size:8.6px}}
      .entrylamp{{margin-top:4px;padding:5px 7px}}.entrytop{{gap:6px}}.entrytop .name{{font-size:11.5px}}.entrytop .score{{font-size:16.5px}}.entrytop .state{{font-size:10.5px}}.entrysummary{{font-size:9.4px}}.entryfacts{{font-size:8.3px;gap:2px 7px}}
      .decision{{margin-top:4px;padding:4px 6px}}.dt{{font-size:9.9px;margin-bottom:2px}}.main{{font-size:10.4px;padding:4px 7px;margin-bottom:3px;line-height:1.06}}.decision-evidence{{font-size:8.3px;line-height:1.18;height:27px;padding:2px 0 2px 6px;margin-bottom:4px}}
      .priceitem{{padding:3px 4px;font-size:8.7px}}.priceitem b{{font-size:8.2px;margin-right:2px}}
      .t1{{margin-top:4px;padding-top:3px}}.tl{{font-size:9.9px}}.tm{{font-size:14.6px}}.ts{{font-size:9.3px}}
    }}
    @media(max-width:720px){{
      .head{{grid-template-columns:1fr}}.pricebar{{grid-template-columns:repeat(2,minmax(0,1fr))}}.priceitem{{border-bottom:1px solid rgba(85,170,255,.18)}}.priceitem:last-child{{grid-column:1 / -1}}.panel{{overflow:visible}}
    }}
    </style></head><body><div class='panel'>
      <div class='head'><div><h1>{safe(t.resolved_symbol)}｜{safe(t.name)}</h1><div class='streak'>{safe(header_trend)}</div>{persona_html}</div><div class='fvleft'><b>模型合理價值區間 / FAIR VALUE</b>{fair}<span class='fvnote'>技術錨 + V8.4校準 / 樣本少｜研究參考</span></div></div>
      <div class='info'><span class='label'>{safe(d.get('資料標題','資料狀態'))}</span><br>開盤：{fmt(d.get('開盤'))}｜現價：{fmt(d.get('現價'))}｜漲跌：{fmt(d.get('漲跌'))} / {fmt(d.get('漲跌幅'))}%<br>今日高：{fmt(d.get('最高'))}｜今日低：{fmt(d.get('最低'))}｜{safe(d.get('VWAP位置', p.tags[1] if len(p.tags)>1 else ''))}<span class='ptime'>{safe(d.get('價格時間',''))}</span>{t0_line}{compare_line}</div>
      <div class='entrylamp {readiness_color}'><div class='entrytop'><span class='name'>{readiness_icon} AI低接成熟度</span><span class='score'>{readiness_score}%</span><span class='state'>{readiness_label}</span></div><div class='entrysummary'>{readiness_summary}</div><div class='entryfacts'>{readiness_detail}</div></div>
      <div class='decision'>
        <div class='dt'>AI決策｜{decision_title}</div>
        <div class='main'>{main_message}</div>
        <div class='decision-evidence' title='{evidence_tooltip}'><b>證據</b> {evidence}<span class='sep'>｜</span><b>市場</b> {market}<span class='sep'>｜</span><b>{'Short' if t.market == 'US' else '籌碼'}</b> {chip}</div>
        <div class='pricebar'>
          <div class='priceitem' title='第一批與第二批低接價'><b>低接</b>{fmt(d.get('低接第一批'))}／{fmt(d.get('低接第二批'))}</div>
          <div class='priceitem' title='{safe(d.get('攻擊'))}'><b>攻擊</b>{safe(d.get('攻擊'))}</div>
          <div class='priceitem' title='{safe(d.get('轉強'))}'><b>轉強</b>{safe(d.get('轉強'))}</div>
          <div class='priceitem' title='防守價'><b>停手</b>{fmt(d.get('防守'))}</div>
          <div class='priceitem' title='不追價'><b>不追</b>{fmt(d.get('不追'))}</div>
        </div>
      </div>
      <div class='t1'><div class='tl'>下一交易日參考預測</div><div class='tm'>下一交易日收盤預估：{fmt(p.final_t1)}</div><div class='ts'>下一交易日路徑上緣：{fmt(p.final_t1_high)}｜下一交易日風險低點：{fmt(p.final_t1_low)}</div></div>
    </div></body></html>
    """
    html_block(html, height=642, scrolling=False)
