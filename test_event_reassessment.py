# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from event_reassessment import assess_event_delta, event_fingerprint
from learning import forecast_snapshot
from models import FinalForecast, NewsItem, RawForecast, TickerInfo
from v13_research.compatibility import prediction_row_to_seed


class EventReassessmentTests(unittest.TestCase):
    def _news(self, title: str, score: float = 0.0, tag: str = "headline_neutral") -> NewsItem:
        return NewsItem("GoogleNews", "2026-07-17 16:30", score, tag, title, "https://example.test")

    def test_same_headline_does_not_reassess(self):
        row = self._news("台積電本季財報優於預期", 0.12, "bullish_event")
        result = assess_event_delta([row], [row], ticker="2330.TW", market="TW")
        self.assertFalse(result["needs_reassessment"])
        self.assertEqual(result["new_event_count"], 0)

    def test_forward_slowdown_reassesses_after_close(self):
        baseline = [self._news("台積電本季財報優於預期", 0.12, "bullish_event")]
        latest = baseline + [
            self._news("台積電獲利創高但下一季需求放緩、毛利率下滑", -0.18, "bearish_event")
        ]
        result = assess_event_delta(
            baseline,
            latest,
            ticker="2330.TW",
            market="TW",
            revision_of="original123",
            now=datetime(2026, 7, 17, 16, 45, tzinfo=ZoneInfo("Asia/Taipei")),
        )
        self.assertTrue(result["needs_reassessment"])
        self.assertEqual(result["revision_type"], "AFTER_CLOSE_EVENT")
        self.assertEqual(result["revision_of"], "original123")
        self.assertEqual(result["event_category"], "company_hard_negative")
        self.assertGreaterEqual(result["event_severity"], 3)

    def test_geo_statement_reassesses_without_forcing_direction(self):
        latest = [self._news("川普表示可能對伊朗採取軍事行動", -0.06, "policy_geo")]
        result = assess_event_delta([], latest, ticker="MU", market="US")
        self.assertTrue(result["needs_reassessment"])
        self.assertEqual(result["event_category"], "geo_policy_escalation")
        self.assertEqual(result["decision_influence"], "full_reanalysis_only")

    def test_low_impact_headline_is_observation_only(self):
        latest = [self._news("公司舉辦例行活動", 0.01)]
        result = assess_event_delta([], latest, ticker="2330.TW", market="TW")
        self.assertFalse(result["needs_reassessment"])

    def test_old_rotated_headline_cannot_invalidate_new_forecast(self):
        old = self._news("公司兩週前已下修財測", -0.20, "bearish_event")
        result = assess_event_delta(
            [],
            [old],
            ticker="2330.TW",
            market="TW",
            not_before_epoch=datetime(2026, 7, 18, 9, 0, tzinfo=ZoneInfo("Asia/Taipei")).timestamp(),
        )
        self.assertFalse(result["needs_reassessment"])
        self.assertEqual(result["new_event_count"], 0)

    def test_syndicated_publisher_suffix_has_same_identity(self):
        left = self._news("Chip demand slows - Reuters")
        right = self._news("Chip demand slows - Bloomberg")
        self.assertEqual(event_fingerprint(left), event_fingerprint(right))

    def test_revision_snapshot_is_distinct_and_v13_compatible(self):
        ticker = TickerInfo("2330", "2330.TW", "台積電", "TW", "stock")
        raw = RawForecast(1000.0, 1010.0, 1020.0, 990.0, {"A": 40.0, "B": 35.0, "C": 25.0}, 995.0, 1030.0)
        forecast = FinalForecast(
            ticker=ticker,
            stopped=False,
            stop_reason="",
            raw=raw,
            final_t0=1000.0,
            final_t1=1010.0,
            final_t1_high=1020.0,
            final_t1_low=990.0,
            confidence=60.0,
            no_chase=1030.0,
            low_entry=995.0,
            decision_card={
                "現價": 1000.0,
                "VWAP位置": "VWAP上方",
                "_direction_engine": {"label": "UP", "score": 12.0, "p_up": 0.45, "p_neutral": 0.35, "p_down": 0.20},
            },
            tags=[],
            one_liner="測試",
            reality_anchor="after close",
            radar={},
            trace=None,
            data_truths=[],
        )
        base = forecast_snapshot(forecast)
        revised = forecast_snapshot(forecast, revision_meta={
            "revision_type": "AFTER_CLOSE_EVENT",
            "revision_of": base["id"],
            "event_bundle_id": "event-bundle-1",
            "event_fingerprint": "event-1",
            "event_severity": 3,
            "event_category": "company_hard_negative",
        })
        self.assertNotEqual(base["id"], revised["id"])
        self.assertEqual(revised["revision_of"], base["id"])
        self.assertTrue(revised["event_revision"])
        seed = prediction_row_to_seed(revised)
        self.assertEqual(seed.prediction_id, revised["id"])
        self.assertEqual(seed.ticker, "2330.TW")


if __name__ == "__main__":
    unittest.main()
