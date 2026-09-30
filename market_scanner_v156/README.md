# TINO V157 Market Discovery Scanner

Colab 是唯一的全市場掃描執行端；掃描成功後寫入已掛載 Google Drive 的 `TINO_V156/snapshots/manual/`。V1116 的 `AI Market Scanner` 只讀取 Drive 快照。開啟或重新整理網站頁面不會觸發 1900 檔掃描。GitHub Actions 全市場掃描不在此操作流程中。

## V157 變更

- 在同一批 OHLC 下載上計算族群 1/3/5 日報酬、上漲廣度、量能加速，避免再逐檔重抓行情。
- 全市場先做輕量流動性／趨勢／5日動能 Fast Filter，再取最高 300 檔進 KNN 與 Backtest；條件不足時不補滿 300。
- 對族群作相對強度計算時排除個股自身；欄位含 RS vs Market、RS vs Sector 與族群內龍頭排名。
- 法人資料透過 FinMind 單次批次查詢，不按 1900 檔各自發請求；來源失效時顯示 `UNAVAILABLE`。
- 卡片與 CSV 顯示 Stock / Sector / Leader / Flow / Entry 五個可解釋分項及 coverage。
- V157 總分先以 `SHADOW_UNCALIBRATED` 輸出，不改既有推薦排序與 V1116 預測模型；完成 walk-forward 校準後再決定是否啟用。
- 每次成功執行比較前次成功快照的龍頭名次，並輸出前次推薦的 T+1 日K驗證 CSV；目標與停損同日觸及會標成歧義。
- `run_manifest.json` 最後以原子替換更新；掃描未成功時保留舊 manifest 與 Last Known Good 結果。
- V1116 卡牌新增候選 Top10 行情按鈕，只查詢最多 10 檔官方 MIS 行情，不改快照、排名或 Entry/T1/T2/Stop。

## Colab 手動流程

1. 安裝 Notebook 套件並掛載 Google Drive。
2. 將新版 `tino_v156_scanner.py` 放到 `MyDrive/TINO_V156/`。
3. 執行掃描儲存格；輸出寫到 `MyDrive/TINO_V156/snapshots/manual/`。
4. V1116 的 Drive 根資料夾設定需指向 `TINO_V156`，讀取器從其 `manual/` 子資料夾載入最新成功 `run_manifest.json`。

Notebook 的掃描仍需手動執行；Colab 筆記本執行不會讓閒置 Runtime 自動常駐。此版本不啟用 GitHub Actions 全市場掃描。

## 3 年歷史保留

維持 `period="3y"`、KNN lookback 360 與既有 Backtest。改成 1 年大約只有 250 個交易日，會少於目前 360 日 KNN 視窗並壓縮不同市況樣本；MA60/ATR14 可以計算，但 KNN shrinkage 與策略回測穩健度會先受損。速度優化放在「先以共享日線資料建立橫截面指標、失敗快照不發布」，不縮短重模型歷史。

## 重要欄位

`sector`, `leader_rank`, `leader_rotation`, `sector_1d_pct`, `sector_3d_pct`, `sector_5d_pct`, `sector_breadth_pct`, `rs_market_5d_pct`, `rs_sector_5d_pct`, `inst_net_3d/5d/10d`, `inst_flow_status`, `stock_edge_score`, `sector_edge_score`, `leader_edge_score`, `institutional_flow_edge_score`, `entry_edge_score`, `v157_total_score`, `v157_score_coverage_pct`。
