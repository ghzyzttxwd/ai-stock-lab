from __future__ import annotations

import argparse
import json
from pathlib import Path

from .board_policy import sanitize_pending_for_retail
from .shadow_ledger import (
    AUDIT_SCHEMA_VERSION,
    EXECUTION_POLICY_VERSION,
    FUND_NAMES,
    build_pending_decision,
    execute_pending,
    expire_pending,
    immutable_write,
    load_ledger,
    ledger_content_hash,
    mark_to_market,
    normalize_symbol,
    portfolio_drawdown,
    save_ledger,
    sha256_json,
    validate_ledger,
)
from .targets import build_shadow_targets


def _bar(candidate: dict) -> dict | None:
    symbol = normalize_symbol(candidate.get('symbol') or candidate.get('code') or candidate.get('raw_code'))
    opening = float(candidate.get('open') or 0.0)
    close = float(candidate.get('close') or 0.0)
    if not symbol or opening <= 0 or close <= 0:
        return None
    return {
        'code': symbol,
        'name': candidate.get('name') or symbol,
        'open': opening,
        'high': float(candidate.get('high') or close),
        'low': float(candidate.get('low') or close),
        'close': close,
        'preclose': float(candidate.get('preclose') or 0.0),
        'tradestatus': str(candidate.get('tradestatus', '1')),
        'source': candidate.get('source') or 'v2-enriched',
    }


def bars_from_enriched(enriched: dict) -> dict[str, dict]:
    result = {}
    for candidate in enriched.get('candidates') or []:
        bar = _bar(candidate)
        if bar:
            result[bar['code']] = bar
    return result


def critical_symbols(states: dict[str, dict]) -> dict[str, str]:
    result = {}
    for state in states.values():
        for symbol, position in (state.get('positions') or {}).items():
            result[normalize_symbol(symbol)] = position.get('name') or symbol
        pending = state.get('pending_decision') or {}
        for target in pending.get('targets') or []:
            symbol = normalize_symbol(target.get('symbol'))
            if symbol:
                result[symbol] = target.get('name') or symbol
    return result


def supplement_execution_bars(bars: dict[str, dict], symbols: dict[str, str], trade_date: str) -> tuple[dict[str, dict], list[str]]:
    missing = {symbol: name for symbol, name in symbols.items() if symbol not in bars}
    if not missing:
        return bars, []
    from engine.real_market import AKShareMarket
    fetched = AKShareMarket().execution_bars(missing, trade_date)
    output = dict(bars)
    output.update({normalize_symbol(k): v for k, v in fetched.items()})
    return output, sorted(set(missing) - set(output))


def previous_trade_session(trade_date: str) -> str:
    import akshare as ak
    from .provider import bounded_call

    frame = bounded_call(35, lambda: ak.stock_zh_index_daily_tx(symbol='sh000001'), 'previous trading session')
    dates = sorted({str(x)[:10] for x in frame['date'].tolist() if str(x)[:10] < trade_date})
    if not dates:
        raise RuntimeError(f'cannot resolve previous exchange session before {trade_date}')
    return dates[-1]


def _already_processed(state_root: Path, audit_path: Path, trade_date: str) -> dict | None:
    if not audit_path.exists():
        return None
    event = json.loads(audit_path.read_text(encoding='utf-8'))
    event_hash = event.get('event_hash')
    for fund_id in FUND_NAMES:
        path = state_root / 'ledgers' / f'{fund_id}.json'
        if not path.exists():
            raise RuntimeError(f'audit exists without ledger {path}')
        state = json.loads(path.read_text(encoding='utf-8'))
        if str(state.get('last_processed_date') or '')[:10] != trade_date or state.get('audit_head') != event_hash:
            raise RuntimeError(f'V2 audit/ledger consistency failure for {fund_id} on {trade_date}')
    return {'status': 'already_processed', 'trade_date': trade_date, 'event_hash': event_hash, 'audit_path': str(audit_path)}


def run_shadow_session(
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
        raise RuntimeError('V2 shadow accounting blocked by enrichment safety')

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

    execution = {}
    mtm = {}
    previous_heads = {fund_id: state.get('audit_head') for fund_id, state in states.items()}
    opening_state_hashes = {fund_id: ledger_content_hash(state) for fund_id, state in states.items()}
    for fund_id, state in states.items():
        pending = state.get('pending_decision')
        if pending:
            if str(pending.get('decision_date') or '')[:10] == previous_trade_date:
                safe_pending, retail_adjustments = sanitize_pending_for_retail(pending)
                execution[fund_id] = execute_pending(state, safe_pending, bars, trade_date)
                execution[fund_id].setdefault('policy_adjustments', []).extend(retail_adjustments)
            else:
                execution[fund_id] = expire_pending(state, pending, trade_date, previous_trade_date)
            state['pending_decision'] = None
        else:
            execution[fund_id] = {
                'decision_date': None, 'trade_date': trade_date, 'opening_equity': float(state['cash']),
                'fills': [], 'rejected_orders': [], 'policy_adjustments': [],
                'valuation_fallback_symbols': [], 'fees': 0.0,
            }
        mtm[fund_id] = mark_to_market(state, bars, trade_date, execution[fund_id]['fees'])

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
        state['pending_decision'] = pending
        state.setdefault('decisions', []).append({
            'decision_date': trade_date,
            'strategy_version': pending['strategy_version'],
            'target_version': pending['target_version'],
            'regime': pending['regime'],
            'portfolio_stats': pending['portfolio_stats'],
            'targets': pending['targets'],
            'source_ref': source_ref,
            'calls_sol': False,
        })
        state['last_processed_date'] = trade_date
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
        'fills': {fund_id: len(x['fills']) for fund_id, x in execution.items()},
        'rejected_orders': {fund_id: len(x['rejected_orders']) for fund_id, x in execution.items()},
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
    report = run_shadow_session(
        snapshot, enriched, Path(args.state_root), previous_trade_date=args.previous_trade_date,
    )
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
