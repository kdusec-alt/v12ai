import csv
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import autonomous_paper_v1118 as worker
import memory_store


class AutonomousPaperContracts(unittest.TestCase):
    def test_candidate_gate_and_ranking(self):
        rows = [
            {'ticker':'A','is_recommended5':'True','execution_status':'ACTIONABLE','recommendation_score':'80'},
            {'ticker':'B','is_recommended5':'False','execution_status':'ACTIONABLE','recommendation_score':'99'},
            {'ticker':'C','is_recommended5':'True','execution_status':'WAIT_PULLBACK','recommendation_score':'99'},
            {'ticker':'D','is_recommended5':'True','execution_status':'ACTIONABLE','recommendation_score':'90'},
        ]
        self.assertEqual([r['ticker'] for r in worker.select_candidates(rows)], ['D','A'])

    def test_empty_scanner_keeps_cash_and_records_run(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store,'MEMORY_DIR',Path(folder)), patch.object(memory_store,'_post_memory_write'):
            path=Path(folder)/'tino_market_scanner_v156_20261007.csv'
            with path.open('w',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=['ticker','is_recommended5','execution_status'])
                writer.writeheader();writer.writerow({'ticker':'A','is_recommended5':'False','execution_status':'ACTIONABLE'})
            now=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc)
            first=worker.run_cycle(folder,now=now,settle=lambda:{'settled':0})
            self.assertEqual((first['candidates'],first['paper_buy']), (0,0))
            self.assertEqual(worker.run_cycle(folder,now=now)['status'],'duplicate')

    def test_formal_block_does_not_become_paper_buy(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store,'MEMORY_DIR',Path(folder)), patch.object(memory_store,'_post_memory_write'):
            path=Path(folder)/'tino_market_scanner_v156_20261007.csv'
            with path.open('w',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=['ticker','is_recommended5','execution_status','recommendation_score'])
                writer.writeheader();writer.writerow({'ticker':'MU','is_recommended5':'True','execution_status':'ACTIONABLE','recommendation_score':'90'})
            row={'id':'p1','ticker':'MU','market':'US','asset_type':'stock','run_time_tw':'2026-10-06T16:00:00+08:00','target_trade_date':'2026-10-07','anchor_close':100,'next_high_est':110,'public_decision_snapshot':{'action_code':'BLOCK','session_date':'2026-10-06','entry':{'invalidation_price':95}}}
            report=worker.run_cycle(folder,now=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc),
                                    analyze=lambda _:SimpleNamespace(stopped=False),log=lambda _:row,
                                    capture=lambda *_,**kwargs:{'status':'recorded','experiment_status':'OBSERVE'},settle=lambda:{'settled':0})
            self.assertEqual((report['recorded'],report['paper_buy']), (1,0))

    def test_approved_formal_buy_creates_one_paper_position(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store,'MEMORY_DIR',Path(folder)), patch.object(memory_store,'_post_memory_write'):
            path=Path(folder)/'tino_market_scanner_v156_20261007.csv'
            with path.open('w',newline='') as file:
                writer=csv.DictWriter(file,fieldnames=['ticker','is_recommended5','execution_status','recommendation_score'])
                writer.writeheader();writer.writerow({'ticker':'MU','is_recommended5':'True','execution_status':'ACTIONABLE','recommendation_score':'90'})
            row={'id':'p1','ticker':'MU','market':'US','asset_type':'stock','run_time_tw':'2026-10-06T16:00:00+08:00','target_trade_date':'2026-10-07','anchor_close':100,'next_high_est':110,'public_decision_snapshot':{'action_code':'BUY','session_date':'2026-10-06','entry':{'invalidation_price':95}}}
            report=worker.run_cycle(folder,now=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc),
                                    analyze=lambda _:SimpleNamespace(stopped=False),log=lambda _:row,
                                    capture=lambda *_,**kwargs:{'status':'recorded','experiment_status':'PENDING'},settle=lambda:{'settled':0})
            self.assertEqual((report['recorded'],report['paper_buy']), (1,1))


if __name__=='__main__': unittest.main()
