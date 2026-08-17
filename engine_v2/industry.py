from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from .provider import bounded_call


def _num(value, default=None):
    try:
        if value is None:
            return default
        x = float(value)
        return default if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return default


def _stock_code(value) -> str | None:
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


def _industry_code(value) -> str:
    text = str(value or '').strip()
    if text.endswith('.SI'):
        text = text[:-3]
    return text


def _is_mainboard(code: str | None) -> bool:
    return bool(code) and code.startswith(('600', '601', '603', '605', '000', '001', '002', '003'))


def _assign_membership(stock_map: dict, duplicates: dict, code: str, item: dict) -> None:
    previous = stock_map.get(code)
    if previous and previous.get('industry_code') != item.get('industry_code'):
        duplicates.setdefault(code, {
            'code': code,
            'industry_codes': [previous.get('industry_code')],
            'industry_names': [previous.get('industry_name')],
        })
        if item.get('industry_code') not in duplicates[code]['industry_codes']:
            duplicates[code]['industry_codes'].append(item.get('industry_code'))
            duplicates[code]['industry_names'].append(item.get('industry_name'))
    stock_map[code] = item


def _rank(values: dict[str, float | None]) -> dict[str, float | None]:
    valid = sorted((float(v), k) for k, v in values.items() if v is not None)
    out = {k: None for k in values}
    n = len(valid)
    if not n:
        return out
    i = 0
    while i < n:
        j = i + 1
        while j < n and valid[j][0] == valid[i][0]:
            j += 1
        avg = (i + j - 1) / 2
        score = 50.0 if n == 1 else 100.0 * avg / (n - 1)
        for _, k in valid[i:j]:
            out[k] = round(score, 2)
        i = j
    return out


def _period_return(values: list[float], sessions: int) -> float | None:
    if len(values) <= sessions or values[-1 - sessions] <= 0:
        return None
    return values[-1] / values[-1 - sessions] - 1


def _history_metric(frame, industry_code: str, industry_name: str, trade_date: str) -> dict:
    if frame is None or frame.empty:
        raise RuntimeError(f'empty SW index history for {industry_code}')
    work = frame.copy()
    required = {'日期', '收盘'}
    missing = required - set(work.columns)
    if missing:
        raise RuntimeError(f'SW index history {industry_code} missing columns: {sorted(missing)}')
    work['_date'] = work['日期'].astype(str).str[:10]
    work = work[work['_date'] <= trade_date].sort_values('_date')
    if work.empty or str(work.iloc[-1]['_date']) != trade_date:
        latest = str(work.iloc[-1]['_date']) if not work.empty else None
        raise RuntimeError(f'SW index history stale {industry_code}: latest={latest} trade_date={trade_date}')
    closes = [_num(x) for x in work['收盘'].tolist()]
    closes = [x for x in closes if x is not None and x > 0]
    if len(closes) < 61:
        raise RuntimeError(f'SW index history too short {industry_code}: {len(closes)} sessions')
    amounts = [_num(x) for x in work['成交额'].tolist()] if '成交额' in work.columns else []
    latest_amount = amounts[-1] if amounts else None
    recent_amounts = [x for x in amounts[-20:] if x is not None and x > 0]
    amount_activity = None
    if latest_amount is not None and latest_amount > 0 and recent_amounts:
        baseline = sum(recent_amounts) / len(recent_amounts)
        if baseline > 0:
            amount_activity = latest_amount / baseline
    return {
        'industry_code': industry_code,
        'industry_name': industry_name,
        'asof': trade_date,
        'r20': _period_return(closes, 20),
        'r60': _period_return(closes, 60),
        'day_pct': closes[-1] / closes[-2] - 1 if len(closes) >= 2 and closes[-2] > 0 else None,
        'amount_activity': amount_activity,
        'turnover_pct': None,
        'pe': None,
        'pb': None,
    }


