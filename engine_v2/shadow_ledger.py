from __future__ import annotations

import hashlib
import json
import math
import copy
from dataclasses import asdict, dataclass
from pathlib import Path


LEDGER_SCHEMA_VERSION = 'v2-shadow-ledger-1.0'
AUDIT_SCHEMA_VERSION = 'v2-shadow-audit-1.0'
EXECUTION_POLICY_VERSION = 'v1-parity-2026-08-14'
FUND_NAMES = {
    'A': 'V2 保守稳健影子基金',
    'B': 'V2 趋势追强影子基金',
    'C': 'V2 短线快攻影子基金',
    'D': 'V2 综合判断影子基金（规则兜底）',
    'L': 'V2 长线价值影子基金',
}


@dataclass(frozen=True)
class ExecutionPolicy:
    initial_cash: float = 1_000_000.0
    lot_size: int = 100
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_sell_rate: float = 0.0005
    slippage_bps: float = 5.0
    limit_lock_ratio: float = 0.097
    d_existing_position_cap: float = 0.15
    d_new_position_cap: float = 0.10


DEFAULT_POLICY = ExecutionPolicy()


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha256_json(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()


def ledger_content_hash(state: dict) -> str:
    """Hash ledger economics while excluding the circular pointer to its audit event."""
    payload=copy.deepcopy(state)
    payload.pop('audit_head',None)
    return sha256_json(payload)


def normalize_symbol(value: object) -> str:
    text = str(value or '').strip().lower()
    if text.startswith(('sh.', 'sz.')) and len(text) >= 9:
        return text[:3] + ''.join(x for x in text[3:] if x.isdigit())[-6:]
    digits = ''.join(x for x in text if x.isdigit())[-6:]
    if len(digits) != 6:
        return text
    return ('sh.' if digits.startswith('6') else 'sz.') + digits


def fee_for(side: str, gross: float, policy: ExecutionPolicy = DEFAULT_POLICY) -> float:
    commission = max(policy.min_commission, float(gross) * policy.commission_rate)
    stamp = float(gross) * policy.stamp_duty_sell_rate if side == 'SELL' else 0.0
    return round(commission + stamp, 2)


def slipped_price(side: str, open_price: float, policy: ExecutionPolicy = DEFAULT_POLICY) -> float:
    slip = policy.slippage_bps / 10_000
    return round(float(open_price) * (1 + slip if side == 'BUY' else 1 - slip), 3)


def round_lot(quantity: float, policy: ExecutionPolicy = DEFAULT_POLICY) -> int:
    return max(0, int(math.floor(float(quantity) / policy.lot_size)) * policy.lot_size)


def locked_at_limit(side: str, bar: dict, policy: ExecutionPolicy = DEFAULT_POLICY) -> bool:
    previous = float(bar.get('preclose') or 0.0)
    opening = float(bar.get('open') or 0.0)
    if previous <= 0 or opening <= 0:
        return False
    change = opening / previous - 1.0
    return (
        side == 'BUY' and change >= policy.limit_lock_ratio
    ) or (
        side == 'SELL' and change <= -policy.limit_lock_ratio
    )


def new_ledger(fund_id: str, created_date: str, policy: ExecutionPolicy = DEFAULT_POLICY) -> dict:
    if fund_id not in FUND_NAMES:
        raise ValueError(f'unknown V2 shadow fund {fund_id}')
    return {
        'schema_version': LEDGER_SCHEMA_VERSION,
        'fund_id': fund_id,
        'name': FUND_NAMES[fund_id],
        'strategy_family': 'v2-shadow',
        'initial_cash': policy.initial_cash,
        'cash': policy.initial_cash,
        'positions': {},
        'fills': [],
        'rejected_orders': [],
        'equity_curve': [],
        'pending_decision': None,
        'decisions': [],
        'created_date': created_date,
        'last_processed_date': None,
        'audit_head': None,
        'execution_policy_version': EXECUTION_POLICY_VERSION,
        'execution_policy': asdict(policy),
    }


def load_ledger(path: Path, fund_id: str, created_date: str) -> dict:
    if not path.exists():
        return new_ledger(fund_id, created_date)
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('fund_id') != fund_id:
        raise RuntimeError(f'ledger identity mismatch {path}: {state.get("fund_id")} != {fund_id}')
    if state.get('schema_version') != LEDGER_SCHEMA_VERSION:
        raise RuntimeError(f'unsupported ledger schema in {path}: {state.get("schema_version")}')
    if float(state.get('initial_cash') or 0.0) != DEFAULT_POLICY.initial_cash:
        raise RuntimeError(f'V2 initial cash invariant violated in {path}')
    return state


def save_ledger(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def immutable_write(path: Path, payload: dict) -> bool:
    """Create an audit event once; identical retries are harmless, rewrites are rejected."""
    text = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
    if path.exists():
        current = json.loads(path.read_text(encoding='utf-8'))
        if canonical_json(current) == canonical_json(payload):
            return False
        raise RuntimeError(f'refusing to rewrite immutable V2 audit event: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8') + '\n'
    tmp.replace(path)
    return True


def portfolio_drawdown(state: dict) -> float:
    values = [float(x.get('equity') or 0.0) for x in state.get('equity_curve') or []]
    if not values:
        return 0.0
    peak = max(values)
    return values[-1] / peak - 1.0 if peak > 0 else 0.0


def _target_map(state: dict, targets: list[dict], policy: ExecutionPolicy) -> tuple[dict[str, dict], list[dict]]:
    output: dict[str, dict] = {}
    adjustments: list[dict] = []
    positions = state.get('positions') or {}
    for raw in targets:
        item = dict(raw)
        symbol = normalize_symbol(item.get('symbol') or item.get('code') or item.get('raw_code'))
        if not symbol:
            continue
        requested = max(0.0, min(1.0, float(item.get('target_weight') or 0.0)))
        applied = requested
        if state.get('fund_id') == 'D':
            cap = policy.d_existing_position_cap if symbol in positions else policy.d_new_position_cap
            applied = min(applied, cap)
            if applied < requested - 1e-12:
                adjustments.append({
                    'symbol': symbol,
                    'requested_weight': round(requested, 6),
                    'applied_weight': round(applied, 6),
                    'reason': 'd_new_position_cap' if symbol not in positions else 'd_existing_position_cap',
                })
        item['symbol'] = symbol
        item['target_weight'] = round(applied, 6)
        output[symbol] = item
    return output, adjustments


def _rejection(
    state: dict,
    pending: dict,
    trade_date: str,
    symbol: str,
    side: str,
    reason: str,
    **details,
) -> dict:
    target = next((x for x in pending.get('targets') or [] if normalize_symbol(x.get('symbol')) == symbol), {})
    return {
        'fund_id': state['fund_id'],
        'decision_date': pending.get('decision_date'),
        'trade_date': trade_date,
        'symbol': symbol,
        'name': target.get('name') or (state.get('positions') or {}).get(symbol, {}).get('name') or symbol,
        'side': side,
        'target_weight': target.get('target_weight'),
        'reason': reason,
        'details': details,
    }


def execute_pending(
    state: dict,
    pending: dict,
    bars: dict[str, dict],
    trade_date: str,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> dict:
    """Execute one previous-session decision using the same assumptions as V1.

    Unlike V1, every blocked or too-small intent is retained for audit.
    """
    positions = state.setdefault('positions', {})
    normalized_bars = {normalize_symbol(k): dict(v) for k, v in bars.items()}
    cash = float(state.get('cash') or 0.0)
    opening_equity = cash
    valuation_fallbacks = []
    for symbol, position in positions.items():
        bar = normalized_bars.get(symbol) or {}
        opening = float(bar.get('open') or position.get('last_price') or position.get('avg_cost') or 0.0)
        if not bar or float(bar.get('open') or 0.0) <= 0:
            valuation_fallbacks.append(symbol)
        opening_equity += float(position.get('qty') or 0) * opening

    target_map, adjustments = _target_map(state, list(pending.get('targets') or []), policy)
    symbols = set(positions) | set(target_map)
    diffs: list[tuple[float, str, float]] = []
    rejected: list[dict] = []
    for symbol in sorted(symbols):
        bar = normalized_bars.get(symbol)
        current_qty = float((positions.get(symbol) or {}).get('qty') or 0)
        target_weight = float((target_map.get(symbol) or {}).get('target_weight') or 0.0)
        side = 'BUY' if target_weight > 0 and current_qty <= 0 else 'SELL' if target_weight <= 0 and current_qty > 0 else 'REBALANCE'
        if not bar:
            rejected.append(_rejection(state, pending, trade_date, symbol, side, 'missing_execution_bar'))
            continue
        opening = float(bar.get('open') or 0.0)
        if opening <= 0:
            rejected.append(_rejection(state, pending, trade_date, symbol, side, 'invalid_open_price'))
            continue
        if str(bar.get('tradestatus', '1')) != '1':
            rejected.append(_rejection(state, pending, trade_date, symbol, side, 'suspended'))
            continue
        current_value = current_qty * opening
        target_value = opening_equity * target_weight
        diffs.append((target_value - current_value, symbol, opening))

    fills: list[dict] = []
    for diff, symbol, opening in sorted(diffs):
        if diff >= 0 or symbol not in positions:
            continue
        if locked_at_limit('SELL', normalized_bars[symbol], policy):
            rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 'limit_down_locked'))
            continue
        position = positions[symbol]
        if position.get('acquired_date') == trade_date:
            rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 't_plus_one_locked'))
            continue
        quantity = min(int(position.get('qty') or 0), round_lot(abs(diff) / opening, policy))
        if quantity <= 0:
            rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 'below_board_lot'))
            continue
        price = slipped_price('SELL', opening, policy)
        gross = round(price * quantity, 2)
        fees = fee_for('SELL', gross, policy)
        cash += gross - fees
        position['qty'] = int(position.get('qty') or 0) - quantity
        fill = {
            'fund_id': state['fund_id'], 'decision_date': pending.get('decision_date'),
            'trade_date': trade_date, 'symbol': symbol,
            'name': position.get('name') or symbol, 'side': 'SELL',
            'open_price': opening, 'price': price, 'qty': quantity,
            'gross': gross, 'fees': fees, 'net_cash_change': round(gross - fees, 2),
            'slippage_bps': policy.slippage_bps, 'note': 'V2 目标仓位再平衡',
        }
        fills.append(fill)
        if position['qty'] <= 0:
            positions.pop(symbol, None)

    for diff, symbol, opening in sorted(diffs, reverse=True):
        if diff <= 0:
            continue
        if locked_at_limit('BUY', normalized_bars[symbol], policy):
            rejected.append(_rejection(state, pending, trade_date, symbol, 'BUY', 'limit_up_locked'))
            continue
        price = slipped_price('BUY', opening, policy)
        quantity = round_lot(diff / price, policy)
        while quantity > 0:
            gross = round(price * quantity, 2)
            fees = fee_for('BUY', gross, policy)
            if gross + fees <= cash:
                break
            quantity -= policy.lot_size
        if quantity <= 0:
            reason = 'insufficient_cash' if diff >= price * policy.lot_size else 'below_board_lot'
            rejected.append(_rejection(state, pending, trade_date, symbol, 'BUY', reason, cash=round(cash, 2)))
            continue
        gross = round(price * quantity, 2)
        fees = fee_for('BUY', gross, policy)
        cash -= gross + fees
        target = target_map.get(symbol) or {}
        old = positions.get(symbol)
        if old:
            old_quantity = int(old.get('qty') or 0)
            new_quantity = old_quantity + quantity
            old['avg_cost'] = round((float(old.get('avg_cost') or 0.0) * old_quantity + gross + fees) / new_quantity, 4)
            old['qty'] = new_quantity
            old['acquired_date'] = trade_date
            position = old
        else:
            position = {
                'name': target.get('name') or symbol,
                'qty': quantity,
                'avg_cost': round((gross + fees) / quantity, 4),
                'opened_date': trade_date,
                'acquired_date': trade_date,
                'last_price': price,
            }
            positions[symbol] = position
        for key in ('industry', 'thesis', 'invalidation', 'v2_score'):
            if target.get(key) is not None:
                position[key] = target.get(key)
        fill = {
            'fund_id': state['fund_id'], 'decision_date': pending.get('decision_date'),
            'trade_date': trade_date, 'symbol': symbol,
            'name': position.get('name') or symbol, 'side': 'BUY',
            'open_price': opening, 'price': price, 'qty': quantity,
            'gross': gross, 'fees': fees, 'net_cash_change': round(-(gross + fees), 2),
            'slippage_bps': policy.slippage_bps, 'note': 'V2 目标仓位再平衡',
        }
        fills.append(fill)

    state['cash'] = round(cash, 2)
    state.setdefault('fills', []).extend(fills)
    state.setdefault('rejected_orders', []).extend(rejected)
    return {
        'decision_date': pending.get('decision_date'),
        'trade_date': trade_date,
        'opening_equity': round(opening_equity, 2),
        'fills': fills,
        'rejected_orders': rejected,
        'policy_adjustments': adjustments,
        'valuation_fallback_symbols': valuation_fallbacks,
        'fees': round(sum(float(x['fees']) for x in fills), 2),
    }


