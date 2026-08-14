from __future__ import annotations

import signal
from collections.abc import Callable
from typing import TypeVar

T = TypeVar('T')


def bounded_call(seconds: int, fn: Callable[[], T], label: str = 'provider') -> T:
    """Bound third-party data calls on Linux GitHub runners.

    V2 prefers a visible failed shadow run over an indefinitely hung job. This helper has no
    retries by design; workflow-level reruns and future source fallbacks remain explicit.
    """
    if not hasattr(signal, 'SIGALRM'):
        return fn()
    old_handler = signal.getsignal(signal.SIGALRM)

    def _alarm(_signum, _frame):
        raise TimeoutError(f'{label} exceeded {seconds}s')

    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
