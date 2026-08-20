from __future__ import annotations

from typing import Any


def _code(symbol: str) -> str:
    digits = ''.join(ch for ch in str(symbol) if ch.isdigit())
    return digits[-6:]


def _clock(value: Any) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    if ' ' in text:
        text = text.rsplit(' ', 1)[-1]
    if 'T' in text:
        text = text.rsplit('T', 1)[-1]
    return text[:5] if len(text) >= 5 and ':' in text else None


def _minute_frame(ak, symbol: str, trade_date: str):
    code = _code(symbol)
    start = f'{trade_date} 09:30:00'
    end = f'{trade_date} 15:00:00'
    errors = []
    try:
        frame = ak.stock_zh_a_hist_min_em(
            symbol=code, period='1', start_date=start, end_date=end, adjust='',
        )
        if frame is not None and not frame.empty:
            return frame, 'eastmoney-1m'
    except Exception as exc:
        errors.append(f'hist_min={type(exc).__name__}: {exc}')
    try:
        frame = ak.stock_zh_a_hist_pre_min_em(
            symbol=code, start_time='09:30:00', end_time='15:00:00',
        )
        if frame is not None and not frame.empty:
            return frame, 'eastmoney-pre-1m'
    except Exception as exc:
        errors.append(f'pre_min={type(exc).__name__}: {exc}')
    raise RuntimeError('; '.join(errors) or 'minute quote history unavailable')


def _number(row, *names: str) -> float:
    for name in names:
        value = row.get(name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0.0


def first_trigger_minute(frame, plan: dict, trigger_reason: str) -> str | None:
    entry = dict((plan or {}).get('entry') or {})
    mode = str(entry.get('mode') or '')
    trigger = float(entry.get('trigger_price') or 0.0)
    lower = float(entry.get('valid_min') or 0.0)
    upper = float(entry.get('valid_max') or 0.0)
    if trigger_reason in {'gap_or_open_above_trigger', 'open_inside_pullback_zone', 'open_inside_entry_range'}:
        return '09:30'
    for _, row in frame.iterrows():
        high = _number(row, '最高', 'high')
        low = _number(row, '最低', 'low')
        if high <= 0 or low <= 0:
            continue
        touched = False
        if mode == 'breakout' and trigger > 0:
            touched = high >= trigger
        elif mode == 'pullback' and trigger > 0:
            touched = low <= trigger
        elif mode == 'range' and upper > 0:
            floor = lower if lower > 0 else upper
            touched = low <= upper and high >= floor
        if touched:
            return _clock(row.get('时间') or row.get('time') or row.get('datetime'))
    return None


def annotate_conditional_buy_fills(ak, fills: list[dict], targets: list[dict], trade_date: str) -> None:
    """Attach evidence only; never changes eligibility, simulated price, quantity, or fees."""
    target_map = {str(x.get('symbol')): x for x in (targets or []) if x.get('symbol')}
    cache: dict[str, tuple[Any, str] | Exception] = {}
    for fill in fills:
        if fill.get('side') != 'BUY' or fill.get('execution_price_field') != 'conditional_trigger':
            continue
        symbol = str(fill.get('symbol') or '')
        target = target_map.get(symbol) or {}
        plan = dict(target.get('trade_plan') or {})
        reason = str(fill.get('trigger_reason') or '')
        if reason in {'gap_or_open_above_trigger', 'open_inside_pullback_zone', 'open_inside_entry_range'}:
            fill['market_reference_time'] = '09:30'
            fill['market_reference_time_basis'] = 'market_open'
            fill['market_reference_source'] = 'daily-open'
            continue
        try:
            cached = cache.get(symbol)
            if cached is None:
                try:
                    cached = _minute_frame(ak, symbol, trade_date)
                except Exception as exc:
                    cached = exc
                cache[symbol] = cached
            if isinstance(cached, Exception):
                raise cached
            frame, source = cached
            minute = first_trigger_minute(frame, plan, reason)
            if minute:
                fill['market_reference_time'] = minute
                fill['market_reference_time_basis'] = 'first_trigger_minute'
                fill['market_reference_source'] = source
            else:
                fill['market_reference_time_unavailable'] = 'trigger_minute_not_found'
        except Exception as exc:
            fill['market_reference_time_unavailable'] = f'{type(exc).__name__}: {exc}'[:240]
