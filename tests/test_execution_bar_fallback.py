import unittest

from engine.execution_bar_cache import _execution_bars_with_cache
from engine.execution_bar_fallback import _bar_from_row


class _Market:
    pass


class ExecutionBarFallbackTests(unittest.TestCase):
    def test_baostock_row_requires_exact_requested_date_and_symbol(self):
        row = {
            'date': '2026-08-21',
            'code': 'sh.600000',
            'open': '10.10',
            'high': '10.60',
            'low': '10.00',
            'close': '10.50',
            'preclose': '10.00',
            'volume': '123400',
            'amount': '1300000',
            'turn': '1.2',
            'tradestatus': '1',
            'pctChg': '5.0',
            'isST': '0',
        }
        bar = _bar_from_row(
            row,
            symbol='sh.600000',
            name='浦发银行',
            trade_date='2026-08-21',
        )

        self.assertIsNotNone(bar)
        self.assertEqual(bar['bar_date'], '2026-08-21')
        self.assertEqual(bar['source'], 'baostock-execution')
        self.assertAlmostEqual(bar['open'], 10.10)
        self.assertAlmostEqual(bar['close'], 10.50)

        stale = dict(row, date='2026-08-20')
        self.assertIsNone(
            _bar_from_row(
                stale,
                symbol='sh.600000',
                name='浦发银行',
                trade_date='2026-08-21',
            )
        )

        wrong_symbol = dict(row, code='sh.600001')
        self.assertIsNone(
            _bar_from_row(
                wrong_symbol,
                symbol='sh.600000',
                name='浦发银行',
                trade_date='2026-08-21',
            )
        )

    def test_invalid_price_row_stays_missing(self):
        row = {
            'date': '2026-08-21',
            'code': 'sh.600000',
            'open': '0',
            'close': '10.50',
        }
        self.assertIsNone(
            _bar_from_row(
                row,
                symbol='sh.600000',
                name='浦发银行',
                trade_date='2026-08-21',
            )
        )

    def test_fallback_receives_only_primary_misses_and_primary_wins(self):
        market = _Market()
        primary_calls = []
        fallback_calls = []

        def primary(_self, symbols, trade_date):
            primary_calls.append((dict(symbols), trade_date))
            return {
                'sh.600000': {
                    'code': 'sh.600000',
                    'close': 10.0,
                    'source': 'tencent-execution',
                }
            }

        def fallback(symbols, trade_date):
            fallback_calls.append((dict(symbols), trade_date))
            return {
                'sh.600000': {
                    'code': 'sh.600000',
                    'close': 999.0,
                    'source': 'baostock-execution',
                },
                'sz.000001': {
                    'code': 'sz.000001',
                    'close': 12.0,
                    'source': 'baostock-execution',
                },
            }

        symbols = {'sh.600000': '浦发银行', 'sz.000001': '平安银行'}
        out = _execution_bars_with_cache(
            market,
            primary,
            symbols,
            '2026-08-21',
            fallback=fallback,
        )

        self.assertEqual(primary_calls, [(symbols, '2026-08-21')])
        self.assertEqual(fallback_calls, [({'sz.000001': '平安银行'}, '2026-08-21')])
        self.assertEqual(out['sh.600000']['source'], 'tencent-execution')
        self.assertEqual(out['sh.600000']['close'], 10.0)
        self.assertEqual(out['sz.000001']['source'], 'baostock-execution')

    def test_successful_fallback_row_is_cached(self):
        market = _Market()
        primary_count = 0
        fallback_count = 0

        def primary(_self, symbols, trade_date):
            nonlocal primary_count
            primary_count += 1
            return {}

        def fallback(symbols, trade_date):
            nonlocal fallback_count
            fallback_count += 1
            return {
                sym: {
                    'code': sym,
                    'close': 10.0,
                    'bar_date': trade_date,
                    'source': 'baostock-execution',
                }
                for sym in symbols
            }

        symbols = {'sh.600000': '浦发银行'}
        first = _execution_bars_with_cache(
            market,
            primary,
            symbols,
            '2026-08-21',
            fallback=fallback,
        )
        second = _execution_bars_with_cache(
            market,
            primary,
            symbols,
            '2026-08-21',
            fallback=fallback,
        )

        self.assertEqual(first, second)
        self.assertEqual(primary_count, 1)
        self.assertEqual(fallback_count, 1)

    def test_failed_fallback_is_not_negatively_cached(self):
        market = _Market()
        attempts = 0

        def primary(_self, symbols, trade_date):
            return {}

        def fallback(symbols, trade_date):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return {}
            return {
                sym: {
                    'code': sym,
                    'close': 10.0,
                    'bar_date': trade_date,
                    'source': 'baostock-execution',
                }
                for sym in symbols
            }

        symbols = {'sh.600000': '浦发银行'}
        first = _execution_bars_with_cache(
            market,
            primary,
            symbols,
            '2026-08-21',
            fallback=fallback,
        )
        second = _execution_bars_with_cache(
            market,
            primary,
            symbols,
            '2026-08-21',
            fallback=fallback,
        )

        self.assertEqual(first, {})
        self.assertIn('sh.600000', second)
        self.assertEqual(attempts, 2)


if __name__ == '__main__':
    unittest.main()
