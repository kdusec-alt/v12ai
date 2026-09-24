from datetime import date
from types import SimpleNamespace
import unittest

from near20_buy_point_v1116 import forecast_near20_buy_point


def _forecast(market="TW", *, verified=True, status="closed_reference", last=110.0,
              company_line="Company News｜2330.TW｜未取得直接公司催化劑"):
    # Deterministic series with a confirmed local trough around 100 followed by
    # a rebound; 20 completed bars plus one preceding close for returns.
    closes = [106, 104, 102, 100, 101, 104, 105, 103, 104, 106,
              107, 108, 106, 107, 109, 110, 111, 109, 110, 112, 111]
    lows = [x - 1 for x in closes]
    highs = [x + 1 for x in closes]
    lows[3] = 99
    highs[4] = 103
    highs[5] = 106
    highs[6] = 107
    frame = SimpleNamespace(
        ticker=None,
        truth=SimpleNamespace(accepted=True, fallback=False, freshness="latest"),
        recent_closes=closes, recent_highs=highs, recent_lows=lows,
        recent_volumes=[1000] * len(closes), atr14=3.0, last=last,
        price_date="2026-09-24", market_status=status,
        context={"price_meta": {"price_verified": verified,
                                "history_scope": "formal_daily_only"}},
    )
    ticker = SimpleNamespace(market=market, resolved_symbol="2330.TW" if market == "TW" else "MU",
                             exchange="TWSE" if market == "TW" else "NASDAQ",
                             asset_type="stock", name="Test")
    frame.ticker = ticker
    frame.context["exchange_rule"] = {"fixed_daily_limit": True, "price_limit_pct": .10}
    return SimpleNamespace(ticker=ticker, price_frame=frame, decision_card={}, radar={"Company News": company_line}, news_items=[])


class Near20BuyPointTests(unittest.TestCase):
    def test_unverified_price_abstains(self):
        result = forecast_near20_buy_point(_forecast(verified=False))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["touch_probability_pct"])

    def test_valid_history_returns_observation_not_trade_instruction(self):
        result = forecast_near20_buy_point(_forecast(last=103.0))
        self.assertIn(result["status"], {"in_zone", "watch_pullback", "zone_broken"})
        self.assertFalse(result["formal_execution"])
        self.assertEqual(result["data_coverage"], 20)
        self.assertIn("校準", result["calibration_status"])

    def test_probability_declines_when_same_zone_is_farther_away(self):
        near = forecast_near20_buy_point(_forecast(last=108.0))
        far = forecast_near20_buy_point(_forecast(last=125.0))
        self.assertGreater(near["touch_probability_pct"], far["touch_probability_pct"])

    def test_missing_price_date_abstains_instead_of_using_host_clock(self):
        forecast = _forecast()
        forecast.price_frame.price_date = ""
        result = forecast_near20_buy_point(forecast)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("日期", result["reason"])

    def test_us_is_not_limited_by_taiwan_price_rule(self):
        result = forecast_near20_buy_point(_forecast("US", last=103.0))
        self.assertIsNone(result["exchange_limit_pct"])


if __name__ == "__main__":
    unittest.main()
