import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from research_evidence_v1118 import evidence_from_forecast, bounded_context
from ai_research_v1118 import research_candidates, validate_research
import autonomous_paper_v1118 as worker
import memory_store


class ResearchEvidenceTests(unittest.TestCase):
    def test_etf_has_no_company_eps_and_news_does_not_claim_full_article(self):
        raw=NS(title='Verified title',source='Official',time='2026-11-04',link='https://example.com',tag='')
        f=NS(ticker=NS(asset_type='etf'),news_items=[raw],price_frame=NS(price_date='2026-11-04',context={'fundamental':{'eps':999},'news_causal_v1073':{'selected_keys':[]}}))
        with patch('news_causal_intelligence_v1073.select_effective_news_items',return_value=[raw]):
            result=evidence_from_forecast(f)
        self.assertFalse(result['financials']['applicable']);self.assertNotIn('eps',result['financials'])
        self.assertFalse(result['news'][0]['article_body_available'])
        self.assertLessEqual(len(bounded_context({'news':[{'title':'x'*10000}]*100})['news']),4)

    def test_one_api_request_receives_bounded_evidence_and_respects_event_hold(self):
        context={'financials':{'eps':2,'eps_kind':'quarterly'},'news':[{'title':'Guidance cut','source':'Official'}],
                 'event_assessment':{'entry_gate':'block_until_first_reaction'}}
        candidate={'ticker':'A','execution_status':'ACTIONABLE','data_quality':90,'research_context':context}
        answer={'market_view':'cautious','decisions':[{'ticker':'A','verdict':'PAPER_REVIEW','confidence':90,'thesis':'case','bear_case':'risk','risk_trigger':'price','missing_evidence':'cash flow'}]}
        calls=[]
        class Response:
            def raise_for_status(self):pass
            def json(self):return {'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(answer)}]}]}
        def request(url,**kwargs):
            calls.append(kwargs)
            supplied=json.loads(kwargs['json']['input'])[0]['research_context']
            self.assertEqual(supplied['financials']['eps'],2)
            self.assertEqual(supplied['news'][0]['title'],'Guidance cut')
            return Response()
        result=research_candidates([candidate],api_key='test',requester=request)
        self.assertEqual(len(calls),1);self.assertEqual(result['decisions'][0]['verdict'],'WATCH')

    def test_research_uses_one_frozen_analysis_per_ticker(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(memory_store,'MEMORY_DIR',Path(folder)),patch.object(memory_store,'_post_memory_write'):
            Path(folder,'tino_market_scanner_v156_today.csv').write_text('ticker,execution_status,recommendation_score,data_quality\nA,ACTIONABLE,90,90\n')
            analyzed=[];logged=[]
            f=NS(stopped=False,price_frame=NS(context={'fundamental':{'eps':2,'eps_kind':'quarterly'}}),news_items=[])
            def analyze(symbol):analyzed.append(symbol);return f
            def log(forecast):logged.append(forecast);return {'id':'f1','ticker':'A','public_decision_snapshot':{'action_code':'BLOCK'}}
            def research(rows):
                self.assertEqual(len(logged),1)
                self.assertEqual(rows[0]['research_context']['financials']['eps'],2)
                return {'status':'completed','decisions':[]}
            result=worker.run_cycle(folder,analyze=analyze,log=log,research=research,capture=lambda *a,**kw:{'status':'recorded','experiment_status':'OBSERVE'},settle=lambda:{})
            self.assertEqual(analyzed,['A']);self.assertEqual(len(logged),1)
            self.assertEqual(result['paper_buy'],0)


if __name__=='__main__':unittest.main()