def expire_pending(state: dict, pending: dict, trade_date: str, previous_trade_date: str | None) -> dict:
    rejected = []
    for target in pending.get('targets') or []:
        symbol = normalize_symbol(target.get('symbol'))
        rejected.append(_rejection(
            state, pending, trade_date, symbol, 'CANCEL', 'stale_pending_decision',
            expected_previous_trade_date=previous_trade_date,
        ))
    state.setdefault('rejected_orders', []).extend(rejected)
    return {
        'decision_date': pending.get('decision_date'), 'trade_date': trade_date,
        'opening_equity': None, 'fills': [], 'rejected_orders': rejected,
        'policy_adjustments': [], 'valuation_fallback_symbols': [], 'fees': 0.0,
    }


def mark_to_market(state: dict, bars: dict[str, dict], trade_date: str, fees_today: float = 0.0) -> dict:
    normalized_bars = {normalize_symbol(k): dict(v) for k, v in bars.items()}
    cash = float(state.get('cash') or 0.0)
    market_value = 0.0
    holdings = []
    warnings = []
    for symbol, position in sorted((state.get('positions') or {}).items()):
        bar = normalized_bars.get(symbol) or {}
        close = float(bar.get('close') or position.get('last_price') or position.get('avg_cost') or 0.0)
        if not bar or float(bar.get('close') or 0.0) <= 0:
            warnings.append(symbol)
        position['last_price'] = close
        value = int(position.get('qty') or 0) * close
        market_value += value
        holdings.append({
            'symbol': symbol, 'name': position.get('name') or symbol,
            'qty': int(position.get('qty') or 0), 'avg_cost': float(position.get('avg_cost') or 0.0),
            'close': close, 'market_value': round(value, 2),
            'industry': position.get('industry'), 'thesis': position.get('thesis'),
            'invalidation': position.get('invalidation'),
        })
    equity = round(cash + market_value, 2)
    curve = state.setdefault('equity_curve', [])
    if any(str(x.get('date'))[:10] == trade_date for x in curve):
        raise RuntimeError(f'duplicate V2 equity mark for {state.get("fund_id")} on {trade_date}')
    point = {
        'date': trade_date, 'equity': equity, 'cash': round(cash, 2),
        'market_value': round(market_value, 2), 'fees': round(float(fees_today), 2),
    }
    curve.append(point)
    return {'equity': equity, 'cash': round(cash, 2), 'market_value': round(market_value, 2), 'holdings': holdings, 'valuation_fallback_symbols': warnings}


