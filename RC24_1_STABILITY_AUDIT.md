# TINO V12 RC24.1 Stable Observation｜完整檔案閃退檢查

## 結論

本次完整掃描後，`Auto Audit` 與 `Market Heat` 在上傳版本中已是 no-op / disabled，並不是目前最主要的執行路徑風險。

仍留在 Streamlit 主執行緒中的高風險點有三個：

1. **每次 app boot / rerun 都執行完整 Memory 初始化**
   - `app.py` 原本每次 rerun 都呼叫 `ensure_memory_initialized(migrate=True)`。
   - 若 GitHub Remote Memory 已設定，單次一般 rerun 可能同步執行約 14 次 GitHub GET/PUT；首次建立檔案時還可能更多。
   - 每個 GitHub request timeout 最高 12 秒，會讓 Streamlit worker 長時間卡在 render path。

2. **預測 / Audit / Ledger 寫入時，直接在前台同步 GitHub**
   - `append_jsonl()` 寫完 Prediction/Audit 後，原本會立即 mirror ledger，再同步 prediction/audit 檔到 GitHub。
   - 這使「按一次分析」在預測完成後仍可能卡在多個遠端 GET/PUT，不符合主畫面只做輕量工作的原則。

3. **Watch Center 30 秒背景 fragment 預設開啟**
   - Watchlist 最多 60 檔，報價是逐檔外部查詢。
   - 前一輪若超過 30 秒，下一輪可能重疊，增加 worker thread / network / memory 壓力。

> Streamlit Cloud 的 `Oh no. Error running app.` 沒有提供伺服器 traceback，因此無法只憑截圖宣稱唯一 exception 行號；但以上三項是完整程式碼中已確認、仍違反 Stable Observation 架構的前台重任務。

## 本修正版處理

- `app.py`
  - 改用 `ensure_memory_initialized_bootsafe()`。
  - 每個 process 只做一次本機初始化。
  - 不在 boot/rerun 執行 GitHub restore/sync。

- `tino_persistent_store.py`
  - 新增 boot-safe local-only initializer 與 process lock/cache。
  - Streamlit 一般寫入預設不做同步遠端 I/O。
  - 原本的 remote restore/sync 功能保留，供外部程序使用。
  - Streamlit secrets / remote config 改成 process cache，避免每次 ledger merge 重複讀取。

- `memory_store.py`
  - Prediction / Audit / Profile 寫入維持本機 atomic write、backup、ledger mirror。
  - GitHub 同步預設不再內嵌於 forecast/render path。
  - 只有明確設定 `TINO_INLINE_REMOTE_SYNC=1` 才會恢復 inline remote sync；穩定觀察期不建議開啟。

- `ui_watch_center.py`
  - 30 秒背景刷新預設改為關閉。
  - `st.fragment` 預設完全停用，只有明確設定 `TINO_WATCH_FRAGMENT=1` 才會啟用。

- `ui_learning_center.py`
  - 改用 boot-safe initializer。
  - 文案同步更新為 Remote Memory 由外部程序處理。

- `sync_memory_remote.py`
  - 新增外部／手動 Remote Memory 還原與同步入口。
  - 內含 lock 與 status JSON，禁止從 `app.py` 呼叫。

## 未更動

- `data_sources_tw.py`：未覆蓋，Price Guard 不受影響。
- `orchestrator.py`：未更動。
- V9 前台布局、戰術語言、資訊密度：未更動。
- `auto_audit_scheduler.py`：維持 disabled_bootsafe。
- `data_sources_market_heat.py`：維持安全占位，不抓外部資料。

## 驗證結果

- `python -m compileall -q .`：PASS
- 全部主要模組 import：PASS
- Streamlit `AppTest` 連續 20 次 rerun：0 exception，RSS 約 362.6MB → 364.2MB，未持續上升
- Boot-safe remote network mock：0 次遠端呼叫
- Auto Audit disabled guard：PASS
- Market Heat disabled guard：PASS
- Offline smoke functional tests：13 項 PASS
- 原有 `test_file_size_guard` 仍失敗：`data_sources_tw.py` 1687 行 > 700 行。這是上傳 baseline 已存在的結構問題，本修正沒有碰該檔案。

## 部署建議

1. 先整包部署本修正版。
2. 穩定觀察至少 30～60 分鐘，期間不要設定：
   - `TINO_INLINE_REMOTE_SYNC=1`
   - `TINO_WATCH_FRAGMENT=1`
3. Remote Memory 要同步時，改由外部執行：
   - `python sync_memory_remote.py`
4. 若仍出現 `Oh no`，請從 Streamlit「Manage app → Logs」保留 crash 前後 100 行；有 traceback 後才能確認是否還有平台 OOM、依賴或網路層問題。

## 測試限制

目前執行環境無法連到 Yahoo/TWSE 外網，因此未做 live price/news 的完整 end-to-end 網路測試；Price Guard 相關檔案本次未修改。
