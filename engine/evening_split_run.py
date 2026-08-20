from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .broker import execute_conditional_buys
from .daily_run import FUNDS, STATE_ROOT, _pending_decision_date, export_web, run_real
from .exchange_calendar import exchange_calendar_previous_session
from .runtime_metrics import reset as reset_runtime_metrics, stage, write_github_summary
from .state import load_state, save_state
from .trade_price_time import annotate_conditional_buy_fills
from .trading_plan import EXECUTION_MODEL, PLAN_VERSION, pending_is_conditional


def _previous_session(market, trade_date: str) -> str:
    """Resolve yesterday's actual exchange session without quote/history inference."""
    return exchange_calendar_previous_session(trade_date, market.ak)


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
    """Completed daily OHLC is used only to verify whether yesterday's condition was touched."""
    if not critical:
        return {}
    snapshot = market.snapshot()
    bars = {x['code']: x for x in snapshot if x.get('code') in critical}
    missing = {symbol: name for symbol, name in critical.items() if symbol not in bars}
    if missing:
        print(f'[15:10-plan] supplementing {len(missing)} critical symbols outside liquid snapshot')
        bars.update(market.execution_bars(missing, trade_date))
    return bars


def settle_previous_conditional_entries(requested_date: str) -> str:
    """At 15:10, settle only yesterday's entry conditions that today's OHLC actually triggered."""
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
            # Same-day rerun is a read-only refresh / migration of tomorrow's plan.
            continue
        pending = list(state.get('pending_targets') or [])
        if not pending:
            continue
        decision_date = _pending_decision_date(state)
        if decision_date != previous:
            continue

        with stage(f'settlement.fund.{fid}'):
            if pending_is_conditional(pending):
                fills, skipped = execute_conditional_buys(
                    state, pending, bars, trade_date,
                    note='上一交易日条件计划 · 15:10结算当日已触发买单',
                )
                # Metadata only: preserve the first minute where the declared entry condition
                # was actually touched. This never changes fill eligibility, price, size, or fees.
                annotate_conditional_buy_fills(market.ak, fills, pending, trade_date)
                state.setdefault('fills', []).extend(fills)
                state['last_entry_settlement'] = {
                    'trade_date': trade_date,
                    'settled_at': settled_at,
                    'fills': len(fills),
                    'not_triggered_or_skipped': skipped,
                    'plan_version': PLAN_VERSION,
                    'price_time_evidence': 'first_trigger_minute_when_available',
                }
                print(f'[15:10-plan] {fid} triggered buys={len(fills)} skipped={len(skipped)}')
            else:
                # Migration safety: never execute an old target that has no declared price condition.
                state['last_entry_settlement'] = {
                    'trade_date': trade_date,
                    'settled_at': settled_at,
                    'fills': 0,
                    'legacy_plan_cancelled': True,
                }
                print(f'[15:10-plan] {fid} cancelled legacy fixed-price pending targets')

            state['pending_targets'] = []
            state.pop('pending_decision_date', None)
            state['execution_model'] = EXECUTION_MODEL
            save_state(STATE_ROOT / f'{fid}.json', state)

    return trade_date


def main() -> None:
    reset_runtime_metrics()
    try:
        with stage('job.total'):
            now = datetime.now(ZoneInfo('Asia/Shanghai'))
            requested = now.date().isoformat()

            with stage('job.settle_previous_entries'):
                settle_previous_conditional_entries(requested)

            export_web._real_mode = True
            with stage('job.run_real'):
                trade_date, candidates, market_score, snapshots, benchmarks = run_real(requested)
            with stage('job.export_web'):
                export_web(trade_date, candidates, market_score, snapshots, benchmarks)
            print(
                f'[15:10-plan] completed {trade_date}; market_score={market_score}; '
                'only pre-declared conditions can create buys; no trigger means cash remains cash'
            )
    finally:
        write_github_summary()


if __name__ == '__main__':
    main()
