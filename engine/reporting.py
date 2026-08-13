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


def _period_return(vals: list[float], sessions: int):
    if len(vals) <= sessions:
        return None
    base = vals[-1-sessions]
    if base <= 0:
        return None
    return round((vals[-1] / base - 1) * 100, 2)


def _losing_streak(rets: list[float]) -> int:
    n = 0
    for r in reversed(rets):
        if r < 0:
            n += 1
        else:
            break
    return n


def _health(days: int, ret_total: float, ret20, ret60, max_dd: float) -> tuple[str, str]:
    """Simple transparent monitoring label; not an investment recommendation."""
    if days < 20:
        return '观察期', '样本还少，先积累至少20个交易日。'
    if days >= 60 and ret60 is not None and ret60 <= -5:
        return '长期表现差', '近60个交易日收益明显为负，需要认真复盘策略。'
    if max_dd <= -10:
        return '需关注', '最大回撤已超过10%，风险表现偏差。'
    if ret20 is not None and ret20 <= -5:
        return '需关注', '近20个交易日表现偏弱。'
    if ret_total > 0:
        return '正常', '当前累计收益为正，继续观察长期稳定性。'
    return '观察中', '累计收益尚未转正，但还未触发长期差的判定。'


def metrics(curve: list[dict], initial_cash: float) -> dict:
    if not curve:
        return {
            'return_pct': 0, 'today_pct': 0, 'max_drawdown_pct': 0, 'volatility_pct': 0,
            'return_5d_pct': None, 'return_20d_pct': None, 'return_60d_pct': None,
            'trading_days': 0, 'losing_streak_days': 0,
            'health': '观察期', 'health_reason': '尚未开始记录真实交易日。'
        }
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
    total = round((vals[-1]/initial_cash-1)*100, 2)
    today = round(rets[-1]*100, 2) if rets else 0
    r5 = _period_return(vals, 5)
    r20 = _period_return(vals, 20)
    r60 = _period_return(vals, 60)
    dd = round(max_dd*100, 2)
    days = len(vals)
    health, reason = _health(days, total, r20, r60, dd)
    return {
        'return_pct': total,
        'today_pct': today,
        'max_drawdown_pct': dd,
        'volatility_pct': round(vol*100, 2),
        'return_5d_pct': r5,
        'return_20d_pct': r20,
        'return_60d_pct': r60,
        'trading_days': days,
        'losing_streak_days': _losing_streak(rets),
        'health': health,
        'health_reason': reason,
    }