def _score_industries(metrics: dict[str, dict]) -> dict[str, dict]:
    ranks = {
        'r20': _rank({k: v.get('r20') for k, v in metrics.items()}),
        'r60': _rank({k: v.get('r60') for k, v in metrics.items()}),
        'day': _rank({k: v.get('day_pct') for k, v in metrics.items()}),
        'activity': _rank({k: v.get('amount_activity') for k, v in metrics.items()}),
    }
    for code, item in metrics.items():
        parts = [
            (ranks['r20'].get(code), 0.45),
            (ranks['r60'].get(code), 0.30),
            (ranks['day'].get(code), 0.15),
            (ranks['activity'].get(code), 0.10),
        ]
        usable = [(v, w) for v, w in parts if v is not None]
        item['industry_score'] = round(
            sum(v * w for v, w in usable) / sum(w for _, w in usable), 2
        ) if usable else 50.0
        item['score_components'] = {
            'r20_rank': ranks['r20'].get(code),
            'r60_rank': ranks['r60'].get(code),
            'day_rank': ranks['day'].get(code),
            'activity_rank': ranks['activity'].get(code),
        }
    return metrics


def build_industry_scores(analysis, trade_date: str) -> dict[str, dict]:
    """Legacy pure helper retained for tests and old cached artifacts."""
    if analysis is None or analysis.empty:
        return {}
    work = analysis.copy()
    work['_date'] = work['发布日期'].astype(str).str[:10]
    work = work[work['_date'] <= trade_date]
    metrics = {}
    for code, group in work.groupby(work['指数代码'].astype(str)):
        g = group.sort_values('_date')
        closes = [_num(x) for x in g['收盘指数'].tolist()]
        closes = [x for x in closes if x is not None and x > 0]
        if len(closes) < 21:
            continue
        latest = g.iloc[-1]
        normalized_code = _industry_code(code)
        turnover = _num(latest.get('换手率'))
        metrics[normalized_code] = {
            'industry_code': normalized_code,
            'industry_name': str(latest['指数名称']).strip(),
            'asof': str(latest['_date']),
            'r20': _period_return(closes, 20),
            'r60': _period_return(closes, 60),
            'day_pct': (_num(latest.get('涨跌幅'), 0.0) or 0.0) / 100.0,
            'amount_activity': turnover,
            'turnover_pct': turnover,
            'pe': _num(latest.get('市盈率')),
            'pb': _num(latest.get('市净率')),
        }
    return _score_industries(metrics)


