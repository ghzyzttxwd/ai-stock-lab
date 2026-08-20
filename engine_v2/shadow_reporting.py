from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from .conditional_plan import EXECUTION_MODEL, PLAN_VERSION
from .shadow_ledger import FUND_NAMES, ledger_content_hash, sha256_json


def _ordered_audit_events(state_root: Path) -> list[tuple[Path, dict]]:
    files = sorted((state_root / 'audit').glob('*.json'))
    if not files:
        raise RuntimeError('V2 audit chain is empty')

    by_hash: dict[str, tuple[Path, dict]] = {}
    parent_by_hash: dict[str, str | None] = {}
    children: dict[str | None, list[str]] = defaultdict(list)

    for path in files:
        event = json.loads(path.read_text(encoding='utf-8'))
        claimed = event.get('event_hash')
        body = dict(event)
        body.pop('event_hash', None)
        actual = sha256_json(body)
        if claimed != actual:
            raise RuntimeError(f'V2 audit event hash mismatch: {path}')
        if not claimed:
            raise RuntimeError(f'V2 audit event missing hash: {path}')
        if claimed in by_hash:
            raise RuntimeError(f'V2 duplicate audit event hash: {claimed}')

        parents = set((event.get('previous_event_hashes') or {}).values())
        if len(parents) != 1:
            raise RuntimeError(f'V2 audit event must have one shared parent: {path} parents={parents}')
        parent = next(iter(parents))
        by_hash[claimed] = (path, event)
        parent_by_hash[claimed] = parent
        children[parent].append(claimed)

    roots = children.get(None, [])
    if len(roots) != 1:
        raise RuntimeError(f'V2 audit chain must have exactly one root: roots={roots}')

    for claimed, parent in parent_by_hash.items():
        if parent is not None and parent not in by_hash:
            path = by_hash[claimed][0]
            raise RuntimeError(f'V2 audit parent not found: {path} parent={parent}')

    ordered: list[tuple[Path, dict]] = []
    seen: set[str] = set()
    current = roots[0]
    while current is not None:
        if current in seen:
            raise RuntimeError(f'V2 audit cycle detected at {current}')
        seen.add(current)
        ordered.append(by_hash[current])
        next_children = children.get(current, [])
        if len(next_children) > 1:
            raise RuntimeError(f'V2 audit chain branches at {current}: children={next_children}')
        current = next_children[0] if next_children else None

    if len(seen) != len(by_hash):
        missing = sorted(set(by_hash) - seen)
        raise RuntimeError(f'V2 audit chain is disconnected: unreachable={missing}')
    return ordered


def _audit_events(state_root: Path) -> list[tuple[Path, dict]]:
    return _ordered_audit_events(state_root)


