"""Bounded public evidence from the already-computed forecast; no extra fetches."""
from collections.abc import Mapping
import math


def mapping(value):return value if isinstance(value,Mapping) else {}


def clean(value):
    if value is None or isinstance(value,(bool,int)):return value
    if isinstance(value,float):return value if math.isfinite(value) else None
    return str(value)[:180]


def evidence_from_forecast(forecast):
    frame=getattr(forecast,'price_frame',None);ctx=mapping(getattr(frame,'context',{}))
    f=mapping(ctx.get('fundamental'));ticker=getattr(forecast,'ticker',None)
    etf=str(getattr(ticker,'asset_type','')).lower()=='etf' or f.get('asset_type')=='etf'
    financial={'applicable':not etf}
    if not etf:
        financial.update({k:clean(f.get(k)) for k in (
            'source','quarterly_source_date','eps','eps_kind','eps_basis','revenue','revenue_kind',
            'revenue_yoy','quarterly_gross_margin','quarterly_free_cashflow','quality_period_consistent',
            'forward_pe','next_earnings')})
        financial['independently_verified']=f.get('statement_verified') is True
        financial['forward_pe_period_verified']=False
    from news_causal_intelligence_v1073 import select_effective_news_items
    causal=mapping(ctx.get('news_causal_v1073'))
    # No adjudication is evidence missing, never permission to use raw headlines.
    items=select_effective_news_items(getattr(forecast,'news_items',[]) or [],causal) if 'selected_keys' in causal else []
    news=[]
    for item in items[:4]:
        news.append({'title':str(getattr(item,'title',''))[:180],
                     'source':str(getattr(item,'source',''))[:60],
                     'published_at':str(getattr(item,'time',''))[:48],
                     'url':str(getattr(item,'link',''))[:180],
                     'article_body_available':False})
    snapshot=getattr(forecast,'decision_snapshot',None)
    snap=snapshot.to_dict() if callable(getattr(snapshot,'to_dict',None)) else {}
    return {'price_date':str(getattr(frame,'price_date',''))[:10],
            'financials':financial,'news':news,
            'event_assessment':{k:clean(causal.get(k)) for k in ('causal_state','entry_gate','company_text','company_score','selected_count')},
            'formal_action':snap.get('action_code'),'formal_reason':str(snap.get('reason',''))[:180],
            'missing_evidence':['News input contains headlines, not full articles.',
                                'No independent filing verification unless explicitly marked.']}


def bounded_context(value):
    """Whitelist nested evidence; CSV/user fields cannot inject arbitrary payloads."""
    ctx=mapping(value)
    return {'price_date':str(ctx.get('price_date',''))[:10],
            'financials':{str(k)[:48]:clean(v) for k,v in list(mapping(ctx.get('financials')).items())[:18]},
            'news':[{k:clean(mapping(row).get(k)) for k in ('title','source','published_at','url','article_body_available')}
                    for row in list(ctx.get('news') or [])[:4] if isinstance(row,Mapping)],
            'event_assessment':{str(k)[:48]:clean(v) for k,v in list(mapping(ctx.get('event_assessment')).items())[:6]},
            'formal_action':clean(ctx.get('formal_action')),'formal_reason':clean(ctx.get('formal_reason')),
            'missing_evidence':[str(x)[:120] for x in list(ctx.get('missing_evidence') or [])[:4]]}
