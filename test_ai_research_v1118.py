import unittest
from unittest.mock import patch
import json

from ai_research_v1118 import validate_research, research_candidates


class ResearchContracts(unittest.TestCase):
    def test_ai_cannot_invent_or_override_wait_gate(self):
        candidates=[{'ticker':'A','execution_status':'ACTIONABLE','data_quality':'90'}, {'ticker':'B','execution_status':'WAIT_PULLBACK','data_quality':'90'}, {'ticker':'C','execution_status':'ACTIONABLE','data_quality':'50'}]
        decision=lambda ticker,confidence=90:{'ticker':ticker,'verdict':'PAPER_REVIEW','confidence':confidence,
                      'thesis':'價格與營收有支持','bear_case':'估值可能過高','risk_trigger':'跌破支撐','missing_evidence':'等待法人'}
        result=validate_research({'market_view':'審慎','decisions':[decision('A'),decision('B'),decision('C'),decision('UNKNOWN'),decision('A')]},candidates)
        self.assertEqual([(d['ticker'],d['verdict']) for d in result['decisions']], [('A','PAPER_REVIEW'),('B','WATCH'),('C','WATCH')])

    def test_weak_or_unconfigured_research_abstains(self):
        candidate={'ticker':'A','execution_status':'ACTIONABLE'}
        weak={'ticker':'A','verdict':'PAPER_REVIEW','confidence':40,'thesis':'猜測','bear_case':'','risk_trigger':'','missing_evidence':''}
        self.assertEqual(validate_research({'market_view':'','decisions':[weak]},[candidate])['decisions'][0]['verdict'],'WATCH')
        with patch.dict('os.environ', {'OPENAI_API_KEY':''}):
            self.assertEqual(research_candidates([candidate],api_key='')['status'],'unconfigured')

    def test_structured_api_response_is_grounded(self):
        candidate={'ticker':'A','execution_status':'ACTIONABLE'}
        payload={'market_view':'無需勉強進場','decisions':[{'ticker':'A','verdict':'REJECT','confidence':90,
                 'thesis':'估值不利','bear_case':'可能上漲','risk_trigger':'波動','missing_evidence':'財報'}]}
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(payload)}]}]}
        def requester(url, **kwargs):
            self.assertEqual(url,'https://api.openai.com/v1/responses')
            self.assertEqual(kwargs['json']['store'],False)
            self.assertEqual(kwargs['json']['text']['format']['type'],'json_schema')
            return Response()
        result=research_candidates([candidate],api_key='test',requester=requester)
        self.assertEqual(result['decisions'][0]['verdict'],'REJECT')

if __name__=='__main__': unittest.main()
