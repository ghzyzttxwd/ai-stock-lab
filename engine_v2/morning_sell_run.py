from __future__ import annotations

import argparse
import json
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from .conditional_plan import EXECUTION_MODEL, PLAN_VERSION
from .intraday_quotes import is_exchange_session, live_execution_bars, previous_exchange_session
from .shadow_ledger import (
    AUDIT_SCHEMA_VERSION,
    EXECUTION_POLICY_VERSION,
    FUND_NAMES,
    immutable_write,
    ledger_content_hash,
    load_ledger,
    normalize_symbol,
    save_ledger,
    sha256_json,
    validate_ledger,
)
from .shadow_run import critical_symbols
from .split_execution import execute_conditional_exit_scan


SCHEDULED_MORNING_TIME = '09:40'
CHECKPOINTS = ('09:40', '10:30', '11:20', '13:30', '14:30', '14:55')
MAX_CHECKPOINT_LATENESS_MINUTES = 10


def _checkpoint_delay_minutes(now: datetime, scheduled_time: str) -> float:
    hour, minute = (int(x) for x in scheduled_time.split(':', 1))
    scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return (now - scheduled).total_seconds() / 60.0


def _readonly_noop_safety() -> dict:
    return {
        'writes_v1_ledger': False,
        'calls_sol': False,
        'forced_clock_sell': False,
        'reads_market_quotes': False,
        'mutates_v2_ledger': False,
    }


def _execution_snapshot(
    state: dict,
    bars: dict[str, dict],
    trade_date: str,
    fees: float,
    executed_at: str,
    scheduled_time: str = SCHEDULED_MORNING_TIME,
) -> dict:
    normalized = {normalize_symbol(k): dict(v) for k, v in bars.items()}
    cash = float(state.get('cash') or 0.0)
    market_value = 0.0
    missing = []
    for symbol, position in (state.get('positions') or {}).items():
        bar = normalized.get(symbol) or {}
        price = float(bar.get('close') or position.get('last_price') or position.get('avg_cost') or 0.0)
        if not bar or float(bar.get('close') or 0.0) <= 0:
            missing.append(symbol)
        position['last_price'] = price
        market_value += int(position.get('qty') or 0) * price
    return {
        'date': trade_date,
        'phase': 'conditional_exit_scan',
        'scheduled_time': scheduled_time,
        'executed_at': executed_at,
        'equity': round(cash + market_value, 2),
        'cash': round(cash, 2),
        'market_value': round(market_value, 2),
        'fees': round(float(fees), 2),
        'valuation_fallback_symbols': sorted(missing),
    }


def _audit_path(state_root: Path, trade_date: str, scheduled_time: str) -> Path:
    safe_clock = scheduled_time.replace(':', '')
    return state_root / 'audit' / f'{trade_date}-execution-{safe_clock}.json'


def _already_done(state_root: Path, trade_date: str, scheduled_time: str) -> dict | None:
    daily = state_root / 'audit' / f'{trade_date}.json'
    if daily.exists():
        event = json.loads(daily.read_text(encoding='utf-8'))
        source_ref = event.get('source_ref') or {}
        return {
            'status': 'already_processed', 'trade_date': trade_date,
            'event_hash': event.get('event_hash'), 'audit_path': str(daily),
            'scheduled_time': source_ref.get('scheduled_time'),
            'executed_at': source_ref.get('executed_at'),
            'actual_clock': source_ref.get('actual_clock'),
            'delay_minutes': source_ref.get('delay_minutes'),
            'execution_model': source_ref.get('execution_model'),
            'plan_version': source_ref.get('plan_version'),
        }

    path = _audit_path(state_root, trade_date, scheduled_time)
    if not path.exists() and scheduled_time == SCHEDULED_MORNING_TIME:
        legacy = state_root / 'audit' / f'{trade_date}-execution.json'
        if legacy.exists():
            path = legacy
    if not path.exists():
        return None

    event = json.loads(path.read_text(encoding='utf-8'))
    claimed = event.get('event_hash')
    body = dict(event)
    body.pop('event_hash', None)
    if claimed != sha256_json(body):
        raise RuntimeError(f'V2 conditional-exit audit hash mismatch: {path}')
    source_ref = event.get('source_ref') or {}
    return {
        'status': 'already_conditional_exit_scan', 'trade_date': trade_date,
        'event_hash': claimed, 'audit_path': str(path),
        'scheduled_time': source_ref.get('scheduled_time') or scheduled_time,
        'executed_at': source_ref.get('executed_at'),
        'actual_clock': source_ref.get('actual_clock'),
        'delay_minutes': source_ref.get('delay_minutes'),
        'execution_model': source_ref.get('execution_model'),
        'plan_version': source_ref.get('plan_version'),
    }


