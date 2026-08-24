from __future__ import annotations

import math
import signal
import time
from datetime import date, timedelta


PER_SYMBOL_TIMEOUT_SECONDS = 5
EASTMONEY_BUDGET_SECONDS = 45
SINA_BUDGET_SECONDS = 45
MIN_MINUTE_ROWS = 180
MINUTE_OPEN_LATEST = "09:35:00"
MINUTE_CLOSE_EARLIEST = "14:55:00"


def _f(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        text = str(value).strip()
        return float(text) if text else default
    except Exception:
        return default


def _bounded(seconds: int, fn):
    if seconds <= 0:
        raise TimeoutError("execution-bar fallback budget exhausted")
    if not hasattr(signal, "SIGALRM"):
        return fn()
    old = signal.getsignal(signal.SIGALRM)

    def alarm(_signum, _frame):
        raise TimeoutError(f"execution-bar fallback call exceeded {seconds}s")

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _records(frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", False):
        return []
    try:
        return [dict(x) for x in frame.to_dict("records")]
    except Exception:
        return [dict(x) for x in frame if isinstance(x, dict)] if isinstance(frame, list) else []


def _daily_bar(records: list[dict], symbol: str, name: str, trade_date: str) -> dict | None:
    rows = sorted(records, key=lambda x: str(x.get("日期") or "")[:10])
    idx = next((i for i, x in enumerate(rows) if str(x.get("日期") or "")[:10] == trade_date), None)
    if idx is None or idx <= 0:
        return None
    row, prev = rows[idx], rows[idx - 1]
    open_px, high_px, low_px, close_px = (_f(row.get(k)) for k in ("开盘", "最高", "最低", "收盘"))
    preclose = _f(prev.get("收盘"))
    if min(open_px, high_px, low_px, close_px, preclose) <= 0:
        return None
    if high_px < max(open_px, close_px) or low_px > min(open_px, close_px):
        return None
    return {
        "date": trade_date,
        "bar_date": trade_date,
        "bar_date_evidence": "eastmoney_daily_exact_date_match",
        "code": symbol,
        "raw_code": symbol[-6:],
        "name": name,
        "source": "eastmoney-execution",
        "open": open_px,
        "high": high_px,
        "low": low_px,
        "close": close_px,
        "preclose": preclose,
        "amount": _f(row.get("成交额")),
        "volume": _f(row.get("成交量")) * 100.0,
        "turn": _f(row.get("换手率")),
        "pctChg": _f(row.get("涨跌幅"), (close_px / preclose - 1.0) * 100.0),
        "tradestatus": "1",
        "isST": "0",
    }


def _minute_bar(records: list[dict], symbol: str, name: str, trade_date: str) -> dict | None:
    rows = sorted(records, key=lambda x: str(x.get("day") or ""))
    current = [x for x in rows if str(x.get("day") or "")[:10] == trade_date]
    previous = [x for x in rows if str(x.get("day") or "")[:10] < trade_date]
    if len(current) < MIN_MINUTE_ROWS or not previous:
        return None
    first_stamp = str(current[0].get("day") or "")
    last_stamp = str(current[-1].get("day") or "")
    if first_stamp[11:19] > MINUTE_OPEN_LATEST or last_stamp[11:19] < MINUTE_CLOSE_EARLIEST:
        return None
    open_px = _f(current[0].get("open"))
    close_px = _f(current[-1].get("close"))
    high_px = max((_f(x.get("high")) for x in current), default=0.0)
    lows = [_f(x.get("low")) for x in current if _f(x.get("low")) > 0]
    low_px = min(lows) if lows else 0.0
    preclose = _f(previous[-1].get("close"))
    if min(open_px, high_px, low_px, close_px, preclose) <= 0:
        return None
    if high_px < max(open_px, close_px) or low_px > min(open_px, close_px):
        return None
    return {
        "date": trade_date,
        "bar_date": trade_date,
        "bar_date_evidence": f"sina_minute_completed_session:{first_stamp}->{last_stamp}",
        "code": symbol,
        "raw_code": symbol[-6:],
        "name": name,
        "source": "sina-minute-execution",
        "open": open_px,
        "high": high_px,
        "low": low_px,
        "close": close_px,
        "preclose": preclose,
        "volume": sum(max(0.0, _f(x.get("volume"))) for x in current),
        "turn": 0.0,
        "pctChg": (close_px / preclose - 1.0) * 100.0,
        "tradestatus": "1",
        "isST": "0",
    }


def _fetch_eastmoney(ak, symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
    d = date.fromisoformat(trade_date)
    start = (d - timedelta(days=14)).strftime("%Y%m%d")
    end = d.strftime("%Y%m%d")
    deadline = time.monotonic() + EASTMONEY_BUDGET_SECONDS
    out = {}
    for symbol, name in symbols.items():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        timeout = max(1, min(PER_SYMBOL_TIMEOUT_SECONDS, int(remaining)))
        try:
            frame = _bounded(timeout, lambda symbol=symbol: ak.stock_zh_a_hist(
                symbol=symbol[-6:], period="daily", start_date=start, end_date=end, adjust=""
            ))
            bar = _daily_bar(_records(frame), symbol, name, trade_date)
            if bar:
                out[symbol] = bar
        except Exception as exc:
            print(f"[v3-market] Eastmoney exact bar {symbol} failed: {exc}")
    print(f"[v3-market] Eastmoney exact-date fallback requested={len(symbols)} returned={len(out)}")
    return out


def _fetch_sina(ak, symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
    deadline = time.monotonic() + SINA_BUDGET_SECONDS
    out = {}
    for symbol, name in symbols.items():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        timeout = max(1, min(PER_SYMBOL_TIMEOUT_SECONDS, int(remaining)))
        try:
            frame = _bounded(timeout, lambda symbol=symbol: ak.stock_zh_a_minute(
                symbol=symbol.replace(".", ""), period="1", adjust=""
            ))
            bar = _minute_bar(_records(frame), symbol, name, trade_date)
            if bar:
                out[symbol] = bar
        except Exception as exc:
            print(f"[v3-market] Sina exact bar {symbol} failed: {exc}")
    print(f"[v3-market] Sina exact-date fallback requested={len(symbols)} returned={len(out)}")
    return out


def fetch_alternate_execution_bars(ak, symbols: dict[str, str], trade_date: str) -> dict[str, dict]:
    """Recover primary-provider misses using only dated, completed-session evidence."""
    if not symbols:
        return {}
    out = _fetch_eastmoney(ak, symbols, trade_date)
    remaining = {symbol: name for symbol, name in symbols.items() if symbol not in out}
    if remaining:
        out.update(_fetch_sina(ak, remaining, trade_date))
    print(
        f"[v3-market] alternate exact-date fallback trade_date={trade_date} "
        f"requested={len(symbols)} returned={len(out)} missing={len(symbols) - len(out)}"
    )
    return out
