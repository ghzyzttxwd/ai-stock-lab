import unittest

import pandas as pd

from engine_v2.trade_price_time import first_trigger_minute


class V2TradePriceTimeTests(unittest.TestCase):
    def test_breakout_uses_first_minute_that_touches_trigger(self):
        frame = pd.DataFrame([
            {'时间':'2026-08-21 09:44:00','最高':159.8,'最低':159.1},
            {'时间':'2026-08-21 09:45:00','最高':160.2,'最低':159.7},
            {'时间':'2026-08-21 09:46:00','最高':161.0,'最低':160.0},
        ])
        plan = {'entry':{'mode':'breakout','trigger_price':160.0,'valid_min':160.0,'valid_max':162.0}}
        self.assertEqual(first_trigger_minute(frame, plan, 'intraday_breakout_triggered'), '09:45')

    def test_open_based_fill_is_0930_without_guessing(self):
        frame = pd.DataFrame()
        plan = {'entry':{'mode':'pullback','trigger_price':10.0,'valid_min':9.5,'valid_max':10.0}}
        self.assertEqual(first_trigger_minute(frame, plan, 'open_inside_pullback_zone'), '09:30')


if __name__ == '__main__':
    unittest.main()
