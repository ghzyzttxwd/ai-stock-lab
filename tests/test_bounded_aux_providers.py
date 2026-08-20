import unittest

import pandas as pd

from engine.bounded_aux_providers import _bounded_benchmarks, _bounded_minute_frame
from engine.real_market import AKShareMarket


class _MinuteAk:
    def __init__(self):
        self.primary_calls = 0
        self.fallback_calls = 0

    def stock_zh_a_hist_min_em(self, **_kwargs):
        self.primary_calls += 1
        raise RuntimeError('primary minute provider unavailable')

    def stock_zh_a_hist_pre_min_em(self, **_kwargs):
        self.fallback_calls += 1
        return pd.DataFrame([{'时间': '2026-08-20 09:31:00', '开盘': 10.0, '最高': 10.2, '最低': 9.9, '收盘': 10.1}])


class _BenchmarkAk:
    def stock_zh_index_daily_tx(self, *, symbol):
        if symbol == 'sh000905':
            raise RuntimeError('provider failed')
        return pd.DataFrame([
            {'date': '2026-08-18', 'close': 100.0},
            {'date': '2026-08-19', 'close': 101.0},
            {'date': '2026-08-20', 'close': 102.0},
        ])


class BoundedAuxProviderTests(unittest.TestCase):
    def test_minute_frame_uses_bounded_fallback(self):
        ak = _MinuteAk()
        frame, source = _bounded_minute_frame(ak, 'sh.600000', '2026-08-20')
        self.assertFalse(frame.empty)
        self.assertEqual(source, 'eastmoney-pre-1m')
        self.assertEqual(ak.primary_calls, 1)
        self.assertEqual(ak.fallback_calls, 1)

    def test_benchmark_failure_degrades_one_index_without_aborting_all(self):
        market = AKShareMarket.__new__(AKShareMarket)
        market.ak = _BenchmarkAk()
        result = _bounded_benchmarks(market, '2026-08-18', '2026-08-20')
        self.assertEqual(len(result), 3)
        hs300 = next(x for x in result if x['symbol'] == 'sh000300')
        zz500 = next(x for x in result if x['symbol'] == 'sh000905')
        zz1000 = next(x for x in result if x['symbol'] == 'sh000852')
        self.assertEqual(hs300['return_pct'], 2.0)
        self.assertIsNone(zz500['return_pct'])
        self.assertEqual(zz500['curve'], [])
        self.assertEqual(zz1000['return_pct'], 2.0)


if __name__ == '__main__':
    unittest.main()