def _load_l1_catalog(ak) -> tuple[list[dict], dict]:
    errors = []
    try:
        info = bounded_call(30, ak.sw_index_first_info, 'SW L1 directory Legulegu')
        need = {'行业代码', '行业名称'}
        missing = sorted(need - {str(x) for x in info.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        rows = []
        for _, r in info.iterrows():
            code = _industry_code(r.get('行业代码'))
            name = str(r.get('行业名称') or '').strip()
            if code and name:
                rows.append({
                    'industry_code': code,
                    'industry_name': name,
                    'declared_components': int(_num(r.get('成份个数'), 0) or 0),
                })
        if len(rows) < 25:
            raise RuntimeError(f'too few SW L1 industries: {len(rows)}')
        return rows, {'source': 'legulegu-sw-index-first-info', 'errors': errors}
    except Exception as exc:
        errors.append(f'legulegu={type(exc).__name__}: {exc}')
    try:
        realtime = bounded_call(35, lambda: ak.index_realtime_sw(symbol='一级行业'), 'SW L1 directory SWS fallback')
        need = {'指数代码', '指数名称'}
        missing = sorted(need - {str(x) for x in realtime.columns})
        if missing:
            raise RuntimeError(f'missing columns: {missing}')
        rows = []
        for _, r in realtime.iterrows():
            code = _industry_code(r.get('指数代码'))
            name = str(r.get('指数名称') or '').strip()
            if code and name:
                rows.append({'industry_code': code, 'industry_name': name, 'declared_components': 0})
        if len(rows) < 25:
            raise RuntimeError(f'too few SW L1 industries: {len(rows)}')
        return rows, {'source': 'sws-realtime-fallback', 'errors': errors}
    except Exception as exc:
        errors.append(f'sws={type(exc).__name__}: {exc}')
        raise RuntimeError('SW L1 directory unavailable; ' + ' | '.join(errors)) from exc


def _request_json(url: str, params: dict, label: str) -> dict:
    import requests
    last = None
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      'Chrome/114.0.0.0 Safari/537.36'
    }
    for attempt in range(1, 3):
        try:
            response = requests.get(url, params=params, headers=headers, verify=False, timeout=(5, 15))
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last = exc
            if attempt < 2:
                time.sleep(0.5)
    raise RuntimeError(f'{label} failed after 2 attempts: {type(last).__name__}: {last}') from last


def _fetch_sw_history_direct(code: str):
    import pandas as pd
    payload = _request_json(
        'https://www.swsresearch.com/institute-sw/api/index_publish/trend/',
        {'swindexcode': code, 'period': 'DAY'},
        f'SW index history {code}',
    )
    rows = payload.get('data')
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f'empty/invalid SW history payload code={code}')
    frame = pd.DataFrame(rows)
    frame.rename(columns={
        'swindexcode': '代码', 'bargaindate': '日期', 'openindex': '开盘',
        'maxindex': '最高', 'minindex': '最低', 'closeindex': '收盘',
        'bargainamount': '成交量', 'bargainsum': '成交额',
    }, inplace=True)
    required = ['代码', '日期', '收盘', '开盘', '最高', '最低', '成交量', '成交额']
    missing = [x for x in required if x not in frame.columns]
    if missing:
        raise RuntimeError(f'SW history payload missing columns code={code}: {missing}')
    return frame[required]


def _fetch_sw_components_direct(code: str) -> list[dict]:
    payload = _request_json(
        'https://www.swsresearch.com/institute-sw/api/index_publish/details/component_stocks/',
        {'swindexcode': code, 'page': '1', 'page_size': '10000'},
        f'SW components {code}',
    )
    data = payload.get('data') or {}
    rows = data.get('results')
    if not isinstance(rows, list):
        raise RuntimeError(f'invalid SW component payload code={code}')
    return rows


def _parallel_catalog_fetch(catalog: list[dict], worker, label: str) -> tuple[dict[str, object], list[dict]]:
    values = {}
    failures = []
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix=f'v2-sw-{label}')
    futures = {executor.submit(worker, row): row for row in catalog}
    try:
        for future in as_completed(futures):
            row = futures[future]
            try:
                values[row['industry_code']] = future.result()
            except Exception as exc:
                failures.append({
                    'industry_code': row['industry_code'],
                    'industry_name': row['industry_name'],
                    'error': f'{type(exc).__name__}: {exc}',
                })
                if len(failures) > 2:
                    for pending in futures:
                        pending.cancel()
                    raise RuntimeError(f'Shenwan {label} exceeded failure budget: {failures[:3]}') from exc
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return values, failures


def _load_industry_histories(catalog: list[dict], trade_date: str) -> tuple[dict[str, dict], list[dict]]:
    by_code = {row['industry_code']: row for row in catalog}
    frames, failures = _parallel_catalog_fetch(
        catalog,
        lambda row: _fetch_sw_history_direct(row['industry_code']),
        'history',
    )
    metrics = {}
    for code, frame in frames.items():
        row = by_code[code]
        try:
            metrics[code] = _history_metric(frame, code, row['industry_name'], trade_date)
        except Exception as exc:
            failures.append({
                'industry_code': code,
                'industry_name': row['industry_name'],
                'error': f'{type(exc).__name__}: {exc}',
            })
    if len(failures) > 2 or len(metrics) < max(25, len(catalog) - 2):
        raise RuntimeError(
            f'Shenwan per-index history incomplete industries={len(metrics)}/{len(catalog)} failures={len(failures)}'
        )
    scored = _score_industries(metrics)
    for row in catalog:
        code = row['industry_code']
        if code not in scored:
            scored[code] = {
                'industry_code': code, 'industry_name': row['industry_name'], 'asof': None,
                'r20': None, 'r60': None, 'day_pct': None, 'amount_activity': None,
                'turnover_pct': None, 'pe': None, 'pb': None, 'industry_score': 50.0,
                'score_components': {
                    'r20_rank': None, 'r60_rank': None, 'day_rank': None, 'activity_rank': None,
                },
            }
    return scored, failures


