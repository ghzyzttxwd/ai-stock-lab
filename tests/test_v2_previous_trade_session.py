import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from engine_v2.shadow_run import previous_trade_session


class PreviousTradeSessionTests(unittest.TestCase):
    def test_prefers_exchange_calendar(self):
        fake_ak = SimpleNamespace(
            tool_trade_date_hist_sina=lambda: pd.DataFrame({
            'trade_date': ['2026-08-20', '2026-08-21', '2026-08-24'],
            }),
            stock_zh_index_daily_tx=lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError('index fallback should not run')
            ),
        )
        with patch.dict('sys.modules', {'akshare': fake_ak}):
            self.assertEqual(previous_trade_session('2026-08-24'), '2026-08-21')

    def test_falls_back_to_index_history(self):
        fake_ak = SimpleNamespace(
            tool_trade_date_hist_sina=lambda: (_ for _ in ()).throw(KeyError('calendar unavailable')),
            stock_zh_index_daily_tx=lambda **_kwargs: pd.DataFrame({
                'date': ['2026-08-20', '2026-08-21', '2026-08-24'],
            }),
        )
        with patch.dict('sys.modules', {'akshare': fake_ak}):
            self.assertEqual(previous_trade_session('2026-08-24'), '2026-08-21')


if __name__ == '__main__':
    unittest.main()
