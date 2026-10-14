import copy
from datetime import datetime, timezone
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
    def test_audit_scorecard_counts_only_unique_verified_target_sessions(self):
        p1={'market':'TW','ticker':'6770.TW','target_trade_date':'2026-10-14','official_sample_key':'s1'}
        p2={'market':'TW','ticker':'6770.TW','target_trade_date':'2026-10-15','official_sample_key':'s2'}
        good={'target':'next','market':'TW','ticker':'6770.TW','official_sample_key':'s1',
              'target_trade_date':'2026-10-14','actual_price_date':'2026-10-14',
              'actual_valid':True,'predicted_close':100,'actual_close':102,'anchor_close':99,
              'price_sample_quality':'verified','direction_hit':True,'close_in_predicted_range':False}
        duplicate={**good,'audit_time_tw':'2026-10-14T18:00:00+08:00'}
        wrong_date={**good,'official_sample_key':'s2','actual_price_date':'2026-10-13'}
        report=lab.prediction_audit_scorecard([p1,p1,p2],[good,duplicate,wrong_date], '6770.TW')
        self.assertEqual((report['samples'],report['pending'],report['mae_pct'],report['direction_pct']), (1,1,2.0,100.0))
        self.assertEqual(report['range_pct'],0.0)
        self.assertEqual(report['breakdown'][0]['有效樣本'], 1)

    def test_scorecard_breaks_down_market_session_and_predicted_direction(self):
        rows=[]
        for market, session, direction, hit in [('TW','intraday','UP',False),('US','pre_market','DOWN',True)]:
            rows.append({'target':'next','market':market,'ticker':market,'target_trade_date':'2026-10-14',
                         'actual_price_date':'2026-10-14','actual_valid':True,'predicted_close':100,
                         'actual_close':102,'anchor_close':99,'price_sample_quality':'verified',
                         'prediction_session_mode':session,'predicted_direction':direction,'direction_hit':hit})
        groups=lab.prediction_audit_scorecard([],rows)['breakdown']
        self.assertEqual({(r['市場'],r['預測時段'],r['預測方向']) for r in groups},
                         {('TW','intraday','UP'),('US','pre_market','DOWN')})

    def test_reference_limited_audit_is_not_accuracy_sample(self):
        row={'target':'next','market':'US','ticker':'MU','target_trade_date':'2026-10-07',
             'actual_price_date':'2026-10-07','actual_valid':True,'predicted_close':100,
             'actual_close':110,'anchor_close':100,'price_sample_quality':'reference_limited'}
        report=lab.prediction_audit_scorecard([], [row])
        self.assertEqual((report['samples'],report['limited'],report['mae_pct']), (0,1,None))

    def test_unresolved_targets_separate_waiting_from_overdue_by_market_clock(self):
        rows=[{'market':'TW','ticker':'6770.TW','target_trade_date':'2026-10-13'},
              {'market':'TW','ticker':'6770.TW','target_trade_date':'2026-10-14'},
              {'market':'US','ticker':'MU','target_trade_date':'2026-10-13'}]
        result=lab.prediction_audit_scorecard(rows, [], as_of=datetime(2026,10,14,1,0,tzinfo=timezone.utc))
        self.assertEqual((result['pending'],result['waiting'],result['overdue']), (3,2,1))

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
        p=prediction();p['run_time_tw']='2026-10-07T22:30:00+08:00'
        self.assertEqual(lab.build_experiment(p)['status'],'OBSERVE')
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
            self.assertEqual(lab.capture_query(prediction(),f,origin='autonomous')['status'],'recorded')
            duplicate=lab.capture_query(prediction(),f)
            self.assertEqual(duplicate['status'],'duplicate')
            self.assertEqual(duplicate['experiment_status'],'PENDING')
            self.assertEqual(memory_store.read_jsonl(Path(folder)/'paper_lab'/'experiments.jsonl')[0]['origin'],'autonomous')
            self.assertEqual(lab.reconcile_local()['settled'],1)
            self.assertEqual(lab.reconcile_local()['settled'],0)
    def test_search_precedes_standby(self):
        s=Path(__file__).with_name('app.py').read_text();start=s.index('_boot_print("render_input_start")');self.assertLess(s.index('symbol, analyze, clear = render_input(st)',start),s.index('render_standby(st)',start))

if __name__=='__main__':unittest.main()
