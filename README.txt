TINO V12 RC3.2｜第二次查詢 Segmentation Fault 修復

覆蓋 GitHub 根目錄：
- app.py
- ui_admin.py

必要設定：
Streamlit App Settings → Python version 請使用 3.11。
最新 Log 仍顯示 Python 3.14.6；Pandas/Numpy/PyArrow 原生層在記憶體壓力下可能直接 Segmentation fault。

程式修正：
1. 第二次查詢前先釋放上一個 Forecast，避免舊/新完整物件同時存在。
2. 切換即時股價或預測學習時釋放 Forecast。
3. Admin 收合面板不再每次重跑建立 Trace/Truth Guard DataFrame。
4. 診斷資料改成手動按需載入。
