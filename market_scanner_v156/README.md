# TINO V156 Market Scanner（獨立排程）

V156 掃描器在獨立 GitHub Actions 執行，不掛入 V1116 個股分析流程。完整成功快照上傳 Google Drive；V1116 的「🌌 AI Market Scanner」入口只讀 Latest manifest 與結果 CSV，5 分鐘快取，不會因開頁而重跑全市場掃描。Colab 仍保留手動掃描。

## 啟用前設定

1. 在 Google Cloud 建立 Service Account 並啟用 Google Drive API。此專案的輸出目標使用現有 `TINO_V156/snapshots` 資料夾（Folder ID：`1U_QKNRHVhClaO-1cGdC24sINnmeia3es`）；將這個資料夾分享給 Service Account 的 `client_email`，權限設為編輯者。手動結果仍留在 `snapshots/manual`。
2. 在 repository 的 **Settings → Secrets and variables → Actions** 建立兩個 Repository secrets：
   - `GOOGLE_SERVICE_ACCOUNT_JSON`：Service Account JSON 金鑰完整內容。
   - `GOOGLE_DRIVE_FOLDER_ID`：共享目標資料夾的 ID。
3. 在 V1116 網站的 Streamlit 部署平台 **Settings → Secrets** 加入同名兩項，網站 Scanner 頁用唯讀 Drive 權限讀快照。
4. 將此 PR 合併到預設分支後，到 **Actions** 啟用 `TINO V156 Daily Market Scan`；排程只會從預設分支上的 workflow 執行。第一次用 **Run workflow** 手動測試。未設定 Actions secrets 時，workflow 會在掃描前停止。

**金鑰安全：**不要把 Service Account JSON 放在程式碼、Notebook、Issues 或 Pull Request。建議使用專用帳號，且只分享輸出資料夾。

## 執行排程

GitHub Actions 排程為 `21:00 UTC`（台北時間平日 05:00），GitHub 可能延後排程啟動。排程使用 GitHub-hosted Ubuntu Runner，掃描失敗或資料覆蓋率不足時不會更新 Drive 最新快照；前一個成功快照保留。

網站讀取器位於 `market_scanner_v156/market_scanner_drive_reader.py`，只在管理員選擇 Scanner 入口時讀取 Drive，5 分鐘快取。讀取失敗會隔離顯示，不影響個股分析、即時股價、預測學習或 AI Research Lab。手動測試可在 Colab 執行 `TINO_V156_Colab_Manual.ipynb`。此程式保留 3 年歷史，未安裝 FinMind 時只會略過台指夜盤輔助資料。

## 本次輸出

每次成功掃描建立獨立的 `TINO_V156_YYYYMMDD_HHMMSS` Drive 資料夾，放入候選 CSV、診斷 CSV、Metadata JSON 和 run manifest；最後更新父資料夾中的 `TINO_V156_LATEST.json`。若空 Universe、掃描候選空白或下載失敗率超標，流程失敗並保留既有 Latest pointer。
