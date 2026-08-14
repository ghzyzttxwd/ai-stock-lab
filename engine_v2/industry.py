from __future__ import annotations

import math
import time
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


def _industry_code(value) -> str:
    text=str(value or '').strip()
    if text.endswith('.SI'):
        text=text[:-3]
    return text


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


def _bounded_retry(label: str, fn, *, attempts: int = 2, timeout_seconds: int = 45, delay_seconds: float = 3.0):
    """Bounded retry for transient provider responses; never loop indefinitely."""
    last=None
    for attempt in range(1, attempts+1):
        try:
            return bounded_call(timeout_seconds,fn,f'{label} attempt {attempt}/{attempts}')
        except Exception as exc:
            last=exc
            print(f'[V2 INDUSTRY] {label} attempt {attempt}/{attempts} failed: {type(exc).__name__}: {exc}')
            if attempt < attempts:
                time.sleep(delay_seconds)
    raise RuntimeError(f'{label} failed after {attempts} attempts: {type(last).__name__}: {last}') from last


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
        normalized_code=_industry_code(code)
        metrics[normalized_code]={
            'industry_code':normalized_code,
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


def _load_l1_catalog(ak) -> tuple[list[dict], dict]:
    """Load the SW L1 directory without depending on the realtime SWS endpoint.

    The directory only supplies stable industry codes/names; live prices are not required here.
    Prefer the independent Legulegu-backed AKShare interface and keep SWS realtime as fallback.
    """
    errors=[]
    try:
        info=bounded_call(30,ak.sw_index_first_info,'SW L1 directory Legulegu')
        need={'行业代码','行业名称'}
        missing=sorted(need-{str(x) for x in info.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        rows=[]
        for _,r in info.iterrows():
            code=_industry_code(r.get('行业代码'))
            name=str(r.get('行业名称') or '').strip()
            if code and name:
                rows.append({
                    'industry_code':code,
                    'industry_name':name,
                    'declared_components':int(_num(r.get('成份个数'),0) or 0),
                })
        if len(rows)<25:
            raise RuntimeError(f'too few SW L1 industries: {len(rows)}')
        return rows,{'source':'legulegu-sw-index-first-info','errors':errors}
    except Exception as exc:
        errors.append(f'legulegu={type(exc).__name__}: {exc}')

    try:
        realtime=bounded_call(35,lambda:ak.index_realtime_sw(symbol='一级行业'),'SW L1 directory SWS fallback')
        need={'指数代码','指数名称'}
        missing=sorted(need-{str(x) for x in realtime.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        rows=[]
        for _,r in realtime.iterrows():
            code=_industry_code(r.get('指数代码'))
            name=str(r.get('指数名称') or '').strip()
            if code and name:
                rows.append({'industry_code':code,'industry_name':name,'declared_components':0})
        if len(rows)<25:
            raise RuntimeError(f'too few SW L1 industries: {len(rows)}')
        return rows,{'source':'sws-realtime-fallback','errors':errors}
    except Exception as exc:
        errors.append(f'sws={type(exc).__name__}: {exc}')
        raise RuntimeError('SW L1 directory unavailable; '+' | '.join(errors)) from exc


def load_sw_l1_snapshot(trade_date: str) -> dict:
    import akshare as ak
    td=date.fromisoformat(trade_date)
    catalog,catalog_meta=_load_l1_catalog(ak)
    analysis=_bounded_retry(
        'SW L1 daily analysis',
        lambda:ak.index_analysis_daily_sw(
            symbol='一级行业',
            start_date=(td-timedelta(days=100)).strftime('%Y%m%d'),
            end_date=td.strftime('%Y%m%d'),
        ),
        attempts=2,
        timeout_seconds=45,
        delay_seconds=3.0,
    )
    industries=build_industry_scores(analysis,trade_date)
    latest_asof=max((x.get('asof') or '' for x in industries.values()),default='')
    if len(industries)<25 or latest_asof!=trade_date:
        raise RuntimeError(
            f'Shenwan industry analysis incomplete industries={len(industries)} latest={latest_asof} trade_date={trade_date}'
        )

    stock_map={}
    failures=[]
    successful_declared=0
    for row in catalog:
        idx=row['industry_code']
        name=row['industry_name']
        try:
            df=bounded_call(25,lambda i=idx:ak.index_component_sw(symbol=i),f'SW component {idx}')
            successful_declared += int(row.get('declared_components') or 0)
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

    if len(stock_map)<2500 or len(failures)>2:
        raise RuntimeError(
            f'Shenwan industry snapshot incomplete industries={len(industries)} stocks={len(stock_map)} failures={len(failures)}'
        )
    return {
        'trade_date':trade_date,
        'taxonomy':'Shenwan L1',
        'industries':industries,
        'stock_map':stock_map,
        'counts':{
            'industries':len(industries),
            'mainboard_stocks':len(stock_map),
            'component_failures':len(failures),
        },
        'source':{
            'catalog':catalog_meta,
            'analysis':'sws-index-analysis-daily-bounded-retry-2',
            'components':'sws-index-component',
            'successful_declared_components':successful_declared,
            'failure_sample':failures[:2],
        },
    }
