from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .broker import execute_target_weights
from .daily_run import FUNDS, STATE_ROOT, _pending_decision_date, export_web, run_real
from .state import load_state, save_state


EXECUTION_MODEL = '15:10_CLOSE_REBALANCE'


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
    """Use one full-market close snapshot first; only fetch rare missing critical symbols."""
    if not critical:
        return {}
    snapshot = market.snapshot()
    bars = {x['code']: x for x in snapshot if x.get('code') in critical}
    missing = {symbol: name for symbol, name in critical.items() if symbol not in bars}
    if missing:
        print(f'[close-rebalance] supplementing {len(missing)} critical symbols outside liquid snapshot')
        bars.update(market.execution_bars(missing, trade_date))
    return bars


def settle_previous_decisions_at_close(requested_date: str) -> str:
    """Execute the previous session's target once, after today's close.

    All SELL/reduce and BUY/add legs are settled together from the completed-session close
    snapshot. The workflow starts at 15:10 Asia/Shanghai; 15:10 is the accounting/job time,
    while simulated fills use the 15:00 exchange close as their reference price. This avoids
    the old close-buy -> next-morning-sell round trip.
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

        fills = execute_target_weights(
            state, pending, bars, trade_date,
            sides=('SELL', 'BUY'), price_field='close',
            note='上一交易日决策 · 15:10收盘调仓结算（参考15:00收盘价）',
        )
        state.setdefault('fills', []).extend(fills)
        state['close_rebalance_date'] = trade_date
        state['close_rebalance_at'] = settled_at
        state['close_rebalance_reference'] = '15:00_close'
        state['execution_model'] = EXECUTION_MODEL
        # The previous decision is fully settled. daily_run now creates the next session target.
        state['pending_targets'] = []
        state.pop('pending_decision_date', None)
        save_state(STATE_ROOT / f'{fid}.json', state)
        sells = sum(1 for x in fills if x.get('side') == 'SELL')
        buys = sum(1 for x in fills if x.get('side') == 'BUY')
        print(
            f'[close-rebalance] {fid} sells={sells} buys={buys} '
            f'decision={decision_date} trade_date={trade_date}'
        )

    return trade_date


def main() -> None:
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    requested = now.date().isoformat()
    settle_previous_decisions_at_close(requested)

    export_web._real_mode = True
    trade_date, candidates, market_score, snapshots, benchmarks = run_real(requested)
    export_web(trade_date, candidates, market_score, snapshots, benchmarks)

    # Make the public snapshot explicit: there is no scheduled morning trade anymore.
    import json
    d_path = export_web.__globals__['ROOT'] / 'web/d/data.json'
    e_path = export_web.__globals__['ROOT'] / 'web/e/data.json'
    d = json.loads(d_path.read_text(encoding='utf-8'))
    d['execution_model'] = EXECUTION_MODEL
    d['execution_note'] = '15:10启动结算；买卖均参考当日15:00收盘价模拟成交'
    for item in d.get('decisions') or []:
        item['timing'] = '下一交易日15:10统一结算卖出/减仓与买入/加仓；模拟成交参考当日15:00收盘价'
    d_path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')

    e = json.loads(e_path.read_text(encoding='utf-8'))
    e['execution_model'] = EXECUTION_MODEL
    e['execution_note'] = '15:10启动结算；买卖均参考当日15:00收盘价模拟成交'
    e_path.write_text(json.dumps(e, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[close-rebalance] completed web refresh for {trade_date}, market_score={market_score}')


if __name__ == '__main__':
    main()
