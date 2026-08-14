from __future__ import annotations

import math
from datetime import date, timedelta

from .provider import bounded_call


def _num(value, default=None):
    try:
        if value is None:
            return default
        x=float(value)
        return default if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return default


def _stock_code(value) -> str | None:
    if value is None:
        return None
    if isinstance(value,float):
        if math.isnan(value): return None
        if value.is_integer(): value=int(value)
    text=str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit(): text=text[:-2]
    digits=''.join(ch for ch in text if ch.isdigit())
    if not digits: return None
    return digits[-6:].zfill(6)


def _is_mainboard(code: str | None) -> bool:
    return bool(code) and code.startswith(('600','601','603','605','000','001','002','003'))


def _rank(values: dict[str,float | None]) -> dict[str,float | None]:
    valid=sorted((float(v),k) for k,v in values.items() if v is not None)
    out={k:None for k in values}
    n=len(valid)
    if not n: return out
    i=0
    while i<n:
        j=i+1
        while j<n and valid[j][0]==valid[i][0]: j+=1
        avg=(i+j-1)/2
        score=50.0 if n==1 else 100.0*avg/(n-1)
        for _,k in valid[i:j]: out[k]=round(score,2)
        i=j
    return out


def _period_return(values: list[float], sessions: int) -> float | None:
    if len(values)<=sessions or values[-1-sessions]<=0:
        return None
    return values[-1]/values[-1-sessions]-1


def build_industry_scores(analysis, trade_date: str) -> dict[str,dict]:
    """Convert SW L1 daily analysis into transparent relative-strength scores."""
    if analysis is None or analysis.empty:
        return {}
    work=analysis.copy()
    work['_date']=work['发布日期'].astype(str).str[:10]
    work=work[work['_date']<=trade_date]
    metrics={}
    for code,group in work.groupby(work['指数代码'].astype(str)):
        g=group.sort_values('_date')
        closes=[_num(x) for x in g['收盘指数'].tolist()]
        closes=[x for x in closes if x is not None and x>0]
        if len(closes)<21:
            continue
        latest=g.iloc[-1]
        metrics[str(code)]={
            'industry_code':str(code),
            'industry_name':str(latest['指数名称']).strip(),
            'asof':str(latest['_date']),
            'r20':_period_return(closes,20),
            'r60':_period_return(closes,60),
            'day_pct':(_num(latest.get('涨跌幅'),0.0) or 0.0)/100.0,
            'turnover_pct':_num(latest.get('换手率')),
            'pe':_num(latest.get('市盈率')),
            'pb':_num(latest.get('市净率')),
        }
    ranks={
        'r20':_rank({k:v['r20'] for k,v in metrics.items()}),
        'r60':_rank({k:v['r60'] for k,v in metrics.items()}),
        'day':_rank({k:v['day_pct'] for k,v in metrics.items()}),
        'turn':_rank({k:v['turnover_pct'] for k,v in metrics.items()}),
    }
    for code,item in metrics.items():
        parts=[
            (ranks['r20'].get(code),0.45),
            (ranks['r60'].get(code),0.30),
            (ranks['day'].get(code),0.15),
            (ranks['turn'].get(code),0.10),
        ]
        usable=[(v,w) for v,w in parts if v is not None]
        item['industry_score']=round(sum(v*w for v,w in usable)/sum(w for _,w in usable),2) if usable else 50.0
        item['score_components']={
            'r20_rank':ranks['r20'].get(code),
            'r60_rank':ranks['r60'].get(code),
            'day_rank':ranks['day'].get(code),
            'turnover_rank':ranks['turn'].get(code),
        }
    return metrics


def load_sw_l1_snapshot(trade_date: str) -> dict:
    import akshare as ak
    td=date.fromisoformat(trade_date)
    catalog=bounded_call(45,lambda:ak.index_realtime_sw(symbol='一级行业'),'SW L1 catalog')
    analysis=bounded_call(
        90,
        lambda:ak.index_analysis_daily_sw(
            symbol='一级行业',
            start_date=(td-timedelta(days=100)).strftime('%Y%m%d'),
            end_date=td.strftime('%Y%m%d'),
        ),
        'SW L1 daily analysis',
    )
    industries=build_industry_scores(analysis,trade_date)
    stock_map={}
    failures=[]
    for _,row in catalog.iterrows():
        idx=str(row['指数代码']).strip()
        name=str(row['指数名称']).strip()
        try:
            df=bounded_call(25,lambda i=idx:ak.index_component_sw(symbol=i),f'SW component {idx}')
            for _,r in df.iterrows():
                code=_stock_code(r.get('证券代码'))
                if not _is_mainboard(code):
                    continue
                included=str(r.get('计入日期') or '')[:10]
                if included and included!='NaT' and included>trade_date:
                    continue
                stock_map[code]={
                    'industry_code':idx,
                    'industry_name':name,
                    'included_date':included if included and included!='NaT' else None,
                    'industry_score':industries.get(idx,{}).get('industry_score'),
                }
        except Exception as exc:
            failures.append({'industry_code':idx,'industry_name':name,'error':f'{type(exc).__name__}: {exc}'})
    if len(industries)<25 or len(stock_map)<2500 or failures:
        raise RuntimeError(
            f'Shenwan industry snapshot incomplete industries={len(industries)} stocks={len(stock_map)} failures={len(failures)}'
        )
    return {
        'trade_date':trade_date,
        'taxonomy':'Shenwan L1',
        'industries':industries,
        'stock_map':stock_map,
        'counts':{'industries':len(industries),'mainboard_stocks':len(stock_map)},
    }
