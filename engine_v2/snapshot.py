from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from .fundamentals import load_point_in_time_fundamentals
from .industry import load_sw_l1_snapshot
from .regime import classify_market_regime


def _num(value, default=None):
    try:
        if value is None:
            return default
        if isinstance(value,str):
            value=value.replace(',','').replace('%','').strip()
        x=float(value)
        return default if math.isnan(x) or math.isinf(x) else x
    except (TypeError,ValueError):
        return default


def _raw_code(symbol: str) -> str:
    digits=''.join(ch for ch in str(symbol) if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ''


def _is_mainboard(code: str) -> bool:
    return code.startswith(('600','601','603','605','000','001','002','003'))


def _activity_map(df) -> dict[str,object]:
    if df is None or df.empty or not {'item','value'}.issubset({str(x) for x in df.columns}):
        return {}
    return {str(r['item']).strip():r['value'] for _,r in df.iterrows()}


def _activity_value(items: dict, aliases: tuple[str,...]) -> float | None:
    for alias in aliases:
        if alias in items:
            return _num(items[alias])
    for key,value in items.items():
        if any(alias in key for alias in aliases):
            return _num(value)
    return None


def _latest_highlow(df, trade_date: str) -> dict:
    work=df.copy()
    work['_date']=work['date'].astype(str).str[:10]
    work=work[work['_date']<=trade_date].sort_values('_date')
    if work.empty:
        raise RuntimeError('high/low breadth has no row on or before trade date')
    row=work.iloc[-1]
    return {
        'date':str(row['_date']),
        'high20':int(_num(row.get('high20'),0) or 0),
        'low20':int(_num(row.get('low20'),0) or 0),
        'high60':int(_num(row.get('high60'),0) or 0),
        'low60':int(_num(row.get('low60'),0) or 0),
    }


def _index_features(ak, trade_date: str) -> dict:
    specs=('sh000300','sh000905','sh000852')
    per=[]
    for symbol in specs:
        df=ak.stock_zh_index_daily_tx(symbol=symbol)
        if df is None or df.empty:
            raise RuntimeError(f'empty index history {symbol}')
        work=df.copy(); work['_date']=work['date'].astype(str).str[:10]
        work=work[work['_date']<=trade_date].sort_values('_date')
        closes=[_num(x) for x in work['close'].tolist()]
        closes=[x for x in closes if x is not None and x>0]
        if len(closes)<61:
            raise RuntimeError(f'insufficient index history {symbol}: {len(closes)}')
        c=closes[-1]
        r3=c/closes[-4]-1
        r20=c/closes[-21]-1
        r60=c/closes[-61]-1
        ma20=sum(closes[-20:])/20
        ma60=sum(closes[-60:])/60
        dd20=c/max(closes[-20:])-1
        trend=50+200*r20+90*r60
        if c>ma20>ma60: trend+=10
        elif c<ma20<ma60: trend-=10
        trend=max(0,min(100,trend))
        per.append({'symbol':symbol,'r3':r3,'r20':r20,'r60':r60,'drawdown20':dd20,'trend_score':trend})
    return {
        'index_trend_score':round(sum(x['trend_score'] for x in per)/len(per),2),
        'index_drawdown20':round(sum(x['drawdown20'] for x in per)/len(per),6),
        'index_return3':round(sum(x['r3'] for x in per)/len(per),6),
        'indices':per,
    }


def _sentiment_snapshot(ak, trade_date: str) -> dict:
    d8=trade_date.replace('-','')
    up=ak.stock_zt_pool_em(date=d8)
    broken=ak.stock_zt_pool_zbgc_em(date=d8)
    down=ak.stock_zt_pool_dtgc_em(date=d8)
    main_up=[]
    for _,r in up.iterrows():
        code=str(r.get('代码','')).zfill(6)
        if not _is_mainboard(code): continue
        main_up.append({
            'code':code,'name':str(r.get('名称','')).strip(),
            'boards':int(_num(r.get('连板数'),1) or 1),
            'breaks':int(_num(r.get('炸板次数'),0) or 0),
            'short_industry':str(r.get('所属行业') or '').strip(),
            'first_seal':str(r.get('首次封板时间') or ''),
            'last_seal':str(r.get('最后封板时间') or ''),
        })
    main_broken=[]
    for _,r in broken.iterrows():
        code=str(r.get('代码','')).zfill(6)
        if _is_mainboard(code):
            main_broken.append({'code':code,'name':str(r.get('名称','')).strip(),'short_industry':str(r.get('所属行业') or '').strip()})
    main_down=[]
    for _,r in down.iterrows():
        code=str(r.get('代码','')).zfill(6)
        if _is_mainboard(code):
            main_down.append({'code':code,'name':str(r.get('名称','')).strip()})
    theme_counts=Counter(x['short_industry'] for x in main_up if x['short_industry'])
    theme_max=max(theme_counts.values(),default=1)
    for x in main_up:
        concentration=theme_counts.get(x['short_industry'],0)/theme_max
        x['leader_score']=round(min(100,52+9*x['boards']-4*x['breaks']),2)
        x['theme_score']=round(45+45*concentration,2)
    return {
        'limit_up_count':len(main_up),
        'broken_limit_count':len(main_broken),
        'limit_down_count':len(main_down),
        'limit_break_rate':round(len(main_broken)/max(1,len(main_up)+len(main_broken)),4),
        'max_board':max((x['boards'] for x in main_up),default=0),
        'top_short_industries':theme_counts.most_common(12),
        'limit_up':main_up,
        'broken_limit':main_broken,
        'limit_down':main_down,
    }


def _preselect_style_union(market_rows: list[dict], fundamentals: dict, industry: dict, sentiment: dict, max_union: int=180) -> dict:
    fund=fundamentals['stocks']; swmap=industry['stock_map']
    rows=[]
    by_code={}
    for row in market_rows:
        code=_raw_code(row.get('code') or row.get('raw_code'))
        if not _is_mainboard(code): continue
        item={**row,'raw_code':code}
        f=fund.get(code,{})
        sw=swmap.get(code,{})
        item.update({
            'quality_score':f.get('quality_score'),
            'cashflow_score':f.get('cashflow_score'),
            'fundamental_ready':bool(f.get('fundamental_ready')),
            'financial_distress':bool(f.get('financial_distress')),
            'fundamental_period':fundamentals['selected_period'] if f else None,
            'industry':sw.get('industry_name'),
            'industry_code':sw.get('industry_code'),
            'industry_score':sw.get('industry_score'),
        })
        rows.append(item); by_code[code]=item

    def top(key,n,reverse=True,eligible=lambda x:True):
        vals=[x for x in rows if eligible(x)]
        return sorted(vals,key=key,reverse=reverse)[:n]

    pools={}
    pools['A']=top(
        lambda x:(x.get('quality_score') or -1, x.get('risk',50), x.get('amount',0)),45,
        eligible=lambda x:x.get('fundamental_ready') and not x.get('financial_distress'),
    )
    pools['B']=top(
        lambda x:((x.get('industry_score') or 0)*0.7 + (x.get('r60_snapshot') or 0)*100*0.3, x.get('amount',0)),55,
        eligible=lambda x:(x.get('industry_score') or 0)>=45,
    )
    limit_codes={x['code'] for x in sentiment['limit_up']}|{x['code'] for x in sentiment['broken_limit']}
    c_limit=[by_code[c] for c in limit_codes if c in by_code]
    c_extra=top(
        lambda x:((x.get('industry_score') or 0),(x.get('pctChg') or 0),x.get('amount',0)),25,
        eligible=lambda x:(x.get('industry_score') or 0)>=55,
    )
    pools['C']=(c_limit+c_extra)[:55]
    pools['D']=top(
        lambda x:((x.get('industry_score') or 50)+(x.get('quality_score') or 50)+(x.get('r60_snapshot') or 0)*100,x.get('amount',0)),55,
    )
    pools['L']=top(
        lambda x:((x.get('quality_score') or -1)-max(0,(_num(x.get('peTTM'),0) or 0)-12)*0.8-max(0,(_num(x.get('pbMRQ'),0) or 0)-1.5)*3,x.get('amount',0)),50,
        eligible=lambda x:x.get('fundamental_ready') and not x.get('financial_distress'),
    )
    pools['LIQUID']=top(lambda x:x.get('amount',0),35)

    membership=defaultdict(set)
    chosen={}
    for label,pool in pools.items():
        for x in pool:
            code=x['raw_code']; chosen[code]=x; membership[code].add(label)
    if len(chosen)>max_union:
        # Preserve names selected by multiple independent styles, then liquidity.
        ordered=sorted(chosen.values(),key=lambda x:(len(membership[x['raw_code']]),x.get('amount',0)),reverse=True)
        chosen={x['raw_code']:x for x in ordered[:max_union]}
    output=[]
    for code,item in chosen.items():
        output.append({**item,'v2_preselect_for':sorted(membership[code])})
    output.sort(key=lambda x:(len(x['v2_preselect_for']),x.get('amount',0)),reverse=True)
    return {
        'counts':{k:len(v) for k,v in pools.items()},
        'union_count':len(output),
        'rows':output,
    }


def build_snapshot(requested_date: str | None=None) -> dict:
    import akshare as ak
    from engine.real_market import AKShareMarket

    market=AKShareMarket()
    req=requested_date or date.today().isoformat()
    trade_date=market.latest_trade_date(req)

    activity=ak.stock_market_activity_legu()
    items=_activity_map(activity)
    up=_activity_value(items,('上涨','上涨家数'))
    down=_activity_value(items,('下跌','下跌家数'))
    flat=_activity_value(items,('平盘','平盘家数')) or 0
    active=(up or 0)+(down or 0)+flat
    if up is None or down is None or active<1000:
        raise RuntimeError(f'market activity breadth unusable: items={list(items)[:20]}')

    highlow=_latest_highlow(ak.stock_a_high_low_statistics(symbol='all'),trade_date)
    sentiment=_sentiment_snapshot(ak,trade_date)
    idx=_index_features(ak,trade_date)
    regime_features={
        **idx,
        'advancer_ratio':up/active,
        'new_high_ratio':highlow['high20']/active,
        'new_low_ratio':highlow['low20']/active,
        'limit_up_count':sentiment['limit_up_count'],
        'limit_down_count':sentiment['limit_down_count'],
        'limit_break_rate':sentiment['limit_break_rate'],
    }
    regime=classify_market_regime(regime_features)

    industry=load_sw_l1_snapshot(trade_date)
    industry_by_code={code:x['industry_name'] for code,x in industry['stock_map'].items()}
    fundamentals=load_point_in_time_fundamentals(trade_date,industry_by_code=industry_by_code)
    market_rows=market.snapshot()
    preselect=_preselect_style_union(market_rows,fundamentals,industry,sentiment)

    source=str(market_rows[0].get('source') or 'unknown') if market_rows else 'unknown'
    return {
        'snapshot_version':'v2-shadow-data-0.1',
        'trade_date':trade_date,
        'requested_date':req,
        'source_notes':{
            'stock_snapshot':source,
            'industry':'Shenwan L1',
            'fundamentals':'Eastmoney performance report filtered by announcement date and mainboard',
            'sentiment':'Eastmoney limit pools + Legulegu market activity',
            'execution':'not executed; this artifact cannot write any ledger',
        },
        'market':{
            'regime':{'label':regime.label,'score':regime.score,'confidence':regime.confidence,'reasons':list(regime.reasons)},
            'features':regime_features,
            'activity':{'up':up,'down':down,'flat':flat,'active':active,'advancer_ratio':round(up/active,4),'reported_date':str(items.get('统计日期') or '')[:10]},
            'high_low':highlow,
            'sentiment':{k:v for k,v in sentiment.items() if k not in {'limit_up','broken_limit','limit_down'}},
        },
        'industry':{
            'taxonomy':industry['taxonomy'],'counts':industry['counts'],
            'top_strength':sorted(industry['industries'].values(),key=lambda x:x['industry_score'],reverse=True)[:12],
            'bottom_strength':sorted(industry['industries'].values(),key=lambda x:x['industry_score'])[:8],
        },
        'fundamentals':{
            'selected_period':fundamentals['selected_period'],
            'current_period':fundamentals['current_period'],
            'current_mainboard_announced':fundamentals['current_mainboard_announced'],
            'previous_mainboard_announced':fundamentals['previous_mainboard_announced'],
            'current_coverage_vs_previous':fundamentals['current_coverage_vs_previous'],
            'score_period_reason':fundamentals['score_period_reason'],
            'scored_stocks':len(fundamentals['stocks']),
            'fresh_report_events':len(fundamentals['fresh_report_events']),
            'not_yet_used':fundamentals['not_yet_used'],
        },
        'preselection':preselect,
        'safety':{
            'writes_ledgers':False,
            'calls_sol':False,
            'historical_backtest_grade':False,
            'note':'forward point-in-time shadow input; no trading is performed by this module',
        },
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--date')
    ap.add_argument('--output',required=True)
    args=ap.parse_args()
    snap=build_snapshot(args.date)
    text=json.dumps(snap,ensure_ascii=False,indent=2)
    path=Path(args.output); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text+'\n',encoding='utf-8')
    print(json.dumps({
        'trade_date':snap['trade_date'],
        'regime':snap['market']['regime'],
        'industry_counts':snap['industry']['counts'],
        'fundamentals':snap['fundamentals'],
        'preselection_union':snap['preselection']['union_count'],
        'stock_source':snap['source_notes']['stock_snapshot'],
    },ensure_ascii=False,indent=2))


if __name__=='__main__': main()
