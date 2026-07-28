import unittest

from market_command_v1071 import assess_market_command


class MarketCommandV1071Tests(unittest.TestCase):
    def test_bad_headline_alone_cannot_declare_crash(self):
        row = assess_market_command("US", {}, [{"tag": "severity=5", "title": "war"}])
        self.assertEqual(row["code"], "WAIT_CONFIRM")

    def test_broad_price_and_volatility_confirm_selloff(self):
        row = assess_market_command(
            "US",
            {"sox": -6.3, "nq": -3.0, "qqq": -2.8, "smh": -6.0, "vix": 29, "vix_change": 20},
            [{"tag": "shock_level=4", "title": "tariff escalation"}],
            {"市場風控": "偏空", "事件": "關稅"},
        )
        self.assertIn(row["code"], {"SELL_OFF", "CRASH"})
        self.assertTrue(row["price_confirmed"])
        self.assertGreaterEqual(row["radar_evidence_count"], 2)

    def test_missing_data_never_fabricates_precision(self):
        row = assess_market_command("TW", {"tx_night": None, "sox": None})
        self.assertEqual(row["code"], "WAIT_CONFIRM")
        self.assertEqual(row["facts"], [])


if __name__ == "__main__":
    unittest.main()
