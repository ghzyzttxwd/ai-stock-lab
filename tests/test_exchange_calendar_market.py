import unittest
from unittest.mock import patch

from engine.real_market import AKShareMarket


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


if __name__ == '__main__':
    unittest.main()
