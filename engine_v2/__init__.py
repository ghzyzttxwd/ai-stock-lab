"""V2 shadow research package.

This package is deliberately not imported by the production V1 pipeline.
"""

V2_VERSION = "2.0-shadow-0.2"

# Production safety belt for delayed GitHub Actions schedules. The scheduled
# checkpoint is a price-observation window, not permission to relabel a much later
# quote as an earlier fill. This wrapper only activates for the CLI production path
# that passes --scheduled-time, leaving ordinary unit-test calls unchanged.
try:
    import sys as _sys
    from datetime import datetime as _datetime
    from zoneinfo import ZoneInfo as _ZoneInfo
    from . import split_execution as _split_execution

    _orig_execute_conditional_exit_scan = _split_execution.execute_conditional_exit_scan
    _checkpoint_slots = {'09:40', '10:30', '11:20', '13:30', '14:30', '14:55'}
    _max_checkpoint_lateness_minutes = 10

    def _production_has_scheduled_time() -> bool:
        return '--scheduled-time' in _sys.argv

    def _guarded_execute_conditional_exit_scan(state, bars, trade_date, *, clock, pending=None, policy=_split_execution.DEFAULT_POLICY):
        slot = str(clock)
        if _production_has_scheduled_time() and slot in _checkpoint_slots:
            now = _datetime.now(_ZoneInfo('Asia/Shanghai'))
            hour, minute = (int(x) for x in slot.split(':', 1))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            delay = (now - scheduled).total_seconds() / 60.0
            if delay < 0 or delay > _max_checkpoint_lateness_minutes:
                print(f'[V2 SAFETY] stale checkpoint {slot}, actual={now.strftime("%H:%M:%S")}, delay={delay:.1f}m; no trades')
                return {
                    'phase': 'conditional_exit',
                    'decision_date': (pending or {}).get('decision_date'),
                    'trade_date': trade_date,
                    'reference_price_field': 'live_conditional',
                    'fills': [],
                    'rejected_orders': [],
                    'checks': [
                        {
                            'symbol': symbol,
                            'action': 'WAIT',
                            'reason': 'stale_checkpoint_skipped',
                            'scheduled_time': slot,
                            'actual_execution_time': now.isoformat(timespec='seconds'),
                            'delay_minutes': round(delay, 2),
                        }
                        for symbol in (state.get('positions') or {})
                    ],
                    'policy_adjustments': [],
                    'valuation_fallback_symbols': [],
                    'fees': 0.0,
                }

            result = _orig_execute_conditional_exit_scan(
                state, bars, trade_date, clock=clock, pending=pending, policy=policy
            )
            actual = now.isoformat(timespec='seconds')
            for fill in result.get('fills') or []:
                fill['scheduled_time'] = slot
                fill['actual_execution_time'] = actual
                fill['actual_clock'] = now.strftime('%H:%M')
            return result

        return _orig_execute_conditional_exit_scan(
            state, bars, trade_date, clock=clock, pending=pending, policy=policy
        )

    _split_execution.execute_conditional_exit_scan = _guarded_execute_conditional_exit_scan
except Exception as _v2_checkpoint_guard_error:
    print(f'[V2] checkpoint safety guard install warning: {_v2_checkpoint_guard_error}')
