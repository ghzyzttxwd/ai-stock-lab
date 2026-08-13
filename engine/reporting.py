from __future__ import annotations
from math import sqrt


def mark_to_market(state: dict, bars: dict[str, dict], trade_date: str) -> dict:
    equity = float(state['cash'])
    holdings = []
    for sym, p in state.get('positions', {}).items():
        bar = bars.get(sym, {})
        px = float(bar.get('close') or p.get('last_price') or p['avg_cost'])
        p['last_price'] = px
        value = p['qty'] * px
        pnl = (px / p['avg_cost'] - 1) if p['avg_cost'] else 0
        holdings.append({'symbol': sym, 'name': p.get('name', sym), 'qty': p['qty'], 'avg_cost': p['avg_cost'],
                         'last_price': px, 'market_value': round(value, 2), 'pnl_pct': round(pnl*100, 2)})
        equity += value
    state['equity_curve'].append({'date': trade_date, 'equity': round(equity, 2)})
    return {'equity': round(equity, 2), 'holdings': holdings}


def metrics(curve: list[dict], initial_cash: float) -> dict:
    if not curve:
        return {'return_pct': 0, 'max_drawdown_pct': 0, 'volatility_pct': 0}
    vals = [float(x['equity']) for x in curve]
    rets = [vals[i]/vals[i-1]-1 for i in range(1, len(vals)) if vals[i-1] > 0]
    peak, max_dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        max_dd = min(max_dd, v/peak-1)
    vol = 0
    if len(rets) > 1:
        m = sum(rets)/len(rets)
        var = sum((x-m)**2 for x in rets)/len(rets)
        vol = sqrt(var) * sqrt(244)
    return {
        'return_pct': round((vals[-1]/initial_cash-1)*100, 2),
        'max_drawdown_pct': round(max_dd*100, 2),
        'volatility_pct': round(vol*100, 2),
    }
