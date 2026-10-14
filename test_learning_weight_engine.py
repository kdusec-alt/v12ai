from datetime import date, timedelta
import unittest

import learning_weight_engine as engine


def audit(day, ticker, actual="UP"):
    return {
        "market": "TW", "ticker": ticker, "target": "next", "target_trade_date": day,
        "actual_price_date": day, "actual_valid": True, "price_sample_quality": "verified",
        "actual_close": 101, "anchor_close": 100, "actual_direction": actual,
        "prediction_run_time_tw": f"{(date.fromisoformat(day) - timedelta(days=1)).isoformat()}T16:00:00+08:00",
        "audit_time_tw": f"{day}T16:00:00+08:00", "audit_id": f"{day}:{ticker}",
        "direction_family_contributions": {"trend": 4.0 if actual == "UP" else -4.0},
        "family_attribution": {"trend": {"aligned": True, "impact_weight": 0.5}},
    }


class LearningWeightGateTest(unittest.TestCase):
    def test_wrong_date_or_invalid_close_cannot_train(self):
        good = audit("2026-10-12", "A")
        wrong = {**audit("2026-10-12", "B"), "actual_price_date": "2026-10-13"}
        invalid = {**audit("2026-10-12", "C"), "actual_valid": "true"}
        rows = engine._canonical_audits([good, wrong, invalid], "TW")
        self.assertEqual([row["ticker"] for row in rows], ["A"])

    def test_after_open_or_unknown_timezone_cannot_train(self):
        good = audit("2026-10-12", "A")
        late = {**audit("2026-10-12", "B"), "prediction_run_time_tw": "2026-10-12T09:01:00+08:00"}
        naive = {**audit("2026-10-12", "C"), "prediction_run_time_tw": "2026-10-11T16:00:00"}
        self.assertEqual([row["ticker"] for row in engine._canonical_audits([good, late, naive], "TW")], ["A"])

    def test_same_session_never_crosses_train_validation(self):
        start = date(2026, 9, 1)
        rows = [audit((start + timedelta(days=day)).isoformat(), f"T{ticker}")
                for day in range(10) for ticker in range(6)]
        model = engine._train_market("TW", rows, {})
        self.assertEqual(model["validation_protocol"], engine.VALIDATION_PROTOCOL)
        self.assertEqual(model["train_count"] + model["validation_count"], len(rows))
        self.assertLess(model["train_through"], model["validation_from"])
        self.assertEqual(model["validation_sessions"], 3)

    def test_many_tickers_on_few_days_are_not_independent_sessions(self):
        rows = [audit(f"2026-10-{day:02d}", f"T{ticker}") for day in range(1, 4) for ticker in range(20)]
        model = engine._train_market("TW", rows, {})
        self.assertEqual(model["status"], "COLLECTING")
        self.assertEqual(model["unique_sessions"], 3)

    def test_legacy_active_weights_are_neutral_until_revalidated(self):
        self.assertFalse(engine._model_is_applied({"status": "ACTIVE", "multipliers": {"trend": 1.06}}))


if __name__ == "__main__":
    unittest.main()
