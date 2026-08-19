from __future__ import annotations

import math

from .board_policy import sanitize_pending_for_retail
from .conditional_plan import PLAN_VERSION, build_exit_plan, ensure_exit_plans
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


def _locked_at_price(side: str, bar: dict, price: float, policy: ExecutionPolicy) -> bool:
    previous=float(bar.get('preclose') or 0.0)
    if previous<=0 or price<=0:
        return False
    change=price/previous-1.0
    return (side=='BUY' and change>=policy.limit_lock_ratio) or (side=='SELL' and change<=-policy.limit_lock_ratio)


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
    """Legacy fixed-price side executor retained only for old audit/correction compatibility."""
    side = str(side).upper()
    if side not in {'SELL', 'BUY'}:
        raise ValueError(f'unsupported split execution side: {side}')

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
                rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 'limit_down_locked', phase='legacy_sell'))
                continue
            position = positions[symbol]
            if position.get('acquired_date') == trade_date:
                rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 't_plus_one_locked', phase='legacy_sell'))
                continue
            quantity = min(int(position.get('qty') or 0), round_lot(abs(diff) / reference, policy))
            if quantity <= 0:
                rejected.append(_rejection(state, pending, trade_date, symbol, 'SELL', 'below_board_lot', phase='legacy_sell'))
                continue
            price = slipped_price('SELL', reference, policy)
            gross = round(price * quantity, 2)
            fees = fee_for('SELL', gross, policy)
            cash += gross - fees
            position['qty'] = int(position.get('qty') or 0) - quantity
            fill = {
                'fund_id': state['fund_id'], 'decision_date': pending.get('decision_date'), 'trade_date': trade_date,
                'symbol': symbol, 'name': position.get('name') or symbol, 'side': 'SELL',
                'reference_price': reference, 'execution_price_field': price_field, 'price': price,
                'qty': quantity, 'gross': gross, 'fees': fees, 'net_cash_change': round(gross - fees, 2),
                'slippage_bps': policy.slippage_bps, 'note': note or 'V2 legacy固定价卖出',
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
                rejected.append(_rejection(state, pending, trade_date, symbol, 'BUY', 'limit_up_locked', phase='legacy_buy'))
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
                rejected.append(_rejection(state, pending, trade_date, symbol, 'BUY', reason, cash=round(cash, 2), phase='legacy_buy'))
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
                    'name': target.get('name') or symbol, 'qty': quantity,
                    'avg_cost': round((gross + fees) / quantity, 4), 'opened_date': trade_date,
                    'acquired_date': trade_date, 'last_price': price,
                }
                positions[symbol] = position
            for key in ('industry', 'thesis', 'invalidation', 'v2_score'):
                if target.get(key) is not None:
                    position[key] = target.get(key)
            fill = {
                'fund_id': state['fund_id'], 'decision_date': pending.get('decision_date'), 'trade_date': trade_date,
                'symbol': symbol, 'name': position.get('name') or symbol, 'side': 'BUY',
                'reference_price': reference, 'execution_price_field': price_field, 'price': price,
                'qty': quantity, 'gross': gross, 'fees': fees, 'net_cash_change': round(-(gross + fees), 2),
                'slippage_bps': policy.slippage_bps, 'note': note or 'V2 legacy固定价买入',
            }
            fills.append(fill)

    state['cash'] = round(cash, 2)
    state.setdefault('fills', []).extend(fills)
    state.setdefault('rejected_orders', []).extend(rejected)
    return {
        'phase': side.lower(), 'decision_date': pending.get('decision_date'), 'trade_date': trade_date,
        'reference_price_field': price_field, 'reference_equity': round(reference_equity, 2),
        'fills': fills, 'rejected_orders': rejected,
        'policy_adjustments': [*retail_adjustments, *adjustments],
        'valuation_fallback_symbols': valuation_fallbacks,
        'fees': round(sum(float(x['fees']) for x in fills), 2),
    }


