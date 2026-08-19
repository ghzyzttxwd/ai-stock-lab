from __future__ import annotations

import argparse
import json
from pathlib import Path

from .board_policy import sanitize_pending_for_retail
from .shadow_ledger import (
    AUDIT_SCHEMA_VERSION,
    EXECUTION_POLICY_VERSION,
    FUND_NAMES,
    execute_pending,
    expire_pending,
    immutable_write,
    ledger_content_hash,
    load_ledger,
    normalize_symbol,
    save_ledger,
    sha256_json,
    validate_ledger,
)
from .shadow_run import critical_symbols


def _close_snapshot(state: dict, bars: dict[str, dict], trade_date: str, fees: float) -> dict:
    cash = float(state.get('cash') or 0.0)
    market_value = 0.0
    missing = []
    normalized = {normalize_symbol(k): dict(v) for k, v in bars.items()}
    for symbol, position in (state.get('positions') or {}).items():
        bar = normalized.get(symbol) or {}
        close = float(bar.get('close') or position.get('last_price') or position.get('avg_cost') or 0.0)
        if not bar or float(bar.get('close') or 0.0) <= 0:
            missing.append(symbol)
        position['last_price'] = close
        market_value += int(position.get('qty') or 0) * close
    return {
        'date': trade_date,
        'equity': round(cash + market_value, 2),
        'cash': round(cash, 2),
        'market_value': round(market_value, 2),
        'fees': round(float(fees), 2),
        'valuation_fallback_symbols': sorted(missing),
    }


