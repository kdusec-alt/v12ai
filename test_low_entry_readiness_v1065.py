# -*- coding: utf-8 -*-
from types import SimpleNamespace
import unittest

from low_entry_readiness_v1065 import assess_low_entry_readiness


def forecast(last=169.50, first=168.01, second=166.11, stop=164.89, confirmation=171.48):
    decision = {
        "現價": last,
        "低接第一批": first,
        "低接第二批": second,
        "防守": stop,
        "不追": 173.41,
        "攻擊": "事件前縮小試單",
        "轉強": f"站穩 {confirmation:.2f} 才轉強",
        "VWAP位置": "VWAP 下方",
        "漲跌幅": 2.56,
        "標題": "AI進場決策卡｜事件卡｜公布後確認",
        "主訊息": "FOMC 公布前等待確認",
        "決策分": 6,
        "_direction_engine": {"gate_state": "B回測", "score": 6},
        "_trend_snapshot": {"ma20_gap_pct": -2.0},
        "_price_meta": {"decision_blocked": False},
    }
    radar = {
        "Fair Value": "保守 162.72｜中性 169.50｜樂觀 176.28",
        "左側籌碼摘要": "法人分歧｜外資偏空",
        "三大法人": "外資 NA",
        "資券 / 融資融券": "空單觀察",
        "空方成本 / 回補": "等待回補",
    }
    return SimpleNamespace(
        decision_card=decision,
        radar=radar,
        news_items=[],
        ticker=SimpleNamespace(market="US"),
        no_chase=173.41,
        confidence=37,
    )


class LowEntryReadinessV1065Tests(unittest.TestCase):
    def test_wait_card_answers_pullback_confirmation_and_invalid_prices(self):
        result = assess_low_entry_readiness(forecast())
        self.assertEqual(result["label"], "再等等")
        self.assertIn("168.01", result["summary"])
        self.assertIn("171.48", result["summary"])
        self.assertIn("164.89", result["summary"])
        self.assertIn("A 回測", result["summary"])
        self.assertIn("B 站穩", result["summary"])

    def test_below_stop_says_rebuild_before_low_entry(self):
        result = assess_low_entry_readiness(forecast(last=163.00))
        self.assertEqual(result["color"], "red")
        self.assertIn("重新站回 164.89", result["summary"])
        self.assertIn("暫停低接", result["summary"])


if __name__ == "__main__":
    unittest.main()
