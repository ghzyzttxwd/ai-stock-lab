from __future__ import annotations

import argparse
import json
import math
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


def _normalize_code(value) -> str | None:
    """Normalize numeric/string stock codes without allowing pandas' 1.0 vs '000001' split."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            value = int(value)
    text = str(value).strip()
    if text.endswith('.0'):
        head = text[:-2]
        if head.isdigit():
            text = head
    digits = ''.join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    if len(digits) > 6:
        digits = digits[-6:]
    return digits.zfill(6)


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
        code = _normalize_code(row.get('股票代码'))
        industry = str(row.get('所处行业', '')).strip()
        if code and industry and industry.lower() != 'nan':
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
        'taxonomy': {},
        'ready': False,
    }

    summary = None
    flow = None
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
                'sample_codes': sorted(mapping)[:5],
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

    current_map = maps[0] if maps else {}
    previous_map = maps[1] if len(maps) > 1 else {}
    overlap = set(current_map) & set(previous_map)
    merged = dict(previous_map)
    merged.update(current_map)
    result['membership']['merged'] = {
        'mapped_stocks': len(merged),
        'current_previous_overlap': len(overlap),
        'current_not_in_previous': len(set(current_map) - set(previous_map)),
        'status': 'PASS' if len(merged) >= 2500 else 'DEGRADED' if len(merged) >= 1000 else 'FAIL',
        'scope': 'forward_asof_announcements_only',
        'warning': 'industry labels come from disclosed report tables; historical membership backtest remains separate',
    }

    ths_labels = set()
    if summary is not None and not summary.empty and '板块' in summary.columns:
        ths_labels.update(str(x).strip() for x in summary['板块'].tolist() if str(x).strip())
    if flow is not None and not flow.empty and '行业' in flow.columns:
        ths_labels.update(str(x).strip() for x in flow['行业'].tolist() if str(x).strip())
    direct_match = sum(1 for industry in merged.values() if industry in ths_labels)
    ratio = direct_match / len(merged) if merged else 0.0
    unmatched_top = {}
    for industry in merged.values():
        if industry not in ths_labels:
            unmatched_top[industry] = unmatched_top.get(industry, 0) + 1
    result['taxonomy'] = {
        'ths_unique_labels': len(ths_labels),
        'report_unique_labels': len(set(merged.values())),
        'direct_match_stocks': direct_match,
        'direct_match_ratio': round(ratio, 4),
        'largest_unmatched_labels': sorted(unmatched_top.items(), key=lambda x: x[1], reverse=True)[:12],
        'status': 'PASS' if ratio >= 0.65 else 'DEGRADED' if ratio >= 0.35 else 'FAIL',
    }

    strength_ok = any(x.get('status') == 'PASS' for x in result['strength'].values())
    membership_ok = result['membership']['merged']['status'] == 'PASS'
    taxonomy_ok = result['taxonomy']['status'] == 'PASS'
    result['ready'] = strength_ok and membership_ok and taxonomy_ok
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
