import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from us_short_pressure import parse_daily_file, summarize, _candidate_days


class ShortPressureTests(unittest.TestCase):
    def test_finra_ratio_is_off_exchange_and_not_short_interest(self):
        payload = (
            b"Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
            b"20261001|ONDS|400|10|1000|B,Q,N\n"
        )
        row = parse_daily_file(payload, "ONDS", "20261001")
        self.assertEqual(row["ratio_pct"], 40.0)
        self.assertIsNone(parse_daily_file(payload, "ONDS", "20260930"))

    def test_three_day_trend_requires_three_fresh_sessions(self):
        now = datetime(2026, 10, 2, 9, tzinfo=ZoneInfo("America/New_York"))
        rows = [
            {"date": day, "ratio_pct": ratio}
            for day, ratio in [("2026-09-29", 45), ("2026-09-30", 48), ("2026-10-01", 51)]
        ]
        result = summarize(rows, now)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["trend"], "RISING_ACTIVITY")
        self.assertIsNone(result["avg_7_pct"])
        self.assertFalse(summarize(rows[:2], now)["accepted"])
        self.assertFalse(summarize(rows, datetime(2026, 10, 12, tzinfo=ZoneInfo("America/New_York")))["accepted"])

    def test_before_publication_do_not_request_current_date(self):
        now = datetime(2026, 10, 2, 17, tzinfo=ZoneInfo("America/New_York"))
        self.assertNotIn("20261002", _candidate_days(now))


if __name__ == "__main__":
    unittest.main()
