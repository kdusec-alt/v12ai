"""Counterfactual cases: final entry permission must use adjudicated events."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_v1096_single_decision_lifecycle_audit import forecast
from decision_core_v1096 import build_decision_snapshot, build_typed_evidence


def case(causal=None, raw_scores=(0.5,), controlled=False):
    f=forecast(foreign=5000,foreign_3=10000)
    f.final_t1=101.5 if controlled else 105
    f.raw.raw_abc={'A':20,'B':60,'C':20} if controlled else {'A':60,'B':30,'C':10}
    f.position_status='FLAT'
    f.news_items=[SimpleNamespace(score=x,title='unverified or duplicated headline') for x in raw_scores]
    if causal is not None:f.price_frame.context['news_causal_v1073']=causal
    return f


def event(**overrides):
    result={'accepted':True,'selected_count':1,'dominant_published_at':'2026-08-04T10:00:00+08:00',
            'company_family_count':1,'combined_score':-0.2,'causal_state':'event_price_confirming',
            'dominant_entity_verified':True,'dominant_scope':'company','dominant_subject_role':'self',
            'dominant_event':{'semantic_score':-0.2},'dominant_impact_score':92,
            'can_compare_to_price':True,'entry_gate':'normal','causal_text':'重大籌資利空與價格同向'}
    result.update(overrides);return result


def decision(f):
    with patch('decision_core_v1096.assess_entry_opportunity',return_value=f._test_entry):
        return build_decision_snapshot(f).to_dict()


class NewsDecisionIntegrationTests(unittest.TestCase):
    def test_price_setup_can_still_buy_without_inventing_news_support(self):
        f=case();s=decision(f)
        self.assertEqual(s['action_code'],'BUY')
        self.assertFalse(next(x for x in s['evidence'] if x['family']=='event')['accepted'])

    def test_raw_headline_spam_and_opposite_sentiment_do_not_override_event(self):
        for scores in [(0.8,),(-0.8,),(0.8,)*30,(-0.8,)*30]:
            with self.subTest(scores=scores):
                s=decision(case(event(),scores));gate=s['reasoning']['cross_module_gate']
                self.assertTrue(gate['event_bearish_veto'])
                self.assertFalse(gate['entry_qualified'])
                self.assertNotEqual(s['action_code'],'BUY')
                self.assertFalse(gate['allow_pullback']);self.assertFalse(gate['allow_breakout'])

    def test_positive_event_wait_blocks_both_right_entry_and_controlled_low_lane(self):
        causal=event(combined_score=0.2,dominant_event={'semantic_score':0.2},
                     causal_state='event_awaiting_market_reaction',can_compare_to_price=False,
                     entry_gate='block_until_first_reaction')
        for controlled in (False,True):
            with self.subTest(controlled=controlled):
                gate=decision(case(causal,controlled=controlled))['reasoning']['cross_module_gate']
                self.assertTrue(gate['event_reaction_wait'])
                self.assertFalse(gate['entry_qualified'])
                self.assertFalse(gate['allow_immediate_buy'])
                self.assertFalse(gate['allow_pullback'])

    def test_stale_unverified_or_scheduled_event_is_not_directional_support(self):
        for state in ('event_context_only','event_time_unverified','scheduled_event_pending','degraded'):
            with self.subTest(state=state):
                facts=build_typed_evidence(case(event(causal_state=state,combined_score=0.3)))
                self.assertFalse(next(x for x in facts if x.family=='event').accepted)

    def test_absorbed_negative_event_is_not_falsely_labelled_unabsorbed_major_veto(self):
        gate=decision(case(event(causal_state='negative_event_absorbed')))['reasoning']['cross_module_gate']
        self.assertFalse(gate['event_bearish_veto'])

    def test_no_news_does_not_turn_abc_defense_into_buy(self):
        f=case(event(combined_score=0.3,dominant_event={'semantic_score':0.3}));f.raw.raw_abc={'A':6,'B':40,'C':54};f.news_items=[]
        s=decision(f)
        self.assertNotEqual(s['action_code'],'BUY')
        self.assertFalse(s['reasoning']['cross_module_gate']['entry_qualified'])

    def test_compact_hero_keeps_the_formal_event_blocker_visible(self):
        from test_jarvis_v1117 import fixture
        from ui_jarvis_v1117 import command_html
        f,p=fixture()
        p['public_snapshot']['reasoning']={'cross_module_gate':{'event_reaction_wait':True}}
        output=command_html(f,p)
        self.assertIn('等待事件首輪量價反應，暫不進場',output)
        self.assertNotIn('<details',output)

    def test_empty_or_unmatched_event_selection_does_not_readmit_raw_headlines(self):
        from news_causal_intelligence_v1073 import select_effective_news_items
        raw=[SimpleNamespace(score=0.9,title='raw positive story',source='unit',tag='',time='2026-08-04')]
        self.assertEqual(select_effective_news_items(raw,{'selected_keys':[]}),[])
        self.assertEqual(select_effective_news_items(raw,{'selected_keys':['not-present']}),[])

    def test_direction_signal_uses_net_guidance_semantics_not_raw_sentiment(self):
        from orchestrator import _adjudicated_company_news_signal
        price=SimpleNamespace(price_date='2026-08-04')
        causal=event(company_score=-0.18)
        signal=_adjudicated_company_news_signal(price,causal)
        self.assertLess(signal.score,0)
        self.assertEqual(signal.source,'news_causal_intelligence')
        self.assertIsNone(_adjudicated_company_news_signal(price,event(accepted=False)))

    def test_full_pipeline_rejected_headlines_do_not_move_forecast_or_action(self):
        from test_news_causal_intelligence_v1073 import _tw_frame
        from orchestrator import orchestrate
        from models import NewsItem
        baseline=orchestrate(_tw_frame(),news_items=[])
        raw=NewsItem('GoogleNewsTW','2026-07-28 12:00',0.9,
                     'tw_company timestamp_provenance=v1079 timestamp_status=unverified timestamp_verified=0',
                     '泛用記憶體 massive growth and record earnings','')
        noisy=orchestrate(_tw_frame(),news_items=[raw]*30)
        self.assertFalse(baseline.stopped);self.assertFalse(noisy.stopped)
        self.assertEqual(noisy.final_t1,baseline.final_t1)
        self.assertEqual(noisy.decision_snapshot.action_code,baseline.decision_snapshot.action_code)
        self.assertFalse(next(x for x in noisy.decision_snapshot.evidence if x.family=='event').accepted)

    def test_full_pipeline_post_close_event_reaches_formal_wait_gate(self):
        from test_news_causal_intelligence_v1073 import _tw_frame, TAIPEI
        from news_causal_intelligence_v1073 import analyze_news_causality
        from orchestrator import orchestrate
        from models import NewsItem
        from datetime import datetime
        price=_tw_frame()
        news=[NewsItem('Official','2026-07-28 16:00',0.18,'tw_company_bullish_earnings',
                       '泛用記憶體 Q2 earnings beat estimates and raises guidance','')]
        causal=analyze_news_causality(price,news,now=datetime(2026,7,28,17,tzinfo=TAIPEI))
        self.assertEqual(causal['entry_gate'],'block_until_first_reaction')
        with patch('orchestrator.analyze_news_causality',return_value=causal):
            result=orchestrate(price,news_items=news)
        self.assertFalse(result.stopped)
        gate=result.decision_snapshot.to_dict()['reasoning']['cross_module_gate']
        self.assertTrue(gate['event_reaction_wait'])
        self.assertFalse(gate['entry_qualified'])
        self.assertNotEqual(result.decision_snapshot.action_code,'BUY')
        self.assertFalse(result.decision_snapshot.to_dict()['entry']['conditional_next_session']['eligible'])


if __name__=='__main__':unittest.main()
