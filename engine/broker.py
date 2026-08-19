from __future__ import annotations
import math
from .config import CONFIG
from .trading_plan import PLAN_VERSION, build_exit_plan_from_fill
from .universe import is_main_board


def fee_for(side: str, gross: float) -> float:
    commission = max(CONFIG.min_commission, gross * CONFIG.commission_rate)
    stamp = gross * CONFIG.stamp_duty_sell_rate if side == 'SELL' else 0.0
    return round(commission + stamp, 2)


def slipped_price(side: str, reference_price: float) -> float:
    slip = CONFIG.slippage_bps / 10_000
    return round(reference_price * (1 + slip if side == 'BUY' else 1 - slip), 3)


def round_lot(qty: float) -> int:
    return max(0, int(math.floor(qty / CONFIG.lot_size)) * CONFIG.lot_size)


def _bar_price(bar: dict, price_field: str) -> float:
    return float(bar.get(price_field) or 0.0)


def _locked_at_limit(side: str, bar: dict, price_field: str = 'open') -> bool:
    pre=float(bar.get('preclose') or 0)
    px=_bar_price(bar, price_field)
    if pre <= 0 or px <= 0:
        return False
    change=px/pre-1
    return (side == 'BUY' and change >= 0.097) or (side == 'SELL' and change <= -0.097)


def _locked_at_reference(side: str, bar: dict, reference_price: float) -> bool:
    pre=float(bar.get('preclose') or 0)
    if pre <= 0 or reference_price <= 0:
        return False
    change=reference_price/pre-1
    return (side == 'BUY' and change >= 0.097) or (side == 'SELL' and change <= -0.097)


def _execution_target_map(state: dict, targets: list[dict]) -> dict[str,dict]:
    """Final deterministic safety clamp before any simulated order is sized."""
    positions=state.get('positions') or {}
    d_fund=state.get('fund_id') in ('D_MAIN','D')
    out={}
    for x in targets:
        sym=x['symbol']
        item={**x}
        weight=max(0.0,float(item.get('target_weight',0.0)))
        if not is_main_board(sym):
            weight=0.0
        elif d_fund:
            weight=min(weight,CONFIG.max_single_weight_d)
            if sym not in positions:
                weight=min(weight,CONFIG.max_new_position_weight_d)
        item['target_weight']=weight
        out[sym]=item
    return out


