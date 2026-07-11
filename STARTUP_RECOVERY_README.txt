TINO V12.2 Startup Recovery

這個修復包整合三個啟動修正：
1. app.py：保留唯一 st.set_page_config，加入 BootSafe 啟動階段追蹤。
2. data_sources.py：移除錯放的 st.set_page_config。
3. ticker_resolver.py：移除錯誤的自我匯入，恢復 resolve_ticker 正常定義。

部署時請將壓縮包內容直接覆蓋 GitHub repository 根目錄，至少覆蓋：
- app.py
- data_sources.py
- ticker_resolver.py
- requirements.txt

不要放進 .github 資料夾，也不要多包一層資料夾。
