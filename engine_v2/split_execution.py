from __future__ import annotations

from .board_policy import sanitize_pending_for_retail
from .shadow_ledger import (
    DEFAULT_POLICY,
    ExecutionPolicy,
    _rejection,
    _target_map,
    fee_for,
    normalize_symbol,
    round_lot,
    slipped_price,
)


def _reference_price(bar: dict, price_field: str) -> float:
    return float(bar.get(price_field) or 0.0)


def _locked_at_reference(side: str, bar: dict, price_field: str, policy: ExecutionPolicy) -> bool:
    previous = float(bar.get('preclose') or 0.0)
    price = _reference_price(bar, price_field)
    if previous <= 0 or price <= 0:
        return False
    change = price / previous - 1.0
    return (
        side == 'BUY' and change >= policy.limit_lock_ratio
    ) or (
        side == 'SELL' and change <= -policy.limit_lock_ratio
    )


def execute_pending_side(
    state: dict,
    pending: dict,
    bars: dict[str, dict],
    trade_date: str,
    *,
    side: str,
    price_field: str = 'close',
    note: str | None = None,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> dict:
    """Execute only one side of a previous-session target at a chosen reference price.

    This is used by V2's split model: SELL at the 09:40 live quote, BUY at the completed
    session close. It mutates the ledger and records fills/rejections, but deliberately
    does not clear `pending_decision`; the caller owns phase completion.
    """
    side = str(side).upper()
    if side not in {'SELL', 'BUY'}:
        raise ValueError(f'unsupported split execution side: {side}')

    # Final retail-account safety boundary. This is intentionally repeated at execution
    # time so stale/manual targets cannot bypass the upstream main-board-only universe.
    pending, retail_adjustments = sanitize_pending_for_retail(pending)

    positions = state.setdefault('positions', {})
    normalized_bars = {normalize_symbol(k): dict(v) for k, v in bars.items()}
    cash = float(state.get('cash') or 0.0)
    reference_equity = cash
    valuation_fallbacks: list[str] = []
    for symbol, position in positions.items():
        bar = normalized_bars.get(symbol) or {}
        price = _reference_price(bar, price_field) or float(position.get('last_price') or position.get('avg_cost') or 0.0)
        if not bar or _reference_price(bar, price_field) <= 0:
            valuation_fallbacks.append(symbol)
        reference_equity += float(position.get('qty') or 0) * price

    target_map, adjustments = _target_map(state, list(pending.get('targets') or []), policy)
    symbols = set(positions) | set(target_map)
    diffs: list[tuple[float, str, float]] = []
    rejected: list[dict] = []
    for symbol in sorted(symbols):
        bar = normalized_bars.get(symbol)
        current_qty = float((positions.get(symbol) or {}).get('qty') or 0)
        target_weight = float((target_map.get(symbol) or {}).get('target_weight') or 0.0)
        if not bar:
            rejected.append(_rejection(state, pending, trade_date, symbol, side, 'missing_execution_bar', phase=side.lower()))
            continue
        price = _reference_price(bar, price_field)
        if price <= 0:
            rejected.append(_rejection(state, pending, trade_date, symbol, side, f'invalid_{price_field}_price', phase=side.lower()))
            continue
        if str(bar.get('tradestatus', '1')) != '1':
            rejected.append(_rejection(state, pending, trade_date, symbol, side, 'suspended', phase=side.lower()))
            continue
        current_value = current_qty * price
        target_value = reference_equity * target_weight
        diffs.append((target_value - current_value, symbol, price))

    fills: list[dict] = []
    if side == 'SELL':
        iterable = sorted(diffs)
        for diff, symbol, reference in iterable:
            if diff >= 0 or symbol not in positions:
                continue
            if _locked_at_reference('SELL', normalized_bars[symbol], price_field, policy):
                rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 'limit_down_locked', phase='morning_sell'))
                continue
            position = positions[symbol]
            if position.get('acquired_date') == trade_date:
                rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 't_plus_one_locked', phase='morning_sell'))
                continue
            quantity = min(int(position.get('qty') or 0), round_lot(abs(diff) / reference, policy))
            if quantity <= 0:
                rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 'below_board_lot', phase='morning_sell'))
                continue
            price = slipped_price('SELL', reference, policy)
            gross = round(price * quantity, 2)
            fees = fee_for('SELL', gross, policy)
            cash += gross - fees
            position['qty'] = int(position.get('qty') or 0) - quantity
            fill = {
                'fund_id': state['fund_id'],
                'decision_date': pending.get('decision_date'),
                'trade_date': trade_date,
                'symbol': symbol,
                'name': position.get('name') or symbol,
                'side': 'SELL',
                'reference_price': reference,
                'execution_price_field': price_field,
                'price': price,
                'qty': quantity,
                'gross': gross,
                'fees': fees,
                'net_cash_change': round(gross - fees, 2),
                'slippage_bps': policy.slippage_bps,
                'note': note or 'V2 09:40目标仓位卖出/减仓',
            }
            fills.append(fill)
            if position['qty'] <= 0:
                positions.pop(symbol, None)
    else:
        iterable = sorted(diffs, reverse=True)
        for diff, symbol, reference in iterable:
            if diff <= 0:
                continue
            if _locked_at_reference('BUY', normalized_bars[symbol], price_field, policy):
                rejected.append(_rejection(state, pending, trade_date, symbol, 'BUY', 'limit_up_locked', phase='close_buy'))
                continue
            price = slipped_price('BUY', reference, policy)
            quantity = round_lot(diff / price, policy)
            while quantity > 0:
                gross = round(price * quantity, 2)
                fees = fee_for('BUY', gross, policy)
                if gross + fees <= cash:
                    break
                quantity -= policy.lot_size
            if quantity <= 0:
                reason = 'insufficient_cash' if diff >= price * policy.lot_size else 'below_board_lot'
                rejected.append(_rejection(state, pending, trade_date, symbol, 'BUY', reason, cash=round(cash, 2), phase='close_buy'))
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
                'fund_id': state['fund_id'],
                'decision_date': pending.get('decision_date'),
                'trade_date': trade_date,
                'symbol': symbol,
                'name': position.get('name') or symbol,
                'side': 'BUY',
                'reference_price': reference,
                'execution_price_field': price_field,
                'price': price,
                'qty': quantity,
                'gross': gross,
                'fees': fees,
                'net_cash_change': round(-(gross + fees), 2),
                'slippage_bps': policy.slippage_bps,
                'note': note or 'V2 收盘目标仓位买入/加仓',
            }
            fills.append(fill)

    state['cash'] = round(cash, 2)
    state.setdefault('fills', []).extend(fills)
    state.setdefault('rejected_orders', []).extend(rejected)
    return {
        'phase': side.lower(),
        'decision_date': pending.get('decision_date'),
        'trade_date': trade_date,
        'reference_price_field': price_field,
        'reference_equity': round(reference_equity, 2),
        'fills': fills,
        'rejected_orders': rejected,
        'policy_adjustments': [*retail_adjustments, *adjustments],
        'valuation_fallback_symbols': valuation_fallbacks,
        'fees': round(sum(float(x['fees']) for x in fills), 2),
    }


def combine_phase_executions(*executions: dict) -> dict:
    executions = tuple(x for x in executions if x)
    if not executions:
        return {
            'decision_date': None, 'trade_date': None, 'fills': [], 'rejected_orders': [],
            'policy_adjustments': [], 'valuation_fallback_symbols': [], 'fees': 0.0,
        }
    return {
        'decision_date': executions[0].get('decision_date'),
        'trade_date': executions[-1].get('trade_date'),
        'phases': [x.get('phase') for x in executions],
        'fills': [item for x in executions for item in (x.get('fills') or [])],
        'rejected_orders': [item for x in executions for item in (x.get('rejected_orders') or [])],
        'policy_adjustments': [item for x in executions for item in (x.get('policy_adjustments') or [])],
        'valuation_fallback_symbols': sorted({item for x in executions for item in (x.get('valuation_fallback_symbols') or [])}),
        'fees': round(sum(float(x.get('fees') or 0.0) for x in executions), 2),
    }
