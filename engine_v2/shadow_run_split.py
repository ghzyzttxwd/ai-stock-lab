from __future__ import annotations

import argparse
import json
from pathlib import Path

from .shadow_ledger import (
    AUDIT_SCHEMA_VERSION,
    EXECUTION_POLICY_VERSION,
    FUND_NAMES,
    build_pending_decision,
    expire_pending,
    immutable_write,
    ledger_content_hash,
    load_ledger,
    mark_to_market,
    normalize_symbol,
    portfolio_drawdown,
    save_ledger,
    sha256_json,
    validate_ledger,
)
from .shadow_run import (
    _already_processed,
    bars_from_enriched,
    critical_symbols,
    previous_trade_session,
    supplement_execution_bars,
)
from .split_execution import combine_phase_executions, execute_pending_side
from .targets import build_shadow_targets


EXECUTION_MODEL = '09:40_SELL_15:10_OPEN_BUY'


def run_shadow_session_split(
    snapshot: dict,
    enriched: dict,
    state_root: Path,
    previous_trade_date: str | None = None,
    supplemental_bars: dict[str, dict] | None = None,
    allow_network_bars: bool = True,
) -> dict:
    trade_date = str(enriched.get('trade_date') or '')[:10]
    if not trade_date or str(snapshot.get('trade_date') or '')[:10] != trade_date:
        raise RuntimeError('snapshot and enrichment trade dates do not match')
    safety = dict(enriched.get('safety') or {})
    if not safety.get('ready_for_strategy_targets') or safety.get('calls_sol'):
        raise RuntimeError('V2 split shadow accounting blocked by enrichment safety')

    audit_path = state_root / 'audit' / f'{trade_date}.json'
    existing = _already_processed(state_root, audit_path, trade_date)
    if existing:
        return existing

    states = {
        fund_id: load_ledger(state_root / 'ledgers' / f'{fund_id}.json', fund_id, trade_date)
        for fund_id in FUND_NAMES
    }
    processed = {str(x.get('last_processed_date') or '')[:10] == trade_date for x in states.values()}
    if True in processed:
        raise RuntimeError(f'partial V2 processing detected for {trade_date}; refusing to continue')

    bars = bars_from_enriched(enriched)
    bars.update({normalize_symbol(k): v for k, v in (supplemental_bars or {}).items()})
    critical = critical_symbols(states)
    missing_critical = sorted(set(critical) - set(bars))
    if missing_critical and allow_network_bars:
        bars, missing_critical = supplement_execution_bars(bars, critical, trade_date)

    has_pending = any(state.get('pending_decision') for state in states.values())
    if has_pending and previous_trade_date is None:
        previous_trade_date = previous_trade_session(trade_date)

    execution: dict[str, dict] = {}
    mtm: dict[str, dict] = {}
    previous_heads = {fund_id: state.get('audit_head') for fund_id, state in states.items()}
    opening_state_hashes = {fund_id: ledger_content_hash(state) for fund_id, state in states.items()}

    for fund_id, state in states.items():
        pending = state.get('pending_decision')
        if pending:
            decision_date = str(pending.get('decision_date') or '')[:10]
            if decision_date == previous_trade_date:
                if str(state.get('morning_sell_date') or '')[:10] == trade_date:
                    # Morning audit already contains SELL/reduce. At 15:10 account only BUY/add,
                    # using today's opening price from the completed daily bar.
                    execution[fund_id] = execute_pending_side(
                        state, pending, bars, trade_date,
                        side='BUY', price_field='open',
                        note='V2 上一交易日决策 · 15:10结算买入/加仓（参考当日开盘价）',
                    )
                else:
                    # Morning provider/workflow failed: settle SELL from close as safety fallback,
                    # then account BUY from the day's open. The fallback is explicitly audited.
                    sell_phase = execute_pending_side(
                        state, pending, bars, trade_date,
                        side='SELL', price_field='close',
                        note='V2 09:40任务未完成 · 15:10按收盘价兜底卖出/减仓',
                    )
                    buy_phase = execute_pending_side(
                        state, pending, bars, trade_date,
                        side='BUY', price_field='open',
                        note='V2 上一交易日决策 · 15:10结算买入/加仓（参考当日开盘价）',
                    )
                    execution[fund_id] = combine_phase_executions(sell_phase, buy_phase)
                    state['morning_sell_fallback'] = True
                state['pending_decision'] = None
            else:
                execution[fund_id] = expire_pending(state, pending, trade_date, previous_trade_date)
                state['pending_decision'] = None
        else:
            execution[fund_id] = {
                'decision_date': None, 'trade_date': trade_date,
                'fills': [], 'rejected_orders': [], 'policy_adjustments': [],
                'valuation_fallback_symbols': [], 'fees': 0.0,
            }
        mtm[fund_id] = mark_to_market(state, bars, trade_date, execution[fund_id].get('fees', 0.0))

    drawdowns = {fund_id: portfolio_drawdown(state) for fund_id, state in states.items()}
    targets_payload = build_shadow_targets(enriched, fund_drawdowns=drawdowns)
    target_safety = dict(targets_payload.get('safety') or {})
    if not target_safety.get('targets_valid') or target_safety.get('calls_sol') or target_safety.get('executes_orders'):
        raise RuntimeError('V2 targets failed safety gate before ledger commit')

    source_ref = {
        'snapshot_version': snapshot.get('snapshot_version'),
        'snapshot_sha256': sha256_json(snapshot),
        'enrichment_version': enriched.get('enrichment_version'),
        'enrichment_sha256': sha256_json(enriched),
        'target_version': targets_payload.get('target_version'),
        'execution_model': EXECUTION_MODEL,
        'board_policy': targets_payload.get('board_policy'),
        'data_quality': {
            'snapshot_source': (snapshot.get('source_notes') or {}).get('stock_snapshot'),
            'snapshot_grade': (snapshot.get('safety') or {}).get('snapshot_grade'),
            'industry_counts': (snapshot.get('industry') or {}).get('counts'),
            'enrichment_coverage': enriched.get('coverage'),
            'missing_critical_execution_bars': missing_critical,
        },
    }

    fund_events = {}
    for fund_id, state in states.items():
        pending = build_pending_decision(fund_id, targets_payload, source_ref)
        pending['execute_on'] = 'next_session_0940_sell_1510_open_buy'
        pending['execution_model'] = EXECUTION_MODEL
        state['pending_decision'] = pending
        state.setdefault('decisions', []).append({
            'decision_date': trade_date,
            'strategy_version': pending['strategy_version'],
            'target_version': pending['target_version'],
            'regime': pending['regime'],
            'portfolio_stats': pending['portfolio_stats'],
            'targets': pending['targets'],
            'source_ref': source_ref,
            'execution_model': EXECUTION_MODEL,
            'calls_sol': False,
        })
        state['last_processed_date'] = trade_date
        state['last_execution_date'] = trade_date
        state['execution_model'] = EXECUTION_MODEL
        validate_ledger(state)
        fund_events[fund_id] = {
            'opening_state_sha256': opening_state_hashes[fund_id],
            'execution': execution[fund_id],
            'close': mtm[fund_id],
            'drawdown': round(drawdowns[fund_id], 6),
            'next_decision': pending,
            'closing_ledger_content_sha256': ledger_content_hash(state),
        }

    event = {
        'schema_version': AUDIT_SCHEMA_VERSION,
        'event_kind': 'open_buy_and_decision',
        'trade_date': trade_date,
        'previous_trade_date': previous_trade_date,
        'execution_policy_version': EXECUTION_POLICY_VERSION,
        'source_ref': source_ref,
        'regime': targets_payload.get('regime'),
        'target_diagnostics': {
            'stats': targets_payload.get('stats'),
            'overlap_jaccard': targets_payload.get('overlap_jaccard'),
            'high_overlap_pairs': targets_payload.get('high_overlap_pairs'),
            'concentration_flags': targets_payload.get('concentration_flags'),
            'board_policy': targets_payload.get('board_policy'),
        },
        'previous_event_hashes': previous_heads,
        'funds': fund_events,
        'safety': {
            'calls_sol': False,
            'reads_v1_ledger': False,
            'writes_v1_ledger': False,
            'state_root': 'shadow_state/v2',
        },
    }
    event_hash = sha256_json(event)
    event['event_hash'] = event_hash
    for state in states.values():
        state['audit_head'] = event_hash

    immutable_write(audit_path, event)
    for fund_id, state in states.items():
        save_ledger(state_root / 'ledgers' / f'{fund_id}.json', state)

    return {
        'status': 'processed', 'trade_date': trade_date,
        'previous_trade_date': previous_trade_date, 'event_hash': event_hash,
        'audit_path': str(audit_path), 'source_ref': source_ref,
        'drawdowns': drawdowns,
        'target_stats': targets_payload.get('stats'),
        'board_policy': targets_payload.get('board_policy'),
        'concentration_flags': targets_payload.get('concentration_flags'),
        'fills': {fund_id: len(x.get('fills') or []) for fund_id, x in execution.items()},
        'rejected_orders': {fund_id: len(x.get('rejected_orders') or []) for fund_id, x in execution.items()},
        'missing_critical_execution_bars': missing_critical,
        'safety': event['safety'],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--enriched', required=True)
    parser.add_argument('--state-root', default='shadow_state/v2')
    parser.add_argument('--previous-trade-date')
    parser.add_argument('--report', required=True)
    args = parser.parse_args()

    snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    enriched = json.loads(Path(args.enriched).read_text(encoding='utf-8'))
    report = run_shadow_session_split(
        snapshot, enriched, Path(args.state_root), previous_trade_date=args.previous_trade_date,
    )
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
