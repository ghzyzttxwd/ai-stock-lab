import unittest
from engine.universe import is_main_board
from engine.risk import clamp_d_targets
from engine.broker import round_lot, fee_for, _locked_at_limit
from engine.daily_run import _previous_trade_date, _pending_is_fresh

class EngineTests(unittest.TestCase):
    def test_board_filter(self):
        self.assertTrue(is_main_board('sh.600519'))
        self.assertTrue(is_main_board('sh.603000'))
        self.assertTrue(is_main_board('sz.000001'))
        self.assertTrue(is_main_board('sz.002594'))
        self.assertFalse(is_main_board('sh.688001'))
        self.assertFalse(is_main_board('sz.300750'))
        self.assertFalse(is_main_board('bj.920001'))

    def test_lot_rounding(self):
        self.assertEqual(round_lot(999), 900)
        self.assertEqual(round_lot(99), 0)

    def test_d_risk_cap(self):
        targets=[{'symbol':f'sh.600{i:03d}','name':'x','target_weight':.30} for i in range(12)]
        clean,notes=clamp_d_targets(targets)
        self.assertLessEqual(len(clean),10)
        self.assertLessEqual(max(x['target_weight'] for x in clean),.15)
        self.assertLessEqual(sum(x['target_weight'] for x in clean),.90+1e-9)
        self.assertTrue(notes)

    def test_limit_lock(self):
        self.assertTrue(_locked_at_limit('BUY', {'open':11.0,'preclose':10.0}))
        self.assertTrue(_locked_at_limit('SELL', {'open':9.0,'preclose':10.0}))
        self.assertFalse(_locked_at_limit('BUY', {'open':10.5,'preclose':10.0}))

    def test_min_commission(self):
        self.assertEqual(fee_for('BUY',1000),5.0)
        self.assertGreater(fee_for('SELL',100000), fee_for('BUY',100000))

    def test_previous_trade_date(self):
        histories={
            'sh.600000':[
                {'date':'2026-08-12'},
                {'date':'2026-08-13'},
                {'date':'2026-08-14'},
            ]
        }
        self.assertEqual(_previous_trade_date(histories,'2026-08-14'),'2026-08-13')

    def test_pending_must_be_from_immediately_previous_trade_date(self):
        state={
            'pending_targets':[{'symbol':'sh.600000','target_weight':0.1}],
            'pending_decision_date':'2026-08-13',
            'decisions':[],
        }
        self.assertTrue(_pending_is_fresh(state,'2026-08-13'))
        self.assertFalse(_pending_is_fresh(state,'2026-08-14'))

if __name__=='__main__': unittest.main()
