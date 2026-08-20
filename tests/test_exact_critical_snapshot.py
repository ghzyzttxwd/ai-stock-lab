import unittest

from engine.exact_critical_snapshot import overlay_exact_critical_rows


class ExactCriticalSnapshotTests(unittest.TestCase):
    def test_spot_ohlc_is_replaced_by_exact_daily_bar(self):
        rows = [{
            'code': 'sh.600000',
            'name': '浦发银行',
            'source': 'sina',
            'open': 99.0,
            'high': 100.0,
            'low': 98.0,
            'close': 99.5,
            'preclose': 98.5,
            'amount': 9_900_000,
            'tradestatus': '1',
        }]
        exact = {
            'sh.600000': {
                'code': 'sh.600000',
                'name': '浦发银行',
                'source': 'tencent-execution',
                'open': 10.1,
                'high': 10.6,
                'low': 10.0,
                'close': 10.5,
                'preclose': 10.0,
                'amount': 1_300_000,
                'tradestatus': '1',
            }
        }
        out = overlay_exact_critical_rows(
            rows,
            exact,
            '2026-08-20',
            {'sh.600000': '浦发银行'},
        )
        row = next(x for x in out if x['code'] == 'sh.600000')
        self.assertEqual(row['source'], 'sina')
        self.assertAlmostEqual(row['open'], 10.1)
        self.assertAlmostEqual(row['close'], 10.5)
        self.assertEqual(row['exact_bar_date'], '2026-08-20')
        self.assertEqual(row['exact_bar_source'], 'tencent-execution')
        self.assertEqual(row['exact_bar_date_evidence'], 'execution_bars_exact_date_match')

    def test_missing_critical_exact_bar_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'coverage incomplete'):
            overlay_exact_critical_rows(
                [],
                {},
                '2026-08-20',
                {'sh.600000': '浦发银行'},
            )

    def test_exact_bar_appends_critical_symbol_missing_from_spot_universe(self):
        exact = {
            'sz.000001': {
                'code': 'sz.000001',
                'raw_code': '000001',
                'name': '平安银行',
                'source': 'tencent-execution',
                'open': 12.0,
                'high': 12.4,
                'low': 11.9,
                'close': 12.3,
                'preclose': 12.1,
                'amount': 2_000_000,
                'tradestatus': '1',
            }
        }
        out = overlay_exact_critical_rows(
            [],
            exact,
            '2026-08-20',
            {'sz.000001': '平安银行'},
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['code'], 'sz.000001')
        self.assertEqual(out[0]['exact_bar_date'], '2026-08-20')


if __name__ == '__main__':
    unittest.main()
