import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import paper_lab_v1118 as lab


def prediction(action='BUY'):
    return {'id':'p1','market':'US','ticker':'MU','asset_type':'stock',
            'run_time_tw':'2026-10-06T16:00:00+08:00','target_trade_date':'2026-10-07',
            'anchor_close':100,'next_high_est':110,'public_decision_snapshot':{
                'action_code':action,'session_date':'2026-10-06','entry':{'invalidation_price':95}}}


def audit():
    return {'prediction_id':'p1','ticker':'MU','target':'next','actual_valid':True,
            'target_trade_date':'2026-10-07','actual_price_date':'2026-10-07',
            'actual_open':100,'actual_high':112,'actual_low':97,'actual_close':108}


class PaperContracts(unittest.TestCase):
    def test_inputs_unchanged(self):
        p,a=prediction(),audit(); before=copy.deepcopy((p,a));lab.settle_experiment(lab.build_experiment(p),a);self.assertEqual((p,a),before)
    def test_only_formal_buy_enters(self):
        for action in ['HOLD','BLOCK','REDUCE','SELL','']:
            exp=lab.build_experiment(prediction(action));self.assertEqual(exp['status'],'OBSERVE');self.assertEqual(lab.settle_experiment(exp,audit())['status'],'OBSERVED')
    def test_no_historical_target_fill(self):
        exp=lab.build_experiment(prediction());exp['observed_at']='2026-10-07T22:00:00+08:00';self.assertEqual(lab.settle_experiment(exp,audit())['status'],'EXCLUDED')
    def test_us_premarket_same_taiwan_calendar_date(self):
        exp=lab.build_experiment(prediction());exp['observed_at']='2026-10-07T14:36:39+08:00'
        self.assertEqual(lab.settle_experiment(exp,audit())['status'],'CLOSED')
    def test_us_after_open_and_missing_timezone_excluded(self):
        exp=lab.build_experiment(prediction())
        for observed in ('2026-10-07T22:30:00+08:00','2026-10-07T14:36:39'):
            exp['observed_at']=observed
            self.assertEqual(lab.settle_experiment(exp,audit())['status'],'EXCLUDED')
    def test_wrong_or_unverified_audit_rejected(self):
        exp=lab.build_experiment(prediction())
        for k,v in [('prediction_id','wrong'),('ticker','NVDA'),('target','today'),('actual_valid',False),('actual_price_date','2026-10-08'),('target_trade_date','2026-10-08')]:
            a=audit();a[k]=v;self.assertIsNone(lab.settle_experiment(exp,a))
    def test_invalid_ohlc_rejected(self):
        exp=lab.build_experiment(prediction())
        for k,v in [('actual_open',float('nan')),('actual_high',99),('actual_low',-1),('actual_close',None)]:
            a=audit();a[k]=v;self.assertIsNone(lab.settle_experiment(exp,a))
    def test_next_open_with_slippage(self):
        r=lab.settle_experiment(lab.build_experiment(prediction()),audit());self.assertEqual(r['entry_price'],100.05);self.assertEqual(r['exit_reason'],'TARGET');self.assertLess(r['net_return_pct'],10)
    def test_both_hit_stop_first(self):
        a=audit();a['actual_low']=94;r=lab.settle_experiment(lab.build_experiment(prediction()),a);self.assertEqual(r['exit_reason'],'STOP');self.assertTrue(r['both_levels_hit']);self.assertLess(r['net_return_pct'],0)
    def test_gap_cancels_before_buy(self):
        a=audit();a.update(actual_open=94,actual_low=90);r=lab.settle_experiment(lab.build_experiment(prediction()),a);self.assertEqual(r['status'],'CANCELLED');self.assertNotIn('entry_price',r)
    def test_t1_close_exit(self):
        a=audit();a['actual_high']=109;r=lab.settle_experiment(lab.build_experiment(prediction()),a);self.assertEqual(r['exit_reason'],'T1_CLOSE')
    def test_tw_single_price_no_fill(self):
        p=prediction();p['market']='TW';a=audit();a.update(actual_open=100,actual_high=100,actual_low=100,actual_close=100);self.assertEqual(lab.settle_experiment(lab.build_experiment(p),a)['status'],'UNVERIFIED')
    def test_tw_fee_tax_explicit(self):
        p=prediction();p['market']='TW';exp=lab.build_experiment(p);self.assertEqual(exp['sell_tax_bps'],30);self.assertEqual(exp['fee_bps_per_side'],14.25)
    def test_duplicates_same_symbol_session(self):
        p=prediction();exp=lab.build_experiment(p);p['id']='p2';self.assertEqual(exp['experiment_id'],lab.build_experiment(p)['experiment_id'])
    def test_financial_truth_and_no_future(self):
        f={'accepted':True,'date':'2026-10-06','yoy':30,'yoy_verified':True};self.assertEqual(lab.build_experiment(prediction(),f)['fundamental']['yoy'],30)
        f['date']='2026-10-07';self.assertEqual(lab.build_experiment(prediction(),f)['fundamental'],{})
        p=prediction();p['asset_type']='etf';f['date']='2026-10-06';self.assertEqual(lab.build_experiment(p,f)['fundamental'],{})
    def test_research_asof_exact(self):
        r={'run_time_tw':'2026-10-06T18:00:00+08:00','shadow_direction':'UP'};self.assertEqual(lab.build_experiment(prediction(),research=r)['research'],{})
    def test_learning_minimum_and_verified_growth(self):
        row={'status':'OBSERVED','underlying_return_pct':1,'fundamental':{'yoy':5,'yoy_verified':True}}
        self.assertIn('不足',lab.learning_summary([row])[0]['研究狀態']);self.assertIn('可提出',lab.learning_summary([row]*20)[0]['研究狀態'])
        row['fundamental']['yoy_verified']=False;self.assertEqual(lab.learning_summary([row]),[])
    def test_record_once_and_settle_once(self):
        import memory_store
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store,'MEMORY_DIR',Path(folder)),patch.object(memory_store,'_post_memory_write'),patch.object(memory_store,'read_audit_log',return_value=[audit()]),patch.object(lab,'_research',return_value={}):
            f=SimpleNamespace(price_frame=SimpleNamespace(context={}))
            self.assertEqual(lab.capture_query(prediction(),f)['status'],'recorded')
            self.assertEqual(lab.capture_query(prediction(),f)['status'],'duplicate')
            self.assertEqual(lab.reconcile_local()['settled'],1)
            self.assertEqual(lab.reconcile_local()['settled'],0)
    def test_search_precedes_standby(self):
        s=Path(__file__).with_name('app.py').read_text();start=s.index('_boot_print("render_input_start")');self.assertLess(s.index('symbol, analyze, clear = render_input(st)',start),s.index('render_standby(st)',start))

if __name__=='__main__':unittest.main()
