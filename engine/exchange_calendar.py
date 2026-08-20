from __future__ import annotations

from datetime import date, timedelta


def _calendar_dates(requested_date: str, ak=None) -> list[str]:
    date.fromisoformat(requested_date)
    if ak is None:
        import akshare as ak

    try:
        frame = ak.tool_trade_date_hist_sina()
    except Exception as exc:
        raise RuntimeError(f'Cannot load exchange trading calendar: {exc}') from exc

    if frame is None or frame.empty or 'trade_date' not in frame.columns:
        raise RuntimeError('Exchange trading calendar is empty or missing trade_date')

    dates = sorted({str(value)[:10] for value in frame['trade_date'].tolist() if str(value)[:10]})
    if not dates:
        raise RuntimeError('Exchange trading calendar contains no usable dates')
    if requested_date > dates[-1]:
        raise RuntimeError(
            f'Exchange trading calendar ends at {dates[-1]} and cannot classify {requested_date}'
        )
    return dates


def exchange_calendar_latest_session(requested_date: str, ak=None) -> str:
    """Resolve the latest exchange session on or before ``requested_date``.

    Session classification is deliberately independent from quote and daily-bar freshness.
    Data feeds may lag after the close; the exchange calendar is the authority. Calendar
    lookup failures are fatal so callers fail closed instead of silently processing stale data.
    """
    dates = _calendar_dates(requested_date, ak)
    valid = [value for value in dates if value <= requested_date]
    if not valid:
        raise RuntimeError(f'Exchange trading calendar has no session on or before {requested_date}')
    return valid[-1]


def exchange_calendar_previous_session(trade_date: str, ak=None) -> str:
    """Resolve the exchange session immediately before ``trade_date``.

    Do not call a quote/history endpoint merely to infer the previous session. That endpoint
    can lag or hang independently of the exchange calendar, which would block settlement.
    """
    current = date.fromisoformat(trade_date)
    return exchange_calendar_latest_session((current - timedelta(days=1)).isoformat(), ak)
