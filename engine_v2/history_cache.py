from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

from engine.real_market import _f, _tx_amount_and_volume, _tx_amount_mode, _tx_symbol


CACHE_VERSION = 'v2-tencent-qfq-1'


def _cache_path(root: Path, symbol: str) -> Path:
    safe = ''.join(ch for ch in symbol.lower() if ch.isalnum() or ch in '._-')
    return root / f'{safe}.json'


def _read_cache(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('cache_version') != CACHE_VERSION:
            return []
        rows = list(payload.get('rows') or [])
        rows.sort(key=lambda x: str(x.get('date') or '')[:10])
        return rows
    except Exception:
        return []


def _write_cache(path: Path, symbol: str, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'cache_version': CACHE_VERSION, 'symbol': symbol, 'asof': rows[-1]['date'], 'rows': rows}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(path)


def merge_history_rows(cached: list[dict], fetched: list[dict], history_limit: int = 120) -> list[dict]:
    by_date = {}
    for row in cached + fetched:
        key = str(row.get('date') or '')[:10]
        if key:
            by_date[key] = row
    return [by_date[key] for key in sorted(by_date)][-history_limit:]


def adjustment_drift(cached: list[dict], fetched: list[dict], tolerance: float = 0.001) -> bool:
    """Detect a qfq back-adjustment change before appending an incremental tail."""
    old = {str(row.get('date'))[:10]: _f(row.get('close')) for row in cached}
    compared = 0
    for row in fetched:
        key = str(row.get('date'))[:10]
        before = old.get(key)
        after = _f(row.get('close'))
        if before and after:
            compared += 1
            if abs(after / before - 1.0) > tolerance:
                return True
    return False if compared else False


def _normalize_rows(frame, item: dict) -> list[dict]:
    if frame is None or frame.empty:
        return []
    records = list(frame.to_dict('records'))
    last = records[-1]
    mode = _tx_amount_mode(last.get('amount'), last.get('close'), item.get('amount', 0))
    rows = []
    for row in records:
        close = _f(row.get('close'))
        amount_yuan, volume_shares = _tx_amount_and_volume(row.get('amount'), close, mode)
        rows.append({
            'date': str(row.get('date'))[:10],
            'code': item['code'], 'name': item.get('name', item['code']),
            'open': _f(row.get('open')), 'high': _f(row.get('high')),
            'low': _f(row.get('low')), 'close': close,
            'volume': volume_shares, 'amount': amount_yuan,
            'turn': 0.0, 'pctChg': 0.0, 'tradestatus': '1', 'isST': '0',
        })
    return rows


def _fetch_symbol(market, item: dict, trade_date: str, cache_root: Path) -> tuple[str, list[dict], str, str | None]:
    symbol = item['code']
    path = _cache_path(cache_root, symbol)
    cached = _read_cache(path)
    cached_asof = str(cached[-1].get('date'))[:10] if cached else None
    if cached_asof == trade_date and len(cached) >= 61:
        return symbol, cached[-market.history_limit:], 'hit', None

    end_date = date.fromisoformat(trade_date)
    incremental = bool(cached_asof and len(cached) >= 61 and cached_asof < trade_date)
    full_start = end_date - timedelta(days=240)
    if incremental:
        # Refetch an overlap so ex-right/ex-dividend qfq rewrites are detectable.
        start_date = date.fromisoformat(cached_asof) - timedelta(days=14)
    else:
        start_date = full_start

    last_error = None
    for attempt in (1, 2):
        try:
            frame = market.ak.stock_zh_a_hist_tx(
                symbol=_tx_symbol(symbol),
                start_date=start_date.strftime('%Y%m%d'),
                end_date=end_date.strftime('%Y%m%d'),
                adjust='qfq', timeout=20,
            )
            fetched = _normalize_rows(frame, item)
            mode = 'incremental' if incremental else 'miss'
            if incremental and adjustment_drift(cached, fetched):
                frame = market.ak.stock_zh_a_hist_tx(
                    symbol=_tx_symbol(symbol),
                    start_date=full_start.strftime('%Y%m%d'),
                    end_date=end_date.strftime('%Y%m%d'),
                    adjust='qfq', timeout=20,
                )
                fetched = _normalize_rows(frame, item)
                mode = 'refresh'
            combined = merge_history_rows(cached if mode == 'incremental' else [], fetched, market.history_limit)
            if len(combined) < 61 or str(combined[-1].get('date'))[:10] != trade_date:
                raise RuntimeError(f'current/long history unavailable rows={len(combined)} asof={combined[-1]["date"] if combined else None}')
            _write_cache(path, symbol, combined)
            return symbol, combined, mode, None
        except Exception as exc:
            last_error = f'{type(exc).__name__}: {exc}'
            if attempt == 1:
                time.sleep(0.6)
    return symbol, [], 'failed', last_error


def load_histories_cached(market, selected: list[dict], trade_date: str, cache_root: Path) -> tuple[dict[str, list[dict]], dict]:
    """Fetch Tencent histories with a small bounded worker pool and persistent daily cache."""
    if not selected:
        raise RuntimeError('No symbols supplied for V2 cached histories')
    workers = max(1, min(4, int(os.getenv('V2_HISTORY_WORKERS', '3'))))
    started = time.monotonic()
    histories: dict[str, list[dict]] = {}
    modes = {'hit': 0, 'incremental': 0, 'refresh': 0, 'miss': 0, 'failed': 0}
    errors = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='v2-tencent') as pool:
        futures = [pool.submit(_fetch_symbol, market, item, trade_date, cache_root) for item in selected]
        for future in as_completed(futures):
            symbol, rows, mode, error = future.result()
            modes[mode] = modes.get(mode, 0) + 1
            if rows:
                histories[symbol] = rows
            if error:
                errors.append({'symbol': symbol, 'error': error})

    required = max(1, math.ceil(len(selected) * 0.75))
    if len(histories) < required:
        raise RuntimeError(
            f'cached Tencent history coverage too low: {len(histories)}/{len(selected)}, '
            f'require >= {required}; failures={len(errors)}'
        )
    diagnostics = {
        'cache_version': CACHE_VERSION,
        'workers': workers,
        'selected': len(selected),
        'current_histories': len(histories),
        'cache_hits': modes['hit'],
        'incremental_fetches': modes['incremental'],
        'adjustment_full_refreshes': modes['refresh'],
        'full_fetches': modes['miss'],
        'failures': modes['failed'],
        'elapsed_s': round(time.monotonic() - started, 2),
        'sample_errors': errors[:10],
    }
    print('[v2-history-cache] ' + json.dumps(diagnostics, ensure_ascii=False))
    return histories, diagnostics
