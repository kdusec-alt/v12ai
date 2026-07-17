# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from event_reassessment import event_watch_display
from ui_admin import _clear_event_watch_session


class _Session(dict):
    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value


class _FakeStreamlit:
    def __init__(self):
        self.session_state = _Session()


class EventWatchControlsTests(unittest.TestCase):
    def test_unchanged_status_is_small_caption(self):
        payload = event_watch_display(
            {"status": "unchanged", "checked_at_tw": "2026-07-17 21:25:00"},
            ticker="MRVL",
        )
        self.assertEqual(payload["level"], "caption")
        self.assertIn("無新重大事件", payload["text"])

    def test_severity_two_is_warning(self):
        payload = event_watch_display(
            {"event_severity": 2},
            notice="Forward 更新",
            ticker="MRVL",
        )
        self.assertEqual(payload["level"], "warning")

    def test_severity_three_and_four_are_red_errors(self):
        for severity in (3, 4):
            with self.subTest(severity=severity):
                payload = event_watch_display(
                    {"event_severity": severity},
                    notice="重大事件",
                    ticker="MRVL",
                )
                self.assertEqual(payload["level"], "error")
                self.assertIn("🚨", payload["text"])

    def test_logout_clears_all_event_watch_state(self):
        fake = _FakeStreamlit()
        for key in (
            "event_reassessment_queue",
            "event_news_baseline",
            "event_baseline_created_at",
            "event_reassessment_notice",
            "event_reassessment_notice_severity",
            "last_event_watch_report",
        ):
            fake.session_state[key] = "value"
        fake.session_state["forecast"] = "preserved"

        _clear_event_watch_session(fake)

        self.assertEqual(fake.session_state["forecast"], "preserved")
        self.assertFalse(any(key.startswith("event_") for key in fake.session_state))
        self.assertNotIn("last_event_watch_report", fake.session_state)


if __name__ == "__main__":
    unittest.main()