def _conditional_entry_reference(plan: dict, bar: dict) -> tuple[float | None, str]:
    entry=dict((plan or {}).get('entry') or {})
    mode=str(entry.get('mode') or '')
    trigger=float(entry.get('trigger_price') or 0.0)
    lower=float(entry.get('valid_min') or 0.0)
    upper=float(entry.get('valid_max') or 0.0)
    op=float(bar.get('open') or 0.0)
    high=float(bar.get('high') or 0.0)
    low=float(bar.get('low') or 0.0)
    if min(op,high,low)<=0 or high<low:
        return None,'invalid_ohlc'
    if mode=='breakout':
        if op>=trigger:
            if upper>0 and op>upper:
                return None,'gap_above_max_chase'
            return op,'gap_or_open_above_trigger'
        if high>=trigger and (upper<=0 or trigger<=upper):
            return trigger,'intraday_breakout_triggered'
        return None,'breakout_not_triggered'
    if mode=='pullback':
        if op<=trigger:
            if lower>0 and op<lower:
                return None,'gap_below_valid_floor'
            return op,'open_inside_pullback_zone'
        if low<=trigger and (lower<=0 or trigger>=lower):
            return trigger,'intraday_pullback_triggered'
        return None,'pullback_not_triggered'
    if mode=='range':
        if lower<=op<=upper:
            return op,'open_inside_entry_range'
        if op<lower:
            return None,'open_below_valid_range'
        if low<=upper and high>=lower:
            return upper,'intraday_entered_range'
        return None,'range_not_touched'
    return None,'unsupported_entry_mode'


