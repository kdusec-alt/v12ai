# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from data_sources_tw import _score_news, _tw_company_news_relevant
from data_sources_us import (
    _score_us_news,
    _us_catalyst_family,
    _us_news_profile_queries,
    _us_news_relevant_to_ticker,
    _us_source_authority,
)
from models import NewsItem, TickerInfo
from orchestrator import _directional_company_news, _news_summary


class NewsEventScoringTests(unittest.TestCase):
    def test_tw_forward_slowdown_outweighs_backward_beat(self):
        score, _tag = _score_news("台積電獲利創高但下一季需求放緩、毛利率下滑")
        self.assertLessEqual(score, -0.06)

    def test_tw_positive_forward_remains_positive(self):
        score, _tag = _score_news("台積電獲利優於預期並上調展望")
        self.assertGreater(score, 0.06)

    def test_tw_gds_offering_is_negative_capital_event(self):
        score, tag = _score_news("廣達發行GDS募資22億美元，折價約7.8%")
        self.assertLessEqual(score, -0.16)
        self.assertEqual(tag, "capital_financing")

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

    def test_us_gds_offering_is_negative_even_with_customer_earnings(self):
        score, tag = _score_us_news(
            "Quanta GDS offering after Microsoft earnings",
            "company",
        )
        self.assertLessEqual(score, -0.08)
        self.assertTrue(tag.startswith("bearish_"))

    def test_us_weak_guidance_outweighs_backward_beat(self):
        score, tag = _score_us_news(
            "Corning earnings beat estimates but weak third-quarter guidance is not enough",
            "company",
        )
        self.assertLessEqual(score, -0.08)
        self.assertIn("earnings_forward_risk", tag)

    def test_earnings_publishers_count_as_one_forward_first_family(self):
        rows = [
            NewsItem(
                "GoogleNewsUS", "latest", 0.12, "bullish_us_company_earnings",
                "Corning earnings beat estimates on strong AI optical sales", "",
            ),
            NewsItem(
                "GoogleNewsUS", "latest", -0.12, "bearish_us_company_earnings_forward_risk",
                "Corning guidance is in line but not enough as stock sinks", "",
            ),
            NewsItem(
                "GoogleNewsUS", "latest", 0.10, "bullish_us_company_earnings",
                "Corning posts higher quarterly profit and sales", "",
            ),
        ]
        summary = _news_summary(rows)
        self.assertEqual(summary["earnings"]["state"], "backward_beat_high_bar_reset")
        self.assertTrue(summary["earnings_family_counted_once"])
        self.assertLess(summary["score"], 0)

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

    def test_unknown_us_stock_receives_universal_catalyst_query(self):
        ticker = TickerInfo("XYZ", "XYZ", "Example Systems, Inc.", "US", "stock")
        queries = _us_news_profile_queries(ticker)
        self.assertEqual(queries[0][1], "company")
        self.assertIn('"Example Systems, Inc."', queries[0][0])
        self.assertIn("demonstrates", queries[0][0])
        self.assertIn("contract", queries[0][0])

    def test_catalyst_family_distinguishes_demo_from_order(self):
        self.assertEqual(_us_catalyst_family("Marvell demonstrates 2nm optical technology"), "product_demo")
        self.assertEqual(_us_catalyst_family("Ondas wins new defense contract"), "order")
        self.assertEqual(_us_catalyst_family("Marvell Technology earnings beat estimates"), "earnings_guidance")

    def test_unknown_publisher_cannot_gain_full_directional_weight(self):
        self.assertEqual(_us_source_authority("Unknown Blog"), ("source_tier3", 0.72))
        self.assertEqual(_us_source_authority("Reuters"), ("source_tier1", 1.0))


if __name__ == "__main__":
    unittest.main()
