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


def is_mainboard_code(code: str | None) -> bool:
    if not code:
        return False
    return code.startswith(('600','601','603','605','000','001','002','003'))


def _clean_text(value) -> str | None:
    if value is None:
        return None
    text=str(value).strip()
    if not text or text.lower() in {'nan','none','null','--','-'}:
        return None
    return text


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
            'name':_clean_text(r.get('股票简称')),
            'industry':_clean_text(r.get('所处行业')),
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


def _overlap_block(current: dict, previous: dict) -> dict:
    cur_codes=set(current); prev_codes=set(previous)
    intersection=cur_codes & prev_codes
    missing=sorted(cur_codes-prev_codes)
    return {
        'current_count':len(cur_codes),
        'previous_count':len(prev_codes),
        'intersection':len(intersection),
        'current_not_previous':len(missing),
        'current_overlap_ratio':round(len(intersection)/len(cur_codes),4) if cur_codes else 0.0,
        'current_not_previous_samples':[current[x] for x in missing[:30]],
    }


def validate_financial_overlap(trade_date: str) -> dict:
    import akshare as ak
    td=date.fromisoformat(trade_date)
    periods=[date(td.year,6,30),date(td.year,3,31)] if td >= date(td.year,6,30) else [date(td.year,3,31),date(td.year-1,12,31)]
    data={}
    stores={}
    for p in periods:
        started=time.monotonic()
        df=_timeout(90,lambda q=p:ak.stock_yjbb_em(date=q.strftime('%Y%m%d')))
        rr=_rows(df,trade_date)
        all_by_code={x['code']:x for x in rr}
        main=[x for x in rr if is_mainboard_code(x['code'])]
        main_by_code={x['code']:x for x in main}
        stores[p.isoformat()]={'all':all_by_code,'main':main_by_code}
        data[p.isoformat()]={
            'raw_rows':len(df),
            'announced_rows':len(rr),
            'unique_codes':len(all_by_code),
            'mainboard_announced_rows':len(main_by_code),
            'mainboard_with_industry':sum(1 for x in main_by_code.values() if x['industry']),
            'prefix_counts':dict(Counter(_prefix(x['code']) for x in rr)),
            'mainboard_code_sample':sorted(main_by_code)[:10],
            'latency_s':round(time.monotonic()-started,2),
        }

    cur=stores[periods[0].isoformat()]
    prev=stores[periods[1].isoformat()]
    all_overlap=_overlap_block(cur['all'],prev['all'])
    main_overlap=_overlap_block(cur['main'],prev['main'])

    # A newly reported mainboard issuer should almost always have existed in the previous quarter.
    # A small allowance remains for IPOs/relistings/corporate events. The point is to reject the
    # massive off-universe pollution seen in the unfiltered report endpoint, not require 100% identity.
    main_ready=(
        main_overlap['current_count'] >= 100
        and main_overlap['current_overlap_ratio'] >= 0.95
        and data[periods[1].isoformat()]['mainboard_announced_rows'] >= 2500
    )

    result={
        'trade_date':trade_date,
        'periods':[p.isoformat() for p in periods],
        'reports':data,
        'all_market_overlap':all_overlap,
        'mainboard_overlap':main_overlap,
        'ready_for_quality_merge':main_ready,
        'quality_merge_rule':(
            'Only Shanghai/Shenzhen main-board codes are eligible. Build a previous-quarter baseline, '
            'then overlay current-quarter rows only when their announcement date is <= decision date. '
            'Industry classification is not taken from this report table; V2 uses Shenwan L1.'
        ),
    }
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--output')
    args=ap.parse_args(); report=validate_financial_overlap(args.date)
    text=json.dumps(report,ensure_ascii=False,indent=2); print(text)
    if args.output:
        p=Path(args.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text+'\n',encoding='utf-8')

if __name__=='__main__': main()
