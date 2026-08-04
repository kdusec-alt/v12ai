# -*- coding: utf-8 -*-
import tempfile
import unittest

from conference_sources_v1097 import parse_hot_chips, parse_json_ld, parse_ocp_overview


class ConferenceSourcesV1097Tests(unittest.TestCase):
    def test_hot_chips_official_table_becomes_session(self):
        html = """
        <h2>Conference Day 1: Monday, August 24th, 2026</h2>
        <table><tr><td>4:45PM-6:45PM</td><td>NVIDIA Rubin GPU: Agentic AI</td><td>Jane Doe, NVIDIA</td></tr></table>
        """
        rows = parse_hot_chips(html, "https://hotchips.org/program/conference/")
        self.assertEqual(rows[0]["direct_tickers"], ["NVDA"])
        self.assertEqual(rows[0]["timezone"], "America/Los_Angeles")
        self.assertIn("GPU", rows[0]["technologies"])

    def test_json_ld_event_is_normalised(self):
        html = '<script type="application/ld+json">{"@type":"Event","name":"AI Optical","startDate":"2026-03-17T08:00:00-07:00","performer":{"name":"Marvell"}}</script>'
        rows = parse_json_ld(html, "OFC", "America/Los_Angeles", "https://www.ofcconference.org/schedule/")
        self.assertEqual(rows[0]["direct_tickers"], ["MRVL"])

    def test_ocp_overview_creates_two_days(self):
        html = "<h2>August 11–12, 2026</h2>"
        rows = parse_ocp_overview(html, "https://www.opencompute.org/summit/2026-ocp-apac-summit/schedule-overview")
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
