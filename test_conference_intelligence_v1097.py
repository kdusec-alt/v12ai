# -*- coding: utf-8 -*-
from datetime import datetime
import json
import os
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from conference_intelligence_v1097 import (
    all_conference_sessions, canonical_conference, conference_calendar,
    conference_watch_display,
)


class ConferenceIntelligenceV1097Tests(unittest.TestCase):
    def _rows(self):
        return [{
            "conference": "Flash Memory Summit",
            "event_id": "FMS_2026",
            "session_id": "FMS_2026_MRVL_0930",
            "company": "Marvell",
            "title": "AI Memory Infrastructure",
            "datetime": "2026-08-05T09:30:00",
            "timezone": "America/Los_Angeles",
            "technologies": ["CXL", "AI Storage"],
            "direct_tickers": ["MRVL"],
            "supply_chain_tickers": ["CRDO", "AVGO"],
            "importance": 5,
            "source_tier": "OFFICIAL",
            "source_url": "https://www.fmsnow.com/program/session",
        }]

    def test_fms_old_name_is_canonicalised(self):
        self.assertEqual(canonical_conference("Flash Memory Summit"), "FMS")

    def test_official_schedule_converts_to_taipei_and_never_votes_direction(self):
        with patch.dict(os.environ, {"TINO_CIE_EVENTS_JSON": json.dumps(self._rows()), "TINO_CIE_AUTO_FETCH": "0"}):
            result = conference_calendar(datetime(2026, 8, 4, 8, 0, tzinfo=ZoneInfo("Asia/Taipei")))
        row = result["sessions"][0]
        self.assertEqual(row["conference"], "FMS")
        self.assertEqual(row["start_taipei"], "2026-08-06 00:30")
        self.assertEqual(row["direction_score"], 0.0)
        self.assertEqual(result["direction_vote"], 0.0)
        self.assertEqual(row["lifecycle"], "PRE_EVENT")

    def test_fake_official_domain_is_rejected(self):
        rows = self._rows()
        rows[0]["source_url"] = "https://example.com/fms"
        with patch.dict(os.environ, {"TINO_CIE_EVENTS_JSON": json.dumps(rows), "TINO_CIE_AUTO_FETCH": "0"}):
            self.assertEqual(all_conference_sessions(), [])

    def test_new_terrapinn_fms_domain_is_accepted_as_official(self):
        rows = self._rows()
        rows[0]["source_url"] = "https://www.terrapinn.com/conference/future-memory-storage/agenda.stm"
        with patch.dict(os.environ, {"TINO_CIE_EVENTS_JSON": json.dumps(rows), "TINO_CIE_AUTO_FETCH": "0"}):
            sessions = all_conference_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].source_tier, "OFFICIAL")

    def test_duplicate_session_keeps_one_row(self):
        rows = self._rows() * 2
        with patch.dict(os.environ, {"TINO_CIE_EVENTS_JSON": json.dumps(rows), "TINO_CIE_AUTO_FETCH": "0"}):
            self.assertEqual(len(all_conference_sessions()), 1)

    def test_calendar_keeps_official_events_announced_months_early(self):
        rows = self._rows()
        rows[0]["datetime"] = "2026-11-05T09:30:00"
        with patch.dict(os.environ, {"TINO_CIE_EVENTS_JSON": json.dumps(rows), "TINO_CIE_AUTO_FETCH": "0"}):
            result = conference_calendar(datetime(2026, 8, 4, 8, 0, tzinfo=ZoneInfo("Asia/Taipei")))
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["lifecycle"], "AGENDA_PUBLISHED")

    def test_company_acronym_and_countdown_are_user_readable(self):
        rows = self._rows()
        rows[0]["conference"] = "Hot Chips"
        rows[0]["company"] = "Amd"
        rows[0]["source_url"] = "https://hotchips.org/program/conference/"
        rows[0]["datetime"] = "2026-08-23T08:30:00"
        with patch.dict(os.environ, {"TINO_CIE_EVENTS_JSON": json.dumps(rows), "TINO_CIE_AUTO_FETCH": "0"}):
            result = conference_watch_display(datetime(2026, 8, 4, 8, 30, tzinfo=ZoneInfo("Asia/Taipei")))
        self.assertIn("下一場 AMD｜19天後", result["text"])


if __name__ == "__main__":
    unittest.main()