def verify_audit_chain(state_root: Path) -> dict:
    events = _ordered_audit_events(state_root)
    first_path, _ = events[0]
    last_path, last_event = events[-1]
    head = last_event.get('event_hash')
    for fund_id in FUND_NAMES:
        state = json.loads((state_root / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8'))
        if state.get('audit_head') != head:
            raise RuntimeError(f'V2 ledger head mismatch: {fund_id}')
        expected = ((last_event.get('funds') or {}).get(fund_id) or {}).get('closing_ledger_content_sha256')
        if ledger_content_hash(state) != expected:
            raise RuntimeError(f'V2 ledger content hash mismatch: {fund_id}')
    return {
        'status': 'PASS',
        'events': len(events),
        'head': head,
        'first_date': first_path.stem,
        'last_date': last_path.stem,
    }


def _summary_source_ref(current_audit: dict) -> dict | None:
    source_ref = current_audit.get('source_ref')
    if source_ref is None:
        return None
    source_ref = dict(source_ref)
    if current_audit.get('event_kind') != 'morning_sell':
        return source_ref

    source_ref.setdefault('scheduled_time', '09:40')
    if source_ref.get('executed_at'):
        source_ref['timing_status'] = 'RECORDED'
        return source_ref

    source_ref['executed_at'] = None
    source_ref['timing_status'] = 'LEGACY_ACTUAL_TIME_UNRECORDED'
    source_ref['legacy_note'] = source_ref.get('note')
    source_ref['note'] = (
        'Previous-session SELL/reduce intents executed using an intraday live quote. '
        'This legacy audit event did not record the actual execution timestamp; '
        '09:40 is the scheduled target, not a verified actual execution time.'
    )
    return source_ref


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in values:
        if value <= 0:
            continue
        peak = max(peak, value)
        worst = min(worst, value / peak - 1.0)
    return worst


def ledger_metrics(state: dict) -> dict:
    curve = list(state.get('equity_curve') or [])
    values = [float(x.get('equity') or 0.0) for x in curve]
    initial = float(state.get('initial_cash') or 1_000_000.0)
    fills = list(state.get('fills') or [])
    gross = sum(float(x.get('gross') or 0.0) for x in fills)
    fees = sum(float(x.get('fees') or 0.0) for x in fills)
    positions = state.get('positions') or {}
    industry_values = defaultdict(float)
    market_value = 0.0
    for position in positions.values():
        value = int(position.get('qty') or 0) * float(position.get('last_price') or position.get('avg_cost') or 0.0)
        market_value += value
        industry_values[str(position.get('industry') or 'UNKNOWN')] += value

    curve_date = str(curve[-1].get('date') or '')[:10] if curve else ''
    execution_snapshot = dict(state.get('last_execution_snapshot') or {})
    execution_date = str(execution_snapshot.get('date') or '')[:10]
    execution_only = bool(execution_date and (not curve_date or execution_date > curve_date))
    if execution_only:
        current = float(execution_snapshot.get('equity') or (float(state.get('cash') or 0.0) + market_value))
    else:
        current = values[-1] if values else float(state.get('initial_cash') or 0.0)

    shares = [value / market_value for value in industry_values.values()] if market_value > 0 else []
    hhi = sum(value * value for value in shares)
    daily_returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0]
    volatility = 0.0
    if len(daily_returns) > 1:
        avg = sum(daily_returns) / len(daily_returns)
        volatility = math.sqrt(sum((x - avg) ** 2 for x in daily_returns) / len(daily_returns))
    metrics = {
        'trading_days': len(curve),
        'equity': round(current, 2),
        'cash': round(float(state.get('cash') or 0.0), 2),
        'position_market_value': round(market_value, 2),
        'return_pct': round((current / initial - 1.0) * 100.0, 4) if initial > 0 else None,
        'max_drawdown_pct': round(_max_drawdown(values) * 100.0, 4),
        'daily_volatility_pct': round(volatility * 100.0, 4),
        'fills': len(fills),
        'rejected_orders': len(state.get('rejected_orders') or []),
        'turnover_on_initial_capital': round(gross / initial, 6) if initial > 0 else None,
        'fees': round(fees, 2),
        'fees_pct_of_initial': round(fees / initial * 100.0, 6) if initial > 0 else None,
        'positions': len(positions),
        'industry_weights': dict(sorted(
            ((key, round(value / market_value, 6)) for key, value in industry_values.items()),
            key=lambda item: item[1], reverse=True,
        )) if market_value > 0 else {},
        'industry_hhi': round(hhi, 6),
        'effective_industries': round(1.0 / hhi, 4) if hhi > 0 else 0.0,
    }
    if execution_only:
        metrics['execution_only_date'] = execution_date
    return metrics


def ledger_holdings(state: dict, equity: float) -> list[dict]:
    holdings = []
    exits=state.get('exit_plans') or {}
    for symbol, position in (state.get('positions') or {}).items():
        quantity = int(position.get('qty') or 0)
        average_cost = float(position.get('avg_cost') or 0.0)
        last_price = float(position.get('last_price') or average_cost)
        market_value = quantity * last_price
        holdings.append({
            'symbol': symbol,
            'name': position.get('name') or symbol,
            'industry': position.get('industry') or '未分类',
            'qty': quantity,
            'avg_cost': round(average_cost, 4),
            'last_price': round(last_price, 4),
            'market_value': round(market_value, 2),
            'weight_pct': round(market_value / equity * 100.0, 4) if equity > 0 else 0.0,
            'pnl_pct': round((last_price / average_cost - 1.0) * 100.0, 4) if average_cost > 0 else None,
            'thesis': position.get('thesis'),
            'invalidation': position.get('invalidation'),
            'opportunity_score': position.get('opportunity_score'),
            'setup': position.get('setup'),
            'exit_plan': exits.get(symbol),
        })
    return sorted(holdings, key=lambda item: item['market_value'], reverse=True)


def build_summary(state_root: Path) -> dict:
    verification=verify_audit_chain(state_root)
    events=_audit_events(state_root)
    ledgers = {}
    effective_dates = set()
    heads = set()
    for fund_id in FUND_NAMES:
        path = state_root / 'ledgers' / f'{fund_id}.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        processed=str(state.get('last_processed_date') or '')[:10]
        executed=str(state.get('last_execution_date') or '')[:10]
        effective_dates.add(max(processed,executed))
        heads.add(state.get('audit_head'))
        metrics = ledger_metrics(state)
        ledgers[fund_id] = {
            'fund_id': fund_id,
            'name': state.get('name'),
            'strategy_family': state.get('strategy_family'),
            'execution_model': state.get('execution_model') or EXECUTION_MODEL,
            'plan_version': state.get('plan_version'),
            'metrics': metrics,
            'holdings': ledger_holdings(state, float(metrics.get('equity') or 0.0)),
            'recent_fills': list(state.get('fills') or [])[-20:],
            'recent_rejected_orders': list(state.get('rejected_orders') or [])[-20:],
            'equity_curve': list(state.get('equity_curve') or [])[-120:],
            'pending_decision': state.get('pending_decision'),
            'audit_head': state.get('audit_head'),
        }
    if len(effective_dates) != 1 or len(heads) != 1:
        raise RuntimeError(f'V2 ledgers are not aligned: dates={effective_dates} heads={heads}')
    trade_date = next(iter(effective_dates))
    head=next(iter(heads))
    current_audit=next((event for _,event in reversed(events) if event.get('event_hash')==head),None)
    if not current_audit:
        raise RuntimeError(f'V2 current audit head not found: {head}')
    latest_decision_audit=next((event for _,event in reversed(events) if event.get('target_diagnostics') is not None),current_audit)
    concentration_flags = ((latest_decision_audit.get('target_diagnostics') or {}).get('concentration_flags') or {})
    for fund_id, fund in ledgers.items():
        fund['concentration_flags'] = list(concentration_flags.get(fund_id) or [])
    current_source=_summary_source_ref(current_audit)
    summary = {
        'summary_version': 'v2-shadow-summary-1.2',
        'updated_at': trade_date,
        'initial_cash_per_fund': 1_000_000,
        'mode': 'FORWARD_SHADOW_ONLY',
        'execution_model': (current_source or {}).get('execution_model') or next((f.get('execution_model') for f in ledgers.values() if f.get('execution_model')), None),
        'plan_version': (current_source or {}).get('plan_version') or next((f.get('plan_version') for f in ledgers.values() if f.get('plan_version')), None),
        'regime': latest_decision_audit.get('regime'),
        'source_ref': current_source,
        'target_diagnostics': latest_decision_audit.get('target_diagnostics'),
        'funds': ledgers,
        'audit_head': head,
        'audit_verification': verification,
        'safety': {
            'calls_sol': False,
            'reads_v1_ledger': False,
            'writes_v1_ledger': False,
            'not_for_production_trading': True,
        },
    }
    if current_audit.get('event_kind'):
        summary['audit_event_kind'] = current_audit['event_kind']
    return summary


def attach_hs300_benchmark(summary: dict) -> dict:
    trade_date = str(summary.get('updated_at') or '')[:10]
    starts = []
    for fund in (summary.get('funds') or {}).values():
        curve = list(fund.get('equity_curve') or [])
        if curve:
            start = str(curve[0].get('date') or '')[:10]
            if start:
                starts.append(start)
    start_date = min(starts) if starts else trade_date
    benchmark = {
        'name': '沪深300', 'symbol': 'sh000300', 'start_date': start_date, 'end_date': trade_date,
        'return_pct': None, 'source': 'tencent-index', 'status': 'UNAVAILABLE',
    }
    try:
        from engine.real_market import AKShareMarket
        rows = AKShareMarket().benchmarks(start_date, trade_date)
        item = next((x for x in rows if x.get('symbol') == 'sh000300' or x.get('name') == '沪深300'), None)
        if not item or item.get('return_pct') is None:
            raise RuntimeError('沪深300同期收益未返回')
        benchmark.update(item)
        benchmark.update({'start_date': start_date,'end_date': trade_date,'source': 'tencent-index','status': 'OK'})
    except Exception as exc:
        benchmark['error'] = str(exc)[:240]

    summary['benchmark'] = benchmark
    benchmark_return = benchmark.get('return_pct')
    for fund in (summary.get('funds') or {}).values():
        metrics = fund.get('metrics') or {}
        fund_return = metrics.get('return_pct')
        metrics['excess_hs300_pct'] = (
            round(float(fund_return) - float(benchmark_return), 4)
            if fund_return is not None and benchmark_return is not None else None
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-root', default='shadow_state/v2')
    parser.add_argument('--output', default='shadow_state/v2/summary.json')
    parser.add_argument('--web-output', help='Optional same-origin fallback snapshot for the V2 page')
    args = parser.parse_args()
    summary = attach_hs300_benchmark(build_summary(Path(args.state_root)))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(summary, ensure_ascii=False, indent=2) + '\n'
    output.write_text(serialized, encoding='utf-8')
    if args.web_output:
        web_output = Path(args.web_output)
        web_output.parent.mkdir(parents=True, exist_ok=True)
        web_output.write_text(serialized, encoding='utf-8')
    print(json.dumps({
        'updated_at': summary['updated_at'], 'audit_head': summary['audit_head'],
        'execution_model':summary.get('execution_model'),'plan_version':summary.get('plan_version'),
        'benchmark': summary.get('benchmark'),
        'fund_metrics': {key: value['metrics'] for key, value in summary['funds'].items()},
        'safety': summary['safety'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
