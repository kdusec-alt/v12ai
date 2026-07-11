TINO V12 RC3.1 Stable Navigation + Learning Hotfix

請覆蓋 GitHub 根目錄同名檔案：
- app.py
- ui_admin.py
- ui_watch_center.py
- ui_learning_center.py

修正內容：
1. 個股分析 / 即時股價 / 預測學習改用 on_click callback，不再產生巢狀 st.rerun。
2. Watch Center 的卡片分析切換改為 callback。
3. Learning Center 改為單一區塊按需載入，不再一次建立所有 tabs 的 DataFrame。
4. Admin sidebar 不再每次 rerun 自動重複 log_prediction。
5. Auto-Learning 正式快照由 app.py 在一次 Analyze 完成後立即寫入。
6. Admin 重型管理面板預設關閉，避免每次 rerun 讀取 JSONL / pandas / pyarrow。
7. BaseException 改為 Exception，不攔截 Streamlit 內部控制流程。
8. Memory bootstrap 在 Watch / Learning 頁面改為 migrate=False。

操作：
1. 上傳並覆蓋 GitHub 根目錄四個檔案。
2. Commit 到 main。
3. Streamlit Cloud Reboot app。
