# -*- coding: utf-8 -*-
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import stock_analysis_narrative_v1108 as narrative
from stock_analysis_narrative_v1108 import build_stock_analysis


def _forecast(market="TW", events=None, entry="90～92"):
    ticker = SimpleNamespace(resolved_symbol="2330.TW", name="台積電", market=market,
                             price_limit_pct=0.10 if market == "TW" else None)
    price = SimpleNamespace(
        ticker=ticker, context={"exchange_rule": {"fixed_daily_limit": True,
                                                   "price_limit_pct": 0.10,
                                                   "reference_price": 100.0}},
        price_date="2026-09-23", last=98.0, previous_close=100.0, volume=1500,
        recent_volumes=[1000, 1200, 1100], vwap=99.0,
    )
    return SimpleNamespace(
        ticker=ticker, price_frame=price, decision_card={"現價": 98, "VWAP位置": "VWAP下方"},
        radar={"Fair Value": "下緣情境 80｜現價基準 98｜上緣情境 110",
               "三大法人": "外資買超", "Quantum 貢獻": "產業聯動｜SOX +1.2%",
               "Company News": "Company News｜2330.TW｜未取得直接公司催化劑｜總體新聞不代替公司證據"},
        news_items=list(events or []),
    )


class StockAnalysisNarrativeV1108Tests(unittest.TestCase):
    def setUp(self):
        self._original_cache_path = narrative.COMPANY_EVENT_CACHE_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        narrative.COMPANY_EVENT_CACHE_PATH = Path(self._temp_dir.name) / "events.json"

    def tearDown(self):
        narrative.COMPANY_EVENT_CACHE_PATH = self._original_cache_path
        self._temp_dir.cleanup()

    def test_taiwan_entry_below_daily_floor_is_marked_not_executable(self):
        forecast = _forecast(entry="85～88")
        result = build_stock_analysis(forecast, {"entry_zone": "85～88", "invalidation": "88",
                                                 "primary_risk": "趨勢未確認"}, date(2026, 9, 23))
        self.assertIn("低於今日跌停 90", result["entry"])
        self.assertIn("不可成交", result["entry"])
        self.assertEqual(result["model_low"], "模型技術下緣 80.00")

    def test_us_entry_is_not_clipped_to_taiwan_daily_limit(self):
        forecast = _forecast(market="US")
        result = build_stock_analysis(forecast, {"entry_zone": "90～92", "invalidation": "88",
                                                 "staged_entry": "90～92 止穩先 1/3"},
                                      date(2026, 9, 23))
        self.assertEqual(result["entry"], "90～92 止穩先 1/3")

    def test_only_ticker_specific_news_within_three_weekday_sessions_is_used(self):
        fresh = SimpleNamespace(tag="tw_company_2330", title="台積電公告新合作", time="2026-09-21T10:00:00+08:00",
                                source="公開資訊觀測站", score=0.3)
        stale = SimpleNamespace(tag="tw_company_2330", title="台積電舊公告", time="2026-09-18", source="新聞", score=0.2)
        unrelated = SimpleNamespace(tag="tw_company_2317", title="鴻海公告新合作", time="2026-09-23", source="新聞", score=0.3)
        forecast = _forecast(events=[fresh, stale, unrelated])
        forecast.radar["Company News"] = "Company News｜2330.TW｜中｜公司事件｜偏多｜台積電公告新合作"
        result = build_stock_analysis(forecast,
                                      {"entry_zone": "95～96", "invalidation": "90"}, date(2026, 9, 23))
        self.assertIn("台積電公告新合作", result["evidence"])
        self.assertNotIn("舊公告", result["evidence"])
        self.assertNotIn("鴻海", result["evidence"])

    def test_no_fresh_company_event_explains_alternate_evidence(self):
        result = build_stock_analysis(_forecast(), {"entry_zone": "95～96"}, date(2026, 9, 23))
        self.assertIn("近3個交易日未偵測到新的公司專屬事件", result["evidence"])
        self.assertIn("VWAP", result["evidence"])
        self.assertIn("法人", result["evidence"])

    def test_raw_company_mention_is_rejected_when_right_panel_did_not_adjudicate_it(self):
        item = SimpleNamespace(tag="us_company_mrvl", title="Marvell Technology announces a market update",
                               time="2026-09-23", source="News", score=0.4)
        forecast = _forecast(market="US", events=[item])
        forecast.ticker = SimpleNamespace(resolved_symbol="MRVL", name="Marvell Technology, Inc.", market="US")
        forecast.price_frame.ticker = forecast.ticker
        forecast.radar["Company News"] = "Company News｜MRVL｜未取得直接公司催化劑｜總體新聞不代替公司證據"
        result = build_stock_analysis(forecast, {}, date(2026, 9, 23))
        self.assertIn("近3個交易日未偵測到新的公司專屬事件", result["evidence"])
        self.assertFalse(Path(narrative.COMPANY_EVENT_CACHE_PATH).exists())

    def test_company_event_is_reused_on_later_query_and_expires_after_three_sessions(self):
        item = SimpleNamespace(tag="tw_company_2330", title="台積電公告新合作",
                               time="2026-09-21T10:00:00+08:00",
                               source="公開資訊觀測站", score=0.3)
        forecast = _forecast(events=[item])
        forecast.radar["Company News"] = "Company News｜2330.TW｜中｜公司事件｜偏多｜台積電公告新合作"
        first = build_stock_analysis(forecast, {}, date(2026, 9, 21))
        later = build_stock_analysis(_forecast(events=[]), {}, date(2026, 9, 22))
        expired = build_stock_analysis(_forecast(events=[]), {}, date(2026, 9, 25))
        self.assertIn("台積電公告新合作", first["evidence"])
        self.assertIn("台積電公告新合作", later["evidence"])
        self.assertIn("未偵測到新的公司專屬事件", expired["evidence"])


if __name__ == "__main__":
    unittest.main()
