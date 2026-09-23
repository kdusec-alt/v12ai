from types import SimpleNamespace
import unittest

from chip_deleveraging_guard_v1112 import classify_tw_chip_context


def _forecast(*, market="TW", margin_date="2026-09-23", inst_date="2026-09-23",
              margin_accepted=True, inst_accepted=True, fallback=False,
              margin_values=None, inst_values=None, closes=None):
    ticker = SimpleNamespace(market=market)
    margin = {
        "accepted": margin_accepted, "fallback": fallback, "freshness": "today",
        "date": margin_date, "source": "official-margin",
        **(margin_values or {"margin": -500, "margin_3": -1200, "margin_5": -1700,
                             "short": -100, "short_3": -220, "short_5": -300,
                             "ratio": 1.5}),
    }
    inst = {
        "accepted": inst_accepted, "fallback": fallback, "freshness": "today",
        "date": inst_date, "source": "official-institutional",
        **(inst_values or {"foreign": 1000, "trust": 300, "dealer": -100}),
    }
    price = SimpleNamespace(
        ticker=ticker, price_date="2026-09-23", context={"margin": margin, "inst": inst},
        last=95, previous_close=100,
        recent_closes=closes or [100, 101, 99, 98, 96, 95],
    )
    return SimpleNamespace(ticker=ticker, price_frame=price)


class ChipDeleveragingGuardV1112Tests(unittest.TestCase):
    def test_healthy_deleveraging_requires_current_verified_data(self):
        result = classify_tw_chip_context(_forecast())
        self.assertEqual(result["label"], "健康去槓桿")
        self.assertIn("融資回落", result["text"])

    def test_heavy_institutional_selling_prevents_healthy_label(self):
        forecast = _forecast(inst_values={"foreign": -13000, "trust": 100, "dealer": 0})
        result = classify_tw_chip_context(forecast)
        self.assertNotEqual(result["label"], "健康去槓桿")

    def test_stale_or_mismatched_chip_data_is_not_classified(self):
        stale = classify_tw_chip_context(_forecast(fallback=True))
        mismatched = classify_tw_chip_context(_forecast(inst_date="2026-09-22"))
        self.assertEqual(stale["label"], "本次不分類")
        self.assertEqual(mismatched["label"], "本次不分類")

    def test_unaccepted_chip_data_is_not_classified(self):
        result = classify_tw_chip_context(_forecast(margin_accepted=False))
        self.assertEqual(result["label"], "本次不分類")

    def test_us_does_not_use_taiwan_margin_classification(self):
        result = classify_tw_chip_context(_forecast(market="US"))
        self.assertEqual(result["label"], "跨市場不套用")


if __name__ == "__main__":
    unittest.main()
