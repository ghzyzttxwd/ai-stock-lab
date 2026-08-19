from __future__ import annotations

import json
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from .broker import execute_conditional_sells
from .daily_run import FUNDS, ROOT, STATE_ROOT
from .state import load_state, save_state
from .trading_plan import EXECUTION_MODEL, PLAN_VERSION


def _symbol(code: str) -> str:
    code = ''.join(ch for ch in str(code) if ch.isdigit())[-6:]
    return ('sh.' if code.startswith(('600', '601', '603', '605')) else 'sz.') + code


def _calendar_sessions(market) -> list[str]:
    frame = market.ak.tool_trade_date_hist_sina()
    return sorted({str(x)[:10] for x in frame['trade_date'].tolist() if str(x)[:10]})


def _critical_symbols(states: dict[str, dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for state in states.values():
        for symbol, pos in (state.get('positions') or {}).items():
            out[symbol] = pos.get('name', symbol)
    return out


def _eastmoney_live_bars(market, critical: dict[str, str]) -> dict[str, dict]:
    frame = market.ak.stock_zh_a_spot_em()
    bars: dict[str, dict] = {}
    for _, row in frame.iterrows():
        code = str(row.get('代码', '')).zfill(6)
        symbol = _symbol(code)
        if symbol not in critical:
            continue
        try:
            last = float(row.get('最新价') or 0)
            opening = float(row.get('今开') or 0)
            previous = float(row.get('昨收') or 0)
        except (TypeError, ValueError):
            continue
        if last <= 0:
            continue
        bars[symbol] = {
            'code': symbol,
            'name': str(row.get('名称') or critical.get(symbol) or symbol),
            'open': opening or last,
            'close': last,
            'preclose': previous or last,
            'tradestatus': '1',
            'source': 'eastmoney-intraday',
        }
    return bars


def _sina_live_bars(market, critical: dict[str, str]) -> dict[str, dict]:
    frame = market.ak.stock_zh_a_spot()
    bars: dict[str, dict] = {}
    for _, row in frame.iterrows():
        raw = str(row.get('代码', '')).lower().strip()
        code = ''.join(ch for ch in raw if ch.isdigit())[-6:]
        if len(code) != 6:
            continue
        symbol = ('sh.' if raw.startswith('sh') else 'sz.' if raw.startswith('sz') else _symbol(code)[:3]) + code
        if symbol not in critical:
            continue
        try:
            last = float(row.get('最新价') or 0)
            opening = float(row.get('今开') or 0)
            previous = float(row.get('昨收') or 0)
        except (TypeError, ValueError):
            continue
        if last <= 0:
            continue
        bars[symbol] = {
            'code': symbol,
            'name': str(row.get('名称') or critical.get(symbol) or symbol),
            'open': opening or last,
            'close': last,
            'preclose': previous or last,
            'tradestatus': '1',
            'source': 'sina-intraday',
        }
    return bars


def _live_bars(market, critical: dict[str, str]) -> tuple[dict[str, dict], str]:
    if not critical:
        return {}, 'none'
    try:
        bars = _eastmoney_live_bars(market, critical)
        if bars:
            return bars, 'eastmoney'
    except Exception as exc:
        print(f'[conditional-scan] eastmoney intraday failed: {exc}')
    bars = _sina_live_bars(market, critical)
    if not bars:
        raise RuntimeError('conditional intraday quote snapshot returned no held symbols')
    return bars, 'sina'


def _portfolio_snapshot(state: dict, bars: dict[str, dict]) -> dict:
    cash = float(state.get('cash') or 0.0)
    equity = cash
    holdings = []
    exits = state.get('exit_plans') or {}
    for symbol, pos in (state.get('positions') or {}).items():
        bar = bars.get(symbol) or {}
        price = float(bar.get('close') or pos.get('last_price') or pos.get('avg_cost') or 0.0)
        pos['last_price'] = price
        qty = int(pos.get('qty') or 0)
        avg = float(pos.get('avg_cost') or 0.0)
        value = qty * price
        equity += value
        holdings.append({
            'symbol': symbol,
            'name': pos.get('name', symbol),
            'qty': qty,
            'avg_cost': round(avg, 4),
            'last_price': round(price, 4),
            'market_value': round(value, 2),
            'pnl_pct': round((price / avg - 1.0) * 100.0, 2) if avg > 0 else 0.0,
            'exit_plan': exits.get(symbol),
        })
    equity = round(equity, 2)
    return {'equity': equity, 'cash': round(cash, 2), 'holdings': holdings}


def _today_activity(state: dict, trade_date: str) -> dict:
    fills = [x for x in state.get('fills', []) if str(x.get('trade_date') or '')[:10] == trade_date]
    buys = [x for x in fills if x.get('side') == 'BUY']
    sells = [x for x in fills if x.get('side') == 'SELL']
    return {
        'buy_count': len(buys),
        'sell_count': len(sells),
        'buy_amount': round(sum(float(x.get('gross') or 0.0) for x in buys), 2),
        'sell_amount': round(sum(float(x.get('gross') or 0.0) for x in sells), 2),
        'pending_count': len(state.get('pending_targets') or []),
    }


def _intraday_return(snapshot: dict, state: dict) -> tuple[float, float]:
    initial = float(state.get('initial_cash') or 1_000_000.0)
    current = float(snapshot['equity'])
    cumulative = (current / initial - 1.0) * 100.0 if initial > 0 else 0.0
    curve = state.get('equity_curve') or []
    previous_close = float(curve[-1].get('equity') or 0.0) if curve else initial
    today = (current / previous_close - 1.0) * 100.0 if previous_close > 0 else 0.0
    return round(cumulative, 2), round(today, 2)


def _refresh_public_snapshots(states: dict[str, dict], bars: dict[str, dict], source: str, trade_date: str, clock: str) -> None:
    d_path = ROOT / 'web/d/data.json'
    e_path = ROOT / 'web/e/data.json'

    if d_path.exists():
        data = json.loads(d_path.read_text(encoding='utf-8'))
        state = states['D_MAIN']
        snap = _portfolio_snapshot(state, bars)
        cumulative, today = _intraday_return(snap, state)
        old_scores = {x.get('symbol'): x for x in data.get('holdings') or []}
        holdings = []
        for item in snap['holdings']:
            old=old_scores.get(item['symbol']) or {}
            holdings.append({
                **item,
                'weight': round(item['market_value'] / snap['equity'] * 100.0, 1) if snap['equity'] else 0.0,
                'score': old.get('score', 0),
                'opportunity_score': old.get('opportunity_score'),
            })
        data['updated_at'] = trade_date
        data['updated_time'] = clock
        data['session_phase'] = f'{clock}条件卖出检查完成；15:10结算当天触发的买入条件'
        data['market_source'] = source
        data.setdefault('fund', {}).update({
            'equity': snap['equity'],
            'cash': snap['cash'],
            'position_pct': round((snap['equity'] - snap['cash']) / snap['equity'] * 100.0, 1) if snap['equity'] else 0.0,
        })
        data.setdefault('metrics', {}).update({
            'return_pct': cumulative,
            'today_pct': today,
            'trades': len(state.get('fills') or []),
        })
        data['activity'] = _today_activity(state, trade_date)
        data['holdings'] = holdings
        data['recent_fills'] = list(state.get('fills') or [])[-10:]
        data['execution_model'] = EXECUTION_MODEL
        data['plan_version'] = PLAN_VERSION
        d_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    if e_path.exists():
        data = json.loads(e_path.read_text(encoding='utf-8'))
        by_id = {x.get('id'): x for x in data.get('funds') or []}
        for fid in ('A', 'B', 'C', 'D', 'L'):
            state = states[fid]
            snap = _portfolio_snapshot(state, bars)
            cumulative, today = _intraday_return(snap, state)
            item = by_id.get(fid)
            if not item:
                continue
            item['equity'] = snap['equity']
            item['cash'] = snap['cash']
            item['position_market_value'] = round(snap['equity'] - snap['cash'], 2)
            item['return_pct'] = cumulative
            item['today_pct'] = today
            item['holdings'] = snap['holdings']
            item['activity'] = _today_activity(state, trade_date)
            item['recent_fills'] = list(state.get('fills') or [])[-8:]
        data['updated_at'] = trade_date
        data['updated_time'] = clock
        data['session_phase'] = f'{clock}条件卖出检查完成；15:10结算当天触发的买入条件'
        data['market_source'] = source
        data['execution_model'] = EXECUTION_MODEL
        data['plan_version'] = PLAN_VERSION
        e_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> None:
    from .real_market import AKShareMarket

    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    if not (dt_time(9, 30) <= now.time() <= dt_time(15, 0)):
        print(f'[conditional-scan] outside A-share session at {now.strftime("%H:%M:%S")}; skip')
        return
    trade_date = now.date().isoformat()
    clock = now.strftime('%H:%M')
    scan_key = f'{trade_date}T{clock}'
    market = AKShareMarket()
    sessions = _calendar_sessions(market)
    if trade_date not in set(sessions):
        print(f'[conditional-scan] {trade_date} is not an exchange session; skip')
        return

    states = {
        fid: load_state(STATE_ROOT / f'{fid}.json', fid, name)
        for fid, name in FUNDS.items()
    }
    critical = _critical_symbols(states)
    bars, source = _live_bars(market, critical)

    for fid, state in states.items():
        if state.get('last_conditional_scan_key') == scan_key:
            print(f'[conditional-scan] {fid} already checked {scan_key}')
            continue
        fills, checks = execute_conditional_sells(state, bars, trade_date, clock=clock)
        state.setdefault('fills', []).extend(fills)
        state['last_conditional_scan_key'] = scan_key
        state['last_conditional_scan_at'] = now.isoformat(timespec='seconds')
        state['execution_model'] = EXECUTION_MODEL
        log=state.setdefault('conditional_scan_log',[])
        log.append({'at':state['last_conditional_scan_at'],'fills':len(fills),'checks':checks})
        if len(log)>30:
            del log[:-30]
        _portfolio_snapshot(state,bars)
        save_state(STATE_ROOT / f'{fid}.json', state)
        print(f'[conditional-scan] {fid} fills={len(fills)} checks={len(checks)}')

    _refresh_public_snapshots(states, bars, source, trade_date, clock)
    print(f'[conditional-scan] completed {trade_date} {clock} source={source} holdings={len(critical)} bars={len(bars)}')


if __name__ == '__main__':
    main()
