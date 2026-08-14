from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import date
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


def _quarter_ends_before(day: date) -> tuple[date, date]:
    ends = [date(day.year - 1, 12, 31), date(day.year, 3, 31), date(day.year, 6, 30), date(day.year, 9, 30), date(day.year, 12, 31)]
    valid = [x for x in ends if x <= day]
    current = max(valid)
    previous = max(x for x in ends if x < current)
    return current, previous


def _announced_industry_map(df, asof: str) -> dict[str, str]:
    if df is None or df.empty:
        return {}
    need = {'股票代码', '所处行业', '最新公告日期'}
    if not need.issubset({str(x) for x in df.columns}):
        return {}
    work = df.copy()
    work['_announce'] = work['最新公告日期'].astype(str).str[:10]
    work = work[(work['_announce'] >= '2000-01-01') & (work['_announce'] <= asof)]
    out = {}
    for _, row in work.iterrows():
        code = str(row.get('股票代码', '')).zfill(6)
        industry = str(row.get('所处行业', '')).strip()
        if len(code) == 6 and industry and industry.lower() != 'nan':
            out[code] = industry
    return out


def validate_industry_layer(trade_date: str) -> dict:
    import akshare as ak

    td = date.fromisoformat(trade_date)
    current_q, previous_q = _quarter_ends_before(td)
    result = {
        'trade_date': trade_date,
        'strength': {},
        'membership': {},
        'ready': False,
    }

    # Same taxonomy/provider pair: summary gives breadth/price action, fund-flow gives money flow.
    started = time.monotonic()
    try:
        summary = _timeout(60, ak.stock_board_industry_summary_ths)
        required = {'板块', '涨跌幅', '总成交额', '净流入', '上涨家数', '下跌家数'}
        missing = sorted(required - {str(x) for x in summary.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        result['strength']['ths_summary'] = {
            'status': 'PASS' if len(summary) >= 50 else 'DEGRADED',
            'rows': len(summary),
            'latency_s': round(time.monotonic() - started, 2),
        }
    except Exception as e:
        result['strength']['ths_summary'] = {
            'status': 'FAIL', 'rows': 0,
            'latency_s': round(time.monotonic() - started, 2),
            'error': f'{type(e).__name__}: {e}',
        }

    started = time.monotonic()
    try:
        flow = _timeout(60, lambda: ak.stock_fund_flow_industry(symbol='即时'))
        required = {'行业', '行业指数', '行业-涨跌幅', '净额', '公司家数'}
        missing = sorted(required - {str(x) for x in flow.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        result['strength']['ths_flow'] = {
            'status': 'PASS' if len(flow) >= 50 else 'DEGRADED',
            'rows': len(flow),
            'latency_s': round(time.monotonic() - started, 2),
        }
    except Exception as e:
        result['strength']['ths_flow'] = {
            'status': 'FAIL', 'rows': 0,
            'latency_s': round(time.monotonic() - started, 2),
            'error': f'{type(e).__name__}: {e}',
        }

    # Membership baseline: use only report rows that were publicly announced by the decision date.
    # Current quarter wins; previous quarter fills stocks that have not yet reported the current quarter.
    maps = []
    for label, period in [('current_report', current_q), ('previous_report', previous_q)]:
        started = time.monotonic()
        try:
            df = _timeout(75, lambda p=period: ak.stock_yjbb_em(date=p.strftime('%Y%m%d')))
            mapping = _announced_industry_map(df, trade_date)
            maps.append(mapping)
            result['membership'][label] = {
                'status': 'PASS' if len(mapping) >= 500 else 'DEGRADED',
                'period': period.isoformat(),
                'raw_rows': len(df),
                'announced_mapped_rows': len(mapping),
                'latency_s': round(time.monotonic() - started, 2),
            }
        except Exception as e:
            maps.append({})
            result['membership'][label] = {
                'status': 'FAIL', 'period': period.isoformat(), 'raw_rows': 0,
                'announced_mapped_rows': 0,
                'latency_s': round(time.monotonic() - started, 2),
                'error': f'{type(e).__name__}: {e}',
            }

    merged = dict(maps[1] if len(maps) > 1 else {})
    if maps:
        merged.update(maps[0])
    result['membership']['merged'] = {
        'mapped_stocks': len(merged),
        'status': 'PASS' if len(merged) >= 2500 else 'DEGRADED' if len(merged) >= 1000 else 'FAIL',
        'scope': 'forward_asof_announcements_only',
        'warning': 'industry labels come from disclosed report tables; historical membership backtest remains separate',
    }

    strength_ok = any(x.get('status') == 'PASS' for x in result['strength'].values())
    membership_ok = result['membership']['merged']['status'] == 'PASS'
    result['ready'] = strength_ok and membership_ok
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--output')
    args = ap.parse_args()
    report = validate_industry_layer(args.date)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
