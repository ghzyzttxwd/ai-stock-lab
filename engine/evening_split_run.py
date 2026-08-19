from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .broker import execute_target_weights
from .daily_run import FUNDS, STATE_ROOT, _pending_decision_date, export_web, run_real
from .state import load_state, save_state


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


def _close_bars(market, critical: dict[str, str], trade_date: str) -> dict[str, dict]:
    """Use one full-market snapshot first; only fetch per-symbol bars for rare omissions."""
    if not critical:
        return {}
    snapshot = market.snapshot()
    bars = {x['code']: x for x in snapshot if x.get('code') in critical}
    missing = {symbol: name for symbol, name in critical.items() if symbol not in bars}
    if missing:
        print(f'[close-settle] supplementing {len(missing)} critical symbols outside liquid snapshot')
        bars.update(market.execution_bars(missing, trade_date))
    return bars


def settle_previous_decisions_at_close(requested_date: str) -> str:
    """Finish yesterday's target in two phases without look-ahead.

    The 09:40 workflow has already handled SELL/reduce intents when it succeeded. At the
    completed session close we execute only BUY/add intents from yesterday's decision.
    If the morning workflow did not run, SELL is executed at the close as a safety fallback
    before BUY. New targets are deliberately NOT created here; daily_run does that after
    this settlement using the completed session data.
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
    bars = _close_bars(market, critical, trade_date)

    for fid, state in states.items():
        if str(state.get('last_processed_date') or '')[:10] == trade_date:
            continue
        pending = list(state.get('pending_targets') or [])
        if not pending:
            continue
        decision_date = _pending_decision_date(state)
        if not previous or decision_date != previous:
            # Let daily_run's stale-decision guard deal with it; do not mutate here.
            continue

        fills = []
        if str(state.get('morning_sell_date') or '')[:10] != trade_date:
            fallback = execute_target_weights(
                state, pending, bars, trade_date,
                sides=('SELL',), price_field='close',
                note='09:40卖出任务未完成，按收盘价兜底卖出',
            )
            fills.extend(fallback)
            state['morning_sell_date'] = trade_date
            state['morning_sell_fallback'] = True

        buys = execute_target_weights(
            state, pending, bars, trade_date,
            sides=('BUY',), price_field='close',
            note='上一交易日决策 · 收盘价模拟买入/加仓',
        )
        fills.extend(buys)
        state.setdefault('fills', []).extend(fills)
        state['split_execution_date'] = trade_date
        state['split_execution_phase'] = 'CLOSE_BUY_COMPLETE'
        # The old decision is now fully settled. daily_run will create a fresh next-session target.
        state['pending_targets'] = []
        state.pop('pending_decision_date', None)
        save_state(STATE_ROOT / f'{fid}.json', state)
        print(f'[close-settle] {fid} sells/buys={len(fills)} decision={decision_date} trade_date={trade_date}')

    return trade_date


def main() -> None:
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    requested = now.date().isoformat()
    settle_previous_decisions_at_close(requested)

    export_web._real_mode = True
    trade_date, candidates, market_score, snapshots, benchmarks = run_real(requested)
    export_web(trade_date, candidates, market_score, snapshots, benchmarks)

    # Make the public snapshot explicit about the split-execution model without changing
    # the strategy decision itself.
    import json
    d_path = export_web.__globals__['ROOT'] / 'web/d/data.json'
    e_path = export_web.__globals__['ROOT'] / 'web/e/data.json'
    d = json.loads(d_path.read_text(encoding='utf-8'))
    d['execution_model'] = '09:40_SELL_CLOSE_BUY'
    for item in d.get('decisions') or []:
        item['timing'] = '下一交易日09:40先执行卖出/减仓；买入/加仓按当日收盘价模拟执行'
    d_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')

    e = json.loads(e_path.read_text(encoding='utf-8'))
    e['execution_model'] = '09:40_SELL_CLOSE_BUY'
    e_path.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[close-settle] completed web refresh for {trade_date}, market_score={market_score}')


if __name__ == '__main__':
    main()
