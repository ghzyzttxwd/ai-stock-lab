from __future__ import annotations

import json
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .daily_run import FUNDS, STATE_ROOT, _critical_market_symbols
from .exchange_calendar import exchange_calendar_latest_session, exchange_calendar_previous_session
from .real_market import AKShareMarket, _tx_symbol

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'state' / 'v1_market_diagnostic.json'
CN = ZoneInfo('Asia/Shanghai')


def timed(result: dict, key: str, fn):
    started = time.monotonic()
    try:
        value = fn()
        result[key] = {
            'ok': True,
            'seconds': round(time.monotonic() - started, 3),
            'value': value,
        }
        return value
    except Exception as exc:
        result[key] = {
            'ok': False,
            'seconds': round(time.monotonic() - started, 3),
            'error': f'{type(exc).__name__}: {exc}'[:1000],
        }
        return None


def _last_date(frame) -> str | None:
    if frame is None or getattr(frame, 'empty', True):
        return None
    if 'date' not in frame.columns:
        return None
    return str(frame.iloc[-1]['date'])[:10]


def _raw_history_probe(ak, symbols: list[dict], trade_date: str, *, adjust: str) -> dict:
    d = date.fromisoformat(trade_date)
    start = (d - timedelta(days=40)).strftime('%Y%m%d')
    end = d.strftime('%Y%m%d')
    rows = []
    for item in symbols:
        sym = str(item.get('code') or '')
        if not sym:
            continue
        t0 = time.monotonic()
        try:
            frame = ak.stock_zh_a_hist_tx(
                symbol=_tx_symbol(sym),
                start_date=start,
                end_date=end,
                adjust=adjust,
                timeout=8,
            )
            rows.append({
                'symbol': sym,
                'ok': True,
                'seconds': round(time.monotonic() - t0, 3),
                'last_date': _last_date(frame),
                'row_count': 0 if frame is None else int(len(frame)),
            })
        except Exception as exc:
            rows.append({
                'symbol': sym,
                'ok': False,
                'seconds': round(time.monotonic() - t0, 3),
                'error': f'{type(exc).__name__}: {exc}'[:500],
            })
    current = sum(1 for row in rows if row.get('last_date') == trade_date)
    stale = sum(1 for row in rows if row.get('ok') and row.get('last_date') not in (None, trade_date))
    errors = sum(1 for row in rows if not row.get('ok'))
    return {
        'sample_size': len(rows),
        'current': current,
        'stale': stale,
        'errors': errors,
        'rows': rows,
    }


def main() -> None:
    now = datetime.now(CN)
    requested = now.date().isoformat()
    report: dict = {
        'status': 'diagnostic',
        'requested_date': requested,
        'generated_at_beijing': now.isoformat(timespec='seconds'),
        'state_dates': {},
    }

    for fid in FUNDS:
        path = STATE_ROOT / f'{fid}.json'
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            report['state_dates'][fid] = {
                'last_processed_date': str(state.get('last_processed_date') or '')[:10],
                'conditional_plan_date': str(state.get('conditional_plan_date') or '')[:10],
                'pending_decision_date': str(state.get('pending_decision_date') or '')[:10],
            }
        except Exception as exc:
            report['state_dates'][fid] = {'error': f'{type(exc).__name__}: {exc}'}

    market = AKShareMarket()
    latest = timed(report, 'exchange_calendar_latest', lambda: exchange_calendar_latest_session(requested, market.ak))
    if latest:
        timed(report, 'exchange_calendar_previous', lambda: exchange_calendar_previous_session(latest, market.ak))
    engine_date = timed(report, 'engine_latest_trade_date', lambda: market.latest_trade_date(requested))
    trade_date = engine_date or latest or requested

    snapshot = timed(report, 'full_market_snapshot', market.snapshot)
    selected: list[dict] = []
    if snapshot:
        source_counts = Counter(str(row.get('source') or 'unknown') for row in snapshot)
        report['snapshot_summary'] = {
            'rows': len(snapshot),
            'sources': dict(source_counts),
        }
        selected = market.preselect(snapshot)
        report['preselect_summary'] = {'rows': len(selected)}

        critical = _critical_market_symbols()
        critical_in_snapshot = sum(1 for sym in critical if any(row.get('code') == sym for row in snapshot))
        report['critical_summary'] = {
            'count': len(critical),
            'present_in_snapshot': critical_in_snapshot,
            'missing_from_snapshot': len(critical) - critical_in_snapshot,
        }
    else:
        critical = _critical_market_symbols()
        report['critical_summary'] = {'count': len(critical)}

    sample = selected[:12]
    if not sample:
        sample = [{'code': sym, 'name': name} for sym, name in list(critical.items())[:12]]

    report['raw_qfq_history_probe'] = _raw_history_probe(market.ak, sample, trade_date, adjust='qfq')
    report['raw_unadjusted_history_probe'] = _raw_history_probe(market.ak, sample, trade_date, adjust='')

    critical_sample = [{'code': sym, 'name': name} for sym, name in list(critical.items())[:12]]
    if critical_sample:
        report['critical_unadjusted_history_probe'] = _raw_history_probe(
            market.ak, critical_sample, trade_date, adjust=''
        )
        bars = timed(
            report,
            'critical_execution_bars',
            lambda: market.execution_bars({x['code']: x.get('name', x['code']) for x in critical_sample}, trade_date),
        )
        if isinstance(bars, dict):
            report['critical_execution_bars_summary'] = {
                'requested': len(critical_sample),
                'returned': len(bars),
                'symbols': sorted(bars),
            }

    if sample:
        histories = timed(report, 'histories_sample', lambda: market.histories(sample, trade_date))
        if isinstance(histories, dict):
            report['histories_sample_summary'] = {
                'requested': len(sample),
                'returned': len(histories),
                'current': sum(
                    1
                    for rows in histories.values()
                    if rows and str(rows[-1].get('date') or '')[:10] == trade_date
                ),
            }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
