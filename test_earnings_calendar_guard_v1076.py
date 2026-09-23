# -*- coding: utf-8 -*-
from datetime import date, datetime
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo

from earnings_calendar_guard_v1076 import (
    merge_us_earnings_calendar,
    resolve_us_earnings_calendar,
)
from fundamental_growth_guard import build_us_fundamental_context
from models import DataTruth, PriceFrame, TickerInfo
from news_causal_intelligence_v1073 import analyze_news_causality


NY = ZoneInfo("America/New_York")
TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 7, 29, 10, 0, tzinfo=NY)


class FakeTable:
    def __init__(self, values):
        self.index = values
        self.empty = not values


class FakeTicker:
    def __init__(self, *, calendar=None, earnings_dates=None):
        self._calendar = calendar
        self._earnings_dates = earnings_dates

    def get_calendar(self):
        return self._calendar

    @property
    def calendar(self):
        return self._calendar

    def get_earnings_dates(self, limit=12):
        return self._earnings_dates

    @property
    def earnings_dates(self):
        return self._earnings_dates


def _frame(fundamental):
    ticker = TickerInfo("META", "META", "Meta Platforms, Inc.", "US", "stock")
    return PriceFrame(
        ticker=ticker,
        truth=DataTruth("UNIT", "2026-07-28", False, True, "unit"),
        open=598.0,
        high=600.0,
        low=590.0,
        last=595.0,
        previous_close=595.2,
        volume=1_000_000,
        vwap=595.0,
        atr14=12.0,
        recent_closes=[580.0 + i for i in range(20)],
        recent_highs=[581.0 + i for i in range(20)],
        recent_lows=[579.0 + i for i in range(20)],
        recent_volumes=[1_000_000] * 20,
        price_date="2026-07-28",
        market_status="pre_market",
        context={"fundamental": fundamental, "price_meta": {}},
    )


class EarningsCalendarGuardV1076Tests(unittest.TestCase):
    def test_meta_same_day_calendar_is_not_dropped(self):
        ticker = FakeTicker(calendar={"Earnings Date": [date(2026, 7, 29)]})
        result = resolve_us_earnings_calendar("META", {}, ticker_obj=ticker, now=NOW)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["next_earnings_date"], "2026-07-29")
        self.assertEqual(result["earnings_days"], 0)
        self.assertEqual(result["source"], "Yahoo get_calendar")

    def test_live_calendar_beats_nearer_stale_info_date(self):
        ticker = FakeTicker(calendar={"Earnings Date": [date(2026, 9, 30)]})
        result = resolve_us_earnings_calendar(
            "MU",
            {"nextEarningsDate": "2026-09-23"},
            ticker_obj=ticker,
            now=datetime(2026, 9, 23, 10, 0, tzinfo=NY),
        )
        self.assertEqual(result["next_earnings_date"], "2026-09-30")
        self.assertEqual(result["source"], "Yahoo get_calendar")

    def test_quote_summary_crosschecks_info_only_date(self):
        result = resolve_us_earnings_calendar(
            "MU",
            {"nextEarningsDate": "2026-09-23"},
            now=datetime(2026, 9, 23, 10, 0, tzinfo=NY),
            quote_summary_fetcher=lambda _symbol: {
                "calendarEvents": {
                    "earnings": {"earningsDate": [{"raw": int(datetime(2026, 9, 30, 0, 0, tzinfo=ZoneInfo("UTC")).timestamp())}]}
                }
            },
        )
        self.assertEqual(result["next_earnings_date"], "2026-09-30")
        self.assertEqual(result["source"], "Yahoo quoteSummary calendarEvents")

    def test_us_public_memory_does_not_publish_expired_earnings_date_as_live(self):
        from data_sources_us import US_PUBLIC_MEMORY
        for symbol in ("MU", "MRVL", "ONDS"):
            self.assertNotIn("nextEarningsDate", US_PUBLIC_MEMORY[symbol])
            self.assertNotIn("earningsDays", US_PUBLIC_MEMORY[symbol])

    def test_msft_after_close_timestamp_converts_to_taipei_next_day(self):
        timestamp = int(datetime(2026, 7, 29, 16, 5, tzinfo=NY).timestamp())
        result = resolve_us_earnings_calendar(
            "MSFT",
            {"earningsTimestamp": timestamp},
            now=NOW,
        )
        self.assertEqual(result["earnings_session"], "美股盤後")
        self.assertEqual(result["earnings_taipei"], "台灣 07/30 04:05")

    def test_earnings_dates_fallback_recovers_missing_get_info_field(self):
        ticker = FakeTicker(
            calendar={},
            earnings_dates=FakeTable([
                datetime(2026, 7, 29, 16, 0, tzinfo=NY),
                datetime(2026, 4, 29, 16, 0, tzinfo=NY),
            ]),
        )
        result = resolve_us_earnings_calendar("META", {}, ticker_obj=ticker, now=NOW)
        self.assertEqual(result["next_earnings_date"], "2026-07-29")
        self.assertEqual(result["earnings_session"], "美股盤後")

    def test_quote_summary_is_final_fallback(self):
        def fetcher(_symbol):
            return {
                "calendarEvents": {
                    "earnings": {
                        "earningsTimestamp": int(datetime(2026, 7, 29, 16, 0, tzinfo=NY).timestamp())
                    }
                }
            }

        result = resolve_us_earnings_calendar(
            "META",
            {},
            now=NOW,
            quote_summary_fetcher=fetcher,
        )
        self.assertEqual(result["source"], "Yahoo quoteSummary earningsTimestamp")
        self.assertEqual(result["earnings_taipei"], "台灣 07/30 04:00")

    def test_quote_summary_earnings_date_midnight_does_not_shift_to_prior_us_day(self):
        midnight_utc = int(datetime(2026, 7, 29, 0, 0, tzinfo=ZoneInfo("UTC")).timestamp())
        result = resolve_us_earnings_calendar(
            "META",
            {},
            now=NOW,
            quote_summary_fetcher=lambda _symbol: {
                "calendarEvents": {
                    "earnings": {"earningsDate": [{"raw": midnight_utc}]}
                }
            },
        )
        self.assertEqual(result["next_earnings_date"], "2026-07-29")
        self.assertEqual(result["earnings_session"], "")

    def test_resolved_event_reaches_fundamental_ui_and_scheduled_priority(self):
        timestamp = int(datetime(2026, 7, 29, 16, 5, tzinfo=NY).timestamp())
        info = merge_us_earnings_calendar(
            "META",
            {"trailingEps": 10.44, "totalRevenue": 56_300_000_000},
            now=NOW,
            quote_summary_fetcher=lambda _symbol: {
                "calendarEvents": {
                    "earnings": {"earningsTimestamp": timestamp}
                }
            },
        )
        fundamental = build_us_fundamental_context(info, "2026-07-28")
        frame = _frame(fundamental)
        self.assertEqual(fundamental["next_earnings"], "2026-07-29")
        self.assertEqual(fundamental["earnings_session"], "美股盤後")
        self.assertEqual(fundamental["earnings_taipei"], "台灣 07/30 04:05")
        causal = analyze_news_causality(
            frame,
            [],
            now=datetime(2026, 7, 29, 17, 0, tzinfo=TAIPEI),
        )
        self.assertEqual(causal["causal_state"], "scheduled_event_pending")
        self.assertIn("台灣 07/30 04:05", causal["dominant_headline"])


if __name__ == "__main__":
    unittest.main()
