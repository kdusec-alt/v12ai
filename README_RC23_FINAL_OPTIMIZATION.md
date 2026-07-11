# TINO V12 RC2.3 Final Optimization Patch

本包以 `V12-main0709_RC23_NoLock_Hotfix` 為基底，保留 V9 前台 Golden Master，不重排主 UI，只做後台與既有面板內容補強。

## 本次納入

1. **TW Time Guard / Market Session Guard**
   - 台股一律以 `Asia/Taipei` 判斷。
   - `13:30~13:35` 視為收盤確認期，不再誤判成盤中延遲而鎖死。

2. **No-Lock Price Guard**
   - 台股即時價延遲時不再整個停止分析。
   - 延遲價只作清楚標示的參考，並降低 confidence。

3. **Learning Guard**
   - `limited_price_mode` / `decision_blocked` 不寫入 Official Sample。
   - Raw Log 可保留，避免污染正式學習。

4. **Trend Engine** (`trend_engine.py`)
   - 修正連漲 / 連跌：以正式日 K 收盤價逐日回推。
   - 修正累積漲跌幅：永遠使用 `Close_now / Close_N_days_ago - 1`，不累加每日漲跌幅。
   - 新增 5D / 10D / 20D / 60D 累積漲跌幅。
   - 盤中 / 盤前 / 盤後資料不納入正式連漲連跌，僅標示為盤中參考。

5. **MA Alert Price**
   - 新增月線 MA20、季線 MA60 與距離提醒。
   - 單股分析顯示，不全市場掃描，避免拖慢速度。

6. **Institution Flow Momentum**
   - 三大法人區加入法人連續買賣熱度。
   - 顯示外資 / 投信 / 自營的連買賣天數、10 日累計張數、星等與語意。

7. **Macro + Geo Risk**
   - 事件/Macro 行加入地緣政治風險摘要。
   - 新聞只做風險摘要，不直接改價。

## 測試

已通過：

```bash
python -m py_compile *.py
python smoke_rc23_final.py
```

原本 `smoke_tests.py` 除既有 `data_sources_tw.py too fat` 檢查外，其餘主要功能測試通過。該檢查是既有架構債，非本次新增造成。

