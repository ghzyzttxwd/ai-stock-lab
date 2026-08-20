import unittest

import pandas as pd

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


class _NoRawFakeAk(_FakeAk):
    def stock_zh_a_hist_tx(self, *, adjust, **kwargs):
        if adjust == '':
            raise AssertionError('unadjusted history should not be fetched when snapshot OHLC is complete')
        return super().stock_zh_a_hist_tx(adjust=adjust, **kwargs)


class CurrentBarHistoryBridgeTests(unittest.TestCase):
    def _market(self, ak):
        market = AKShareMarket.__new__(AKShareMarket)
        market.ak = ak
        market.history_limit = 120
        return market

    def test_stale_qfq_is_bridged_from_complete_current_snapshot_row(self):
        market = self._market(_NoRawFakeAk())
        selected = [{
            'code': 'sh.600000',
            'name': '浦发银行',
            'source': 'sina',
            'open': 10.1,
            'high': 10.6,
            'low': 10.0,
            'close': 10.5,
            'preclose': 10.0,
            'amount': 1_300_000,
            'turn': 0.8,
            'tradestatus': '1',
        }]

        histories = market.histories(selected, '2026-08-20')
        row = histories['sh.600000'][-1]

        self.assertEqual(row['date'], '2026-08-20')
        self.assertAlmostEqual(row['close'], 10.5)
        self.assertEqual(row['history_bridge_source'], 'sina')
        self.assertEqual(row['history_bridge_scale'], 1.0)

    def test_stale_qfq_uses_verified_unadjusted_current_bar_when_snapshot_has_no_ohlc(self):
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

        with self.assertRaisesRegex(RuntimeError, 'coverage too low after qfq bridge'):
            market.histories(selected, '2026-08-20')


if __name__ == '__main__':
    unittest.main()
