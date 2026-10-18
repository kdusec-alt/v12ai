import unittest
from datetime import datetime, timezone
import pandas as pd
from market_proxy_observation import observation_from_history
from market_command_v1081 import assess_market_command


class MarketProxyObservationTests(unittest.TestCase):
    def test_intraday_rebound_does_not_erase_completed_down_streak(self):
        frame = pd.DataFrame({"Close": [100, 98, 96, 94, 110]}, index=pd.date_range("2026-10-12", periods=5))
        row = observation_from_history("sox", "^SOX", frame, now=datetime(2026, 10, 16, 15, tzinfo=timezone.utc))
        self.assertEqual(row["consecutive_down"], 3)
        self.assertEqual(row["completed_as_of"], "2026-10-15")
        self.assertEqual(row["session"], "intraday")

    def test_oil_quote_and_daily_change_use_one_matching_date(self):
        frame = pd.DataFrame({"Close": [70, 71]}, index=pd.to_datetime(["2026-10-15", "2026-10-16"]))
        epoch = datetime(2026, 10, 16, 20, tzinfo=timezone.utc).timestamp()
        row = observation_from_history("wti", "CL=F", frame, {"regularMarketPrice":72,"regularMarketTime":epoch}, datetime(2026, 10, 18, 12, tzinfo=timezone.utc))
        self.assertEqual(row["price"], 72)
        self.assertAlmostEqual(row["change_pct"], 2.857, places=3)
        self.assertEqual(row["instrument"], "futures_continuous")
        self.assertIn("+00:00", row["quote_at"])

    def test_stale_or_future_data_cannot_be_made_current(self):
        frame = pd.DataFrame({"Close":[100,101,999]},index=pd.to_datetime(["2026-10-01","2026-10-02","2026-10-20"]))
        row = observation_from_history("sox","^SOX",frame,now=datetime(2026,10,18,tzinfo=timezone.utc))
        self.assertEqual(row["price"],101)
        self.assertTrue(row["stale"])

    def test_persistent_sox_decline_prevents_low_risk_after_small_day(self):
        from datetime import date
        proxies = {"sox":-.1,"nq":.2,"qqq":.1,"smh":.1,"trends":{"sox":{"consecutive_down":4,"return_5d_pct":-9,"completed_as_of":date.today().isoformat()}}}
        result = assess_market_command("US", proxies)
        self.assertGreaterEqual(result["score"],28)
        self.assertIn("連跌 4 日",result["thesis"])

    def test_absent_sources_have_zero_data_coverage(self):
        self.assertEqual(assess_market_command("US", {})["coverage"],0)


if __name__ == "__main__":
    unittest.main()
