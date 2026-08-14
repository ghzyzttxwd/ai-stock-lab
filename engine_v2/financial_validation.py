from __future__ import annotations

import argparse
import json
import math
import signal
import time
from collections import Counter
from datetime import date
from pathlib import Path


def _timeout(seconds, fn):
    if not hasattr(signal, 'SIGALRM'):
        return fn()
    old=signal.getsignal(signal.SIGALRM)
    def alarm(_s,_f): raise TimeoutError(f'provider call exceeded {seconds}s')
    signal.signal(signal.SIGALRM,alarm); signal.setitimer(signal.ITIMER_REAL,seconds)
    try: return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL,0); signal.signal(signal.SIGALRM,old)


def normalize_code(value):
    if value is None: return None
    if isinstance(value,float):
        if math.isnan(value): return None
        if value.is_integer(): value=int(value)
    text=str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit(): text=text[:-2]
    digits=''.join(ch for ch in text if ch.isdigit())
    if not digits: return None
    return digits[-6:].zfill(6)


def _rows(df, asof: str):
    out=[]
    if df is None or df.empty: return out
    for _,r in df.iterrows():
        announced=str(r.get('最新公告日期',''))[:10]
        if not ('2000-01-01' <= announced <= asof):
            continue
        code=normalize_code(r.get('股票代码'))
        if not code: continue
        out.append({
            'code':code,
            'raw_code':str(r.get('股票代码')),
            'name':str(r.get('股票简称','')).strip(),
            'industry':str(r.get('所处行业','')).strip(),
            'announced':announced,
        })
    return out


def _prefix(code):
    if code.startswith(('000','001','002','003')): return 'SZ_MAIN'
    if code.startswith(('300','301')): return 'CYB'
    if code.startswith(('600','601','603','605')): return 'SH_MAIN'
    if code.startswith('688'): return 'STAR'
    if code.startswith(('8','9')): return 'BSE_OR_OTHER'
    if code.startswith(('200','900')): return 'B_SHARE'
    return code[:3]


def validate_financial_overlap(trade_date: str) -> dict:
    import akshare as ak
    td=date.fromisoformat(trade_date)
    periods=[date(td.year,6,30),date(td.year,3,31)] if td >= date(td.year,6,30) else [date(td.year,3,31),date(td.year-1,12,31)]
    data={}
    raw={}
    for p in periods:
        started=time.monotonic()
        df=_timeout(90,lambda q=p:ak.stock_yjbb_em(date=q.strftime('%Y%m%d')))
        raw[p.isoformat()]=df
        rr=_rows(df,trade_date)
        by_code={x['code']:x for x in rr}
        by_name={x['name']:x for x in rr if x['name'] and x['name'].lower() not in {'nan','none'}}
        data[p.isoformat()]={
            'raw_rows':len(df),'announced_rows':len(rr),'unique_codes':len(by_code),'unique_names':len(by_name),
            'prefix_counts':dict(Counter(_prefix(x['code']) for x in rr)),
            'code_sample':sorted(by_code)[:10],
            '_by_code':by_code,'_by_name':by_name,
            'latency_s':round(time.monotonic()-started,2),
        }
    cur=data[periods[0].isoformat()]; prev=data[periods[1].isoformat()]
    cur_codes=set(cur['_by_code']); prev_codes=set(prev['_by_code'])
    cur_names=set(cur['_by_name']); prev_names=set(prev['_by_name'])
    missing_codes=sorted(cur_codes-prev_codes)
    missing_names=sorted(cur_names-prev_names)
    code_missing_but_name_present=[]
    for code in missing_codes:
        item=cur['_by_code'][code]
        old=prev['_by_name'].get(item['name'])
        if old:
            code_missing_but_name_present.append({'current':item,'previous_same_name':old})
    suspicious=[cur['_by_code'][x] for x in missing_codes[:30]]
    for v in data.values():
        v.pop('_by_code',None); v.pop('_by_name',None)
    result={
        'trade_date':trade_date,
        'periods':[p.isoformat() for p in periods],
        'reports':data,
        'overlap':{
            'code_intersection':len(cur_codes & prev_codes),
            'current_not_previous_code':len(missing_codes),
            'name_intersection':len(cur_names & prev_names),
            'current_not_previous_name':len(missing_names),
            'code_missing_but_same_name_present':len(code_missing_but_name_present),
            'same_name_code_mismatch_samples':code_missing_but_name_present[:20],
            'current_not_previous_samples':suspicious,
        },
    }
    # Do not declare quality factors usable until current-period issuer overlap makes sense.
    expected=min(len(cur_codes),1000)
    ratio=len(cur_codes & prev_codes)/len(cur_codes) if cur_codes else 0.0
    result['overlap']['current_code_overlap_ratio']=round(ratio,4)
    result['ready_for_quality_merge']=ratio>=0.90 and len(cur_codes)>=100
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--output')
    args=ap.parse_args(); report=validate_financial_overlap(args.date)
    text=json.dumps(report,ensure_ascii=False,indent=2); print(text)
    if args.output:
        p=Path(args.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text+'\n',encoding='utf-8')

if __name__=='__main__': main()
