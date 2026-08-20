__all__ = []

# V1 uses Eastmoney/Sina first and Tencent full-market as the third complete
# cross-sectional source. The installer only wraps market loading; broker/execution
# rules and V1 ledger semantics remain unchanged.
try:
    from .tencent_full_market import install as _install_v1_tencent_full_market
    _install_v1_tencent_full_market()
except Exception as _v1_market_patch_error:
    # Do not make package import fatal. If the fallback cannot be installed, the
    # production market path will fail closed rather than fabricating data.
    print(f'[V1] Tencent full-market fallback install warning: {_v1_market_patch_error}')

# Production safety belt: scheduled intraday checkpoints are only valid near their
# declared time. GitHub Actions can start scheduled jobs late; never convert a
# 10:42 quote into a fictitious 09:40 fill. This wrapper is active only when the
# production workflow sets CONDITIONAL_SCAN_SLOT, so unit tests/direct broker calls
# are unaffected. engine/morning_run.py also carries the same guard after the
# one-shot repair, giving us defence in depth.
try:
    import os as _os
    from datetime import datetime as _datetime
    from zoneinfo import ZoneInfo as _ZoneInfo
    from . import broker as _broker

    _orig_execute_conditional_sells = _broker.execute_conditional_sells
    _checkpoint_slots = {'09:40', '10:30', '11:20', '13:30', '14:30', '14:55'}
    _max_checkpoint_lateness_minutes = 10

    def _guarded_execute_conditional_sells(state, bars, trade_date, *, clock, **kwargs):
        slot = _os.getenv('CONDITIONAL_SCAN_SLOT', '').strip()
        if slot in _checkpoint_slots and slot == str(clock):
            now = _datetime.now(_ZoneInfo('Asia/Shanghai'))
            hour, minute = (int(x) for x in slot.split(':', 1))
            scheduled = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            delay = (now - scheduled).total_seconds() / 60.0
            if delay < 0 or delay > _max_checkpoint_lateness_minutes:
                checks = [
                    {
                        'symbol': symbol,
                        'action': 'WAIT',
                        'reason': 'stale_checkpoint_skipped',
                        'scheduled_time': slot,
                        'actual_execution_time': now.isoformat(timespec='seconds'),
                        'delay_minutes': round(delay, 2),
                    }
                    for symbol in (state.get('positions') or {})
                ]
                print(f'[V1 SAFETY] stale checkpoint {slot}, actual={now.strftime("%H:%M:%S")}, delay={delay:.1f}m; no trades')
                return [], checks

            fills, checks = _orig_execute_conditional_sells(
                state, bars, trade_date, clock=clock, **kwargs
            )
            actual = now.isoformat(timespec='seconds')
            for fill in fills:
                fill['scheduled_time'] = slot
                fill['actual_execution_time'] = actual
                fill['actual_clock'] = now.strftime('%H:%M')
            return fills, checks

        return _orig_execute_conditional_sells(state, bars, trade_date, clock=clock, **kwargs)

    _broker.execute_conditional_sells = _guarded_execute_conditional_sells
except Exception as _v1_checkpoint_guard_error:
    print(f'[V1] checkpoint safety guard install warning: {_v1_checkpoint_guard_error}')
