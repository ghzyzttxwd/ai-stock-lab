import unittest
from unittest.mock import patch

import pandas as pd

from engine.tencent_full_market import fetch_tencent_full_rows, _overlay_execution_bars


class TencentFullMarketTests(unittest.TestCase):
    def test_full_market_parser_keeps_broad_mainboard_and_no_fake_open(self):
        rows = []
        for i in range(1501):
            # Produce valid Shanghai main-board codes without relying on a live provider.
            code = f'sh{600000 + i:06d}'
            rows.append({
                'code': code,
                'name': f'测试{i}',
                'zxj': '10.00',
                'zdf': '1.00',
                'turnover': '5000',  # Tencent unit is 10k yuan => 50m yuan
                'pe_ttm': '12.5',
                'hsl': '2.0',
                'zdf_d60': '15.0',
            })

        class FakeAk:
            def stock_zh_a_spot_tx(self):
                return pd.DataFrame(rows)

        parsed = fetch_tencent_full_rows(FakeAk())
        self.assertEqual(len(parsed), 1501)
        first = parsed[0]
        self.assertEqual(first['source'], 'tencent-full')
        self.assertEqual(first['open'], 0.0)
        self.assertEqual(first['high'], 0.0)
        self.assertEqual(first['low'], 0.0)
        self.assertEqual(first['amount'], 50_000_000.0)
        self.assertEqual(first['peTTM'], 12.5)
        self.assertAlmostEqual(first['r60_snapshot'], 0.15)

    def test_execution_overlay_uses_real_ohlc_but_preserves_cross_section_fields(self):
        base = [{
            'code': 'sh.600519',
            'raw_code': '600519',
            'name': '贵州茅台',
            'source': 'tencent-full',
            'open': 0.0,
            'high': 0.0,
            'low': 0.0,
            'close': 1293.09,
            'preclose': 1341.99,
            'amount': 10_114_850_000.0,
            'turn': 0.63,
            'pctChg': -3.64,
            'peTTM': 19.85,
            'pbMRQ': 0.0,
            'r60_snapshot': 0.0245,
            'tradestatus': '1',
            'isST': '0',
        }]
        execution = {
            'sh.600519': {
                'code': 'sh.600519',
                'name': '贵州茅台',
                'source': 'tencent-execution',
                'open': 1301.0,
                'high': 1310.0,
                'low': 1288.0,
                'close': 1293.09,
                'preclose': 1341.99,
                'amount': 10_200_000_000.0,
                'tradestatus': '1',
            }
        }
        merged = _overlay_execution_bars(base, execution)
        self.assertEqual(len(merged), 1)
        row = merged[0]
        self.assertEqual(row['open'], 1301.0)
        self.assertEqual(row['high'], 1310.0)
        self.assertEqual(row['low'], 1288.0)
        self.assertEqual(row['source'], 'tencent-full')
        self.assertEqual(row['peTTM'], 19.85)
        self.assertEqual(row['r60_snapshot'], 0.0245)
        self.assertEqual(row['amount'], 10_200_000_000.0)


if __name__ == '__main__':
    unittest.main()