def _load_component_memberships(
    catalog: list[dict], trade_date: str, industries: dict[str, dict]
) -> tuple[dict, dict, int, int, list[dict]]:
    component_sets, failures = _parallel_catalog_fetch(
        catalog,
        lambda row: _fetch_sw_components_direct(row['industry_code']),
        'components',
    )
    stock_map = {}
    duplicates = {}
    rows_mainboard = 0
    successful_declared = 0
    by_code = {row['industry_code']: row for row in catalog}
    for industry_code, rows in component_sets.items():
        catalog_row = by_code[industry_code]
        successful_declared += int(catalog_row.get('declared_components') or 0)
        for raw in rows:
            code = _stock_code(raw.get('stockcode'))
            if not _is_mainboard(code):
                continue
            rows_mainboard += 1
            included = str(raw.get('beginningdate') or '')[:10]
            if included and included not in {'NaT', 'None'} and included > trade_date:
                continue
            _assign_membership(stock_map, duplicates, code, {
                'industry_code': industry_code,
                'industry_name': catalog_row['industry_name'],
                'included_date': included if included and included not in {'NaT', 'None'} else None,
                'industry_score': industries.get(industry_code, {}).get('industry_score', 50.0),
            })
    return stock_map, duplicates, rows_mainboard, successful_declared, failures


def load_sw_l1_snapshot(trade_date: str) -> dict:
    import akshare as ak
    date.fromisoformat(trade_date)
    catalog, catalog_meta = _load_l1_catalog(ak)
    industries, history_failures = _load_industry_histories(catalog, trade_date)
    stock_map, duplicate_assignments, component_rows_mainboard, successful_declared, component_failures = (
        _load_component_memberships(catalog, trade_date, industries)
    )
    duplicate_limit = max(10, int(max(1, len(stock_map)) * 0.005))
    if len(stock_map) < 2500 or len(component_failures) > 2 or len(duplicate_assignments) > duplicate_limit:
        raise RuntimeError(
            f'Shenwan industry snapshot incomplete industries={len(industries)} stocks={len(stock_map)} '
            f'component_failures={len(component_failures)} history_failures={len(history_failures)} '
            f'cross_industry_duplicates={len(duplicate_assignments)}'
        )
    return {
        'trade_date': trade_date,
        'taxonomy': 'Shenwan L1',
        'industries': industries,
        'stock_map': stock_map,
        'counts': {
            'industries': len(industries),
            'mainboard_stocks': len(stock_map),
            'component_rows_mainboard': component_rows_mainboard,
            'duplicate_cross_industry_stocks': len(duplicate_assignments),
            'component_failures': len(component_failures),
            'history_failures': len(history_failures),
        },
        'source': {
            'catalog': catalog_meta,
            'analysis': 'sws-per-index-history-day; bounded-parallel; r20/r60/day + relative-20d-amount activity',
            'components': 'sws-index-component; bounded-parallel',
            'successful_declared_components': successful_declared,
            'unique_vs_declared_ratio': round(len(stock_map) / successful_declared, 4) if successful_declared else None,
            'history_failure_sample': history_failures[:2],
            'duplicate_sample': list(duplicate_assignments.values())[:10],
            'failure_sample': component_failures[:2],
        },
    }
