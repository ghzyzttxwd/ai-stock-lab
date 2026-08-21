from __future__ import annotations

import math
import signal
import time
from typing import Callable


_FIELDS = (
    'date', 'code', 'open', 'high', 'low', 'close', 'preclose', 'volume',
    'amount', 'turn', 'tradestatus', 'pctChg', 'isST',
)
_PROVIDER_BUDGET_SECONDS = 75
_PER_SYMBOL_TIMEOUT_SECONDS = 6


def _f(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        text = str(value).strip()
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def _bounded(seconds: int, fn: Callable):
    """Bound BaoStock network calls on Linux GitHub runners."""
    if seconds <= 0:
        raise TimeoutError('BaoStock execution-bar budget exhausted')
    if not hasattr(signal, 'SIGALRM'):
        return fn()
    old_handler = signal.getsignal(signal.SIGALRM)

    def alarm(_signum, _frame):
        raise TimeoutError(f'BaoStock call exceeded {seconds}s')

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def _bar_from_row(row: dict, *, symbol: str, name: str, trade_date: str) -> dict | None:
    """Convert one BaoStock row only when it is explicit evidence for the requested date."""
    row_date = str(row.get('date') or '')[:10]
    row_symbol = str(row.get('code') or '').strip().lower()
    if row_date != trade_date or row_symbol != symbol.lower():
        return None

    open_px = _f(row.get('open'))
    close_px = _f(row.get('close'))
    if open_px <= 0 or close_px <= 0:
        return None

    return {
        'date': trade_date,
        'bar_date': trade_date,
        'code': symbol,
        'raw_code': symbol[-6:],
        'name': name,
        'source': 'baostock-execution',
        'open': open_px,
        'high': _f(row.get('high'), close_px),
        'low': _f(row.get('low'), close_px),
        'close': close_px,
        'preclose': _f(row.get('preclose'), close_px),
        'volume': _f(row.get('volume')),
        'amount': _f(row.get('amount')),
        'turn': _f(row.get('turn')),
        'pctChg': _f(row.get('pctChg')),
        'tradestatus': str(row.get('tradestatus') or '1'),
        'isST': str(row.get('isST') or '0'),
        'peTTM': 0.0,
        'pbMRQ': 0.0,
        'r60_snapshot': 0.0,
    }


def fetch_baostock_execution_bars(symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
    """Fetch unadjusted exact-date daily bars for symbols Tencent did not provide.

    BaoStock is a second, independent dated source. A row is accepted only when both its
    returned code and returned date exactly match the requested symbol/session. Missing,
    stale, malformed or timed-out rows stay missing so downstream coverage checks fail closed.
    """
    if not symbols:
        return {}

    try:
        import baostock as bs
    except Exception as exc:
        print(f'[market] BaoStock execution fallback unavailable: {exc}')
        return {}

    deadline = time.monotonic() + _PROVIDER_BUDGET_SECONDS
    out: dict[str, dict] = {}
    logged_in = False
    try:
        login_timeout = max(1, min(10, int(deadline - time.monotonic())))
        login = _bounded(login_timeout, bs.login)
        if str(getattr(login, 'error_code', '')) != '0':
            print(
                '[market] BaoStock execution fallback login failed: '
                f'{getattr(login, "error_code", "unknown")} '
                f'{getattr(login, "error_msg", "")}'
            )
            return {}
        logged_in = True

        fields = ','.join(_FIELDS)
        for symbol, name in symbols.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print('[market] BaoStock execution fallback provider budget exhausted')
                break
            timeout = max(1, min(_PER_SYMBOL_TIMEOUT_SECONDS, int(remaining)))
            try:
                result = _bounded(
                    timeout,
                    lambda symbol=symbol: bs.query_history_k_data_plus(
                        symbol,
                        fields,
                        start_date=trade_date,
                        end_date=trade_date,
                        frequency='d',
                        adjustflag='3',
                    ),
                )
                if str(getattr(result, 'error_code', '')) != '0':
                    print(
                        f'[market] critical BaoStock bar {symbol} failed: '
                        f'{getattr(result, "error_code", "unknown")} '
                        f'{getattr(result, "error_msg", "")}'
                    )
                    continue

                while result.next():
                    values = result.get_row_data()
                    row = dict(zip(_FIELDS, values))
                    bar = _bar_from_row(row, symbol=symbol, name=name, trade_date=trade_date)
                    if bar is not None:
                        out[symbol] = bar
                        break
            except Exception as exc:
                print(f'[market] critical BaoStock bar {symbol} failed: {exc}')
    except Exception as exc:
        print(f'[market] BaoStock execution fallback failed: {exc}')
    finally:
        if logged_in:
            try:
                _bounded(5, bs.logout)
            except Exception as exc:
                print(f'[market] BaoStock logout warning: {exc}')

    print(
        f'[market] BaoStock exact-date fallback trade_date={trade_date} '
        f'requested={len(symbols)} returned={len(out)}'
    )
    return out
