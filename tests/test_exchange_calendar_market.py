import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from engine.exchange_calendar_market import _should_enforce_close_gate
from engine.real_market import AKShareMarket


_CN = ZoneInfo('Asia/Shanghai')


class ExchangeCalendarMarketTests(unittest.TestCase):
    def test_installed_market_resolver_uses_exchange_calendar(self):
        market = AKShareMarket.__new__(AKShareMarket)
        market.ak = object()
        with patch(
            'engine.exchange_calendar_market.exchange_calendar_latest_session',
            return_value='2025-01-03',
        ) as resolver:
            trade_date = market.latest_trade_date('2025-01-03')

        self.assertEqual(trade_date, '2025-01-03')
        self.assertEqual(market._resolved_trade_date, '2025-01-03')
        resolver.assert_called_once_with('2025-01-03', market.ak)

    def test_calendar_failure_propagates_instead_of_falling_back_to_stale_quote_date(self):
        market = AKShareMarket.__new__(AKShareMarket)
        market.ak = object()
        with patch(
            'engine.exchange_calendar_market.exchange_calendar_latest_session',
            side_effect=RuntimeError('calendar unavailable'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'calendar unavailable'):
                market.latest_trade_date('2025-01-03')

    def test_before_close_gate_applies_only_when_today_is_an_exchange_session(self):
        before_close = datetime(2026, 8, 21, 10, 0, tzinfo=_CN)
        self.assertTrue(
            _should_enforce_close_gate('2026-08-21', '2026-08-21', before_close)
        )

        # Saturday recovery at 02:xx resolves to Friday. Wall-clock 15:05 must not block an
        # already completed Friday settlement merely because the requested calendar date is today.
        saturday = datetime(2026, 8, 22, 2, 43, tzinfo=_CN)
        self.assertFalse(
            _should_enforce_close_gate('2026-08-22', '2026-08-21', saturday)
        )

        # An explicit historical request is also already completed regardless of current clock.
        self.assertFalse(
            _should_enforce_close_gate('2026-08-20', '2026-08-20', saturday)
        )

    def test_installed_resolver_allows_current_non_session_to_resolve_previous_session(self):
        market = AKShareMarket.__new__(AKShareMarket)
        market.ak = object()
        fake_now = datetime(2026, 8, 22, 2, 43, tzinfo=_CN)

        class _FakeDateTime:
            @classmethod
            def now(cls, _tz):
                return fake_now

        with patch(
            'engine.exchange_calendar_market.exchange_calendar_latest_session',
            return_value='2026-08-21',
        ) as resolver, patch('engine.exchange_calendar_market.datetime', _FakeDateTime):
            trade_date = market.latest_trade_date('2026-08-22')

        self.assertEqual(trade_date, '2026-08-21')
        self.assertEqual(market._resolved_trade_date, '2026-08-21')
        resolver.assert_called_once_with('2026-08-22', market.ak)


if __name__ == '__main__':
    unittest.main()
