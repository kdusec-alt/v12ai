import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
import memory_store
from types import SimpleNamespace
from scheduled_audit_v1118 import update_candidate_checks


class CandidateForecastIntegrityTests(unittest.TestCase):
    def test_daily_audit_never_mixes_previous_history_with_current_ohlc(self):
        from learning_market_clock import fetch_actual_daily_snapshot
        frame=SimpleNamespace(last=105,open=100,high=108,low=99,recent_closes=[90],recent_highs=[92],recent_lows=[88],truth=SimpleNamespace(accepted=True,fallback=False,source="Official"),price_date="2026-10-19",market_status="after_close")
        with patch("data_sources.fetch_price",return_value=frame):
            actual=fetch_actual_daily_snapshot("MU")
        self.assertEqual(actual["actual_close"],105)
        self.assertEqual(actual["actual_high"],108)
        self.assertTrue(actual["actual_valid"])

    def run_update(self, actual):
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store,"MEMORY_DIR",Path(folder)), patch.object(memory_store,"_post_memory_write"):
            path=Path(folder)/"paper_lab"/"scan_runs.jsonl"
            memory_store.append_jsonl(path,{"run_day":"2026-10-16","run_at":"2026-10-16T12:00:00+00:00","forecast_details":[{"prediction_id":"p1","ticker":"MU","observed_at":"2026-10-16T12:00:00+00:00","target_trade_date":"2026-10-19","predicted_close":100,"anchor_price":100,"entry_price":98,"paper_trade_allowed":False}]})
            with patch.object(memory_store,"read_audit_log",return_value=[actual]):
                changed=update_candidate_checks("US")
            return changed,memory_store.read_jsonl(path,10)[-1]["forecast_details"][0]

    def valid(self):
        return {"prediction_id":"p1","ticker":"MU","market":"US","target":"next","target_trade_date":"2026-10-19","actual_valid":True,"actual_price_date":"2026-10-19","actual_close":105,"actual_open":99,"actual_high":107,"actual_low":97,"price_sample_quality":"verified"}

    def test_other_ticker_same_prediction_id_is_rejected(self):
        self.assertEqual(self.run_update({**self.valid(),"ticker":"NVDA"})[0],0)

    def test_nonfinite_close_never_enters_memory(self):
        self.assertEqual(self.run_update({**self.valid(),"actual_close":float("nan")})[0],0)

    def test_unselected_rise_is_observed_without_inventing_trade(self):
        changed,row=self.run_update(self.valid())
        self.assertEqual(changed,1)
        self.assertTrue(row["missed_rise"])
        self.assertTrue(row["entry_level_touched"])
        self.assertEqual(row["predicted_close"],100)
        self.assertNotIn("entry_fill_price",row)


if __name__ == "__main__":
    unittest.main()
