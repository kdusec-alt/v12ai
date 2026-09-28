# TINO V156 Market Scanner（獨立排程）

這個資料夾只放 V156 掃描器，不掛入 V1116 網頁請求流程。Colab 仍可手動執行；GitHub Actions 以同一支掃描核心執行排程，Drive 上傳採版本化 Snapshot，全部檔案上傳成功後才更新 `TINO_V156_LATEST.json`。

## 啟用前設定

1. 在 Google Cloud 建立專用 Service Account，啟用 Google Drive API，並把要存放結果的 Drive 資料夾分享給該帳號 email（編輯者）。
2. 在 repository 的 **Settings → Secrets and variables → Actions** 建立兩個 Repository secrets：
   - `GOOGLE_SERVICE_ACCOUNT_JSON`：Service Account JSON 金鑰完整內容。
   - `GOOGLE_DRIVE_FOLDER_ID`：共享目標資料夾的 ID。
3. 將此變更合併到 repository 預設分支，然後到 **Actions** 啟用 `TINO V156 Daily Market Scan`。排程工作只會從預設分支上的 workflow 執行。
4. 第一次可用 **Run workflow** 手動測試。未設定 secrets 時，workflow 會在掃描前停止。

**金鑰安全：**不要把 Service Account JSON 放在程式碼、Notebook、Issues 或 Pull Request。建議使用專用帳號，且只分享輸出資料夾。

## 執行排程

GitHub Actions 排程為 `21:00 UTC`（台北時間平日 05:00），GitHub 可能延後排程啟動。排程使用 GitHub-hosted Ubuntu Runner，掃描失敗或資料覆蓋率不足時不會更新 Drive 最新快照；前一個成功快照保留。

手動測試可在 Colab 執行 `TINO_V156_Colab_Manual.ipynb`。此程式保留 3 年歷史，未安裝 FinMind 時只會略過台指夜盤輔助資料。

## 本次輸出

每次成功掃描建立獨立的 `TINO_V156_YYYYMMDD_HHMMSS` Drive 資料夾，放入候選 CSV、診斷 CSV、Metadata JSON 和 run manifest；最後更新父資料夾中的 `TINO_V156_LATEST.json`。若空 Universe、掃描候選空白或下載失敗率超標，流程失敗並保留既有 Latest pointer。
