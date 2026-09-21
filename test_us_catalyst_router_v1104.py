# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from data_sources_us import (
    _score_us_news,
    _us_catalyst_family,
    _us_news_profile_queries,
    _us_news_relevant_to_ticker,
    _us_source_authority,
)
from models import NewsItem, TickerInfo


class USCompanyCatalystRouterV1104Tests(unittest.TestCase):
    def test_unknown_symbol_gets_same_generic_catalyst_route(self):
        ticker = TickerInfo("XYZ", "XYZ", "Example Systems, Inc.", "US", "stock")
        queries = _us_news_profile_queries(ticker)
        self.assertEqual(queries[0][1], "company")
        self.assertIn('"Example Systems, Inc."', queries[0][0])
        self.assertIn("demonstrates", queries[0][0])
        self.assertIn("contract", queries[0][0])

    def test_official_publisher_can_verify_headline_without_entity_in_title(self):
        ticker = TickerInfo("MRVL", "MRVL", "Marvell Technology, Inc.", "US", "stock")
        row = NewsItem(
            "GoogleNewsUS/Marvell Technology, Inc.", "2026-09-21 09:00", 0.0,
            "us_company_news|catalyst_product_demo", "Industry-first 2nm optical technology demos", "",
        )
        self.assertTrue(_us_news_relevant_to_ticker(ticker, row, "company"))

    def test_generic_macro_title_cannot_enter_company_bucket(self):
        ticker = TickerInfo("MRVL", "MRVL", "Marvell Technology, Inc.", "US", "stock")
        row = NewsItem("GoogleNewsUS/WSJ", "now", -0.1, "us_company_news", "Oil rises on Middle East concerns", "")
        self.assertFalse(_us_news_relevant_to_ticker(ticker, row, "company"))

    def test_catalyst_semantics_and_source_weight_are_bounded(self):
        self.assertEqual(_us_catalyst_family("Marvell demonstrates 2nm optical technology"), "product_demo")
        self.assertEqual(_us_catalyst_family("Ondas wins new defense contract"), "order")
        self.assertEqual(_us_catalyst_family("Marvell Technology earnings beat estimates"), "earnings_guidance")
        self.assertEqual(_us_source_authority("Unknown Blog"), ("source_tier3", 0.72))
        self.assertEqual(_us_source_authority("Reuters"), ("source_tier1", 1.0))
        score, tag = _score_us_news("Marvell demonstrates 2nm optical technology", "company")
        self.assertIn("catalyst_product_demo", tag)
        self.assertLessEqual(abs(score), 0.32)


if __name__ == "__main__":
    unittest.main()
