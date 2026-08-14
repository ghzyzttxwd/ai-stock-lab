from __future__ import annotations
import math
from .config import CONFIG


def fee_for(side: str, gross: float) -> float:
    commission = max(CONFIG.min_commission, gross * CONFIG.commission_rate)
    stamp = gross * CONFIG.stamp_duty_sell_rate if side == 'SELL' else 0.0
    return round(commission + stamp, 2)


def slipped_price(side: str, open_price: float) -> float:
    slip = CONFIG.slippage_bps / 10_000
    return round(open_price * (1 + slip if side == 'BUY' else 1 - slip), 3)


def round_lot(qty: float) -> int:
    return max(0, int(math.floor(qty / CONFIG.lot_size)) * CONFIG.lot_size)


def _locked_at_limit(side: str, bar: dict) -> bool:
    pre=float(bar.get('preclose') or 0)
    op=float(bar.get('open') or 0)
    if pre <= 0 or op <= 0:
        return False
    change=op/pre-1
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
        if d_fund:
            weight=min(weight,CONFIG.max_single_weight_d)
            if sym not in positions:
                weight=min(weight,CONFIG.max_new_position_weight_d)
        item['target_weight']=weight
        out[sym]=item
    return out


def execute_target_weights(state: dict, targets: list[dict], bars: dict[str, dict], trade_date: str) -> list[dict]:
    fills = []
    positions = state.setdefault('positions', {})
    cash = float(state['cash'])
    total_equity = cash
    for sym, p in positions.items():
        bar = bars.get(sym)
        px = float((bar or {}).get('open') or p.get('last_price', p['avg_cost']))
        total_equity += p['qty'] * px

    target_map = _execution_target_map(state,targets)
    symbols = set(positions) | set(target_map)
    diffs = []
    for sym in symbols:
        bar = bars.get(sym)
        if not bar or float(bar.get('open', 0) or 0) <= 0 or str(bar.get('tradestatus', '1')) != '1':
            continue
        open_px = float(bar['open'])
        current_val = positions.get(sym, {}).get('qty', 0) * open_px
        target_val = total_equity * float(target_map.get(sym, {}).get('target_weight', 0.0))
        diffs.append((target_val - current_val, sym, open_px))

    for diff, sym, open_px in sorted(diffs):
        if diff >= 0 or sym not in positions:
            continue
        if _locked_at_limit('SELL', bars[sym]):
            continue
        p = positions[sym]
        if p.get('acquired_date') == trade_date:
            continue
        qty = min(p['qty'], round_lot(abs(diff) / open_px))
        if qty <= 0:
            continue
        px = slipped_price('SELL', open_px)
        gross = round(px * qty, 2)
        fees = fee_for('SELL', gross)
        cash += gross - fees
        p['qty'] -= qty
        fills.append({'symbol': sym, 'name': p.get('name', sym), 'side': 'SELL', 'trade_date': trade_date,
                      'price': px, 'qty': qty, 'gross': gross, 'fees': fees, 'note': '目标仓位再平衡'})
        if p['qty'] <= 0:
            positions.pop(sym, None)

    for diff, sym, open_px in sorted(diffs, reverse=True):
        if diff <= 0:
            continue
        if _locked_at_limit('BUY', bars[sym]):
            continue
        px = slipped_price('BUY', open_px)
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
                      'price': px, 'qty': qty,'gross': gross, 'fees': fees, 'note': '目标仓位再平衡'})

    state['cash'] = round(cash, 2)
    return fills
