# -*- coding: utf-8 -*-
from __future__ import annotations

from ui_html import fmt, html_block, safe


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
    status = str(getattr(p, 'price_market_status', '') or getattr(p, 'market_status', '') or '')
    # FinalForecast itself does not expose market_status in older builds; use decision text as backup.
    data_title = str(d.get('資料標題', ''))
    is_intraday = data_title.startswith('盤中')
    is_closed = data_title.startswith('收盤')
    t0_line = f"<br><span class='label'>今日收盤預估：</span>{fmt(p.final_t0)}" if is_intraday else ""
    compare_line = ""
    if is_closed:
        try:
            from learning import t1_prediction_vs_actual, today_prediction_vs_actual
            cmp = t1_prediction_vs_actual(p, d.get('現價'))
            text = _strip_compare_prefix(cmp.get('display', ''), '昨測今收：', '昨測今收預覽：')
            if not text or '尚無昨日' in text:
                alt_cmp = today_prediction_vs_actual(p, d.get('現價'))
                text = _strip_compare_prefix(alt_cmp.get('display', ''), '今日預測VS實際：', '今日預測VS實際預覽：')
                compare_line = f"<br><span class='label'>今日預測VS實際：</span>{safe(text)}"
            else:
                compare_line = f"<br><span class='label'>昨測今收：</span>{safe(text)}"
        except Exception as exc:
            compare_line = f"<br><span class='label'>昨測今收：</span>暫無可用比對（{safe(type(exc).__name__)}）"
    t1_title = "下一交易日參考預測"
    t1_prefix = "下一交易日"
    tags = "｜".join([safe(x) for x in p.tags[:4]])
    fair = safe(p.radar.get('Fair Value', ''))
    persona_badge = safe(p.radar.get('US Persona', '') or '')
    persona_html = f"<div class='persona'>{persona_badge}</div>" if persona_badge else ""
    header_trend = _header_trend_line(p)
    header_streak_positive = '+' in header_trend.split('│', 1)[0]
    html = f"""
    <!doctype html><html><head><meta charset='utf-8'>
    <style>
    *{{box-sizing:border-box}}body{{margin:0;background:transparent;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft JhengHei',Arial,sans-serif;color:#edf7ff}}
    .panel{{background:linear-gradient(180deg,#041321 0%,#02080d 100%);border-left:5px solid #37e6ff;min-height:552px;padding:4px 8px 5px;border-right:1px solid rgba(55,230,255,.16);overflow:hidden}}
    .head{{border-bottom:1px solid rgba(55,230,255,.22);padding-bottom:6px;display:grid;grid-template-columns:minmax(0,1fr) minmax(220px,318px);gap:8px;align-items:start}}
    h1{{margin:0;color:#fff;font-size:20px;font-weight:900;letter-spacing:.01em}}.streak{{margin-top:1px;color:{'#6dffb1' if header_streak_positive else '#ff6f8e'};font-weight:800;font-size:11.2px}}
    .fvleft{{border:1px solid rgba(45,212,191,.28);background:linear-gradient(135deg,rgba(6,78,59,.18),rgba(2,18,30,.55));border-radius:12px;padding:6px 8px;color:#ecfeff;font-size:10.5px;line-height:1.16;font-weight:650}}
    .fvleft b{{display:block;color:#a7f3d0;font-size:9.4px;letter-spacing:.35px;margin-bottom:2px;font-weight:800}}
    .fvnote{{display:block;color:#93c5fd;font-size:9px;margin-top:1px}}
    .persona{{display:inline-block;margin-top:5px;border:1px solid rgba(255,215,82,.55);border-radius:13px;color:#fff6c8;background:rgba(18,49,37,.55);padding:3px 8px;font-size:11px;font-weight:800;white-space:nowrap}}
    @media(max-width:1100px){{.head{{grid-template-columns:1fr}}}}
    .info{{margin-top:6px;border:1px solid rgba(85,200,255,.22);border-radius:11px;background:#071727;padding:6px 9px;font-weight:650;line-height:1.16;font-size:11.8px}}.ptime{{display:block;margin-top:2px;color:#a7f3d0;font-size:9.6px;font-weight:750}}.label{{color:#9bdcff;font-weight:800}}
    .decision{{margin-top:6px;border:1px solid rgba(255,211,78,.48);border-radius:13px;background:linear-gradient(180deg,rgba(28,26,34,.96),rgba(13,13,20,.96));padding:6px 8px}}
    .dt{{font-size:11.4px;font-weight:850;color:#fff;margin-bottom:5px}}.blue{{color:#8fd7ff}}.main{{background:rgba(0,0,0,.26);border-radius:9px;color:#fff9c9;font-size:12.4px;line-height:1.12;font-weight:850;padding:6px 9px;margin-bottom:6px}}
    .risk{{border-left:3px solid #ff6f8e;padding-left:8px;color:#dff2ff;font-size:10.4px;font-weight:650;line-height:1.12;margin-bottom:5px;max-height:54px;overflow:hidden}}
    .grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:5px}}.mini{{border:1px solid rgba(85,170,255,.31);background:#071727;border-radius:9px;padding:5px 7px;min-height:36px}}
    .mini b{{display:block;color:#9bdcff;font-size:10.8px;margin-bottom:2px;font-weight:800}}.mini span{{font-size:10.8px;font-weight:750;color:#fff;line-height:1.12}}
    .chips{{margin-top:5px;font-size:10.9px;color:#e6f5ff;font-weight:650;line-height:1.12;max-height:24px;overflow:hidden}}.bottom{{margin-top:6px;background:rgba(0,0,0,.25);border-radius:9px;padding:6px 9px;color:#fff5bc;font-weight:800;font-size:11.4px;line-height:1.12}}
    .t1{{margin-top:7px;border-top:1px solid rgba(55,230,255,.18);padding-top:5px}}.tl{{font-size:11.8px;color:#9bdcff;font-weight:800}}.tm{{font-size:16.6px;line-height:1.0;color:#5ff4ff;font-weight:900}}.ts{{color:#d8f2ff;font-weight:650;font-size:11px}}
    </style></head><body><div class='panel'>
      <div class='head'><div><h1>{safe(t.resolved_symbol)}｜{safe(t.name)}</h1><div class='streak'>{safe(header_trend)}</div>{persona_html}</div><div class='fvleft'><b>模型合理價值區間 / FAIR VALUE</b>{fair}<span class='fvnote'>技術錨 + V8.4校準 / 樣本少｜研究參考</span></div></div>
      <div class='info'><span class='label'>{safe(d.get('資料標題','資料狀態'))}</span><br>開盤：{fmt(d.get('開盤'))}｜現價：{fmt(d.get('現價'))}｜漲跌：{fmt(d.get('漲跌'))} / {fmt(d.get('漲跌幅'))}%<br>今日高：{fmt(d.get('最高'))}｜今日低：{fmt(d.get('最低'))}｜{safe(d.get('VWAP位置', p.tags[1] if len(p.tags)>1 else ''))}<span class='ptime'>{safe(d.get('價格時間',''))}</span>{t0_line}{compare_line}</div>
      <div class='decision'>
        <div class='dt'>{safe(d.get('標題'))}</div>
        <div class='main'>{safe(d.get('主訊息'))}</div>
        <div class='risk'><b class='blue'>市場：</b>{safe(p.radar.get('市場風控'))}<br><b class='blue'>{'Short' if t.market == 'US' else '籌碼'}：</b>{safe(p.radar.get('左側籌碼摘要'))}</div>
        <div class='grid'>
          <div class='mini'><b>低接計畫</b><span>{fmt(d.get('低接第一批'))} 第一批｜{fmt(d.get('低接第二批'))} 第二批</span></div>
          <div class='mini'><b>攻擊</b><span>{safe(d.get('攻擊'))}</span></div>
          <div class='mini'><b>轉強確認</b><span>{safe(d.get('轉強'))}</span></div>
          <div class='mini'><b>停手</b><span>{fmt(d.get('防守'))} 收不回停</span></div>
          <div class='mini'><b>不追</b><span>{fmt(d.get('不追'))} 上方急拉不追</span></div>
        </div>
        <div class='chips'>籌碼摘要：{safe(p.radar.get('左側籌碼摘要'))}</div>
        <div class='bottom'>一句話：{safe(d.get('一句話'))}</div>
      </div>
      <div class='t1'><div class='tl'>{t1_title}</div><div class='tm'>{t1_prefix}收盤預估：{fmt(p.final_t1)}</div><div class='ts'>{t1_prefix}路徑上緣：{fmt(p.final_t1_high)}｜{t1_prefix}風險低點：{fmt(p.final_t1_low)}</div></div>
    </div></body></html>
    """
    html_block(html, height=580, scrolling=False)