def execute_target_weights(
    state: dict,
    targets: list[dict],
    bars: dict[str, dict],
    trade_date: str,
    *,
    sides: tuple[str, ...] = ('SELL', 'BUY'),
    price_field: str = 'open',
    note: str = '目标仓位再平衡',
) -> list[dict]:
    """Legacy deterministic rebalance kept for old reports/tests and explicit corrections.

    Production conditional-plan flows call execute_conditional_buys / execute_conditional_sells instead.
    """
    allowed={str(x).upper() for x in sides}
    fills = []
    positions = state.setdefault('positions', {})
    cash = float(state['cash'])
    total_equity = cash
    for sym, p in positions.items():
        bar = bars.get(sym)
        px = _bar_price(bar or {}, price_field) or float(p.get('last_price', p['avg_cost']))
        total_equity += p['qty'] * px

    target_map = _execution_target_map(state,targets)
    symbols = set(positions) | set(target_map)
    diffs = []
    for sym in symbols:
        bar = bars.get(sym)
        px = _bar_price(bar or {}, price_field)
        if not bar or px <= 0 or str(bar.get('tradestatus', '1')) != '1':
            continue
        current_val = positions.get(sym, {}).get('qty', 0) * px
        target_val = total_equity * float(target_map.get(sym, {}).get('target_weight', 0.0))
        diffs.append((target_val - current_val, sym, px))

    if 'SELL' in allowed:
        for diff, sym, ref_px in sorted(diffs):
            if diff >= 0 or sym not in positions:
                continue
            if _locked_at_limit('SELL', bars[sym], price_field):
                continue
            p = positions[sym]
            if p.get('acquired_date') == trade_date:
                continue
            qty = min(p['qty'], round_lot(abs(diff) / ref_px))
            if qty <= 0:
                continue
            px = slipped_price('SELL', ref_px)
            gross = round(px * qty, 2)
            fees = fee_for('SELL', gross)
            cash += gross - fees
            p['qty'] -= qty
            fills.append({'symbol': sym, 'name': p.get('name', sym), 'side': 'SELL', 'trade_date': trade_date,
                          'price': px, 'qty': qty, 'gross': gross, 'fees': fees, 'note': note,
                          'execution_price_field': price_field})
            if p['qty'] <= 0:
                positions.pop(sym, None)

    if 'BUY' in allowed:
        cash_now=cash
        equity_now=cash_now
        for sym,p in positions.items():
            bar=bars.get(sym) or {}
            px=_bar_price(bar,price_field) or float(p.get('last_price',p['avg_cost']))
            equity_now += p['qty']*px
        buy_diffs=[]
        for sym in set(positions)|set(target_map):
            bar=bars.get(sym)
            ref_px=_bar_price(bar or {},price_field)
            if not bar or ref_px<=0 or str(bar.get('tradestatus','1'))!='1':
                continue
            current_val=positions.get(sym,{}).get('qty',0)*ref_px
            target_val=equity_now*float(target_map.get(sym,{}).get('target_weight',0.0))
            buy_diffs.append((target_val-current_val,sym,ref_px))

        for diff, sym, ref_px in sorted(buy_diffs, reverse=True):
            if diff <= 0:
                continue
            if _locked_at_limit('BUY', bars[sym], price_field):
                continue
            px = slipped_price('BUY', ref_px)
            qty = round_lot(diff / px)
            while qty > 0:
                gross = round(px * qty, 2)
                fees = fee_for('BUY', gross)
                if gross + fees <= cash:
                    break
                qty -= CONFIG.lot_size
            if qty <= 0:
                continue
            gross = round(px * qty, 2)
            fees = fee_for('BUY', gross)
            cash -= gross + fees
            old = positions.get(sym)
            name = target_map.get(sym, {}).get('name', sym)
            if old:
                new_qty = old['qty'] + qty
                old['avg_cost'] = round((old['avg_cost'] * old['qty'] + gross + fees) / new_qty, 4)
                old['qty'] = new_qty
                old['acquired_date'] = trade_date
            else:
                positions[sym] = {'name': name, 'qty': qty, 'avg_cost': round((gross + fees) / qty, 4),
                                  'acquired_date': trade_date, 'last_price': px}
            fills.append({'symbol': sym, 'name': name, 'side': 'BUY', 'trade_date': trade_date,
                          'price': px, 'qty': qty,'gross': gross, 'fees': fees, 'note': note,
                          'execution_price_field': price_field})

    state['cash'] = round(cash, 2)
    return fills


def _conditional_entry_reference(plan: dict, bar: dict) -> tuple[float | None, str]:
    """Resolve whether yesterday's price condition was touched by today's completed OHLC bar."""
    entry = dict((plan or {}).get('entry') or {})
    mode = str(entry.get('mode') or '')
    trigger = float(entry.get('trigger_price') or 0.0)
    lower = float(entry.get('valid_min') or 0.0)
    upper = float(entry.get('valid_max') or 0.0)
    op = float(bar.get('open') or 0.0)
    high = float(bar.get('high') or 0.0)
    low = float(bar.get('low') or 0.0)
    if min(op, high, low) <= 0 or high < low:
        return None, 'invalid_ohlc'

    if mode == 'breakout':
        if op >= trigger:
            if upper > 0 and op > upper:
                return None, 'gap_above_max_chase'
            return op, 'gap_or_open_above_trigger'
        if high >= trigger and (upper <= 0 or trigger <= upper):
            return trigger, 'intraday_breakout_triggered'
        return None, 'breakout_not_triggered'

    if mode == 'pullback':
        if op <= trigger:
            if lower > 0 and op < lower:
                return None, 'gap_below_valid_floor'
            return op, 'open_inside_pullback_zone'
        if low <= trigger and (lower <= 0 or trigger >= lower):
            return trigger, 'intraday_pullback_triggered'
        return None, 'pullback_not_triggered'

    if mode == 'range':
        if lower <= op <= upper:
            return op, 'open_inside_entry_range'
        if op < lower:
            return None, 'open_below_valid_range'
        if low <= upper and high >= lower:
            return upper, 'intraday_entered_range'
        return None, 'range_not_touched'

    return None, 'unsupported_entry_mode'


