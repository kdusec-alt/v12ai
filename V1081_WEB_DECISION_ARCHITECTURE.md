# TINO V1081 Web Decision Architecture

## 目的

V1081 重新整理 Web 從資料取得、Truth Guard、事件重算、個股仲裁、進場時機、前台排序到預測學習的完整鏈路。它修正的是全市場共用規則，不針對任何股票代碼、公司名稱或單一產業寫例外。

V9 Golden Master 前台、左右三層資訊流、正式 Direction、T0/T1/High/Low、Confidence、Prediction DNA、Auto Audit、Genome、Research 權重與校準時程維持不變。

---

## 一、正式資料流

```text
Ticker Resolver
    ↓
Price Route（TWSE/TPEX/US Session）
    ↓
Price + Session Truth / Exchange Rule
    ↓
News Families + Global Event Core + Timestamp Provenance
    ↓
Official Fundamentals / Institutional / Margin / Futures / Macro
    ↓
Orchestrator（唯一正式 Direction / T0 / T1 / High / Low）
    ↓
Decision Thesis / Market Regime / Final Shadow Arbiter
    ↓
V1081 AI 進場時機（execution/narrative only）
    ↓
V9 Battle Panel / Radar / Deep Report
    ↓
Prediction Snapshot → Official Close → Auto Audit → Learning Profile
```

每一層只能使用上一層已驗證的資料。缺失資料保持缺證，不可用 `0`、文字模板或跨 Session 數據替代。

---

## 二、決策優先順序

### P0｜Price / Session Truth

最高否決權：

- 報價來源、日期與 Session 不一致
- 正式收盤、盤前、盤中、盤後混用
- VWAP 不屬於目前 Session
- 價格無法驗證或 `decision_blocked`
- 新聞時間／來源未驗證

成立時不得建立正式進場價或跨資產因果。

### P1｜個股硬風險

優先於事件等待與大盤氣氛：

- 賣壓擴張
- 防守／突破結構失效
- 個股明顯弱於市場或產業
- 公司自身重大風險
- 財報反證與弱勢價格同時成立

這些狀態維持紅燈；事件等待不可把紅燈提升為黃燈。

### P2｜市場可交易性

- 台股漲停不是自動「等隔日」
- 封單、開板、回封、成交流動性未驗證時，使用 `LIMIT_LIQUIDITY_WAIT`
- 收盤後／盤後不顯示「今日可買」
- 盤後重新定價保留正式收盤做 Audit，操作層等下一正式 Session 驗證

### P3｜個股價格結構

VWAP 狀態必須使用不同語言：

- VWAP 上方：等待回測
- VWAP 下方：等待收復，VWAP 是確認門檻，不是直接買價
- VWAP 附近：等待維持或回踩不破
- 收復後回踩不破：才可升級為小量確認

同時納入：

- 開高走低／開低走高
- 現價位於日內高低區的位置
- 自開盤與自高點的回落幅度
- 是否再創低、量縮、承接

### P4｜相對強弱與跨市場

市場／產業代理只能提供背景，不可替個股背書。

```text
Relative Strength Gap = 個股漲跌幅 - 同 Session 市場/產業基準漲跌幅
```

- 大盤強、個股跌：負向個股證據
- 大盤強、個股跟漲不足：不加分
- 個股強於同 Session 基準：才是相對強勢
- SOX/NQ/QQQ/SMH 不同 Session 時，不允許進入同一確認投票

### P5｜基本面

- 財報差 + 價格弱：禁止接刀／弱勢修復
- 財報差 + 價格強勢吸收：利空吸收待回測，不直接追價
- 財報佳 + 價格強：可支持確認
- 基本面資料不足：不加分，也不自行推論

### P6｜事件等待

事件等待是單向安全閘門：

- 只能把綠燈暫時降為等待
- 不能覆蓋紅燈、過熱、漲停流動性或閉市狀態
- 必須有已驗證事件時間與實際經過分鐘
- 超過 30 分鐘後不得繼續顯示「首輪反應尚未完成」
- 時鐘缺證時顯示缺證，不使用永久等待模板

---

## 三、AI 進場狀態

