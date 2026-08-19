from __future__ import annotations
import math
from .config import CONFIG
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


def _execution_target_map(state: dict, targets: list[dict]) -> dict[str,dict]:
    """Final deterministic safety clamp before any simulated order is sized.

    The retail account policy is main-board only. Any ChiNext/STAR/BSE/B-share target is
    clamped to zero here even if an upstream cache/manual target somehow bypassed universe
    filtering. Existing ineligible holdings can still be sold; they can never be increased.
    """
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
    """Rebalance toward target weights using a chosen market-price field.

    `sides` lets the daily simulation split one decision into a 09:40 sell phase and a
    close-price buy phase without re-running the opposite side. The default preserves the
    legacy all-at-open behaviour for callers/tests that do not opt in.
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
        # Recompute equity/diffs after the sell phase so a combined legacy call remains consistent.
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
