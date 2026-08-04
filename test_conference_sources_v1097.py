# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import conference_sources_v1097 as sources
from conference_sources_v1097 import parse_conference_dates, parse_fms, parse_hot_chips, parse_json_ld, parse_ocp_overview


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

    def test_fms_agenda_card_becomes_advance_session(self):
        html = """
        <main><h1>FMS 2026 — August 4–6, 2026</h1>
        <article class="agenda-session">August 5, 2026 9:30 AM Marvell AI Memory Infrastructure CXL and Optical</article>
        </main>
        """
        rows = parse_fms(html, "https://www.fmsnow.com/program/")
        marvell = next(row for row in rows if row["company"] == "Marvell")
        self.assertEqual(marvell["datetime"], "2026-08-05T09:30:00")
        self.assertEqual(marvell["direct_tickers"], ["MRVL"])
        self.assertIn("CRDO", marvell["supply_chain_tickers"])
        self.assertIn("CXL", marvell["technologies"])

    def test_fadu_gen6_maps_direct_and_supply_chain_exposure(self):
        html = """
        <h1>FMS 2026 — August 4–6, 2026</h1>
        <div class="session-card">August 5, 2026 11:00 AM FADU PCIe Gen6 SSD Controller</div>
        """
        row = next(row for row in parse_fms(html, "https://www.fmsnow.com/program/") if row["company"] == "Fadu")
        self.assertEqual(row["direct_tickers"], ["440110.KQ"])
        self.assertIn("SIMO", row["supply_chain_tickers"])
        self.assertIn("8299.TW", row["supply_chain_tickers"])

    def test_fms_dates_without_sessions_still_create_honest_day_alerts(self):
        rows = parse_fms("<h1>FMS 2026</h1><p>August 4–6, 2026</p>", "https://www.fmsnow.com/")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all("詳細議程待官方公布" in row["title"] for row in rows))

    def test_empty_refresh_does_not_erase_last_good_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cie.json"
            cached = {"saved_at": "2026-08-04T00:00:00+00:00", "sessions": [{"session_id": "KEEP"}], "sources": {}}
            cache.write_text(json.dumps(cached), encoding="utf-8")
            with patch.object(sources, "_CACHE", cache), patch.object(sources, "_read_cache", return_value=cached["sessions"]), patch.object(sources, "urlopen", side_effect=OSError("blocked")):
                self.assertEqual(sources.fetch_official_sessions(force=True), cached["sessions"])

    def test_generic_official_page_keeps_future_conference_visible(self):
        rows = parse_conference_dates(
            "<h1>GTC October 5–7, 2026</h1>", "GTC", "America/Los_Angeles",
            "https://www.nvidia.com/gtc/",
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["conference"], "GTC")

    def test_legacy_empty_cache_does_not_suppress_refresh(self):
        with patch.object(sources, "_read_cache", return_value=[]), patch.object(sources, "urlopen", side_effect=OSError("blocked")) as opened:
            self.assertEqual(sources.fetch_official_sessions(), [])
        self.assertTrue(opened.called)

    def test_legacy_nonempty_cache_is_refreshed_after_fms_parser_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cie.json"
            cache.write_text(json.dumps({
                "saved_at": "2026-08-04T00:00:00+00:00",
                "sessions": [{"session_id": "HOT_CHIPS_ONLY"}],
                "sources": {"HOT CHIPS": {"status": "OK", "sessions": 1}},
            }), encoding="utf-8")
            with patch.object(sources, "_CACHE", cache), patch.object(
                sources, "urlopen", side_effect=OSError("blocked")
            ) as opened:
                rows = sources.fetch_official_sessions()
        self.assertTrue(opened.called)
        self.assertEqual(rows, [{"session_id": "HOT_CHIPS_ONLY"}])


if __name__ == "__main__":
    unittest.main()
