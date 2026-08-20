import unittest

import pandas as pd

from engine.current_bar_history import _append_current_qfq
from engine.real_market import AKShareMarket


class _FakeAk:
    def __init__(self, *, raw_preclose=10.0):
        self.raw_preclose = raw_preclose
        self.unadjusted_calls = 0

    def stock_zh_a_hist_tx(self, *, adjust, **_kwargs):
        if adjust == 'qfq':
            return pd.DataFrame([
                {
                    'date': '2026-08-18',
                    'open': 9.7,
                    'high': 10.1,
                    'low': 9.6,
                    'close': 9.8,
                    'amount': 1_000_000,
                },
                {
                    'date': '2026-08-19',
                    'open': 9.9,
                    'high': 10.2,
                    'low': 9.8,
                    'close': 10.0,
                    'amount': 1_100_000,
                },
            ])
        if adjust == '':
            self.unadjusted_calls += 1
            return pd.DataFrame([
                {
                    'date': '2026-08-19',
                    'open': self.raw_preclose,
                    'high': self.raw_preclose,
                    'low': self.raw_preclose,
                    'close': self.raw_preclose,
                    'amount': 1_100_000,
                },
                {
                    'date': '2026-08-20',
                    'open': 10.1,
                    'high': 10.6,
                    'low': 10.0,
                    'close': 10.5,
                    'amount': 1_300_000,
                },
            ])
        raise AssertionError(f'unexpected adjust={adjust!r}')


class CurrentBarHistoryBridgeTests(unittest.TestCase):
    def _market(self, ak):
        market = AKShareMarket.__new__(AKShareMarket)
        market.ak = ak
        market.history_limit = 120
        return market

    def test_complete_spot_snapshot_cannot_bypass_exact_date_daily_bar(self):
        ak = _FakeAk()
        market = self._market(ak)
        selected = [{
            'code': 'sh.600000',
            'name': '浦发银行',
            'source': 'sina',
            # Deliberately bogus but structurally complete spot OHLC. If the bridge ever
            # trusts undated spot rows again, this test will expose the regression.
            'open': 99.1,
            'high': 99.6,
            'low': 99.0,
            'close': 99.5,
            'preclose': 99.0,
            'amount': 9_900_000,
            'turn': 0.8,
            'tradestatus': '1',
        }]

        histories = market.histories(selected, '2026-08-20')
        row = histories['sh.600000'][-1]

        self.assertGreaterEqual(ak.unadjusted_calls, 1)
        self.assertEqual(row['date'], '2026-08-20')
        self.assertAlmostEqual(row['open'], 10.1)
        self.assertAlmostEqual(row['close'], 10.5)
        self.assertEqual(row['history_bridge_source'], 'tencent-execution')
        self.assertEqual(row['history_bridge_bar_date'], '2026-08-20')
        self.assertEqual(row['history_bridge_date_evidence'], 'execution_bars_exact_date_match')

    def test_stale_qfq_uses_verified_unadjusted_current_bar(self):
        ak = _FakeAk()
        market = self._market(ak)
        selected = [{
            'code': 'sh.600000',
            'name': '浦发银行',
            'source': 'tencent-full',
            'open': 0.0,
            'high': 0.0,
            'low': 0.0,
            'close': 10.5,
            'preclose': 10.0,
            'amount': 1_300_000,
            'tradestatus': '1',
        }]

        histories = market.histories(selected, '2026-08-20')
        row = histories['sh.600000'][-1]

        self.assertGreaterEqual(ak.unadjusted_calls, 1)
        self.assertEqual(row['date'], '2026-08-20')
        self.assertAlmostEqual(row['open'], 10.1)
        self.assertAlmostEqual(row['close'], 10.5)
        self.assertEqual(row['history_bridge_source'], 'tencent-execution')
        self.assertEqual(row['history_bridge_bar_date'], '2026-08-20')

    def test_undated_bar_is_rejected(self):
        rows = [{
            'date': '2026-08-19',
            'code': 'sh.600000',
            'name': '浦发银行',
            'open': 9.9,
            'high': 10.2,
            'low': 9.8,
            'close': 10.0,
            'amount': 1_100_000,
        }]
        undated = {
            'code': 'sh.600000',
            'name': '浦发银行',
            'source': 'sina',
            'open': 10.1,
            'high': 10.6,
            'low': 10.0,
            'close': 10.5,
            'preclose': 10.0,
            'amount': 1_300_000,
        }
        self.assertIsNone(_append_current_qfq(rows, undated, '2026-08-20', 120))

    def test_basis_mismatch_is_rejected_and_coverage_fails_closed(self):
        ak = _FakeAk(raw_preclose=20.0)
        market = self._market(ak)
        selected = [{
            'code': 'sh.600000',
            'name': '浦发银行',
            'source': 'tencent-full',
            'open': 0.0,
            'high': 0.0,
            'low': 0.0,
            'close': 10.5,
            'preclose': 10.0,
            'amount': 1_300_000,
            'tradestatus': '1',
        }]

        with self.assertRaisesRegex(RuntimeError, 'coverage too low after dated qfq bridge'):
            market.histories(selected, '2026-08-20')


if __name__ == '__main__':
    unittest.main()
