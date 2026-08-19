from __future__ import annotations

from datetime import date


def normalize_spot_symbol(raw: object) -> str:
    text = str(raw or '').lower().strip()
    digits = ''.join(ch for ch in text if ch.isdigit())[-6:]
    if len(digits) != 6:
        return text
    if text.startswith('sh') or digits.startswith(('600', '601', '603', '605')):
        return 'sh.' + digits
    return 'sz.' + digits


def exchange_sessions(ak) -> list[str]:
    frame = ak.tool_trade_date_hist_sina()
    return sorted({str(x)[:10] for x in frame['trade_date'].tolist() if str(x)[:10]})


def previous_exchange_session(ak, trade_date: str) -> str | None:
    sessions = exchange_sessions(ak)
    return max((x for x in sessions if x < trade_date), default=None)


def is_exchange_session(ak, trade_date: str) -> bool:
    return trade_date in set(exchange_sessions(ak))


def _eastmoney_bars(ak, critical: dict[str, str]) -> dict[str, dict]:
    frame = ak.stock_zh_a_spot_em()
    bars: dict[str, dict] = {}
    for _, row in frame.iterrows():
        symbol = normalize_spot_symbol(row.get('代码'))
        if symbol not in critical:
            continue
        try:
            last = float(row.get('最新价') or 0.0)
            opening = float(row.get('今开') or 0.0)
            previous = float(row.get('昨收') or 0.0)
        except (TypeError, ValueError):
            continue
        if last <= 0:
            continue
        bars[symbol] = {
            'code': symbol,
            'name': str(row.get('名称') or critical.get(symbol) or symbol),
            'open': opening or last,
            'close': last,
            'preclose': previous or last,
            'tradestatus': '1',
            'source': 'eastmoney-intraday',
        }
    return bars


def _sina_bars(ak, critical: dict[str, str]) -> dict[str, dict]:
    frame = ak.stock_zh_a_spot()
    bars: dict[str, dict] = {}
    for _, row in frame.iterrows():
        symbol = normalize_spot_symbol(row.get('代码'))
        if symbol not in critical:
            continue
        try:
            last = float(row.get('最新价') or 0.0)
            opening = float(row.get('今开') or 0.0)
            previous = float(row.get('昨收') or 0.0)
        except (TypeError, ValueError):
            continue
        if last <= 0:
            continue
        bars[symbol] = {
            'code': symbol,
            'name': str(row.get('名称') or critical.get(symbol) or symbol),
            'open': opening or last,
            'close': last,
            'preclose': previous or last,
            'tradestatus': '1',
            'source': 'sina-intraday',
        }
    return bars


def live_execution_bars(ak, critical: dict[str, str]) -> tuple[dict[str, dict], str]:
    """Fetch one current full-market snapshot, then keep only execution-critical symbols."""
    if not critical:
        return {}, 'none'
    try:
        bars = _eastmoney_bars(ak, critical)
        if bars:
            return bars, 'eastmoney-intraday'
    except Exception as exc:
        print(f'[V2 MORNING] eastmoney intraday failed: {exc}')
    bars = _sina_bars(ak, critical)
    if not bars:
        raise RuntimeError('V2 09:40 intraday quote snapshot returned no critical symbols')
    return bars, 'sina-intraday'
