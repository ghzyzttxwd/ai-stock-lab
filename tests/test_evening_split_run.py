from __future__ import annotations

import unittest

from engine.evening_split_run import _session_bars


class _FakeMarket:
    def __init__(self, bars: dict[str, dict]):
        self._bars = bars
        self.execution_requests: list[tuple[dict[str, str], str]] = []

    def snapshot(self):
        raise AssertionError('settlement must not call full-market snapshot')

    def execution_bars(self, symbols: dict[str, str], trade_date: str):
        self.execution_requests.append((dict(symbols), trade_date))
        return {symbol: dict(self._bars[symbol]) for symbol in symbols if symbol in self._bars}


class EveningSplitRunSettlementBarsTests(unittest.TestCase):
    def test_uses_exact_date_execution_bars_without_full_market_snapshot(self):
        critical = {'sh.600000': '浦发银行', 'sz.000001': '平安银行'}
        market = _FakeMarket(
            {
                'sh.600000': {'code': 'sh.600000', 'date': '2026-08-24', 'high': 12.3, 'low': 11.7},
                'sz.000001': {'code': 'sz.000001', 'date': '2026-08-24', 'high': 14.2, 'low': 13.8},
            }
        )

        bars = _session_bars(market, critical, '2026-08-24')

        self.assertEqual(set(bars), set(critical))
        self.assertEqual(market.execution_requests, [(critical, '2026-08-24')])

    def test_fails_closed_when_any_critical_exact_date_bar_is_missing(self):
        critical = {'sh.600000': '浦发银行', 'sz.000001': '平安银行'}
        market = _FakeMarket(
            {'sh.600000': {'code': 'sh.600000', 'date': '2026-08-24', 'high': 12.3, 'low': 11.7}}
        )

        with self.assertRaisesRegex(RuntimeError, 'missing 1/2 critical symbols: sz.000001'):
            _session_bars(market, critical, '2026-08-24')


if __name__ == '__main__':
    unittest.main()
