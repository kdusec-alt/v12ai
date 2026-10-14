from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import memory_store
from scheduled_audit_v1118 import run_close_audit


class ScheduledAuditTest(unittest.TestCase):
    def test_bounded_audit_preserves_empty_or_failed_market_as_record(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store, "MEMORY_DIR", Path(folder)), patch.object(memory_store, "_post_memory_write"):
            seen = []
            def audit(**kwargs):
                seen.append(kwargs)
                return {"audited_t1_count": 0, "errors": [{"error": "close_not_ready"}],
                        "fetched_ticker_count": 1, "v14_weight_learning": {"status": "SKIPPED"}}
            report = run_close_audit("TW", audit=audit, reconcile=lambda: {"settled": 0}, portfolio=lambda: {"recorded": 0})
            self.assertEqual(report["status"], "deferred")
            self.assertEqual(seen[0], {"limit": 2000, "max_tickers": 12, "apply_safe_learning": True, "market_filter": "TW"})
            self.assertEqual(len(memory_store.read_jsonl(Path(folder) / "paper_lab" / "audit_runs.jsonl")), 1)

    def test_rejects_unrecognized_market(self):
        with self.assertRaises(ValueError):
            run_close_audit("ALL")


if __name__ == "__main__":
    unittest.main()
