# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from data_sources_tw import _score_news, _tw_company_news_relevant
from data_sources_us import _score_us_news, _us_news_relevant_to_ticker
from models import NewsItem, TickerInfo
from orchestrator import _directional_company_news


class NewsEventScoringTests(unittest.TestCase):
    def test_tw_forward_slowdown_outweighs_backward_beat(self):
        score, _tag = _score_news("台積電獲利創高但下一季需求放緩、毛利率下滑")
        self.assertLessEqual(score, -0.06)

    def test_tw_positive_forward_remains_positive(self):
        score, _tag = _score_news("台積電獲利優於預期並上調展望")
        self.assertGreater(score, 0.06)

    def test_us_forward_cut_outweighs_backward_beat(self):
        score, tag = _score_us_news(
            "TSMC earnings beat but cuts guidance as demand slows",
            "company",
        )
        self.assertLessEqual(score, -0.06)
        self.assertTrue(tag.startswith("bearish_"))

    def test_us_positive_forward_remains_positive(self):
        score, tag = _score_us_news("NVIDIA beats estimates and raises guidance", "company")
        self.assertGreater(score, 0.06)
        self.assertTrue(tag.startswith("bullish_"))

    def test_apple_company_bucket_rejects_bmw_pollution(self):
        ticker = TickerInfo("AAPL", "AAPL", "Apple Inc.", "US", "stock")
        bmw = NewsItem("GoogleNewsUS", "latest", -0.08, "bearish_us_company_news", "BMW warns tariffs will hurt margins", "")
        apple = NewsItem("GoogleNewsUS", "latest", -0.08, "bearish_us_company_news", "Apple warns tariffs may hurt iPhone margins", "")
        self.assertFalse(_us_news_relevant_to_ticker(ticker, bmw, "company"))
        self.assertTrue(_us_news_relevant_to_ticker(ticker, apple, "company"))

    def test_tw_company_bucket_requires_code_name_or_alias(self):
        ticker = TickerInfo("2337.TW", "2337.TW", "旺宏", "TW", "stock", price_limit_pct=0.10)
        unrelated = NewsItem("GoogleNewsTW", "latest", 0.12, "bullish_event", "台積電上調資本支出", "")
        relevant = NewsItem("GoogleNewsTW", "latest", 0.12, "bullish_event", "旺宏記憶體需求回升", "")
        self.assertFalse(_tw_company_news_relevant(ticker, unrelated))
        self.assertTrue(_tw_company_news_relevant(ticker, relevant))

    def test_policy_geo_is_not_counted_again_as_generic_news(self):
        rows = [
            NewsItem("GoogleNewsUS", "latest", -0.10, "daily_headline_policy_geo", "Iran risk rises", ""),
            NewsItem("GoogleNewsUS", "latest", 0.12, "bullish_us_company_earnings", "Apple raises guidance", ""),
        ]
        filtered = _directional_company_news(rows)
        self.assertEqual(len(filtered), 1)
        self.assertIn("company", filtered[0].tag)


if __name__ == "__main__":
    unittest.main()
