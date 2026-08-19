from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .broker import execute_target_weights
from .daily_run import FUNDS, STATE_ROOT, _pending_decision_date, export_web, run_real
from .state import load_state, save_state


EXECUTION_MODEL = '09:40_SELL_15:10_OPEN_BUY'


def _previous_session(market, trade_date: str) -> str | None:
    frame = market.ak.stock_zh_index_daily_tx(symbol='sh000001')
    dates = sorted({str(x)[:10] for x in frame['date'].tolist() if str(x)[:10] < trade_date})
    return dates[-1] if dates else None


def _critical_symbols(states: dict[str, dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for state in states.values():
        for symbol, pos in (state.get('positions') or {}).items():
            out[symbol] = pos.get('name', symbol)
        for target in state.get('pending_targets') or []:
            symbol = target.get('symbol')
            if symbol:
                out[symbol] = target.get('name', out.get(symbol, symbol))
    return out


def _session_bars(market, critical: dict[str, str], trade_date: str) -> dict[str, dict]:
    """Use today's completed-session snapshot, preserving both open and close fields."""
    if not critical:
        return {}
    snapshot = market.snapshot()
    bars = {x['code']: x for x in snapshot if x.get('code') in critical}
    missing = {symbol: name for symbol, name in critical.items() if symbol not in bars}
    if missing:
        print(f'[15:10-buy] supplementing {len(missing)} critical symbols outside liquid snapshot')
        bars.update(market.execution_bars(missing, trade_date))
    return bars


def settle_previous_decisions_at_close(requested_date: str) -> str:
    """Finish yesterday's target with 09:40 SELL and 15:10 OPEN-price BUY.

    The morning workflow executes SELL/reduce intents around 09:40 using a live quote.
    After today's session is complete, the 15:10 workflow accounts BUY/add intents from the
    same previous-session decision using TODAY'S OPEN as the simulated entry reference.
    The decision therefore existed before today's open; today's completed data is used only
    to retrieve/validate the open price and to build tomorrow's next decision afterwards.
    """
    from .real_market import AKShareMarket

    market = AKShareMarket()
    trade_date = market.latest_trade_date(requested_date)
    previous = _previous_session(market, trade_date)
    states = {
        fid: load_state(STATE_ROOT / f'{fid}.json', fid, name)
        for fid, name in FUNDS.items()
    }
    critical = _critical_symbols(states)
    bars = _session_bars(market, critical, trade_date)
    settled_at = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')

    for fid, state in states.items():
        if str(state.get('last_processed_date') or '')[:10] == trade_date:
            continue
        pending = list(state.get('pending_targets') or [])
        if not pending:
            continue
        decision_date = _pending_decision_date(state)
        if not previous or decision_date != previous:
            # Let daily_run's stale-decision guard expire it; never execute stale targets.
            continue

        fills = []
        if str(state.get('morning_sell_date') or '')[:10] != trade_date:
            # Recovery only. Normal production must sell at 09:40. If that chain failed,
            # settle the SELL side from the completed session before allowing BUY accounting.
            fallback = execute_target_weights(
                state, pending, bars, trade_date,
                sides=('SELL',), price_field='close',
                note='09:40卖出任务未完成 · 15:10按收盘价兜底卖出',
            )
            fills.extend(fallback)
            state['morning_sell_date'] = trade_date
            state['morning_sell_fallback'] = True

        buys = execute_target_weights(
            state, pending, bars, trade_date,
            sides=('BUY',), price_field='open',
            note='上一交易日决策 · 15:10结算买入/加仓（参考当日开盘价）',
        )
        fills.extend(buys)
        state.setdefault('fills', []).extend(fills)
        state['split_execution_date'] = trade_date
        state['split_execution_phase'] = '15:10_OPEN_BUY_COMPLETE'
        state['open_buy_accounted_at'] = settled_at
        state['open_buy_reference'] = 'session_open'
        state['execution_model'] = EXECUTION_MODEL
        # The old decision is fully settled. daily_run now creates a fresh next-session target.
        state['pending_targets'] = []
        state.pop('pending_decision_date', None)
        save_state(STATE_ROOT / f'{fid}.json', state)
        print(f'[15:10-buy] {fid} fills={len(fills)} decision={decision_date} trade_date={trade_date}')

    return trade_date


def main() -> None:
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    requested = now.date().isoformat()
    settle_previous_decisions_at_close(requested)

    export_web._real_mode = True
    trade_date, candidates, market_score, snapshots, benchmarks = run_real(requested)
    export_web(trade_date, candidates, market_score, snapshots, benchmarks)

    import json
    d_path = export_web.__globals__['ROOT'] / 'web/d/data.json'
    e_path = export_web.__globals__['ROOT'] / 'web/e/data.json'
    d = json.loads(d_path.read_text(encoding='utf-8'))
    d['execution_model'] = EXECUTION_MODEL
    d['execution_note'] = '09:40卖出/减仓；15:10结算买入/加仓，买入参考当日开盘价'
    for item in d.get('decisions') or []:
        item['timing'] = '下一交易日09:40卖出/减仓；15:10结算买入/加仓，买入模拟成交参考当日开盘价'
    d_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')

    e = json.loads(e_path.read_text(encoding='utf-8'))
    e['execution_model'] = EXECUTION_MODEL
    e['execution_note'] = '09:40卖出/减仓；15:10结算买入/加仓，买入参考当日开盘价'
    e_path.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[15:10-buy] completed web refresh for {trade_date}, market_score={market_score}')


if __name__ == '__main__':
    main()
