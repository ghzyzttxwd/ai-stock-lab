import unittest

from engine.execution_bar_cache import _execution_bars_with_cache
from engine.execution_bar_fallback import (
    _eastmoney_bar_from_records,
    _sina_minute_bar_from_records,
    fetch_alternate_execution_bars,
)


class _Market:
    pass


def _daily_records(code='600000', current_date='2026-08-21'):
    return [
        {
            '日期': '2026-08-20',
            '股票代码': code,
            '开盘': 9.9,
            '最高': 10.2,
            '最低': 9.8,
            '收盘': 10.0,
            '成交量': 10000,
            '成交额': 1000000,
        },
        {
            '日期': current_date,
            '股票代码': code,
            '开盘': 10.1,
            '最高': 10.6,
            '最低': 10.0,
            '收盘': 10.5,
            '成交量': 12000,
            '成交额': 1300000,
            '换手率': 1.2,
            '涨跌幅': 5.0,
        },
    ]


def _completed_minute_records():
    rows = [
        {
            'day': '2026-08-20 15:00:00',
            'open': 10.0,
            'high': 10.0,
            'low': 10.0,
            'close': 10.0,
            'volume': 100,
        }
    ]
    # The parser requires a completed, broad session rather than a few late spot-like rows.
    for i in range(178):
        hour = 9 + ((31 + i) // 60)
        minute = (31 + i) % 60
        rows.append(
            {
                'day': f'2026-08-21 {hour:02d}:{minute:02d}:00',
                'open': 10.1,
                'high': 10.4,
                'low': 10.0,
                'close': 10.2,
                'volume': 100,
            }
        )
    rows.append(
        {
            'day': '2026-08-21 14:59:00',
            'open': 10.2,
            'high': 10.7,
            'low': 10.1,
            'close': 10.5,
            'volume': 200,
        }
    )
    rows.append(
        {
            'day': '2026-08-21 15:00:00',
            'open': 10.5,
            'high': 10.6,
            'low': 10.4,
            'close': 10.55,
            'volume': 200,
        }
    )
    return rows


class _FakeAk:
    def __init__(self):
        self.daily_calls = []
        self.minute_calls = []

    def stock_zh_a_hist(self, *, symbol, period, start_date, end_date, adjust):
        self.daily_calls.append(symbol)
        if symbol == '600000':
            return _daily_records('600000')
        return _daily_records(symbol, current_date='2026-08-20')

    def stock_zh_a_minute(self, *, symbol, period, adjust):
        self.minute_calls.append(symbol)
        return _completed_minute_records()


class ExecutionBarFallbackTests(unittest.TestCase):
    def test_eastmoney_daily_requires_exact_requested_date_and_symbol(self):
        records = _daily_records()
        bar = _eastmoney_bar_from_records(
            records,
            symbol='sh.600000',
            name='浦发银行',
            trade_date='2026-08-21',
        )
        self.assertIsNotNone(bar)
        self.assertEqual(bar['bar_date'], '2026-08-21')
        self.assertEqual(bar['source'], 'eastmoney-execution')
        self.assertAlmostEqual(bar['preclose'], 10.0)
        self.assertAlmostEqual(bar['close'], 10.5)

        self.assertIsNone(
            _eastmoney_bar_from_records(
                _daily_records(current_date='2026-08-20'),
                symbol='sh.600000',
                name='浦发银行',
                trade_date='2026-08-21',
            )
        )
        wrong_code = [dict(row, 股票代码='600001') for row in records]
        self.assertIsNone(
            _eastmoney_bar_from_records(
                wrong_code,
                symbol='sh.600000',
                name='浦发银行',
                trade_date='2026-08-21',
            )
        )

    def test_eastmoney_invalid_price_geometry_stays_missing(self):
        records = _daily_records()
        records[-1] = dict(records[-1], 最高=10.0, 收盘=10.5)
        self.assertIsNone(
            _eastmoney_bar_from_records(
                records,
                symbol='sh.600000',
                name='浦发银行',
                trade_date='2026-08-21',
            )
        )

    def test_sina_minute_requires_completed_exact_session(self):
        records = _completed_minute_records()
        bar = _sina_minute_bar_from_records(
            records,
            symbol='sz.000001',
            name='平安银行',
            trade_date='2026-08-21',
        )
        self.assertIsNotNone(bar)
        self.assertEqual(bar['bar_date'], '2026-08-21')
        self.assertEqual(bar['source'], 'sina-minute-execution')
        self.assertTrue(bar['bar_date_evidence'].startswith('sina_minute_completed_session:'))
        self.assertAlmostEqual(bar['preclose'], 10.0)
        self.assertAlmostEqual(bar['close'], 10.55)

        partial = [row for row in records if not str(row.get('day')).startswith('2026-08-21 14:') and not str(row.get('day')).startswith('2026-08-21 15:')]
        self.assertIsNone(
            _sina_minute_bar_from_records(
                partial,
                symbol='sz.000001',
                name='平安银行',
                trade_date='2026-08-21',
            )
        )

    def test_alternate_provider_uses_sina_only_for_eastmoney_misses(self):
        ak = _FakeAk()
        symbols = {'sh.600000': '浦发银行', 'sz.000001': '平安银行'}
        out = fetch_alternate_execution_bars(ak, symbols, '2026-08-21')

        self.assertEqual(set(out), set(symbols))
        self.assertEqual(out['sh.600000']['source'], 'eastmoney-execution')
        self.assertEqual(out['sz.000001']['source'], 'sina-minute-execution')
        self.assertEqual(ak.daily_calls, ['600000', '000001'])
        self.assertEqual(ak.minute_calls, ['sz000001'])

    def test_cache_fallback_receives_only_primary_misses_and_primary_wins(self):
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
                'sh.600000': {'code': 'sh.600000', 'close': 999.0, 'source': 'bad'},
                'sz.000001': {
                    'code': 'sz.000001',
                    'close': 12.0,
                    'bar_date': trade_date,
                    'source': 'eastmoney-execution',
                },
            }

        symbols = {'sh.600000': '浦发银行', 'sz.000001': '平安银行'}
        out = _execution_bars_with_cache(
            market, primary, symbols, '2026-08-21', fallback=fallback
        )

        self.assertEqual(primary_calls, [(symbols, '2026-08-21')])
        self.assertEqual(fallback_calls, [({'sz.000001': '平安银行'}, '2026-08-21')])
        self.assertEqual(out['sh.600000']['source'], 'tencent-execution')
        self.assertEqual(out['sh.600000']['close'], 10.0)
        self.assertEqual(out['sz.000001']['source'], 'eastmoney-execution')

    def test_successful_fallback_is_cached_but_failure_is_retried(self):
        market = _Market()
        fallback_attempts = 0

        def primary(_self, symbols, trade_date):
            return {}

        def fallback(symbols, trade_date):
            nonlocal fallback_attempts
            fallback_attempts += 1
            if fallback_attempts == 1:
                return {}
            return {
                sym: {
                    'code': sym,
                    'close': 10.0,
                    'bar_date': trade_date,
                    'source': 'sina-minute-execution',
                }
                for sym in symbols
            }

        symbols = {'sh.600000': '浦发银行'}
        first = _execution_bars_with_cache(
            market, primary, symbols, '2026-08-21', fallback=fallback
        )
        second = _execution_bars_with_cache(
            market, primary, symbols, '2026-08-21', fallback=fallback
        )
        third = _execution_bars_with_cache(
            market, primary, symbols, '2026-08-21', fallback=fallback
        )

        self.assertEqual(first, {})
        self.assertIn('sh.600000', second)
        self.assertEqual(second, third)
        self.assertEqual(fallback_attempts, 2)


if __name__ == '__main__':
    unittest.main()
