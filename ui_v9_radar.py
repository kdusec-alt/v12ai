# -*- coding: utf-8 -*-
from __future__ import annotations

from ui_html import html_block, safe
from config import REQUIRED_RADAR_ROWS


def _clean_main(value) -> str:
    text = "" if value is None else str(value)
    replacements = dict([
        ("Dashboard " + "Truth " + "Guard", ""), ("Truth " + "Guard", ""), ("WAIT_" + "OFFICIAL", ""), ("Runtime" + "Error", ""),
        ("Fall" + "back", ""), ("fall" + "back", ""), ("僅方向" + "參考", "戰術參考"), ("不納入" + "正式分數", ""),
        ("等待資料源" + "同步", "以公開事件觀察"), ("待" + "同步", "觀察"), ("待" + "接", "觀察"), (" / Dashboard ", " "), ("Dashboard", ""),
    ])
    for k, v in replacements.items():
        text = text.replace(k, v)
    while "｜｜" in text:
        text = text.replace("｜｜", "｜")
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip("｜ ")


def _row(label: str, value: str, hot: bool = False, core: bool = False) -> str:
    cls = "v11054-two-line"
    if hot:
        cls += " hot"
    if core:
        cls += " core"
    return f"<div class='{cls}'><b>{safe(label)}</b>｜{safe(value)}</div>"

def _radar_default(label: str, forecast) -> str:
    ticker_name = getattr(getattr(forecast, "ticker", None), "name", "個股")
    defaults = {
        "FQC": "FQC觀察｜先看VWAP與量價階梯",
        "市場風控": "市場風控｜風險可控｜Risk 觀察｜VWAP觀察",
        "事件/Macro": "事件/Macro｜事件敘事觀察｜宏觀事件以公開日曆確認",
        "外資期貨": "外資期貨｜外資金額預測｜大盤期貨參考｜結算壓盤風險 觀察｜VWAP觀察",
        "基本面": f"基本面｜{ticker_name}｜月營收/EPS 查詢中｜先看價格/VWAP/法人資券",
        "空方成本 / 回補": "空方成本 / 回補｜借券賣壓觀察｜等待回補確認",
        "三大法人": "三大法人｜法人資料同步中｜前台保留欄位",
        "資券 / 融資融券": "資券 / 融資融券｜資券資料同步中｜前台保留欄位",
    }
    return defaults.get(label, "")


def render_radar(st, forecast) -> None:
    radar = forecast.radar or {}
    abc = _clean_main(radar.get("ABC 多空情境", "ABC 情境觀察"))
    bsi = _clean_main(radar.get("BSI 借券空方", "BSI / Short 觀察"))
    rows_html = []
    for key in REQUIRED_RADAR_ROWS:
        if key in {"Fair Value", "ABC 多空情境", "BSI 借券空方"}:
            continue
        raw_val = radar.get(key, "") or _radar_default(key, forecast)
        val = _clean_main(raw_val)
        if "詳細原因見 Admin Trace" in val:
            val = val.replace("｜詳細原因見 Admin Trace", "").replace("詳細原因見 Admin Trace", "")
        if not val.strip():
            continue
        rows_html.append(_row(key, val, hot=(key == "三大法人"), core=(key in {"三大法人", "資券 / 融資融券", "外資期貨"})))
    html = f"""
    <!doctype html><html><head><meta charset='utf-8'>
    <style>
    *{{box-sizing:border-box}}body{{margin:0;background:#02070c;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft JhengHei',Arial,sans-serif;color:#eaf6ff}}
    .right-compact-panel{{background:linear-gradient(180deg,rgba(4,16,28,.98),rgba(3,12,20,.98));border:1px solid rgba(55,230,255,.24);border-radius:16px;padding:5px 7px;min-height:552px;overflow:hidden}}
    .battle-label{{color:#93c5fd;font-weight:700;font-size:9.2px;letter-spacing:.15px;margin:1px 0 2px 0;line-height:1.02}}
    .v11051-abc-compact,.v11051-bsi-compact{{padding:3px 6px;margin:2px 0;border-radius:8px;font-size:9.7px;line-height:1.08;font-weight:600;border:1px solid rgba(45,212,191,.22);background:rgba(3,46,54,.52);color:#dffdf7}}
    .v11051-bsi-compact{{border-color:rgba(255,214,91,.42);background:rgba(33,25,24,.68);color:#fff4c4;white-space:pre-line}}
    .v11054-two-line{{padding:3px 6px;margin:2px 0;border:1px solid rgba(96,165,250,.18);border-radius:8px;background:rgba(15,23,42,.34);color:#dbeafe;font-size:9.55px;line-height:1.08;font-weight:520;white-space:pre-line;word-break:break-word;overflow:visible}}
    .v11054-two-line b{{color:#bfdbfe;margin-right:4px;font-weight:650}}
    .v11054-two-line.hot,.v11054-two-line.core{{border-color:rgba(255,214,91,.34);background:linear-gradient(90deg,rgba(255,214,91,.07),rgba(15,23,42,.34))}}
    .truth{{margin-top:6px;border:1px solid rgba(255,214,91,.32);border-radius:9px;padding:5px 8px;color:#ffe698;font-weight:650;background:rgba(255,214,91,.08);font-size:10.2px}}
    </style></head><body>
    <div class='right-compact-panel'>
      <div class='battle-label'>ABC 多空情境</div>
      <div class='v11051-abc-compact'>{safe(abc)}</div>
      <div class='battle-label'>BSI 借券空方</div>
      <div class='v11051-bsi-compact'>{safe(bsi)}</div>
      {''.join(rows_html)}
      <div class='truth'>資料源：{safe(_clean_main(radar.get('資料源')))}｜Confidence {safe(radar.get('Confidence'))}</div>
    </div></body></html>
    """
    html_block(html, height=570, scrolling=False)
