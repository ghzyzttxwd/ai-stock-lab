from __future__ import annotations

import signal


def _bounded(seconds: int, fn):
    """Run one provider call with a hard wall-clock bound on Linux runners."""
    if not hasattr(signal, 'SIGALRM'):
        return fn()
    old = signal.getsignal(signal.SIGALRM)

    def alarm(_sig, _frame):
        raise TimeoutError(f'provider call exceeded {seconds}s')

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _bounded_minute_frame(ak, symbol: str, trade_date: str):
    from .trade_price_time import _code

    code = _code(symbol)
    start = f'{trade_date} 09:30:00'
    end = f'{trade_date} 15:00:00'
    errors = []

    try:
        frame = _bounded(
            20,
            lambda: ak.stock_zh_a_hist_min_em(
                symbol=code,
                period='1',
                start_date=start,
                end_date=end,
                adjust='',
            ),
        )
        if frame is not None and not frame.empty:
            return frame, 'eastmoney-1m'
    except Exception as exc:
        errors.append(f'hist_min={type(exc).__name__}: {exc}')

    try:
        frame = _bounded(
            20,
            lambda: ak.stock_zh_a_hist_pre_min_em(
                symbol=code,
                start_time='09:30:00',
                end_time='15:00:00',
            ),
        )
        if frame is not None and not frame.empty:
            return frame, 'eastmoney-pre-1m'
    except Exception as exc:
        errors.append(f'pre_min={type(exc).__name__}: {exc}')

    raise RuntimeError('; '.join(errors) or 'minute quote history unavailable')


def _bounded_benchmarks(self, start_date: str, trade_date: str) -> list[dict]:
    from .real_market import _f, _period_return

    specs = [('沪深300', 'sh000300'), ('中证500', 'sh000905'), ('中证1000', 'sh000852')]
    result = []
    for name, symbol in specs:
        item = {
            'name': name,
            'symbol': symbol,
            'return_pct': None,
            'return_5d_pct': None,
            'return_20d_pct': None,
            'return_60d_pct': None,
            'curve': [],
        }
        try:
            df = _bounded(25, lambda s=symbol: self.ak.stock_zh_index_daily_tx(symbol=s))
            if df is None or df.empty:
                result.append(item)
                continue
            df = df.copy()
            df['date'] = df['date'].astype(str).str[:10]
            df = df[(df['date'] >= start_date) & (df['date'] <= trade_date)]
            vals = [_f(x) for x in df['close'].tolist() if _f(x) > 0]
            dates = df['date'].tolist()[-len(vals):] if vals else []
            if vals:
                base = vals[0]
                item['return_pct'] = round((vals[-1] / base - 1) * 100, 2)
                item['return_5d_pct'] = _period_return(vals, 5)
                item['return_20d_pct'] = _period_return(vals, 20)
                item['return_60d_pct'] = _period_return(vals, 60)
                item['curve'] = [
                    {'date': d, 'equity': round(1_000_000 * v / base, 2)}
                    for d, v in zip(dates, vals)
                ]
        except Exception as exc:
            print(f'[market] bounded benchmark failed {symbol}: {exc}')
        result.append(item)
    return result


def install() -> None:
    """Bound provider calls that otherwise can stall the whole 15:10 job."""
    from .real_market import AKShareMarket
    from . import exchange_calendar, trade_price_time

    if not getattr(exchange_calendar._calendar_dates, '_v1_bounded_provider_installed', False):
        original_calendar_dates = exchange_calendar._calendar_dates

        def calendar_dates(requested_date: str, ak=None):
            return _bounded(20, lambda: original_calendar_dates(requested_date, ak))

        calendar_dates._v1_bounded_provider_installed = True
        exchange_calendar._calendar_dates = calendar_dates

    if not getattr(AKShareMarket.benchmarks, '_v1_bounded_provider_installed', False):
        _bounded_benchmarks._v1_bounded_provider_installed = True
        AKShareMarket.benchmarks = _bounded_benchmarks

    if not getattr(trade_price_time._minute_frame, '_v1_bounded_provider_installed', False):
        _bounded_minute_frame._v1_bounded_provider_installed = True
        trade_price_time._minute_frame = _bounded_minute_frame
