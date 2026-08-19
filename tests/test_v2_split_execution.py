import unittest

from engine_v2.split_execution import combine_phase_executions, execute_pending_side
from engine_v2.morning_sell_run import SCHEDULED_MORNING_TIME, _execution_snapshot
from engine_v2.shadow_reporting import _summary_source_ref
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

    def test_morning_snapshot_distinguishes_schedule_from_actual_execution_time(self):
        state = {
            'cash': 1000.0,
            'positions': {
                'sh.600000': {
                    'qty': 100,
                    'avg_cost': 10.0,
                    'last_price': 10.0,
                }
            },
        }
        bars = {'sh.600000': {'close': 10.5}}
        executed_at = '2026-08-19T10:32:43+08:00'
        snapshot = _execution_snapshot(state, bars, '2026-08-19', 3.5, executed_at)
        self.assertEqual(SCHEDULED_MORNING_TIME, '09:40')
        self.assertEqual(snapshot['phase'], 'morning_sell')
        self.assertEqual(snapshot['scheduled_time'], '09:40')
        self.assertEqual(snapshot['executed_at'], executed_at)
        self.assertNotEqual(snapshot['executed_at'][11:16], snapshot['scheduled_time'])

    def test_legacy_morning_audit_never_claims_scheduled_time_is_actual(self):
        legacy = {
            'event_kind': 'morning_sell',
            'source_ref': {
                'execution_bar_source': 'sina-intraday',
                'note': 'Previous-session SELL/reduce intents executed at the 09:40 live quote.',
            },
        }
        source_ref = _summary_source_ref(legacy)
        self.assertEqual(source_ref['scheduled_time'], '09:40')
        self.assertIsNone(source_ref['executed_at'])
        self.assertEqual(source_ref['timing_status'], 'LEGACY_ACTUAL_TIME_UNRECORDED')
        self.assertIn('not a verified actual execution time', source_ref['note'])
        self.assertIn('09:40 live quote', source_ref['legacy_note'])

    def test_1510_buy_uses_session_open_and_does_not_sell_old_position(self):
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
            'sh.600000': {'open': 10.0, 'close': 11.0, 'preclose': 10.0, 'tradestatus': '1'},
            'sh.600001': {'open': 19.0, 'close': 20.0, 'preclose': 18.5, 'tradestatus': '1'},
        }
        result = execute_pending_side(
            state, pending, bars, '2026-08-19',
            side='BUY', price_field='open',
        )
        self.assertTrue(result['fills'])
        self.assertTrue(all(x['side'] == 'BUY' for x in result['fills']))
        self.assertTrue(all(x['execution_price_field'] == 'open' for x in result['fills']))
        self.assertEqual(result['fills'][0]['reference_price'], 19.0)
        self.assertIn('sh.600000', state['positions'])
        self.assertIn('sh.600001', state['positions'])

    def test_open_buy_hard_blocks_chinext_and_star_even_if_pending_is_injected(self):
        state = {
            'fund_id': 'A',
            'cash': 100000.0,
            'positions': {},
            'fills': [], 'rejected_orders': [],
        }
        symbols = ('sz.300001', 'sz.301001', 'sh.688001', 'sh.689001')
        pending = {
            'decision_date': '2026-08-18',
            'targets': [
                {'symbol': symbol, 'name': symbol, 'target_weight': 0.2}
                for symbol in symbols
            ],
        }
        bars = {
            symbol: {'open': 10.0, 'close': 11.0, 'preclose': 10.0, 'tradestatus': '1'}
            for symbol in symbols
        }
        result = execute_pending_side(
            state, pending, bars, '2026-08-19',
            side='BUY', price_field='open',
        )
        self.assertEqual(result['fills'], [])
        self.assertEqual(state['positions'], {})
        self.assertEqual(state['cash'], 100000.0)
        blocked = [
            x for x in result['policy_adjustments']
            if x.get('reason') == 'retail_mainboard_only'
        ]
        self.assertEqual({x['symbol'] for x in blocked}, set(symbols))
        self.assertTrue(all(x['applied_weight'] == 0.0 for x in blocked))

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
