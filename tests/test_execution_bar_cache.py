import unittest

from engine.execution_bar_cache import _execution_bars_with_cache


class _Market:
    pass


class ExecutionBarCacheTests(unittest.TestCase):
    def test_duplicate_request_reuses_successful_rows(self):
        market = _Market()
        calls = []

        def provider(_self, symbols, trade_date):
            calls.append((dict(symbols), trade_date))
            return {
                sym: {'code': sym, 'name': name, 'close': 10.0, 'bar_date': trade_date}
                for sym, name in symbols.items()
            }

        symbols = {'sh.600000': '浦发银行', 'sz.000001': '平安银行'}
        first = _execution_bars_with_cache(market, provider, symbols, '2026-08-20')
        second = _execution_bars_with_cache(market, provider, symbols, '2026-08-20')

        self.assertEqual(len(calls), 1)
        self.assertEqual(set(first), set(symbols))
        self.assertEqual(second, first)

    def test_overlap_fetches_only_new_symbol(self):
        market = _Market()
        calls = []

        def provider(_self, symbols, trade_date):
            calls.append(set(symbols))
            return {
                sym: {'code': sym, 'name': name, 'close': 10.0, 'bar_date': trade_date}
                for sym, name in symbols.items()
            }

        _execution_bars_with_cache(
            market,
            provider,
            {'sh.600000': '浦发银行', 'sz.000001': '平安银行'},
            '2026-08-20',
        )
        out = _execution_bars_with_cache(
            market,
            provider,
            {'sz.000001': '平安银行', 'sh.600519': '贵州茅台'},
            '2026-08-20',
        )

        self.assertEqual(calls[0], {'sh.600000', 'sz.000001'})
        self.assertEqual(calls[1], {'sh.600519'})
        self.assertEqual(set(out), {'sz.000001', 'sh.600519'})

    def test_missing_symbol_is_not_negatively_cached(self):
        market = _Market()
        attempts = {'count': 0}

        def provider(_self, symbols, trade_date):
            attempts['count'] += 1
            if attempts['count'] == 1:
                return {}
            return {
                sym: {'code': sym, 'name': name, 'close': 10.0, 'bar_date': trade_date}
                for sym, name in symbols.items()
            }

        symbols = {'sh.600000': '浦发银行'}
        first = _execution_bars_with_cache(market, provider, symbols, '2026-08-20')
        second = _execution_bars_with_cache(market, provider, symbols, '2026-08-20')

        self.assertEqual(first, {})
        self.assertIn('sh.600000', second)
        self.assertEqual(attempts['count'], 2)

    def test_cache_is_isolated_by_trade_date_and_return_values_are_copies(self):
        market = _Market()
        calls = []

        def provider(_self, symbols, trade_date):
            calls.append(trade_date)
            return {
                sym: {'code': sym, 'name': name, 'close': 10.0, 'bar_date': trade_date}
                for sym, name in symbols.items()
            }

        symbols = {'sh.600000': '浦发银行'}
        day1 = _execution_bars_with_cache(market, provider, symbols, '2026-08-20')
        day1['sh.600000']['close'] = 999.0
        day1_again = _execution_bars_with_cache(market, provider, symbols, '2026-08-20')
        day2 = _execution_bars_with_cache(market, provider, symbols, '2026-08-21')

        self.assertEqual(day1_again['sh.600000']['close'], 10.0)
        self.assertEqual(day2['sh.600000']['bar_date'], '2026-08-21')
        self.assertEqual(calls, ['2026-08-20', '2026-08-21'])


if __name__ == '__main__':
    unittest.main()