def execute_conditional_buys(
    state: dict,
    targets: list[dict],
    bars: dict[str, dict],
    trade_date: str,
    *,
    note: str = '上一交易日条件计划 · 15:10结算当天已触发买单',
) -> tuple[list[dict], list[dict]]:
    """Settle only entry plans whose pre-declared price condition was actually touched."""
    positions=state.setdefault('positions',{})
    cash=float(state.get('cash') or 0.0)
    target_map=_execution_target_map(state,targets)
    equity_ref=cash
    for sym,p in positions.items():
        bar=bars.get(sym) or {}
        px=float(bar.get('open') or p.get('last_price') or p.get('avg_cost') or 0.0)
        equity_ref += int(p.get('qty') or 0)*px

    planned=[]
    skipped=[]
    for sym,target in target_map.items():
        plan=target.get('trade_plan') or {}
        if plan.get('plan_version') != PLAN_VERSION:
            skipped.append({'symbol':sym,'reason':'legacy_or_missing_conditional_plan'})
            continue
        if float(target.get('target_weight') or 0.0) <= 0:
            continue
        bar=bars.get(sym)
        if not bar or str(bar.get('tradestatus','1'))!='1':
            skipped.append({'symbol':sym,'reason':'missing_or_suspended_bar'})
            continue
        reference,reason=_conditional_entry_reference(plan,bar)
        if reference is None:
            skipped.append({'symbol':sym,'reason':reason})
            continue
        if _locked_at_reference('BUY',bar,reference):
            skipped.append({'symbol':sym,'reason':'limit_up_locked'})
            continue
        current_val=int((positions.get(sym) or {}).get('qty') or 0)*reference
        target_val=equity_ref*float(target.get('target_weight') or 0.0)
        diff=target_val-current_val
        if diff <= 0:
            skipped.append({'symbol':sym,'reason':'already_at_or_above_target'})
            continue
        planned.append((float(target.get('opportunity_score') or 0.0),diff,sym,reference,reason,target,plan))

    fills=[]
    for _opp,diff,sym,reference,trigger_reason,target,plan in sorted(planned,reverse=True):
        px=slipped_price('BUY',reference)
        qty=round_lot(diff/px)
        while qty>0:
            gross=round(px*qty,2)
            fees=fee_for('BUY',gross)
            if gross+fees <= cash:
                break
            qty-=CONFIG.lot_size
        if qty<=0:
            skipped.append({'symbol':sym,'reason':'insufficient_cash_or_below_lot'})
            continue
        gross=round(px*qty,2)
        fees=fee_for('BUY',gross)
        cash-=gross+fees
        old=positions.get(sym)
        name=target.get('name',sym)
        if old:
            new_qty=int(old.get('qty') or 0)+qty
            old['avg_cost']=round((float(old.get('avg_cost') or 0.0)*int(old.get('qty') or 0)+gross+fees)/new_qty,4)
            old['qty']=new_qty
            old['acquired_date']=trade_date
            old['last_price']=px
            position=old
        else:
            position={'name':name,'qty':qty,'avg_cost':round((gross+fees)/qty,4),
                      'acquired_date':trade_date,'last_price':px}
            positions[sym]=position
        state.setdefault('exit_plans',{})[sym]=build_exit_plan_from_fill(float(position['avg_cost']),plan,trade_date)
        fills.append({
            'symbol':sym,'name':name,'side':'BUY','trade_date':trade_date,
            'reference_price':round(reference,3),'price':px,'qty':qty,'gross':gross,'fees':fees,
            'note':note,'execution_price_field':'conditional_trigger','plan_version':PLAN_VERSION,
            'setup':plan.get('setup'),'trigger_reason':trigger_reason,
            'trigger_price':(plan.get('entry') or {}).get('trigger_price'),
        })
    state['cash']=round(cash,2)
    return fills,skipped


