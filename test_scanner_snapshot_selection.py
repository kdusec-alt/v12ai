import unittest
from unittest.mock import patch
from market_scanner_v156.market_scanner_drive_reader import _load_memory_snapshot


class ScannerSnapshotSelectionTests(unittest.TestCase):
    def test_keeps_unqualified_candidate_and_original_price(self):
        runs=[{"source":"daily.csv","run_at":"2026-10-19T10:00:00+00:00","scanner_candidates":[{"ticker":"A.TW","execution_status":"REJECTED","entry":"99.5","recommendation_score":"80","is_recommended5":"False"}]}]
        with patch("paper_lab_v1118._refresh_scan_runs"), patch("memory_store.read_jsonl",return_value=runs):
            frame,manifest,source,_=_load_memory_snapshot()
        self.assertEqual(source,"daily.csv")
        self.assertEqual(frame.iloc[0]["entry"],"99.5")
        self.assertEqual(frame.iloc[0]["execution_status"],"REJECTED")
        self.assertFalse(manifest["partial"])

    def test_failed_partial_scan_does_not_replace_completed_snapshot(self):
        runs=[{"source":"good.csv","run_at":"2026-10-17T10:00:00+00:00","scan_items":[{"ticker":"A"}]},
              {"source":"partial.csv","run_at":"2026-10-19T10:00:00+00:00","scanner_status":"failure","scan_items":[{"ticker":"B"}]}]
        with patch("paper_lab_v1118._refresh_scan_runs"), patch("memory_store.read_jsonl",return_value=runs):
            frame,manifest,source,_=_load_memory_snapshot()
        self.assertEqual(source,"good.csv")
        self.assertTrue(manifest["partial"])


if __name__ == "__main__":
    unittest.main()
