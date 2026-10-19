import copy
from types import SimpleNamespace as NS
import unittest
from fundamental_technical_risk import assess
from test_jarvis_v1117 import fixture
from ui_jarvis_v1117 import command_html


def frame(healthy=True, price=90):
    return NS(last=price, recent_closes=[100.] * 60, price_date='2026-10-19',
              truth=NS(accepted=True, fallback=False), context={'fundamental': {
                  'accepted': True, 'quarterly_source_date': '2026-08-31',
                  'quality_period_consistent': True, 'statement_verified': True, 'gaap_eps': 2 if healthy else -2,
                  'quarterly_gross_margin': 50, 'quarterly_free_cashflow': 100,
                  'eps': 24.67, 'eps_kind': 'ttm', 'eps_basis': 'gaap_ttm', 'forward_pe': 4.25}})


def test_quadrants():
    for healthy,price,expected in [(True,110,'成長與趨勢同步'), (True,90,'基本面強、技術轉弱'),
        (False,110,'股價與基本面背離'), (False,90,'基本面與技術雙弱')]:
        assert expected in assess(frame(healthy,price))['interpretation']


def test_eps_growth_is_not_health():
    f=frame(); del f.context['fundamental']['quarterly_free_cashflow']
    result=assess(f)
    assert result['health']=='待確認'
    assert result['below_ma60'] is True
    assert '不據此判低估' in ' '.join(result['checks'])


def test_bad_period():
    for period in ['2025-01-01','2027-01-01','unknown']:
        f=frame(); f.context['fundamental']['quarterly_source_date']=period
        assert assess(f)['health']=='待確認'


def test_rejected_synthetic_and_short_history_not_used():
    f=frame(); f.truth.fallback=True
    assert assess(f)['ma60'] is None
    f.truth.fallback=False; f.recent_closes=[100]*19
    assert assess(f)['ma20'] is None
    f.recent_closes=[100]*59+[float('nan')]
    assert assess(f)['ma60'] is None


def test_read_only_and_original_stop_preserved():
    f,p=fixture(); f.price_frame=frame(); before=copy.deepcopy((f,p))
    html=command_html(f,p)
    assert '原交易風控：法人／外資流向偏空' in html
    assert '72.37' in html and '不改寫原交易資格' in html
    assert f==before[0] and p==before[1]
    assert assess(f.price_frame)['execution_authority'] is False


def test_etf_not_company_health():
    f=frame(); f.context['fundamental']['asset_type']='etf'
    assert assess(f)['health']=='不適用（ETF）'


def test_period_mismatch():
    f=frame(); f.context['fundamental']['quality_period_consistent']=False
    assert assess(f)['health']=='待確認'

def test_cashflow_period_alignment():
    from unittest.mock import patch
    from fundamental_growth_guard import fetch_us_quarterly_metrics, build_us_fundamental_context
    import json
    def payload_row(key, day, value):
        return {key: [{"asOfDate": day, "reportedValue": {"raw": value}}]}
    # All input dates must match; capital expenditure can use either sign convention.
    rows=[payload_row("quarterlyTotalRevenue","2026-08-31",100),
          payload_row("quarterlyDilutedEPS","2026-08-31",2),
          payload_row("quarterlyGrossProfit","2026-08-31",40),
          payload_row("quarterlyOperatingCashFlow","2026-08-31",30),
          payload_row("quarterlyCapitalExpenditure","2026-08-31",-10)]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps({"timeseries":{"result":rows}}).encode()
    with patch.dict('os.environ', {'TINO_OFFLINE_TEST':'0'}), patch('urllib.request.urlopen',return_value=Response()):
        metrics=fetch_us_quarterly_metrics('TEST_ALIGNED')
        assert metrics['quarterly_free_cashflow']==20
        ctx=build_us_fundamental_context({'_quarterly_metrics':metrics},'2026-10-19')
        assert ctx['quality_period_consistent'] is True
        rows[-1]=payload_row('quarterlyCapitalExpenditure','2026-05-31',-10)
        mismatched=fetch_us_quarterly_metrics('TEST_MISMATCHED')
        assert 'quarterly_free_cashflow' not in mismatched


def check_unverified_source_cannot_claim_health():
    f=frame(); del f.context['fundamental']['statement_verified']
    assert assess(f)['health']=='待確認'

class RiskTests(unittest.TestCase):
    pass

for name, function in list(globals().items()):
    if name.startswith(("test_", "check_")) and callable(function):
        setattr(RiskTests, name.replace("check_", "test_"), lambda self, fn=function: fn())

if __name__ == '__main__':
    unittest.main()