def execute_conditional_sells(
    state: dict,
    bars: dict[str, dict],
    trade_date: str,
    *,
    clock: str,
) -> tuple[list[dict], list[dict]]:
    """Check hard-stop/take-profit/trailing/rotation/time exits at an intraday quote checkpoint."""
    positions=state.setdefault('positions',{})
    plans=state.setdefault('exit_plans',{})
    cash=float(state.get('cash') or 0.0)
    fills=[]
    checks=[]

    for sym in list(positions):
        p=positions.get(sym)
        if not p:
            continue
        bar=bars.get(sym) or {}
        ref=float(bar.get('close') or 0.0)
        if ref<=0 or str(bar.get('tradestatus','1'))!='1':
            checks.append({'symbol':sym,'action':'WAIT','reason':'no_live_quote'})
            continue
        p['last_price']=ref
        plan=plans.get(sym)
        if not plan or plan.get('plan_version') != PLAN_VERSION:
            # Fail closed: never invent a discretionary sell price for an unmigrated position.
            checks.append({'symbol':sym,'action':'WAIT','reason':'missing_conditional_exit_plan'})
            continue
        plan['highest_price']=round(max(float(plan.get('highest_price') or ref),ref),4)
        if str(p.get('acquired_date') or '')[:10] == trade_date:
            checks.append({'symbol':sym,'action':'WAIT','reason':'t_plus_one_locked'})
            continue
        if _locked_at_reference('SELL',bar,ref):
            checks.append({'symbol':sym,'action':'WAIT','reason':'limit_down_locked'})
            continue

        hard_stop=float(plan.get('hard_stop_price') or 0.0)
        take_profit=float(plan.get('take_profit_price') or 0.0)
        trail_activation=float(plan.get('trailing_activation_price') or 0.0)
        trail_dd=float(plan.get('trailing_drawdown_pct') or 0.025)
        highest=float(plan.get('highest_price') or ref)
        trailing_stop=highest*(1.0-trail_dd)
        rotation=bool(plan.get('rotation_exit'))
        rotation_min=float(plan.get('rotation_min_price') or 0.0)
        max_hold=int(plan.get('max_hold_days') or 3)
        sessions=int(plan.get('sessions_held') or 0)

        action=None
        reason=None
        sell_all=True
        if hard_stop>0 and ref<=hard_stop:
            action='SELL'; reason='hard_stop'
        elif (ref>=trail_activation or plan.get('partial_taken')) and ref<=trailing_stop:
            action='SELL'; reason='trailing_stop'
        elif rotation and rotation_min>0 and ref>=rotation_min:
            action='SELL'; reason='rotation_exit_price_reached'
        elif take_profit>0 and ref>=take_profit:
            action='SELL'; reason='take_profit'
            sell_all=False
        elif sessions>=max_hold and clock>='14:55':
            action='SELL'; reason='max_hold_time_exit'

        if not action:
            checks.append({
                'symbol':sym,'action':'HOLD','reason':'conditions_not_met','price':round(ref,3),
                'hard_stop':hard_stop,'take_profit':take_profit,'trailing_stop':round(trailing_stop,3),
                'rotation_min_price':rotation_min or None,
            })
            continue

        qty=int(p.get('qty') or 0)
        if not sell_all and not plan.get('partial_taken') and qty>=2*CONFIG.lot_size:
            qty=round_lot(qty*0.5)
        else:
            sell_all=True
        if qty<=0:
            checks.append({'symbol':sym,'action':'WAIT','reason':'below_lot'})
            continue
        px=slipped_price('SELL',ref)
        gross=round(px*qty,2)
        fees=fee_for('SELL',gross)
        cash+=gross-fees
        p['qty']=int(p.get('qty') or 0)-qty
        fills.append({
            'symbol':sym,'name':p.get('name',sym),'side':'SELL','trade_date':trade_date,
            'reference_price':round(ref,3),'price':px,'qty':qty,'gross':gross,'fees':fees,
            'note':f'条件计划 · {clock}检查触发 {reason}',
            'execution_price_field':'live_conditional','plan_version':PLAN_VERSION,
            'exit_reason':reason,'scan_time':clock,
        })
        if p['qty']<=0 or sell_all:
            positions.pop(sym,None)
            plans.pop(sym,None)
        else:
            plan['partial_taken']=True
            plan['trailing_activation_price']=min(float(plan.get('trailing_activation_price') or ref),ref)
            checks.append({'symbol':sym,'action':'PARTIAL_SELL','reason':reason,'remaining_qty':p['qty']})

    state['cash']=round(cash,2)
    return fills,checks
