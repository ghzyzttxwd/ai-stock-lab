import unittest

from engine.strategies import strategy_b, strategy_c
from engine.trading_plan import build_conditional_targets, calibrate_opportunity_scores


class V1StrategyDisciplineTests(unittest.TestCase):
    def candidate(self, symbol, bias=0.0):
        return {
            'symbol': symbol, 'name': symbol,
            'market_relative_1': 0.01 + bias,
            'market_relative_3': 0.02 + bias,
            'market_relative_5': 0.03 + bias,
            'close_position': 0.72, 'amount_ratio_3_20': 1.2,
            'risk': 70.0, 'overheat_score': 25.0, 'r1': 0.01,
            'momentum': 75.0 + bias * 100, 'trend': 70.0,
            'liquidity': 80.0, 'quality': 70.0, 'valuation': 60.0,
            'score_d': 70.0, 'close': 10.0, 'atr14_pct': 0.03,
            'ma5': 9.9, 'recent_high_3': 10.05,
        }

    def test_calibration_breaks_saturated_scores(self):
        rows = [self.candidate(f'sh.600{i:03d}', i * 0.001) for i in range(10)]
        calibrate_opportunity_scores(rows)
        scores = [row['opportunity_score'] for row in rows]
        self.assertEqual(len(scores), len(set(scores)))
        self.assertLess(max(scores), 100.0)
        self.assertGreater(max(scores), 90.0)
        self.assertTrue(all('opportunity_score_raw' in row for row in rows))

    def test_weak_market_keeps_b_and_c_in_cash(self):
        row = self.candidate('sh.600001')
        row['opportunity_score'] = 99.0
        self.assertEqual(strategy_b([row], 40.0), [])
        self.assertEqual(strategy_c([row], 40.0), [])

    def test_overheated_or_large_jump_is_not_chased(self):
        row = self.candidate('sh.600001')
        row['opportunity_score'] = 99.0
        row['r1'] = 0.06
        self.assertEqual(strategy_b([row], 80.0), [])
        row['r1'] = 0.01
        row['overheat_score'] = 70.0
        self.assertEqual(strategy_b([row], 80.0), [])

    def test_stopped_name_waits_two_later_decision_sessions(self):
        symbol = 'sh.600001'
        row = self.candidate(symbol)
        row['opportunity_score'] = 99.0
        target = {'symbol': symbol, 'name': symbol, 'target_weight': 0.10}
        state = {
            'positions': {},
            'fills': [{'symbol': symbol, 'side': 'SELL', 'trade_date': '2026-08-25', 'exit_reason': 'hard_stop'}],
            'decisions': [{'date': '2026-08-26'}],
        }
        self.assertEqual(build_conditional_targets('B', [target], [row], '2026-08-26', state=state), [])
        state['decisions'].append({'date': '2026-08-27'})
        allowed = build_conditional_targets('B', [target], [row], '2026-08-28', state=state)
        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]['trade_plan']['strategy_revision'], 'v1-discipline-20260826')

    def test_breakout_chase_band_is_tight(self):
        row = self.candidate('sh.600001')
        row['opportunity_score'] = 99.0
        target = {'symbol': row['symbol'], 'name': row['name'], 'target_weight': 0.10}
        result = build_conditional_targets('B', [target], [row], '2026-08-28', state={'positions': {}, 'fills': [], 'decisions': []})
        self.assertEqual(len(result), 1)
        entry = result[0]['trade_plan']['entry']
        self.assertEqual(entry['mode'], 'breakout')
        self.assertLessEqual(entry['valid_max'] / entry['trigger_price'] - 1.0, 0.0121)


if __name__ == '__main__':
    unittest.main()
