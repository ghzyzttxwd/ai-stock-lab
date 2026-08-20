from __future__ import annotations

import time


_PROVIDER_TIMEOUT_SECONDS = {
    'eastmoney': 35,
    'sina': 30,
}


def _try_snapshot_provider_with_circuit(
    self,
    label: str,
    fn,
    *,
    call_with_timeout,
    sleep_fn=time.sleep,
) -> list[dict] | None:
    """Bound a snapshot provider and remember hard failures for this market instance.

    A hard timeout is not retried immediately because the same upstream call is very likely to
    block again. Fast/transient failures still receive one retry, preserving the previous
    recovery behavior. After a provider exhausts its chance, subsequent snapshot calls on the
    same AKShareMarket instance skip it and move to the next provider/fallback.
    """
    failed = getattr(self, '_v1_failed_snapshot_providers', None)
    if not isinstance(failed, set):
        failed = set()
        setattr(self, '_v1_failed_snapshot_providers', failed)

    if label in failed:
        print(f'[market] snapshot source={label} circuit=open; skipping provider for this run')
        return None

    timeout_seconds = _PROVIDER_TIMEOUT_SECONDS.get(label, 45)
    last_error = None
    for attempt in (1, 2):
        try:
            rows = call_with_timeout(timeout_seconds, fn)
            if rows is None or len(rows) < 500:
                size = 0 if rows is None else len(rows)
                raise RuntimeError(f'suspiciously small snapshot: {size} rows')
            failed.discard(label)
            print(
                f'[market] snapshot source={label} rows={len(rows)} attempt={attempt} '
                f'timeout={timeout_seconds}s'
            )
            return rows
        except TimeoutError as exc:
            last_error = exc
            failed.add(label)
            print(
                f'[market] {label} snapshot hard-timeout after {timeout_seconds}s; '
                'opening circuit for this run and moving to fallback'
            )
            break
        except Exception as exc:
            last_error = exc
            print(f'[market] {label} snapshot attempt {attempt}/2 failed: {exc}')
            if attempt == 1:
                sleep_fn(3)

    failed.add(label)
    print(f'[market] snapshot source={label} unavailable for this run: {last_error}')
    return None


def install() -> None:
    """Install source-aware timeout/circuit behavior without changing fallback order."""
    from .real_market import AKShareMarket, _call_with_timeout

    if getattr(AKShareMarket._try_snapshot_provider, '_v1_snapshot_circuit_installed', False):
        return

    def _try_snapshot_provider(self, label: str, fn) -> list[dict] | None:
        return _try_snapshot_provider_with_circuit(
            self,
            label,
            fn,
            call_with_timeout=_call_with_timeout,
        )

    _try_snapshot_provider._v1_snapshot_circuit_installed = True
    AKShareMarket._try_snapshot_provider = _try_snapshot_provider
