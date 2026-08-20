from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_EVENTS: list[dict[str, Any]] = []
_INSTALLED = False


def reset() -> None:
    with _LOCK:
        _EVENTS.clear()


def record(name: str, elapsed_s: float, *, status: str = 'ok', detail: str = '') -> None:
    event = {
        'name': str(name),
        'elapsed_s': round(float(elapsed_s), 3),
        'status': str(status),
        'detail': str(detail or ''),
    }
    with _LOCK:
        _EVENTS.append(event)
    suffix = f' detail={event["detail"]}' if event['detail'] else ''
    print(f'[PERF] stage={event["name"]} elapsed_s={event["elapsed_s"]:.3f} status={event["status"]}{suffix}')


@contextmanager
def stage(name: str, *, detail: str = ''):
    started = time.monotonic()
    try:
        yield
    except BaseException as exc:
        record(name, time.monotonic() - started, status='error', detail=f'{type(exc).__name__}: {exc}'[:220])
        raise
    else:
        record(name, time.monotonic() - started, status='ok', detail=detail)


def snapshot() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(x) for x in _EVENTS]


def _aggregate(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        name = str(event['name'])
        if name not in grouped:
            grouped[name] = {'name': name, 'calls': 0, 'elapsed_s': 0.0, 'status': 'ok'}
            order.append(name)
        row = grouped[name]
        row['calls'] += 1
        row['elapsed_s'] += float(event['elapsed_s'])
        if event.get('status') != 'ok':
            row['status'] = 'error'
    out = []
    for name in order:
        row = grouped[name]
        row['elapsed_s'] = round(row['elapsed_s'], 3)
        out.append(row)
    return out


def render_summary() -> str:
    rows = _aggregate(snapshot())
    if not rows:
        return '[PERF SUMMARY] no timing events'
    lines = [
        '[PERF SUMMARY] ' + ' | '.join(
            f"{row['name']}={row['elapsed_s']:.3f}s/{row['calls']}x/{row['status']}"
            for row in rows
        )
    ]
    slow = sorted(rows, key=lambda x: float(x['elapsed_s']), reverse=True)[:5]
    lines.append('[PERF SLOWEST] ' + ' | '.join(f"{x['name']}={x['elapsed_s']:.3f}s" for x in slow))
    return '\n'.join(lines)


def write_github_summary() -> None:
    rows = _aggregate(snapshot())
    if not rows:
        return
    print(render_summary())
    target = os.getenv('GITHUB_STEP_SUMMARY', '').strip()
    if not target:
        return
    try:
        path = Path(target)
        with path.open('a', encoding='utf-8') as f:
            f.write('\n### V1 runtime timing\n\n')
            f.write('| Stage | Calls | Total seconds | Status |\n')
            f.write('|---|---:|---:|---|\n')
            for row in sorted(rows, key=lambda x: float(x['elapsed_s']), reverse=True):
                f.write(
                    f"| `{row['name']}` | {row['calls']} | {row['elapsed_s']:.3f} | {row['status']} |\n"
                )
    except Exception as exc:
        print(f'[PERF] GitHub summary write skipped: {exc}')


def _wrap_method(cls: type, method_name: str, metric_name: str) -> None:
    original = getattr(cls, method_name, None)
    if original is None or getattr(original, '_v1_runtime_timed', False):
        return

    def wrapped(self, *args, **kwargs):
        started = time.monotonic()
        try:
            result = original(self, *args, **kwargs)
        except BaseException as exc:
            record(metric_name, time.monotonic() - started, status='error', detail=type(exc).__name__)
            raise
        else:
            record(metric_name, time.monotonic() - started)
            return result

    wrapped._v1_runtime_timed = True  # type: ignore[attr-defined]
    wrapped.__name__ = getattr(original, '__name__', method_name)
    wrapped.__doc__ = getattr(original, '__doc__', None)
    setattr(cls, method_name, wrapped)


def install() -> None:
    """Time final effective AKShareMarket calls without changing behavior."""
    global _INSTALLED
    if _INSTALLED:
        return
    from .real_market import AKShareMarket

    for method_name, metric_name in (
        ('latest_trade_date', 'market.latest_trade_date'),
        ('snapshot', 'market.snapshot'),
        ('preselect', 'market.preselect'),
        ('histories', 'market.histories'),
        ('execution_bars', 'market.execution_bars'),
        ('snapshot_from_histories', 'market.snapshot_from_histories'),
        ('benchmarks', 'market.benchmarks'),
    ):
        _wrap_method(AKShareMarket, method_name, metric_name)
    _INSTALLED = True
