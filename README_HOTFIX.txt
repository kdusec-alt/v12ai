TINO V12-P1.1 Right Radar Restore + VWAP SSOT Patch

Replace only these files:
- data_sources_tw.py
- orchestrator.py
- ui_v9_radar.py

Scope:
1) Add price_snapshot SSOT into TW price context:
   last/open/high/low/previous_close/volume/vwap/time/source/mode/vwap_state.
2) VWAP state is calculated from numeric SSOT fields only:
   VWAP 上方 if price_snapshot.last >= price_snapshot.vwap else VWAP 下方.
3) Restore right radar front-stage lines, especially:
   外資期貨 + 外資金額預測 + 今日預估大盤外資買賣壓 + 匯率/大盤同步模型 + 大盤期貨參考 + 結算壓盤風險 + VWAP.
4) Fundamental fallback stays in front UI as readable text:
   基本面｜個股名｜月營收/EPS 查詢中｜先看價格/VWAP/法人資券
   Admin Trace remains engineering-only and no longer replaces front UI.
5) Right radar keeps core rows visible instead of disappearing when one item is empty.

No changes to Google Sheet, Auto Learning, MIS selector, or main V9 layout.
