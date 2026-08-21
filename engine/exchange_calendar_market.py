from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .exchange_calendar import exchange_calendar_latest_session


_CLOSE_READY = time(15, 5)
_CN = ZoneInfo('Asia/Shanghai')


def _should_enforce_close_gate(requested_date: str, latest_session: str, now: datetime) -> bool:
    """Block before 15:05 only when the requested calendar day is itself today's session.

    On weekends and exchange holidays, ``requested_date`` can equal today's wall-clock date
    while ``latest_session`` correctly resolves to an already completed prior session. Such a
    recovery must not be rejected as "market not closed yet" merely because the clock is before
    15:05 on a non-session day.
    """
    requested = date.fromisoformat(requested_date)
    return (
        requested == now.date()
        and latest_session == requested_date
        and now.time() < _CLOSE_READY
    )


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
        now = datetime.now(_CN)
        trade_date = exchange_calendar_latest_session(requested_date, self.ak)

        # Preserve the no-before-close rule only when today is actually an exchange session.
        # A current-date weekend/holiday request resolves to an older completed session and is
        # therefore a legitimate recovery run, not an attempt to settle an unfinished session.
        if _should_enforce_close_gate(requested_date, trade_date, now):
            return original_latest(self, requested_date)

        self._resolved_trade_date = trade_date
        print(
            f'[market] trading-date source=exchange-calendar requested={requested_date} '
            f'date={trade_date}'
        )
        return trade_date

    latest_trade_date._v1_exchange_calendar_installed = True
    AKShareMarket.latest_trade_date = latest_trade_date
