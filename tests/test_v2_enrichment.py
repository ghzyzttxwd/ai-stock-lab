import unittest
from datetime import date, timedelta

from engine_v2.enrichment import _percentile, _technical_raw


def bars(n=70, start=100.0, drift=0.002, end_date=date(2026, 8, 14)):
    first = end_date - timedelta(days=n - 1)
    out = []
    price = start
    for i in range(n):
        price *= 1.0 + drift
        out.append({
            'date': (first + timedelta(days=i)).isoformat(),
            'open': price * 0.998,
            'high': price * 1.01,
            'low': price * 0.99,
            'close': price,
            'amount': 100_000_000 + i * 1_000_000,
        })
    return out


class V2EnrichmentTests(unittest.TestCase):
    def test_technical_features_end_exactly_on_trade_date(self):
        rows = bars()
        result = _technical_raw(rows, '2026-08-14')
        self.assertIsNotNone(result)
        self.assertEqual(result['date'], '2026-08-14')
        self.assertGreater(result['r20'], 0)
        self.assertGreater(result['amount20'], 100_000_000)

    def test_future_bar_is_ignored(self):
        rows = bars()
        rows.append({
            'date': '2026-08-15', 'open': 9999, 'high': 9999, 'low': 9999,
            'close': 9999, 'amount': 9_999_999_999,
        })
        with_future = _technical_raw(rows, '2026-08-14')
        without_future = _technical_raw(rows[:-1], '2026-08-14')
        self.assertEqual(with_future, without_future)

    def test_missing_trade_date_or_short_history_is_rejected(self):
        self.assertIsNone(_technical_raw(bars(n=60), '2026-08-14'))
        self.assertIsNone(_technical_raw(bars(end_date=date(2026, 8, 13)), '2026-08-14'))

    def test_inverse_percentile_rewards_lower_value(self):
        scored = _percentile({'a': 1.0, 'b': 2.0, 'c': 3.0}, higher_better=False)
        self.assertGreater(scored['a'], scored['b'])
        self.assertGreater(scored['b'], scored['c'])


if __name__ == '__main__':
    unittest.main()
