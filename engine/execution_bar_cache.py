from __future__ import annotations


def _execution_bars_with_cache(self, original, symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
    """Reuse exact-date execution bars within one market-loader instance.

    Only successful symbol rows are cached. Missing symbols are never negatively cached, so a
    later caller can retry an upstream miss. The cache is keyed by trade date and symbol and is
    intentionally process-local: it cannot leak stale bars into another production run.
    Returned rows are copied so callers cannot mutate the cached source of truth.
    """
    if not symbols:
        return {}

    date_key = str(trade_date or '')[:10]
    cache = getattr(self, '_v1_execution_bar_cache_by_date', None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(self, '_v1_execution_bar_cache_by_date', cache)

    bucket = cache.setdefault(date_key, {})
    missing = {sym: name for sym, name in symbols.items() if sym not in bucket}
    hits = len(symbols) - len(missing)

    fresh: dict[str, dict] = {}
    if missing:
        fresh = original(self, missing, trade_date) or {}
        for sym, bar in fresh.items():
            if sym in missing and isinstance(bar, dict):
                bucket[sym] = dict(bar)

    # A runner is short-lived, but keep the process-local cache bounded for direct/interactive
    # callers that may reuse one AKShareMarket object across several dates.
    if len(cache) > 3:
        for old_key in list(cache)[:-3]:
            cache.pop(old_key, None)

    out = {sym: dict(bucket[sym]) for sym in symbols if sym in bucket}
    print(
        f'[market] execution-bar cache trade_date={date_key or trade_date} '
        f'requested={len(symbols)} hits={hits} provider_requested={len(missing)} '
        f'provider_returned={len(fresh)} available={len(out)}'
    )
    return out


def install() -> None:
    """Install a per-instance exact-bar cache without changing execution semantics."""
    from .real_market import AKShareMarket

    if getattr(AKShareMarket.execution_bars, '_v1_execution_bar_cache_installed', False):
        return

    original_execution_bars = AKShareMarket.execution_bars

    def execution_bars(self, symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
        return _execution_bars_with_cache(self, original_execution_bars, symbols, trade_date)

    execution_bars._v1_execution_bar_cache_installed = True
    AKShareMarket.execution_bars = execution_bars
