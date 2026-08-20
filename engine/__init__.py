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

# Session classification is a safety invariant, not an optional market fallback.
# Install this after the Tencent wrapper so every V1 engine path resolves the exchange
# session from the calendar instead of inferring it from a possibly-lagging quote feed.
from .exchange_calendar_market import install as _install_v1_exchange_calendar_market
_install_v1_exchange_calendar_market()

# Exact-date execution bars are requested by several independent safety layers. Cache only
# successful rows for the lifetime of one AKShareMarket instance so Tencent is not queried
# repeatedly for the same date/symbol. Missing rows remain retryable and all downstream
# fail-closed coverage checks stay unchanged.
from .execution_bar_cache import install as _install_v1_execution_bar_cache
_install_v1_execution_bar_cache()

# Eastmoney/Sina are useful first-choice snapshot sources, but a hung provider must not burn
# multiple minutes before Tencent can take over. Hard timeouts open an in-run circuit; fast
# transient errors still get one retry, so fallback order and correctness semantics stay the same.
from .snapshot_provider_circuit import install as _install_v1_snapshot_provider_circuit
_install_v1_snapshot_provider_circuit()

# Tencent qfq daily bars can publish the completed session later than unadjusted/current
# market data. Keep historical features on the qfq basis, but bridge only a verified
# exact-date completed-session daily bar. Basis mismatches fail closed.
from .current_bar_history import install as _install_v1_current_bar_history
_install_v1_current_bar_history()

# Full-market spot rows are intentionally not trusted for accounting-critical OHLC because
# the normalized rows do not carry an explicit session date. Always overlay holdings and
# pending-order symbols with exact-date daily bars after a session has been resolved.
from .exact_critical_snapshot import install as _install_v1_exact_critical_snapshot
_install_v1_exact_critical_snapshot()

# The remaining auxiliary providers (exchange-calendar HTTP, benchmark index history and
# minute-level trigger-time evidence) must never be able to consume the whole 30-minute job.
# Their failures are either fail-closed (calendar) or explicitly degraded metadata
# (benchmarks/trigger minute), matching the existing semantics without unbounded waits.
from .bounded_aux_providers import install as _install_v1_bounded_aux_providers
_install_v1_bounded_aux_providers()

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

# Observability must be the outermost market wrapper so the timing reflects the final effective
# provider stack (timeouts, exact-date overlays, cache and fallbacks included). It never changes
# return values or failure semantics, and failure to install timing must never block production.
try:
    from .runtime_metrics import install as _install_v1_runtime_metrics
    _install_v1_runtime_metrics()
except Exception as _v1_runtime_metrics_error:
    print(f'[V1] runtime timing install warning: {_v1_runtime_metrics_error}')
