from __future__ import annotations

import argparse
import json
import math
import signal
import time
from datetime import date, timedelta
from pathlib import Path


def _timeout(seconds, fn):
    if not hasattr(signal, 'SIGALRM'):
        return fn()
    old = signal.getsignal(signal.SIGALRM)
    def alarm(_s, _f):
        raise TimeoutError(f'provider call exceeded {seconds}s')
    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _code(value):
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            value = int(value)
    text = str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    digits = ''.join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    return digits[-6:].zfill(6)


def _is_main_board(code: str) -> bool:
    return code.startswith(('600','601','603','605','000','001','002','003'))


def validate_sw_industry(trade_date: str) -> dict:
    import akshare as ak

    td = date.fromisoformat(trade_date)
    result = {
        'trade_date': trade_date,
        'taxonomy': 'Shenwan L1',
        'index_catalog': {},
        'daily_analysis': {},
        'history_sample': {},
        'constituents': {},
        'ready': False,
    }

    started = time.monotonic()
    try:
        catalog = _timeout(60, lambda: ak.index_realtime_sw(symbol='一级行业'))
        cols = {str(x) for x in catalog.columns}
        need = {'指数代码','指数名称'}
        if not need.issubset(cols):
            raise RuntimeError(f'missing columns {sorted(need-cols)}')
        catalog = catalog.copy()
        result['index_catalog'] = {
            'status': 'PASS' if 25 <= len(catalog) <= 50 else 'DEGRADED',
            'rows': len(catalog),
            'latency_s': round(time.monotonic()-started,2),
            'sample': catalog[['指数代码','指数名称']].head(5).to_dict('records'),
        }
    except Exception as e:
        catalog = None
        result['index_catalog'] = {
            'status':'FAIL','rows':0,'latency_s':round(time.monotonic()-started,2),
            'error':f'{type(e).__name__}: {e}',
        }

    # One call can provide all SW L1 daily analysis rows over a date range.
    started = time.monotonic()
    try:
        start=(td-timedelta(days=100)).strftime('%Y%m%d')
        end=td.strftime('%Y%m%d')
        analysis = _timeout(90, lambda: ak.index_analysis_daily_sw(symbol='一级行业', start_date=start, end_date=end))
        need={'指数代码','指数名称','发布日期','收盘指数','涨跌幅','换手率','市盈率','市净率'}
        cols={str(x) for x in analysis.columns}
        if not need.issubset(cols):
            raise RuntimeError(f'missing columns {sorted(need-cols)}')
        dates=analysis['发布日期'].astype(str).str[:10]
        valid=analysis[dates <= trade_date].copy()
        valid['_date']=valid['发布日期'].astype(str).str[:10]
        latest=valid['_date'].max() if not valid.empty else None
        latest_rows=valid[valid['_date']==latest] if latest else valid.iloc[0:0]
        result['daily_analysis']={
            'status':'PASS' if latest==trade_date and len(latest_rows)>=25 else 'DEGRADED',
            'rows':len(valid),
            'latest_date':latest,
            'latest_industries':len(latest_rows),
            'unique_dates':int(valid['_date'].nunique()) if not valid.empty else 0,
            'latency_s':round(time.monotonic()-started,2),
        }
    except Exception as e:
        analysis=None
        result['daily_analysis']={
            'status':'FAIL','rows':0,'latency_s':round(time.monotonic()-started,2),
            'error':f'{type(e).__name__}: {e}',
        }

    # Verify historical series on one real L1 industry index independently.
    sample_code=None
    sample_name=None
    if catalog is not None and not catalog.empty:
        sample_code=str(catalog.iloc[0]['指数代码']).strip()
        sample_name=str(catalog.iloc[0]['指数名称']).strip()
    started=time.monotonic()
    try:
        if not sample_code:
            raise RuntimeError('no sample industry code')
        hist=_timeout(60, lambda: ak.index_hist_sw(symbol=sample_code, period='day'))
        need={'代码','日期','收盘','开盘','最高','最低','成交量','成交额'}
        cols={str(x) for x in hist.columns}
        if not need.issubset(cols):
            raise RuntimeError(f'missing columns {sorted(need-cols)}')
        dates=hist['日期'].astype(str).str[:10]
        valid=hist[dates <= trade_date]
        latest=str(valid.iloc[-1]['日期'])[:10] if not valid.empty else None
        result['history_sample']={
            'status':'PASS' if latest==trade_date and len(valid)>=60 else 'DEGRADED',
            'industry_code':sample_code,'industry_name':sample_name,
            'rows':len(valid),'latest_date':latest,
            'latency_s':round(time.monotonic()-started,2),
        }
    except Exception as e:
        result['history_sample']={
            'status':'FAIL','industry_code':sample_code,'industry_name':sample_name,
            'rows':0,'latency_s':round(time.monotonic()-started,2),
            'error':f'{type(e).__name__}: {e}',
        }

    # Build one authoritative current stock -> SW L1 map by enumerating only ~30 L1 indices.
    mapping={}
    duplicates={}
    failures=[]
    total_rows=0
    started=time.monotonic()
    if catalog is not None and not catalog.empty:
        for _, row in catalog.iterrows():
            idx=str(row['指数代码']).strip()
            name=str(row['指数名称']).strip()
            try:
                cons=_timeout(30, lambda i=idx: ak.index_component_sw(symbol=i))
                need={'证券代码','证券名称','计入日期'}
                cols={str(x) for x in cons.columns}
                if not need.issubset(cols):
                    raise RuntimeError(f'missing columns {sorted(need-cols)}')
                total_rows += len(cons)
                for _, c in cons.iterrows():
                    code=_code(c.get('证券代码'))
                    if not code:
                        continue
                    item={'industry_code':idx,'industry_name':name,'included_date':str(c.get('计入日期'))[:10]}
                    if code in mapping and mapping[code]['industry_code'] != idx:
                        duplicates.setdefault(code,[mapping[code]]).append(item)
                    else:
                        mapping[code]=item
            except Exception as e:
                failures.append({'industry_code':idx,'industry_name':name,'error':f'{type(e).__name__}: {e}'})

    mainboard={k:v for k,v in mapping.items() if _is_main_board(k)}
    result['constituents']={
        'status':'PASS' if len(mapping)>=4500 and len(failures)<=2 and len(duplicates)<=10 else 'DEGRADED' if len(mapping)>=3000 else 'FAIL',
        'industry_indices_attempted':0 if catalog is None else len(catalog),
        'component_rows_total':total_rows,
        'unique_stocks':len(mapping),
        'mainboard_stocks':len(mainboard),
        'duplicate_cross_industry_stocks':len(duplicates),
        'failed_industries':len(failures),
        'failure_sample':failures[:5],
        'sample_mapping':dict(list(sorted(mainboard.items()))[:5]),
        'latency_s':round(time.monotonic()-started,2),
        'scope':'current_membership_with_inclusion_date',
        'history_warning':'计入日期可用于审计当前成分的进入时间，但退出历史仍需单独归档；前向影子盘从启用日起每日保存快照。',
    }

    result['ready']=all(result[x].get('status')=='PASS' for x in ('index_catalog','daily_analysis','history_sample','constituents'))
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--date',required=True)
    ap.add_argument('--output')
    args=ap.parse_args()
    report=validate_sw_industry(args.date)
    text=json.dumps(report,ensure_ascii=False,indent=2)
    print(text)
    if args.output:
        p=Path(args.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
