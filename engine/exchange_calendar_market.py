from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .exchange_calendar import exchange_calendar_latest_session


_CLOSE_READY = time(15, 5)
_CN = ZoneInfo('Asia/Shanghai')


def install() -> None:
    """Make AKShareMarket session resolution independent from quote-bar freshness.

    The previous V1 path wrapped ``AKShareMarket.latest_trade_date`` only to remember the
    provider result. If Tencent's daily endpoint lagged after the close, the engine therefore
    resolved a real trading day to yesterday. This wrapper is installed after the Tencent
    fallback and makes the exchange calendar authoritative for the session date. Market/history
    loading still has to provide bars for that date; if it cannot, downstream validation fails
    rather than silently processing yesterday again.
    """
    from .real_market import AKShareMarket

    if getattr(AKShareMarket.latest_trade_date, '_v1_exchange_calendar_installed', False):
        return

    original_latest = AKShareMarket.latest_trade_date

    def latest_trade_date(self, requested_date: str):
        requested = date.fromisoformat(requested_date)
        now = datetime.now(_CN)

        # Preserve the existing no-before-close rule for an explicit current-day market run.
        # Scheduled preflight does not use this method; it calls the calendar resolver directly.
        if requested == now.date() and now.time() < _CLOSE_READY:
            return original_latest(self, requested_date)

        trade_date = exchange_calendar_latest_session(requested_date, self.ak)
        self._resolved_trade_date = trade_date
        print(
            f'[market] trading-date source=exchange-calendar requested={requested_date} '
            f'date={trade_date}'
        )
        return trade_date

    latest_trade_date._v1_exchange_calendar_installed = True
    AKShareMarket.latest_trade_date = latest_trade_date
