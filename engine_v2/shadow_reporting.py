from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from .shadow_ledger import FUND_NAMES, ledger_content_hash, sha256_json


def verify_audit_chain(state_root: Path) -> dict:
    files=sorted((state_root/'audit').glob('*.json'))
    if not files:
        raise RuntimeError('V2 audit chain is empty')
    previous=None
    last_event=None
    for path in files:
        event=json.loads(path.read_text(encoding='utf-8'))
        claimed=event.get('event_hash')
        body=dict(event)
        body.pop('event_hash',None)
        actual=sha256_json(body)
        if claimed != actual:
            raise RuntimeError(f'V2 audit event hash mismatch: {path}')
        parents=set((event.get('previous_event_hashes') or {}).values())
        if parents != {previous}:
            raise RuntimeError(f'V2 audit parent mismatch: {path} parents={parents} expected={previous}')
        previous=claimed
        last_event=event
    for fund_id in FUND_NAMES:
        state=json.loads((state_root/'ledgers'/f'{fund_id}.json').read_text(encoding='utf-8'))
        if state.get('audit_head') != previous:
            raise RuntimeError(f'V2 ledger head mismatch: {fund_id}')
        expected=((last_event.get('funds') or {}).get(fund_id) or {}).get('closing_ledger_content_sha256')
        if ledger_content_hash(state) != expected:
            raise RuntimeError(f'V2 ledger content hash mismatch: {fund_id}')
    return {'status':'PASS','events':len(files),'head':previous,'first_date':files[0].stem,'last_date':files[-1].stem}


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
    current = values[-1] if values else float(state.get('initial_cash') or 0.0)
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
    shares = [value / market_value for value in industry_values.values()] if market_value > 0 else []
    hhi = sum(value * value for value in shares)
    daily_returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0]
    volatility = 0.0
    if len(daily_returns) > 1:
        avg = sum(daily_returns) / len(daily_returns)
        volatility = math.sqrt(sum((x - avg) ** 2 for x in daily_returns) / len(daily_returns))
    return {
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


def ledger_holdings(state: dict, equity: float) -> list[dict]:
    holdings = []
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
        })
    return sorted(holdings, key=lambda item: item['market_value'], reverse=True)


def build_summary(state_root: Path) -> dict:
    verification=verify_audit_chain(state_root)
    ledgers = {}
    dates = set()
    heads = set()
    for fund_id in FUND_NAMES:
        path = state_root / 'ledgers' / f'{fund_id}.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        dates.add(str(state.get('last_processed_date') or '')[:10])
        heads.add(state.get('audit_head'))
        metrics = ledger_metrics(state)
        ledgers[fund_id] = {
            'fund_id': fund_id,
            'name': state.get('name'),
            'strategy_family': state.get('strategy_family'),
            'metrics': metrics,
            'holdings': ledger_holdings(state, float(metrics.get('equity') or 0.0)),
            'recent_fills': list(state.get('fills') or [])[-20:],
            'recent_rejected_orders': list(state.get('rejected_orders') or [])[-20:],
            'equity_curve': list(state.get('equity_curve') or [])[-120:],
            'pending_decision': state.get('pending_decision'),
            'audit_head': state.get('audit_head'),
        }
    if len(dates) != 1 or len(heads) != 1:
        raise RuntimeError(f'V2 ledgers are not aligned: dates={dates} heads={heads}')
    trade_date = next(iter(dates))
    audit = json.loads((state_root / 'audit' / f'{trade_date}.json').read_text(encoding='utf-8'))
    concentration_flags = ((audit.get('target_diagnostics') or {}).get('concentration_flags') or {})
    for fund_id, fund in ledgers.items():
        fund['concentration_flags'] = list(concentration_flags.get(fund_id) or [])
    return {
        'summary_version': 'v2-shadow-summary-1.1',
        'updated_at': trade_date,
        'initial_cash_per_fund': 1_000_000,
        'mode': 'FORWARD_SHADOW_ONLY',
        'regime': audit.get('regime'),
        'source_ref': audit.get('source_ref'),
        'target_diagnostics': audit.get('target_diagnostics'),
        'funds': ledgers,
        'audit_head': next(iter(heads)),
        'audit_verification': verification,
        'safety': {
            'calls_sol': False,
            'reads_v1_ledger': False,
            'writes_v1_ledger': False,
            'not_for_production_trading': True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-root', default='shadow_state/v2')
    parser.add_argument('--output', default='shadow_state/v2/summary.json')
    parser.add_argument('--web-output', help='Optional same-origin fallback snapshot for the V2 page')
    args = parser.parse_args()
    summary = build_summary(Path(args.state_root))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(summary, ensure_ascii=False, indent=2) + '\n'
    output.write_text(serialized, encoding='utf-8')
    if args.web_output:
        web_output = Path(args.web_output)
        web_output.parent.mkdir(parents=True, exist_ok=True)
        web_output.write_text(serialized, encoding='utf-8')
    print(json.dumps({
        'updated_at': summary['updated_at'],
        'audit_head': summary['audit_head'],
        'fund_metrics': {key: value['metrics'] for key, value in summary['funds'].items()},
        'safety': summary['safety'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

