# TINO V12.2 量子糾纏預測層

## 超級公式概念

每個證據先轉成 `[-100, +100]` 的獨立家族分數，再依市場狀態、產業關聯、資料新鮮度與事件年齡取得動態權重：

```text
DirectionScore = Σ(FamilyScore × DynamicWeight)
               + BoundedEntanglementConfirmations

EffectiveScore = DirectionScore
               × DataQuality
               × (1 - ConflictPenalty)
               × (1 - EventUncertainty)
```

### 台股家族

- 趨勢、盤中、價格行為
- 三大法人、融券、BSI
- 融資槓桿、全市場融資熱度
- 外資期貨、外資匯率壓力
- 月營收短線事件
- 台指夜盤、費半/SMH、NQ/QQQ、MU、TSM ADR
- 政策與地緣政治

### 美股家族

- 趨勢、盤中、價格行為
- Short Float 與趨勢確認
- 財報/營收/指引短線事件
- 費半/SMH、NQ/QQQ、MU、TSM ADR、VIX
- 政策與地緣政治

## 事件衰減

- 台股月營收：公告日 100%，下一交易日 55%，之後 0%。
- 美股財報/指引新聞：24 小時內 100%，24–48 小時 45%，之後 0%。
- 結構化財報日期：公告交易日 100%，下一交易日 50%，之後 0%。
- 地緣/政策：24 小時內 100%，24–48 小時 55%，48–72 小時 20%，之後 0%。

## 防呆與防過擬合

- 不以今日跨市場代理值回填歷史價格框架。
- 缺值、過期、樣本、Fallback 不計分。
- SOX+SMH 與 NQ+QQQ 各只形成一個家族。
- Risk 不再固定換算為價格下跌。
- 事件與海外市場反向時，不硬套事件方向，而是降權並提高不確定性。
- 右側資訊先進方向引擎，再由戰術 Overlay 改變左側進場條件，不把同一證據再改價一次。