def execute_conditional_buy_side(
    state: dict,
    pending: dict,
    bars: dict[str, dict],
    trade_date: str,
    *,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> dict:
    """Settle V2 buys only when the previous-session declared condition was really touched."""
    pending, retail_adjustments=sanitize_pending_for_retail(pending)
    positions=state.setdefault('positions',{})
    normalized={normalize_symbol(k):dict(v) for k,v in bars.items()}
    cash=float(state.get('cash') or 0.0)
    reference_equity=cash
    for symbol,position in positions.items():
        bar=normalized.get(symbol) or {}
        px=float(bar.get('open') or position.get('last_price') or position.get('avg_cost') or 0.0)
        reference_equity += int(position.get('qty') or 0)*px

    target_map,adjustments=_target_map(state,list(pending.get('targets') or []),policy)
    fills=[]; rejected=[]; planned=[]
    for symbol,target in target_map.items():
        plan=target.get('trade_plan') or {}
        if plan.get('plan_version')!=PLAN_VERSION:
            rejected.append(_rejection(state,pending,trade_date,symbol,'BUY','legacy_or_missing_conditional_plan',phase='conditional_buy'))
            continue
        if float(target.get('target_weight') or 0.0)<=0:
            continue
        bar=normalized.get(symbol)
        if not bar or str(bar.get('tradestatus','1'))!='1':
            rejected.append(_rejection(state,pending,trade_date,symbol,'BUY','missing_or_suspended_bar',phase='conditional_buy'))
            continue
        reference,reason=_conditional_entry_reference(plan,bar)
        if reference is None:
            rejected.append(_rejection(state,pending,trade_date,symbol,'BUY',reason,phase='conditional_buy'))
            continue
        if _locked_at_price('BUY',bar,reference,policy):
            rejected.append(_rejection(state,pending,trade_date,symbol,'BUY','limit_up_locked',phase='conditional_buy'))
            continue
        current=int((positions.get(symbol) or {}).get('qty') or 0)*reference
        target_value=reference_equity*float(target.get('target_weight') or 0.0)
        diff=target_value-current
        if diff<=0:
            rejected.append(_rejection(state,pending,trade_date,symbol,'BUY','already_at_or_above_target',phase='conditional_buy'))
            continue
        planned.append((float(target.get('opportunity_score') or 0.0),diff,symbol,reference,reason,target,plan))

    for _opp,diff,symbol,reference,reason,target,plan in sorted(planned,reverse=True):
        price=slipped_price('BUY',reference,policy)
        quantity=round_lot(diff/price,policy)
        while quantity>0:
            gross=round(price*quantity,2); fees=fee_for('BUY',gross,policy)
            if gross+fees<=cash:
                break
            quantity-=policy.lot_size
        if quantity<=0:
            rejected.append(_rejection(state,pending,trade_date,symbol,'BUY','insufficient_cash_or_below_lot',cash=round(cash,2),phase='conditional_buy'))
            continue
        gross=round(price*quantity,2); fees=fee_for('BUY',gross,policy); cash-=gross+fees
        old=positions.get(symbol)
        if old:
            old_qty=int(old.get('qty') or 0); new_qty=old_qty+quantity
            old['avg_cost']=round((float(old.get('avg_cost') or 0.0)*old_qty+gross+fees)/new_qty,4)
            old['qty']=new_qty; old['acquired_date']=trade_date; old['last_price']=price
            position=old
        else:
            position={'name':target.get('name') or symbol,'qty':quantity,'avg_cost':round((gross+fees)/quantity,4),
                      'opened_date':trade_date,'acquired_date':trade_date,'last_price':price}
            positions[symbol]=position
        for key in ('industry','thesis','invalidation','v2_score','opportunity_score','setup'):
            if target.get(key) is not None:
                position[key]=target.get(key)
        state.setdefault('exit_plans',{})[symbol]=build_exit_plan(float(position['avg_cost']),plan,trade_date)
        fills.append({
            'fund_id':state['fund_id'],'decision_date':pending.get('decision_date'),'trade_date':trade_date,
            'symbol':symbol,'name':position.get('name') or symbol,'side':'BUY',
            'reference_price':round(reference,3),'execution_price_field':'conditional_trigger','price':price,
            'qty':quantity,'gross':gross,'fees':fees,'net_cash_change':round(-(gross+fees),2),
            'slippage_bps':policy.slippage_bps,'plan_version':PLAN_VERSION,'setup':plan.get('setup'),
            'trigger_reason':reason,'trigger_price':(plan.get('entry') or {}).get('trigger_price'),
            'note':'V2 上一交易日条件计划 · 15:10结算当天已触发买入',
        })

    state['cash']=round(cash,2)
    state.setdefault('fills',[]).extend(fills)
    state.setdefault('rejected_orders',[]).extend(rejected)
    return {
        'phase':'conditional_buy','decision_date':pending.get('decision_date'),'trade_date':trade_date,
        'reference_price_field':'conditional_trigger','reference_equity':round(reference_equity,2),
        'fills':fills,'rejected_orders':rejected,'policy_adjustments':[ *retail_adjustments,*adjustments],
        'valuation_fallback_symbols':[],'fees':round(sum(float(x['fees']) for x in fills),2),
    }


def execute_conditional_exit_scan(
    state: dict,
    bars: dict[str, dict],
    trade_date: str,
    *,
    clock: str,
    pending: dict | None = None,
    policy: ExecutionPolicy = DEFAULT_POLICY,
) -> dict:
    """V2 checkpoint: protective and profit exits only; never sell just because the clock says 09:40."""
    ensure_exit_plans(state,trade_date)
    positions=state.setdefault('positions',{})
    plans=state.setdefault('exit_plans',{})
    normalized={normalize_symbol(k):dict(v) for k,v in bars.items()}
    cash=float(state.get('cash') or 0.0)
    fills=[]; rejected=[]; checks=[]
    pending_for_rejection=pending or {'decision_date':None,'targets':[]}

    for symbol in list(positions):
        position=positions.get(symbol)
        if not position:
            continue
        bar=normalized.get(symbol) or {}
        reference=float(bar.get('close') or 0.0)
        if reference<=0 or str(bar.get('tradestatus','1'))!='1':
            checks.append({'symbol':symbol,'action':'WAIT','reason':'no_live_quote'}); continue
        position['last_price']=reference
        plan=plans.get(symbol) or {}
        session_high=float(bar.get('high') or reference)
        if not math.isfinite(session_high) or session_high < reference:
            session_high=reference
        plan['highest_price']=round(max(float(plan.get('highest_price') or reference),reference,session_high),4)
        if str(position.get('acquired_date') or '')[:10]==trade_date:
            checks.append({'symbol':symbol,'action':'WAIT','reason':'t_plus_one_locked'}); continue
        if _locked_at_price('SELL',bar,reference,policy):
            rejected.append(_rejection(state,pending_for_rejection,trade_date,symbol,'SELL','limit_down_locked',phase='conditional_exit'))
            continue
        hard=float(plan.get('hard_stop_price') or 0.0)
        take=float(plan.get('take_profit_price') or 0.0)
        activation=float(plan.get('trailing_activation_price') or 0.0)
        trail_dd=float(plan.get('trailing_drawdown_pct') or 0.025)
        highest=float(plan.get('highest_price') or reference)
        trail_stop=highest*(1.0-trail_dd)
        rotation=bool(plan.get('rotation_exit')); rotation_min=float(plan.get('rotation_min_price') or 0.0)
        max_hold=int(plan.get('max_hold_days') or 3); sessions=int(plan.get('sessions_held') or 0)
        reason=None; sell_all=True
        if hard>0 and reference<=hard:
            reason='hard_stop'
        elif (highest>=activation or plan.get('partial_taken')) and reference<=trail_stop:
            reason='trailing_stop'
        elif rotation and rotation_min>0 and reference>=rotation_min:
            reason='rotation_exit_price_reached'
        elif take>0 and reference>=take:
            reason='take_profit'; sell_all=False
        elif sessions>=max_hold and clock>='14:55':
            reason='max_hold_time_exit'
        if not reason:
            checks.append({'symbol':symbol,'action':'HOLD','reason':'conditions_not_met','price':round(reference,3),
                           'hard_stop':hard,'take_profit':take,'trailing_stop':round(trail_stop,3),'rotation_min_price':rotation_min or None})
            continue
        quantity=int(position.get('qty') or 0)
        if not sell_all and not plan.get('partial_taken') and quantity>=2*policy.lot_size:
            quantity=round_lot(quantity*0.5,policy)
        else:
            sell_all=True
        if quantity<=0:
            rejected.append(_rejection(state,pending_for_rejection,trade_date,symbol,'SELL','below_board_lot',phase='conditional_exit'))
            continue
        price=slipped_price('SELL',reference,policy); gross=round(price*quantity,2); fees=fee_for('SELL',gross,policy)
        cash+=gross-fees; position['qty']=int(position.get('qty') or 0)-quantity
        fills.append({
            'fund_id':state['fund_id'],'decision_date':(pending or {}).get('decision_date'),'trade_date':trade_date,
            'symbol':symbol,'name':position.get('name') or symbol,'side':'SELL','reference_price':round(reference,3),
            'execution_price_field':'live_conditional','price':price,'qty':quantity,'gross':gross,'fees':fees,
            'net_cash_change':round(gross-fees,2),'slippage_bps':policy.slippage_bps,'plan_version':PLAN_VERSION,
            'exit_reason':reason,'scan_time':clock,'note':f'V2 条件计划 · {clock}检查触发 {reason}',
        })
        if position['qty']<=0 or sell_all:
            positions.pop(symbol,None); plans.pop(symbol,None)
        else:
            plan['partial_taken']=True
            plan['trailing_activation_price']=min(float(plan.get('trailing_activation_price') or reference),reference)
            checks.append({'symbol':symbol,'action':'PARTIAL_SELL','reason':reason,'remaining_qty':position['qty']})

    state['cash']=round(cash,2)
    state.setdefault('fills',[]).extend(fills)
    state.setdefault('rejected_orders',[]).extend(rejected)
    return {
        'phase':'conditional_exit','decision_date':(pending or {}).get('decision_date'),'trade_date':trade_date,
        'reference_price_field':'live_conditional','fills':fills,'rejected_orders':rejected,'checks':checks,
        'policy_adjustments':[],'valuation_fallback_symbols':[],
        'fees':round(sum(float(x['fees']) for x in fills),2),
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