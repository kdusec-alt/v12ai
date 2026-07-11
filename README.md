# TINO V12.2｜Quantum Entanglement Direction Engine

本版直接以 `V12-main0711(1).zip` 為基礎修改，完整保留 V9 前台架構與三層資訊流，新增可追蹤的方向引擎與右側證據仲裁層。

## 核心改動

- 價格路徑與 UP / NEUTRAL / DOWN 方向分離。
- 台股右側法人、資券、BSI、全市場融資、外資期貨、月營收、TV 外資匯率壓力正式進入證據仲裁。
- 台指夜盤採 TAIFEX 正式來源；費半、SMH、NQ、QQQ、MU、TSM ADR 按產業關聯彈性加權。
- SOX/SMH、NQ/QQQ 合併成代理家族，避免同一市場訊號重複計分。
- 月營收、財報、指引只影響公告交易日及下一交易日，之後歸零為短線催化。
- 融資連增依趨勢、盤中與法人結構判讀；弱勢時放大風險，強勢時只降低追價積極度。
- 地緣政治依事件年齡、產業敏感度與海外盤勢確認；記憶體/半導體對費半、MU、夜盤的傳導較高。
- 右側證據可直接改寫左側戰術：風險共振、第二批暫停、等待海外止穩、事件催化急拉不追。
- Direction Audit 分開記錄方向命中率、Brier Score 與價格誤差。

## 執行

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 離線驗證

```bash
TINO_OFFLINE_TEST=1 python smoke_tests.py
TINO_OFFLINE_TEST=1 python smoke_rc23_final.py
TINO_OFFLINE_TEST=1 python verify_rc24_1_stability.py
TINO_OFFLINE_TEST=1 python verify_rc24_2_post_render.py
TINO_OFFLINE_TEST=1 python test_direction_precision.py
TINO_OFFLINE_TEST=1 python test_quantum_entanglement.py
```

實際方向命中率仍必須使用固定快照與正式 T+1 Audit 累積驗證，不能由結構測試直接宣稱提升幅度。