def _already_done(state_root: Path, trade_date: str) -> dict | None:
    daily = state_root / 'audit' / f'{trade_date}.json'
    if daily.exists():
        event = json.loads(daily.read_text(encoding='utf-8'))
        return {
            'status': 'already_processed',
            'trade_date': trade_date,
            'event_hash': event.get('event_hash'),
            'audit_path': str(daily),
        }
    path = state_root / 'audit' / f'{trade_date}-execution.json'
    if not path.exists():
        return None
    event = json.loads(path.read_text(encoding='utf-8'))
    event_hash = event.get('event_hash')
    for fund_id in FUND_NAMES:
        state = json.loads((state_root / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8'))
        if state.get('audit_head') != event_hash or state.get('pending_decision') is not None:
            raise RuntimeError(f'V2 execution catch-up audit/ledger mismatch for {fund_id}')
    return {
        'status': 'already_execution_caught_up',
        'trade_date': trade_date,
        'event_hash': event_hash,
        'audit_path': str(path),
    }


def _pending_decision_date(states: dict[str, dict], trade_date: str) -> str | None:
    dates = {
        str((state.get('pending_decision') or {}).get('decision_date') or '')[:10]
        for state in states.values()
        if state.get('pending_decision')
    }
    dates.discard('')
    if len(dates) > 1:
        raise RuntimeError(f'V2 pending decisions are not aligned: {sorted(dates)}')
    if not dates:
        return None
    decision_date = next(iter(dates))
    if decision_date >= trade_date:
        raise RuntimeError(f'V2 pending decision is not from an earlier session: decision={decision_date} trade={trade_date}')
    return decision_date


def run_execution_catchup(trade_date: str, state_root: Path) -> dict:
    existing = _already_done(state_root, trade_date)
    if existing:
        return existing

    states = {
        fund_id: load_ledger(state_root / 'ledgers' / f'{fund_id}.json', fund_id, trade_date)
        for fund_id in FUND_NAMES
    }
    processed = {str(x.get('last_processed_date') or '')[:10] == trade_date for x in states.values()}
    if True in processed:
        raise RuntimeError(f'partial V2 daily processing detected for {trade_date}; refusing execution catch-up')

    # The pending ledgers are the authoritative source for the decision session. This avoids
    # an unnecessary market-index network request before we can execute already-fixed orders.
    previous_trade_date = _pending_decision_date(states, trade_date)
    critical = critical_symbols(states)
    from engine.real_market import AKShareMarket
    bars = AKShareMarket().execution_bars(critical, trade_date) if critical else {}
    bars = {normalize_symbol(k): v for k, v in bars.items()}
    missing_critical = sorted(set(critical) - set(bars))

    previous_heads = {fund_id: state.get('audit_head') for fund_id, state in states.items()}
    opening_hashes = {fund_id: ledger_content_hash(state) for fund_id, state in states.items()}
    fund_events = {}
    fill_counts = {}
    reject_counts = {}

    for fund_id, state in states.items():
        pending = state.get('pending_decision')
        if pending:
            if previous_trade_date and str(pending.get('decision_date') or '')[:10] == previous_trade_date:
                safe_pending, retail_adjustments = sanitize_pending_for_retail(pending)
                execution = execute_pending(state, safe_pending, bars, trade_date)
                execution.setdefault('policy_adjustments', []).extend(retail_adjustments)
            else:
                execution = expire_pending(state, pending, trade_date, previous_trade_date)
            state['pending_decision'] = None
        else:
            execution = {
                'decision_date': None,
                'trade_date': trade_date,
                'opening_equity': float(state.get('cash') or 0.0),
                'fills': [],
                'rejected_orders': [],
                'policy_adjustments': [],
                'valuation_fallback_symbols': [],
                'fees': 0.0,
            }

        close_snapshot = _close_snapshot(state, bars, trade_date, execution.get('fees', 0.0))
        state['last_execution_date'] = trade_date
        state['last_execution_snapshot'] = close_snapshot
        validate_ledger(state)
        fill_counts[fund_id] = len(execution.get('fills') or [])
        reject_counts[fund_id] = len(execution.get('rejected_orders') or [])
        fund_events[fund_id] = {
            'opening_state_sha256': opening_hashes[fund_id],
            'execution': execution,
            'close_snapshot_uncommitted_to_curve': close_snapshot,
            'next_decision': None,
        }

    event = {
        'schema_version': AUDIT_SCHEMA_VERSION,
        'event_kind': 'execution_catchup',
        'trade_date': trade_date,
        'previous_trade_date': previous_trade_date,
        'execution_policy_version': EXECUTION_POLICY_VERSION,
        'source_ref': {
            'execution_bar_source': 'engine.real_market.AKShareMarket.execution_bars',
            'missing_critical_execution_bars': missing_critical,
            'note': 'Previous-session targets executed independently before current-session target generation.',
        },
        'regime': None,
        'target_diagnostics': None,
        'previous_event_hashes': previous_heads,
        'funds': fund_events,
        'safety': {
            'calls_sol': False,
            'reads_v1_ledger': False,
            'writes_v1_ledger': False,
            'generates_new_targets': False,
            'state_root': 'shadow_state/v2',
        },
    }
    event_hash = sha256_json(event)
    event['event_hash'] = event_hash
    for fund_id, state in states.items():
        state['audit_head'] = event_hash
        fund_events[fund_id]['closing_ledger_content_sha256'] = ledger_content_hash(state)

    event.pop('event_hash', None)
    event_hash = sha256_json(event)
    event['event_hash'] = event_hash
    for state in states.values():
        state['audit_head'] = event_hash

    audit_path = state_root / 'audit' / f'{trade_date}-execution.json'
    immutable_write(audit_path, event)
    for fund_id, state in states.items():
        save_ledger(state_root / 'ledgers' / f'{fund_id}.json', state)

    return {
        'status': 'execution_caught_up',
        'trade_date': trade_date,
        'previous_trade_date': previous_trade_date,
        'event_hash': event_hash,
        'audit_path': str(audit_path),
        'fills': fill_counts,
        'rejected_orders': reject_counts,
        'missing_critical_execution_bars': missing_critical,
        'safety': event['safety'],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--state-root', default='shadow_state/v2')
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    report = run_execution_catchup(args.date, Path(args.state_root))
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
