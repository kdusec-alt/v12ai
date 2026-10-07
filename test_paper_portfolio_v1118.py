from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import memory_store
from memory_store import append_jsonl
import paper_portfolio_v1118 as portfolio


class PaperPortfolioTests(unittest.TestCase):
    def test_verified_trade_uses_cash_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store, 'MEMORY_DIR', Path(folder)), patch.object(memory_store, '_post_memory_write'):
            root = Path(folder) / 'paper_lab'
            append_jsonl(root / 'experiments.jsonl', {'experiment_id':'e1','origin':'autonomous','market':'TW','ticker':'2330'})
            append_jsonl(root / 'outcomes.jsonl', {'experiment_id':'e1','status':'CLOSED','date':'2026-10-08','entry_price':100,'exit_price':110,'fees_unit':2})
            result = portfolio.record_verified_trades()
            self.assertEqual(result['recorded'], 1)
            trade = portfolio.snapshot()['trades'][0]
            self.assertEqual(trade['quantity'], 990)
            self.assertEqual(trade['profit'], 7920)
            self.assertEqual(portfolio.record_verified_trades()['recorded'], 0)
            self.assertEqual(portfolio.snapshot()['cash']['TWD'], 1007920)

    def test_adjustment_preserves_trades_and_rejects_over_withdrawal(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store, 'MEMORY_DIR', Path(folder)), patch.object(memory_store, '_post_memory_write'):
            portfolio.set_capital('USD', 25000)
            self.assertEqual(portfolio.snapshot()['initial']['USD'], 25000)
            with self.assertRaises(ValueError):
                portfolio.set_capital('USD', 0)
            self.assertEqual(portfolio.snapshot()['cash']['USD'], 25000)


if __name__ == '__main__':
    unittest.main()
