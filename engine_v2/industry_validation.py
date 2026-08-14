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


def _normalize_code(value) -> str | None:
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


def _sw_index_code(value) -> str:
    text = str(value).strip()
    return text.split('.')[0]


def validate_industry_layer(trade_date: str) -> dict:
    import akshare as ak

    result = {
        'trade_date': trade_date,
        'primary_taxonomy': '申万一级行业',
        'sw_strength': {},
        'sw_membership': {},
        'sw_history': {},
        'ths_fallback': {},
        'ready': False,
    }

    # Primary: one coherent SW taxonomy for both industry strength and stock membership.
    realtime = None
    started = time.monotonic()
    try:
        realtime = _timeout(60, lambda: ak.index_realtime_sw(symbol='一级行业'))
        required = {'指数代码', '指数名称', '昨收盘', '最新价', '成交额', '成交量'}
        missing = sorted(required - {str(x) for x in realtime.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        realtime = realtime.copy()
        realtime['_code'] = realtime['指数代码'].map(_sw_index_code)
        realtime['_ret'] = (realtime['最新价'].astype(float) / realtime['昨收盘'].astype(float) - 1.0) * 100.0
        result['sw_strength']['realtime_level1'] = {
            'status': 'PASS' if len(realtime) >= 25 else 'DEGRADED',
            'rows': len(realtime),
            'latency_s': round(time.monotonic() - started, 2),
            'top5': [
                {'code': str(r['_code']), 'name': str(r['指数名称']), 'return_pct': round(float(r['_ret']), 2)}
                for _, r in realtime.sort_values('_ret', ascending=False).head(5).iterrows()
            ],
        }
    except Exception as e:
        result['sw_strength']['realtime_level1'] = {
            'status': 'FAIL', 'rows': 0,
            'latency_s': round(time.monotonic() - started, 2),
            'error': f'{type(e).__name__}: {e}',
        }

    info = None
    started = time.monotonic()
    try:
        info = _timeout(60, ak.sw_index_first_info)
        required = {'行业代码', '行业名称', '成份个数'}
        missing = sorted(required - {str(x) for x in info.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        info = info.copy()
        info['_code'] = info['行业代码'].map(_sw_index_code)
        result['sw_membership']['level1_directory'] = {
            'status': 'PASS' if len(info) >= 25 else 'DEGRADED',
            'rows': len(info),
            'declared_components': int(info['成份个数'].fillna(0).astype(float).sum()),
            'latency_s': round(time.monotonic() - started, 2),
        }
    except Exception as e:
        result['sw_membership']['level1_directory'] = {
            'status': 'FAIL', 'rows': 0, 'declared_components': 0,
            'latency_s': round(time.monotonic() - started, 2),
            'error': f'{type(e).__name__}: {e}',
        }

    membership: dict[str, str] = {}
    duplicate_assignments: Counter[str] = Counter()
    component_failures: list[dict] = []
    per_industry: list[dict] = []
    if info is not None and not info.empty:
        for _, row in info.iterrows():
            idx_code = str(row['_code'])
            idx_name = str(row['行业名称']).strip()
            started = time.monotonic()
            try:
                df = _timeout(35, lambda c=idx_code: ak.index_component_sw(symbol=c))
                required = {'证券代码', '证券名称', '计入日期'}
                missing = sorted(required - {str(x) for x in df.columns})
                if missing:
                    raise RuntimeError(f'missing columns: {missing}')
                count = 0
                for value in df['证券代码'].tolist():
                    code = _normalize_code(value)
                    if not code:
                        continue
                    count += 1
                    if code in membership and membership[code] != idx_name:
                        duplicate_assignments[code] += 1
                    membership[code] = idx_name
                per_industry.append({'code': idx_code, 'name': idx_name, 'rows': count, 'latency_s': round(time.monotonic() - started, 2)})
            except Exception as e:
                component_failures.append({'code': idx_code, 'name': idx_name, 'error': f'{type(e).__name__}: {e}'})

    declared = int(result['sw_membership'].get('level1_directory', {}).get('declared_components', 0) or 0)
    unique_count = len(membership)
    coverage_vs_declared = unique_count / declared if declared > 0 else 0.0
    mapping_status = (
        'PASS' if unique_count >= 4000 and len(component_failures) <= 2 and coverage_vs_declared >= 0.82
        else 'DEGRADED' if unique_count >= 2500 and len(component_failures) <= 6
        else 'FAIL'
    )
    result['sw_membership']['components'] = {
        'status': mapping_status,
        'unique_stocks': unique_count,
        'declared_components': declared,
        'coverage_vs_declared': round(coverage_vs_declared, 4),
        'duplicate_cross_industry_assignments': len(duplicate_assignments),
        'failed_industries': component_failures,
        'slowest_industries': sorted(per_industry, key=lambda x: x['latency_s'], reverse=True)[:5],
        'sample': [{'code': k, 'industry': membership[k]} for k in sorted(membership)[:8]],
        'scope': 'current_forward_mapping',
    }

    # Verify that the SW live index directory and component directory use the same taxonomy.
    rt_codes = set(realtime['_code'].astype(str)) if realtime is not None and not realtime.empty else set()
    info_codes = set(info['_code'].astype(str)) if info is not None and not info.empty else set()
    code_overlap = rt_codes & info_codes
    taxonomy_ratio = len(code_overlap) / len(info_codes) if info_codes else 0.0
    result['sw_membership']['taxonomy_consistency'] = {
        'status': 'PASS' if taxonomy_ratio >= 0.95 else 'DEGRADED' if taxonomy_ratio >= 0.8 else 'FAIL',
        'realtime_codes': len(rt_codes),
        'directory_codes': len(info_codes),
        'overlap_codes': len(code_overlap),
        'ratio': round(taxonomy_ratio, 4),
        'missing_from_realtime': sorted(info_codes - rt_codes)[:10],
    }

    # Historical point-in-time classification: do not project today's membership backward.
    started = time.monotonic()
    try:
        hist = _timeout(90, ak.stock_industry_clf_hist_sw)
        required = {'symbol', 'start_date', 'industry_code', 'update_time'}
        missing = sorted(required - {str(x) for x in hist.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        symbols = {_normalize_code(x) for x in hist['symbol'].tolist()}
        symbols.discard(None)
        result['sw_history'] = {
            'status': 'PASS' if len(hist) >= 9000 and len(symbols) >= 3500 else 'DEGRADED',
            'rows': len(hist),
            'unique_stocks': len(symbols),
            'min_start_date': str(hist['start_date'].astype(str).min())[:10] if len(hist) else None,
            'max_start_date': str(hist['start_date'].astype(str).max())[:10] if len(hist) else None,
            'latency_s': round(time.monotonic() - started, 2),
            'scope': 'historical_point_in_time_membership',
        }
    except Exception as e:
        result['sw_history'] = {
            'status': 'FAIL', 'rows': 0, 'unique_stocks': 0,
            'latency_s': round(time.monotonic() - started, 2),
            'error': f'{type(e).__name__}: {e}',
        }

    # Independent THS industry flow remains a fallback / cross-check, not the stock-industry mapping source.
    started = time.monotonic()
    try:
        flow = _timeout(60, lambda: ak.stock_fund_flow_industry(symbol='即时'))
        required = {'行业', '行业指数', '行业-涨跌幅', '净额', '公司家数'}
        missing = sorted(required - {str(x) for x in flow.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        result['ths_fallback'] = {
            'status': 'PASS' if len(flow) >= 50 else 'DEGRADED',
            'rows': len(flow),
            'latency_s': round(time.monotonic() - started, 2),
            'role': 'independent_strength_crosscheck_only',
        }
    except Exception as e:
        result['ths_fallback'] = {
            'status': 'FAIL', 'rows': 0,
            'latency_s': round(time.monotonic() - started, 2),
            'role': 'independent_strength_crosscheck_only',
            'error': f'{type(e).__name__}: {e}',
        }

    strength_ok = result['sw_strength'].get('realtime_level1', {}).get('status') == 'PASS'
    directory_ok = result['sw_membership'].get('level1_directory', {}).get('status') == 'PASS'
    components_ok = result['sw_membership'].get('components', {}).get('status') == 'PASS'
    taxonomy_ok = result['sw_membership'].get('taxonomy_consistency', {}).get('status') == 'PASS'
    history_ok = result['sw_history'].get('status') in {'PASS', 'DEGRADED'}
    result['ready'] = strength_ok and directory_ok and components_ok and taxonomy_ok
    result['historical_membership_ready'] = history_ok
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
