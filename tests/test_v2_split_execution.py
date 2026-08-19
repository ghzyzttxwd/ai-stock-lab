import unittest

from engine_v2.split_execution import combine_phase_executions, execute_pending_side
import engine_v2.morning_sell_run  # noqa: F401 - import catches syntax/regression errors
import engine_v2.shadow_run_split  # noqa: F401


class V2SplitExecutionTests(unittest.TestCase):
    def test_morning_sell_only_uses_live_close_field(self):
        state = {
            'fund_id': 'A',
            'cash': 0.0,
            'positions': {
                'sh.600000': {
                    'name': '测试股', 'qty': 1000, 'avg_cost': 10.0,
                    'acquired_date': '2026-08-18', 'last_price': 10.0,
                }
            },
            'fills': [], 'rejected_orders': [],
        }
        pending = {'decision_date': '2026-08-18', 'targets': []}
        bars = {'sh.600000': {'open': 10.0, 'close': 12.0, 'preclose': 11.5, 'tradestatus': '1'}}
        result = execute_pending_side(
            state, pending, bars, '2026-08-19',
            side='SELL', price_field='close',
        )
        self.assertEqual(result['phase'], 'sell')
        self.assertEqual([x['side'] for x in result['fills']], ['SELL'])
        self.assertEqual(result['fills'][0]['execution_price_field'], 'close')
        self.assertEqual(state['positions'], {})
        self.assertGreater(state['cash'], 0)

    def test_close_buy_only_does_not_sell_old_position(self):
        state = {
            'fund_id': 'A',
            'cash': 100000.0,
            'positions': {
                'sh.600000': {
                    'name': '旧持仓', 'qty': 1000, 'avg_cost': 10.0,
                    'acquired_date': '2026-08-18', 'last_price': 10.0,
                }
            },
            'fills': [], 'rejected_orders': [],
        }
        pending = {
            'decision_date': '2026-08-18',
            'targets': [{'symbol': 'sh.600001', 'name': '新持仓', 'target_weight': 0.5}],
        }
        bars = {
            'sh.600000': {'open': 10.0, 'close': 10.0, 'preclose': 10.0, 'tradestatus': '1'},
            'sh.600001': {'open': 20.0, 'close': 20.0, 'preclose': 20.0, 'tradestatus': '1'},
        }
        result = execute_pending_side(
            state, pending, bars, '2026-08-19',
            side='BUY', price_field='close',
        )
        self.assertTrue(result['fills'])
        self.assertTrue(all(x['side'] == 'BUY' for x in result['fills']))
        self.assertIn('sh.600000', state['positions'])
        self.assertIn('sh.600001', state['positions'])

    def test_phase_combiner_keeps_both_sides_and_fees(self):
        combined = combine_phase_executions(
            {'phase': 'sell', 'decision_date': '2026-08-18', 'trade_date': '2026-08-19',
             'fills': [{'side': 'SELL'}], 'rejected_orders': [], 'policy_adjustments': [],
             'valuation_fallback_symbols': [], 'fees': 5.0},
            {'phase': 'buy', 'decision_date': '2026-08-18', 'trade_date': '2026-08-19',
             'fills': [{'side': 'BUY'}], 'rejected_orders': [], 'policy_adjustments': [],
             'valuation_fallback_symbols': [], 'fees': 6.0},
        )
        self.assertEqual(combined['phases'], ['sell', 'buy'])
        self.assertEqual([x['side'] for x in combined['fills']], ['SELL', 'BUY'])
        self.assertEqual(combined['fees'], 11.0)


if __name__ == '__main__':
    unittest.main()
