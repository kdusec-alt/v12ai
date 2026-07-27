# -*- coding: utf-8 -*-
from types import SimpleNamespace
import unittest

from low_entry_readiness_v1064 import assess_low_entry_readiness, resolve_current_event_families


class Row:
    def __init__(self, source, time, score, tag, title):
        self.source = source
        self.time = time
        self.score = score
        self.tag = tag
        self.title = title
        self.link = ""


def forecast(*, last=100, first=98, second=95, stop=92, no_chase=110,
             vwap="VWAP 上方", title="AI進場決策卡｜攻擊卡｜順勢突破",
             chgp=-1.0, gate="B回測", direction_score=12,
             chip="法人同步偏多｜外資連買3天｜投信連買2天｜融資連減3天｜回補啟動",
             news=None):
    d = {
        "現價": last,
        "低接第一批": first,
        "低接第二批": second,
        "防守": stop,
        "不追": no_chase,
        "攻擊": f"站穩 {last+3:.2f} 才攻",
        "VWAP位置": vwap,
        "漲跌幅": chgp,
        "標題": title,
        "主訊息": "價格與操作結論",
        "決策分": direction_score,
        "_direction_engine": {"gate_state": gate, "score": direction_score},
        "_trend_snapshot": {"ma20_gap_pct": -1.0},
        "_price_meta": {"decision_blocked": False},
    }
    radar = {
        "Fair Value": f"保守 {last-5:.2f}｜中性 {last:.2f}｜樂觀 {last+5:.2f}",
        "左側籌碼摘要": chip,
        "三大法人": chip,
        "資券 / 融資融券": chip,
        "空方成本 / 回補": chip,
    }
    return SimpleNamespace(
        decision_card=d,
        radar=radar,
        news_items=list(news or []),
        ticker=SimpleNamespace(market="TW"),
        no_chase=no_chase,
        confidence=70,
    )


class LowEntryReadinessTests(unittest.TestCase):
    def test_mature_when_price_chip_and_ai_align(self):
        result = assess_low_entry_readiness(forecast(last=98.2, first=98, second=95))
        self.assertGreaterEqual(result["score"], 75)
        self.assertEqual(result["color"], "green")
        self.assertEqual(result["label"], "低接成熟")

    def test_wait_when_event_and_foreign_selling_remain(self):
        rows = [Row(
            "Reuters", "2026-07-27T14:00:00+08:00", -0.18,
            "global_event_core|family=trade_tariff|severity=3|shock_level=3|tariff",
            "Taiwan tariff pressure rises",
        )]
        result = assess_low_entry_readiness(forecast(
            last=100, first=98, vwap="VWAP 下方", title="AI進場決策卡｜事件卡｜縮小試單",
            chip="法人偏空｜外資連賣7天｜投信連買1天｜融資連增3天", news=rows,
        ))
        self.assertLessEqual(result["score"], 54)
        self.assertIn(result["color"], {"yellow", "red"})

    def test_below_stop_is_hard_block(self):
        result = assess_low_entry_readiness(forecast(last=90, stop=92, first=98, second=95))
        self.assertLessEqual(result["score"], 39)
        self.assertEqual(result["color"], "red")
        self.assertTrue(result["hard_blockers"])

    def test_latest_market_oil_down_overrides_old_rise_story(self):
        rows = [
            Row(
                "GoogleNewsGlobal/EnergyNow.com", "2026-07-27T01:24:24+08:00", -0.18,
                "global_event_core|family=energy|severity=3|oil_price_up|shock_level=4|ticker_profile=ai_power",
                "Oil Prices Rise as US, Iran Trade Strikes",
            ),
            Row(
                "TINO_GlobalEventCore_YahooFinance", "2026-07-27T14:41:30+08:00", 0.16,
                "global_event_core|family=energy|severity=4|eventid=oil_supply_shock_20260727|oil_price_down|shock_level=2|ticker_profile=ai_power",
                "Global Event Core｜油價快速回落｜WTI -6.07%／Brent -6.05%",
            ),
        ]
        families = resolve_current_event_families(rows)
        self.assertEqual(families["energy"]["direction"], "down")
        self.assertEqual(families["energy"]["shock_level"], 2)
        self.assertEqual(families["energy"]["conflicting_older_rows"], 1)
        result = assess_low_entry_readiness(forecast(last=98.2, first=98, news=rows))
        self.assertLessEqual(result["max_shock_level"], 2)
        self.assertTrue(any("油價快速回落" in x["text"] for x in result["conditions"]))


if __name__ == "__main__":
    unittest.main()
