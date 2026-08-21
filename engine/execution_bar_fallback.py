from __future__ import annotations

import math
import signal
import time
from datetime import date, timedelta
from typing import Callable


_EASTMONEY_BUDGET_SECONDS = 50
_SINA_BUDGET_SECONDS = 50
_PER_SYMBOL_TIMEOUT_SECONDS = 5
_MIN_MINUTE_ROWS = 180
_MINUTE_OPEN_LATEST = '09:35:00'
_MINUTE_CLOSE_EARLIEST = '14:55:00'


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
    """Bound request-style provider calls on Linux GitHub runners."""
    if seconds <= 0:
        raise TimeoutError('execution-bar fallback budget exhausted')
    if not hasattr(signal, 'SIGALRM'):
        return fn()
    old_handler = signal.getsignal(signal.SIGALRM)

    def alarm(_signum, _frame):
        raise TimeoutError(f'execution-bar fallback call exceeded {seconds}s')

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def _records(frame) -> list[dict]:
    if frame is None or getattr(frame, 'empty', False):
        return []
    if isinstance(frame, list):
        return [dict(x) for x in frame if isinstance(x, dict)]
    try:
        return [dict(x) for x in frame.to_dict('records')]
    except Exception:
        return []


def _eastmoney_bar_from_records(
    records: list[dict], *, symbol: str, name: str, trade_date: str
) -> dict | None:
    """Accept one Eastmoney daily row only with exact date/symbol and a prior close."""
    code = symbol[-6:]
    ordered = sorted(records, key=lambda row: str(row.get('日期') or '')[:10])
    current_index = next(
        (
            i
            for i, row in enumerate(ordered)
            if str(row.get('日期') or '')[:10] == trade_date
            and str(row.get('股票代码') or code).strip()[-6:] == code
        ),
        None,
    )
    if current_index is None or current_index <= 0:
        return None

    row = ordered[current_index]
    prev = ordered[current_index - 1]
    if str(prev.get('日期') or '')[:10] >= trade_date:
        return None

    open_px = _f(row.get('开盘'))
    high_px = _f(row.get('最高'))
    low_px = _f(row.get('最低'))
    close_px = _f(row.get('收盘'))
    preclose = _f(prev.get('收盘'))
    if min(open_px, high_px, low_px, close_px, preclose) <= 0:
        return None
    if high_px < max(open_px, close_px) or low_px > min(open_px, close_px):
        return None

    return {
        'date': trade_date,
        'bar_date': trade_date,
        'bar_date_evidence': 'eastmoney_daily_exact_date_match',
        'code': symbol,
        'raw_code': code,
        'name': name,
        'source': 'eastmoney-execution',
        'open': open_px,
        'high': high_px,
        'low': low_px,
        'close': close_px,
        'preclose': preclose,
        'volume': _f(row.get('成交量')) * 100.0,
        'amount': _f(row.get('成交额')),
        'turn': _f(row.get('换手率')),
        'pctChg': _f(row.get('涨跌幅'), (close_px / preclose - 1.0) * 100.0),
        'tradestatus': '1',
        'isST': '0',
        'peTTM': 0.0,
        'pbMRQ': 0.0,
        'r60_snapshot': 0.0,
    }


def _sina_minute_bar_from_records(
    records: list[dict], *, symbol: str, name: str, trade_date: str
) -> dict | None:
    """Aggregate a completed unadjusted Sina minute session into one exact-date daily bar.

    The minute fallback is deliberately strict: it needs a previous-session close, a near-open
    first current bar, at least 180 current-session rows, and a tail at/after 14:55. This prevents
    a partial intraday response from being promoted into a completed daily OHLC bar.
    """
    rows = sorted(records, key=lambda row: str(row.get('day') or ''))
    current = [row for row in rows if str(row.get('day') or '')[:10] == trade_date]
    previous = [row for row in rows if str(row.get('day') or '')[:10] < trade_date]
    if len(current) < _MIN_MINUTE_ROWS or not previous:
        return None

    first_stamp = str(current[0].get('day') or '')
    last_stamp = str(current[-1].get('day') or '')
    first_time = first_stamp[11:19] if len(first_stamp) >= 19 else ''
    last_time = last_stamp[11:19] if len(last_stamp) >= 19 else ''
    if not first_time or first_time > _MINUTE_OPEN_LATEST:
        return None
    if not last_time or last_time < _MINUTE_CLOSE_EARLIEST:
        return None

    open_px = _f(current[0].get('open'))
    close_px = _f(current[-1].get('close'))
    highs = [_f(row.get('high')) for row in current]
    lows = [_f(row.get('low')) for row in current]
    high_px = max(highs) if highs else 0.0
    positive_lows = [x for x in lows if x > 0]
    low_px = min(positive_lows) if positive_lows else 0.0
    preclose = _f(previous[-1].get('close'))
    if min(open_px, high_px, low_px, close_px, preclose) <= 0:
        return None
    if high_px < max(open_px, close_px) or low_px > min(open_px, close_px):
        return None

    return {
        'date': trade_date,
        'bar_date': trade_date,
        'bar_date_evidence': f'sina_minute_completed_session:{first_stamp}->{last_stamp}',
        'code': symbol,
        'raw_code': symbol[-6:],
        'name': name,
        'source': 'sina-minute-execution',
        'open': open_px,
        'high': high_px,
        'low': low_px,
        'close': close_px,
        'preclose': preclose,
        'volume': sum(max(0.0, _f(row.get('volume'))) for row in current),
        'amount': sum(max(0.0, _f(row.get('amount'))) for row in current),
        'turn': 0.0,
        'pctChg': (close_px / preclose - 1.0) * 100.0,
        'tradestatus': '1',
        'isST': '0',
        'peTTM': 0.0,
        'pbMRQ': 0.0,
        'r60_snapshot': 0.0,
    }