def compact_target(target: dict) -> dict:
    keys = (
        'symbol', 'raw_code', 'name', 'industry', 'industry_code', 'target_weight',
        'v2_score', 'thesis', 'invalidation', 'fundamental_ready', 'limit_status',
        'opportunity_score', 'setup',
    )
    item = {key: target.get(key) for key in keys if key in target}
    item['symbol'] = normalize_symbol(target.get('symbol') or target.get('code') or target.get('raw_code'))
    item['target_weight'] = round(float(target.get('target_weight') or 0.0), 6)
    if 'trade_plan' in target:
        item['trade_plan'] = copy.deepcopy(target.get('trade_plan'))
    return item


def build_pending_decision(fund_id: str, targets_payload: dict, source_ref: dict) -> dict:
    targets = [compact_target(x) for x in (targets_payload.get('targets') or {}).get(fund_id, [])]
    plan_version = str(targets_payload.get('plan_version') or '')
    if plan_version:
        missing = [
            str(target.get('symbol') or '?')
            for target in targets
            if ((target.get('trade_plan') or {}).get('plan_version') != plan_version)
        ]
        if missing:
            raise RuntimeError(
                f'V2 pending decision would drop or mismatch conditional trade plans for {fund_id}: {missing}'
            )
    return {
        'decision_date': targets_payload.get('trade_date'),
        'execute_on': 'next_trading_session_open',
        'strategy_version': f'v2-{fund_id.lower()}-deterministic-0.1',
        'target_version': targets_payload.get('target_version'),
        'regime': targets_payload.get('regime'),
        'portfolio_stats': (targets_payload.get('stats') or {}).get(fund_id),
        'targets': targets,
        'source_ref': source_ref,
        'calls_sol': False,
    }


def validate_ledger(state: dict) -> None:
    if float(state.get('cash') or 0.0) < -0.01:
        raise RuntimeError(f'negative cash in V2 ledger {state.get("fund_id")}: {state.get("cash")}')
    for symbol, position in (state.get('positions') or {}).items():
        if normalize_symbol(symbol) != symbol:
            raise RuntimeError(f'non-normalized symbol in V2 ledger: {symbol}')
        quantity = int(position.get('qty') or 0)
        if quantity <= 0 or quantity % DEFAULT_POLICY.lot_size:
            raise RuntimeError(f'invalid board lot in V2 ledger {symbol}: {quantity}')
    dates = [str(x.get('date'))[:10] for x in state.get('equity_curve') or []]
    if dates != sorted(set(dates)):
        raise RuntimeError(f'non-monotonic or duplicate V2 equity curve in {state.get("fund_id")}')
