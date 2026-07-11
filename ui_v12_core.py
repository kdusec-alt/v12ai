# -*- coding: utf-8 -*-
from __future__ import annotations

from ui_html import html_block, safe


def render_v12_core(st, forecast) -> None:
    core = forecast.decision_card.get("v12_core", {}) if forecast.decision_card else {}
    html = f"""
    <!doctype html><html><head><meta charset='utf-8'>
    <style>
    *{{box-sizing:border-box}}body{{margin:0;background:transparent;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft JhengHei',Arial,sans-serif;color:#eaf6ff}}
    .dock{{margin:4px 0 10px;border:1px solid rgba(54,230,255,.25);border-radius:12px;background:linear-gradient(90deg,rgba(4,18,31,.95),rgba(8,17,30,.94));padding:8px 10px}}
    .title{{font-size:15px;font-weight:1000;color:#36e6ff;margin-bottom:6px;letter-spacing:.03em}}
    .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}}
    .card{{border:1px solid rgba(139,216,255,.22);border-radius:9px;background:#071727;padding:7px 8px;min-height:58px}}
    .k{{color:#9bdcff;font-weight:1000;font-size:12px;margin-bottom:3px}}.v{{color:#f6fbff;font-weight:850;font-size:11.8px;line-height:1.25}}
    .warn{{color:#ffe698}}
    @media(max-width:980px){{.grid{{grid-template-columns:1fr 1fr}}}}
    </style></head><body>
    <div class='dock'>
      <div class='title'>🧠 V12 AI Core｜看得到 Trace、Truth Guard、Learning Audit、Model Health</div>
      <div class='grid'>
        <div class='card'><div class='k'>Prediction Trace</div><div class='v'>{safe(core.get('trace_summary','尚未產生'))}</div></div>
        <div class='card'><div class='k'>Truth Guard</div><div class='v'>{safe(core.get('truth_summary','尚未產生'))}</div></div>
        <div class='card'><div class='k'>Learning Audit</div><div class='v warn'>{safe(core.get('learning_summary','等待 Audit'))}</div></div>
        <div class='card'><div class='k'>Model Health</div><div class='v'>{safe(core.get('model_health','尚未產生'))}</div></div>
      </div>
    </div></body></html>
    """
    html_block(html, height=112, scrolling=False)