def _fetch_eastmoney(ak, symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
    if not symbols:
        return {}
    d = date.fromisoformat(trade_date)
    start = (d - timedelta(days=14)).strftime('%Y%m%d')
    end = d.strftime('%Y%m%d')
    deadline = time.monotonic() + _EASTMONEY_BUDGET_SECONDS
    out: dict[str, dict] = {}

    for symbol, name in symbols.items():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print('[market] Eastmoney exact-date fallback provider budget exhausted')
            break
        timeout = max(1, min(_PER_SYMBOL_TIMEOUT_SECONDS, int(remaining)))
        try:
            frame = _bounded(
                timeout + 1,
                lambda symbol=symbol, timeout=timeout: ak.stock_zh_a_hist(
                    symbol=symbol[-6:],
                    period='daily',
                    start_date=start,
                    end_date=end,
                    adjust='',
                    timeout=timeout,
                ),
            )
            bar = _eastmoney_bar_from_records(
                _records(frame), symbol=symbol, name=name, trade_date=trade_date
            )
            if bar is not None:
                out[symbol] = bar
        except Exception as exc:
            print(f'[market] critical Eastmoney bar {symbol} failed: {exc}')

    print(
        f'[market] Eastmoney exact-date fallback trade_date={trade_date} '
        f'requested={len(symbols)} returned={len(out)}'
    )
    return out


def _fetch_sina_minute(ak, symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
    if not symbols:
        return {}
    deadline = time.monotonic() + _SINA_BUDGET_SECONDS
    out: dict[str, dict] = {}

    for symbol, name in symbols.items():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print('[market] Sina minute exact-date fallback provider budget exhausted')
            break
        timeout = max(1, min(_PER_SYMBOL_TIMEOUT_SECONDS, int(remaining)))
        try:
            frame = _bounded(
                timeout,
                lambda symbol=symbol: ak.stock_zh_a_minute(
                    symbol=symbol.replace('.', ''), period='1', adjust=''
                ),
            )
            bar = _sina_minute_bar_from_records(
                _records(frame), symbol=symbol, name=name, trade_date=trade_date
            )
            if bar is not None:
                out[symbol] = bar
        except Exception as exc:
            print(f'[market] critical Sina minute bar {symbol} failed: {exc}')

    print(
        f'[market] Sina minute exact-date fallback trade_date={trade_date} '
        f'requested={len(symbols)} returned={len(out)}'
    )
    return out


def fetch_alternate_execution_bars(ak, symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
    """Recover Tencent misses from independent sources without weakening date evidence.

    Eastmoney unadjusted daily history is tried first because it directly exposes a dated daily
    record and supports request timeouts. Only the still-missing symbols then use Sina's explicit
    minute timestamps, aggregated under completed-session checks. No undated spot quote is ever
    accepted. If both sources fail, the symbol remains missing and downstream coverage gates fail
    closed exactly as before.
    """
    if not symbols:
        return {}

    out = _fetch_eastmoney(ak, symbols, trade_date)
    remaining = {sym: name for sym, name in symbols.items() if sym not in out}
    if remaining:
        out.update(_fetch_sina_minute(ak, remaining, trade_date))

    print(
        f'[market] alternate exact-date fallback trade_date={trade_date} '
        f'requested={len(symbols)} returned={len(out)} missing={len(symbols) - len(out)}'
    )
    return out
