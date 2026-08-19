# Retail board-policy regression coverage: target generation + final BUY boundary.
import unittest

from engine.broker import execute_target_weights
from engine.pipeline import targets_for
import engine.morning_run  # noqa: F401 - import is a syntax/regression check
import engine.evening_split_run  # noqa: F401 - import is a syntax/regression check


class SplitExecutionTests(unittest.TestCase):
    def test_sell_only_uses_close_price_and_never_buys(self):
        state = {
            'fund_id': 'A',
            'cash': 0.0,
            'positions': {
                'sh.600000': {
                    'name': '测试股', 'qty': 1000, 'avg_cost': 10.0,
                    'acquired_date': '2026-08-18', 'last_price': 10.0,
                }
            },
        }
        bars = {
            'sh.600000': {'open': 10.0, 'close': 12.0, 'preclose': 11.5, 'tradestatus': '1'}
        }
        fills = execute_target_weights(
            state, [], bars, '2026-08-19',
            sides=('SELL',), price_field='close', note='morning',
        )
        self.assertEqual([x['side'] for x in fills], ['SELL'])
        self.assertEqual(fills[0]['execution_price_field'], 'close')
        self.assertEqual(state['positions'], {})
        self.assertGreater(state['cash'], 0)

    def test_buy_only_does_not_sell_existing_position(self):
        state = {
            'fund_id': 'A',
            'cash': 100000.0,
            'positions': {
                'sh.600000': {
                    'name': '旧持仓', 'qty': 1000, 'avg_cost': 10.0,
                    'acquired_date': '2026-08-18', 'last_price': 10.0,
                }
            },
        }
        bars = {
            'sh.600000': {'open': 10.0, 'close': 10.0, 'preclose': 10.0, 'tradestatus': '1'},
            'sh.600001': {'open': 20.0, 'close': 20.0, 'preclose': 20.0, 'tradestatus': '1'},
        }
        targets = [{'symbol': 'sh.600001', 'name': '新持仓', 'target_weight': 0.5}]
        fills = execute_target_weights(
            state, targets, bars, '2026-08-19',
            sides=('BUY',), price_field='close', note='close-buy',
        )
        self.assertTrue(fills)
        self.assertTrue(all(x['side'] == 'BUY' for x in fills))
        self.assertIn('sh.600000', state['positions'])
        self.assertIn('sh.600001', state['positions'])

    def test_target_layer_removes_chinext_and_star_before_strategy(self):
        candidates=[]
        for i in range(10):
            candidates.append({
                'symbol': f'sh.600{i:03d}', 'name': f'M{i}',
                'risk': 50, 'trend': 50, 'liquidity': 50,
                'momentum': 50, 'quality': 50, 'valuation': 50, 'score_d': 50,
            })
        for symbol in ('sz.300001', 'sz.301001', 'sh.688001', 'sh.689001'):
            candidates.append({
                'symbol': symbol, 'name': symbol,
                'risk': 100, 'trend': 100, 'liquidity': 100,
                'momentum': 100, 'quality': 100, 'valuation': 100, 'score_d': 100,
            })
        targets, _ = targets_for('A', candidates, 80.0, {'positions': {}}, use_ai=False)
        selected={x['symbol'] for x in targets}
        self.assertTrue(selected)
        self.assertTrue(all(x.startswith('sh.600') for x in selected))
        self.assertFalse(selected & {'sz.300001', 'sz.301001', 'sh.688001', 'sh.689001'})

    def test_retail_policy_hard_blocks_chinext_and_star_buys(self):
        state = {'fund_id': 'A', 'cash': 100000.0, 'positions': {}}
        bars = {
            'sz.300001': {'open': 10.0, 'close': 10.0, 'preclose': 10.0, 'tradestatus': '1'},
            'sz.301001': {'open': 10.0, 'close': 10.0, 'preclose': 10.0, 'tradestatus': '1'},
            'sh.688001': {'open': 10.0, 'close': 10.0, 'preclose': 10.0, 'tradestatus': '1'},
            'sh.689001': {'open': 10.0, 'close': 10.0, 'preclose': 10.0, 'tradestatus': '1'},
        }
        targets = [
            {'symbol': symbol, 'name': symbol, 'target_weight': 0.2}
            for symbol in bars
        ]
        fills = execute_target_weights(
            state, targets, bars, '2026-08-19',
            sides=('BUY',), price_field='close', note='retail-policy-test',
        )
        self.assertEqual(fills, [])
        self.assertEqual(state['positions'], {})
        self.assertEqual(state['cash'], 100000.0)


if __name__ == '__main__':
    unittest.main()