def run_morning_sell(trade_date: str, state_root: Path, scheduled_time: str | None = None) -> dict:
    """Check one declared V2 intraday exit checkpoint without forcing a clock-based sale."""
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    scheduled_time = str(scheduled_time or now.strftime('%H:%M'))

    if scheduled_time in CHECKPOINTS:
        delay_minutes = _checkpoint_delay_minutes(now, scheduled_time)
        if delay_minutes < 0:
            return {
                'status': 'stale_checkpoint_skipped',
                'trade_date': trade_date,
                'scheduled_time': scheduled_time,
                'executed_at': now.isoformat(timespec='seconds'),
                'actual_clock': now.strftime('%H:%M'),
                'delay_minutes': round(delay_minutes, 2),
                'fills': {},
                'rejected_orders': {},
                'execution_model': EXECUTION_MODEL,
                'plan_version': PLAN_VERSION,
                'safety': _readonly_noop_safety(),
            }
    else:
        delay_minutes = 0.0

    existing = _already_done(state_root, trade_date, scheduled_time)
    if existing:
        if scheduled_time in CHECKPOINTS and delay_minutes > MAX_CHECKPOINT_LATENESS_MINUTES:
            return {
                'status': 'late_duplicate_noop',
                'trade_date': trade_date,
                'scheduled_time': scheduled_time,
                'executed_at': now.isoformat(timespec='seconds'),
                'actual_clock': now.strftime('%H:%M'),
                'delay_minutes': round(delay_minutes, 2),
                'fills': {},
                'rejected_orders': {},
                'execution_model': existing.get('execution_model') or EXECUTION_MODEL,
                'plan_version': existing.get('plan_version') or PLAN_VERSION,
                'event_hash': existing.get('event_hash'),
                'existing_checkpoint': {
                    'status': existing.get('status'),
                    'event_hash': existing.get('event_hash'),
                    'audit_path': existing.get('audit_path'),
                    'scheduled_time': existing.get('scheduled_time'),
                    'executed_at': existing.get('executed_at'),
                    'actual_clock': existing.get('actual_clock'),
                    'delay_minutes': existing.get('delay_minutes'),
                },
                'safety': _readonly_noop_safety(),
            }
        return existing

    if scheduled_time in CHECKPOINTS and delay_minutes > MAX_CHECKPOINT_LATENESS_MINUTES:
        return {
            'status': 'stale_checkpoint_skipped',
            'trade_date': trade_date,
            'scheduled_time': scheduled_time,
            'executed_at': now.isoformat(timespec='seconds'),
            'actual_clock': now.strftime('%H:%M'),
            'delay_minutes': round(delay_minutes, 2),
            'fills': {},
            'rejected_orders': {},
            'execution_model': EXECUTION_MODEL,
            'plan_version': PLAN_VERSION,
            'safety': _readonly_noop_safety(),
        }

    in_session = dt_time(9, 30) <= now.time() <= dt_time(15, 0)
    final_slot_grace = scheduled_time == '14:55' and dt_time(15, 0) < now.time() <= dt_time(15, 5)
    if not (in_session or final_slot_grace):
        return {
            'status': 'outside_session_window',
            'trade_date': trade_date,
            'scheduled_time': scheduled_time,
            'executed_at': now.isoformat(timespec='seconds'),
            'actual_clock': now.strftime('%H:%M'),
            'delay_minutes': round(delay_minutes, 2),
            'fills': {},
            'rejected_orders': {},
            'execution_model': EXECUTION_MODEL,
            'plan_version': PLAN_VERSION,
            'safety': _readonly_noop_safety(),
        }

    import akshare as ak
    if not is_exchange_session(ak, trade_date):
        return {
            'status': 'not_exchange_session', 'trade_date': trade_date,
            'fills': {}, 'rejected_orders': {},
            'scheduled_time': scheduled_time,
            'executed_at': now.isoformat(timespec='seconds'),
            'actual_clock': now.strftime('%H:%M'),
            'delay_minutes': round(delay_minutes, 2),
            'execution_model': EXECUTION_MODEL,
            'plan_version': PLAN_VERSION,
            'safety': _readonly_noop_safety(),
        }
    previous_trade_date = previous_exchange_session(ak, trade_date)
    if not previous_trade_date:
        raise RuntimeError(f'cannot resolve previous exchange session before {trade_date}')

    states = {
        fund_id: load_ledger(state_root / 'ledgers' / f'{fund_id}.json', fund_id, trade_date)
        for fund_id in FUND_NAMES
    }
    if any(str(x.get('last_processed_date') or '')[:10] == trade_date for x in states.values()):
        raise RuntimeError(f'V2 daily processing already started for {trade_date}; refusing intraday conditional scan')

    critical = critical_symbols(states)
    bars, quote_source = live_execution_bars(ak, critical)
    executed_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
    execution_clock = executed_at.split('T', 1)[1][:5]
    scan_key = f'{trade_date}T{scheduled_time}'
    bars = {normalize_symbol(k): v for k, v in bars.items()}
    missing_critical = sorted(set(critical) - set(bars))

    previous_heads = {fund_id: state.get('audit_head') for fund_id, state in states.items()}
    opening_hashes = {fund_id: ledger_content_hash(state) for fund_id, state in states.items()}
    fund_events = {}
    fill_counts = {}
    reject_counts = {}

    for fund_id, state in states.items():
        pending = state.get('pending_decision')
        execution = execute_conditional_exit_scan(
            state, bars, trade_date, clock=scheduled_time, pending=pending,
        )
        for fill in execution.get('fills') or []:
            fill['scheduled_time'] = scheduled_time
            fill['actual_execution_time'] = executed_at
            fill['actual_clock'] = execution_clock
        snapshot = _execution_snapshot(
            state, bars, trade_date, execution.get('fees', 0.0), executed_at, scheduled_time,
        )
        state['morning_sell_date'] = trade_date
        state['conditional_exit_date'] = trade_date
        state['last_conditional_scan_key'] = scan_key
        state['last_conditional_scan_at'] = executed_at
        state['last_execution_date'] = trade_date
        state['last_execution_snapshot'] = snapshot
        state['execution_model'] = EXECUTION_MODEL
        state['plan_version'] = PLAN_VERSION
        validate_ledger(state)
        fill_counts[fund_id] = len(execution.get('fills') or [])
        reject_counts[fund_id] = len(execution.get('rejected_orders') or [])
        fund_events[fund_id] = {
            'opening_state_sha256': opening_hashes[fund_id],
            'execution': execution,
            'intraday_snapshot': snapshot,
            'next_decision': state.get('pending_decision'),
        }

    event = {
        'schema_version': AUDIT_SCHEMA_VERSION,
        'event_kind': 'conditional_exit_scan',
        'trade_date': trade_date,
        'previous_trade_date': previous_trade_date,
        'execution_policy_version': EXECUTION_POLICY_VERSION,
        'source_ref': {
            'execution_bar_source': quote_source,
            'missing_critical_execution_bars': missing_critical,
            'scheduled_time': scheduled_time,
            'executed_at': executed_at,
            'actual_clock': execution_clock,
            'delay_minutes': round(delay_minutes, 2),
            'freshness_limit_minutes': MAX_CHECKPOINT_LATENESS_MINUTES,
            'execution_model': EXECUTION_MODEL,
            'plan_version': PLAN_VERSION,
            'note': (
                f'Checkpoint {scheduled_time} Asia/Shanghai was evaluated at actual time {execution_clock}. '
                'Hard-stop, take-profit, trailing, rotation and time conditions were checked; '
                'the clock itself is not a sell signal.'
            ),
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
            'forced_clock_sell': False,
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

    audit_path = _audit_path(state_root, trade_date, scheduled_time)
    immutable_write(audit_path, event)
    for fund_id, state in states.items():
        save_ledger(state_root / 'ledgers' / f'{fund_id}.json', state)

    return {
        'status': 'conditional_exit_processed',
        'trade_date': trade_date,
        'previous_trade_date': previous_trade_date,
        'scheduled_time': scheduled_time,
        'executed_at': executed_at,
        'actual_clock': execution_clock,
        'delay_minutes': round(delay_minutes, 2),
        'execution_model': EXECUTION_MODEL,
        'plan_version': PLAN_VERSION,
        'event_hash': event_hash,
        'audit_path': str(audit_path),
        'fills': fill_counts,
        'rejected_orders': reject_counts,
        'missing_critical_execution_bars': missing_critical,
        'quote_source': quote_source,
        'safety': event['safety'],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--state-root', default='shadow_state/v2')
    parser.add_argument('--report', required=True)
    parser.add_argument('--scheduled-time', default=None)
    args = parser.parse_args()
    report = run_morning_sell(args.date, Path(args.state_root), args.scheduled_time)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