| 狀態 | 意義 | 核心要求 |
|---|---|---|
| `BUY_TODAY_CONFIRM` | 今日可小量參與 | 個股自身站穩 VWAP、無硬反證、尚未過熱 |
| `WAIT_VWAP_PULLBACK` | 今日等回測 | 現價在 VWAP 上方，等待量縮回測不破 |
| `WAIT_VWAP_RECLAIM` | 等待收復確認 | 現價在 VWAP 下方；收復後維持／回踩不破 |
| `WAIT_RECLAIM_HOLD` | 收復後等承接 | 價格在 VWAP 多空交界 |
| `LIMIT_LIQUIDITY_WAIT` | 漲停成交待確認 | 限價排隊、成交不確定、開板後重評 |
| `WAIT_NEXT_SESSION` | 等下一交易時段 | 盤後、收盤後或正式 Session 尚未驗證 |
| `OVERHEATED_NO_CHASE` | 過熱不追 | 漲幅／ATR／相對延伸過大 |
| `SELLING_EXPANSION_BLOCK` | 賣壓未止 | 禁止接刀，等低點停止下移與 VWAP 收復 |
| `FAILED_BREAKOUT_EXIT` | 突破失敗 | 取消原進場計畫 |
| `DATA_WAIT` | 資料待確認 | 不以缺失資料建立買點 |

每個狀態擁有自己的五欄價格列，主燈、AI文字與價格列必須一致。

---

## 四、即時新聞重算

### P1 Immediate

已驗證的重大公司／政策／財報／宏觀事件，立即要求完整重算：

```text
News Provenance → Event Severity/Scope → Session Truth
→ Price Reaction → Relative Strength → Orchestrator
→ Decision Thesis → V1081 Entry Timing → UI
```

### P2 Recalculate

已驗證的重要事件，完成相同全鏈路重算，但不得在原 Forecast 物件上直接改寫。

### Verify Only

來源、內容或時間未驗證：只顯示觀察，不改方向、信心、主事件、進場狀態或學習。

### Ignore Stale

`stale_reindexed`、舊聞重新收錄與重複新聞：零模型影響，不觸發重算。

---

## 五、前台資訊排序

### 第一層｜立即決策

1. 市場背景（風險預算，不替個股背書）
2. 個股價格現實（開高走低、VWAP、相對強弱）
3. AI 進場時機
4. 現在可否參與
5. 確認／失效條件與狀態專屬五欄價格列
6. 下一交易日正式模型預測

### 第二層｜AI 證據

- 主導證據
- 反證
- 公司基本面
- 籌碼
- 相對強弱
- 事件驗證與時間

### 第三層｜原始深度資料

保留 V9 Radar / Deep Report：ABC、BSI、Quantum、財報、法人、資券、空方成本、事件、Trace、Audit、Research。

---

## 六、預測學習與昨測今收

正式鏈路：

1. 寫入正式 Prediction Snapshot
2. `official_sample_key` 鎖定股票＋目標 Session
3. 事件修正版保留不同 Prediction ID，不把每次查詢算成獨立樣本
4. 只用目標交易日的正式 OHLC／Close Audit
5. `actual_valid=True` 且價格來源 Verified 才進學習
6. 昨測今收優先讀已完成的正式 Audit
7. V1081 進場敘事不覆寫原始 T1，因此不污染正式預測績效
8. Shadow MAE/MFE／路徑／避險結果與正式權重隔離
9. 個股偏壓至少 20 筆正式 Audit、同類錯誤至少 5 次，且仍需 Tino 核准

Admin Integrity 另檢查：

- 待 Audit 正式樣本數
- 同 `official_sample_key` 的事件修正版
- 無效 Actual／孤兒 Audit
- 本機 Memory 檔案
- 最近一次遠端 Memory 同步成功或失敗

---

## 七、分析速度原則

V1081 只做有界加速：

- 不快取完整 `FinalForecast`
- 不啟動背景 Worker
- 台股獨立官方資料最多 3 個 Worker 並行
- 前景 News 與本地 Learning Signal 最多 2 個 Worker 並行
- 市場／日曆／新聞使用短 TTL、有上限的 process-local cache
- 已接受官方資料可短期重用；失敗資料不快取成真相
- 所有加速層失敗時退回原穩定路徑
- 效能時間只寫入 Trace/Admin，不進正式方向與價格模型

觀測欄位：

- `price_ms`
- `news_learning_ms`
- `orchestrate_ms`
- `total_ms`
- Cache hit/miss
- Worker 上限
- Remote Memory sync health

---

## 八、禁止事項

- 禁止 `if ticker == ...`
- 禁止公司名稱／股票代碼白名單
- 禁止用新聞文字直接覆蓋價格真相
- 禁止用大盤強勢替個股弱勢加分
- 禁止把 VWAP 收復門檻顯示成低接買價
- 禁止事件等待永久重置 15–30 分鐘
- 禁止盤後重算覆蓋原始正式預測
- 禁止 Shadow 結果未經核准改正式權重
- 禁止因加速而增加無界執行緒、完整 Forecast cache 或記憶體鏡像
