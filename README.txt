TINO V12 RC3.3｜Auto-Learning 管理面板防當機修復

請覆蓋 GitHub 根目錄：
- app.py
- ui_admin.py

修正內容：
1. Auto-Learning 管理面板改為輕量模式。
2. 開啟管理面板時不再自動讀取 pending summary、recent tables、audit dashboard，也不建立 pandas/pyarrow DataFrame。
3. Storage Status 改用小型 HTML 表格，不走 st.dataframe。
4. 重型 Auto Audit 改成第二層手動開啟，且小批次 limit=300 / max_tickers=20。
5. app.py 會尊重 learning_log_enabled；啟用時每次分析完成仍會自動寫入一次正式 prediction log。
6. 預測公式與 V9 前台 UI 不變。

使用方式：
上傳覆蓋 app.py、ui_admin.py → Commit main → Streamlit Reboot App。
