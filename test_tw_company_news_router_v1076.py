# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch

from data_sources_tw import (
    _select_tw_company_news,
    _tw_company_query_plan,
    fetch_tw_news,
)
from models import NewsItem, TickerInfo


def _delta() -> TickerInfo:
    return TickerInfo("2308.TW", "2308.TW", "台達電", "TW", "stock")


class TWCompanyNewsRouterV1076Tests(unittest.TestCase):
    def test_query_plan_is_generic_and_reserves_earnings_family(self):
        ticker = TickerInfo("9999.TW", "9999.TW", "測試公司", "TW", "stock")
        plan = _tw_company_query_plan(ticker)
        self.assertEqual(
            [row[0] for row in plan],
            [
                "capital_action", "earnings", "forward",
                "forward_risk", "analyst", "company_update",
            ],
        )
        self.assertIn("測試公司", plan[0][1])
        self.assertIn("9999", plan[0][1])
        self.assertIn("GDS", plan[0][1])
        self.assertLessEqual(plan[0][2], 14)
        self.assertIn("自結", plan[1][1])

    def test_earnings_slot_cannot_be_starved_by_generic_headlines(self):
        earnings = NewsItem(
            "GoogleNewsTW/工商時報",
            "2026-07-29 14:04",
            0.24,
            "tw_company_2308_earnings_bullish_event",
            "台達電Q2營收、獲利創高 EPS提高至9.68元",
            "",
        )
        generic = [
            NewsItem(
                "GoogleNewsTW/一般來源",
                f"2026-07-29 1{i}:00",
                -0.08,
                "tw_company_2308_company_update_bearish_event",
                f"台達電市場評論 {i}",
                "",
            )
            for i in range(8)
        ]
        selected = _select_tw_company_news(
            {"earnings": [earnings], "company_update": generic},
            limit=8,
        )
        self.assertIn(earnings, selected)
        self.assertEqual(selected[0], earnings)

    @patch("data_sources_tw._global_tw_macro_geo_news")
    @patch("data_sources_tw._google_news")
    @patch.dict("os.environ", {"TINO_OFFLINE_TEST": "0"})
    def test_delta_q2_release_survives_six_macro_rows(
        self,
        google_news,
        global_news,
    ):
        global_news.return_value = [
            NewsItem(
                "GoogleNewsTW",
                f"2026-07-29 0{i}:00",
                -0.08,
                "tw_daily_policy_geo",
                f"市場宏觀新聞 {i}",
                "",
            )
            for i in range(6)
        ]

        def fake_google(query, limit=12, window_days=60):
            if "自結" in query and "EPS" in query:
                return [
                    NewsItem(
                        "GoogleNewsTW/工商時報",
                        "2026-07-29 14:04",
                        0.24,
                        "bullish_event",
                        "台達電Q2營收、獲利創高 EPS提高至9.68元",
                        "",
                    )
                ]
            return [
                NewsItem(
                    "GoogleNewsTW/一般來源",
                    "2026-07-29 12:00",
                    -0.08,
                    "bearish_event",
                    "台達電跌深後外資動向受關注",
                    "",
                )
            ]

        google_news.side_effect = fake_google
        rows = fetch_tw_news(_delta(), force_refresh=True)
        earnings = [row for row in rows if "Q2營收、獲利創高" in row.title]
        self.assertEqual(len(earnings), 1)
        self.assertIn("tw_company_2308_earnings_", earnings[0].tag)
        self.assertLessEqual(sum("tw_daily_" in row.tag for row in rows), 4)


if __name__ == "__main__":
    unittest.main()
