import unittest

from engine_v2.shadow_ledger import build_pending_decision


PLAN_VERSION = 'v2-conditional-plan-v1'


class V2PendingPlanPreservationTests(unittest.TestCase):
    def _payload(self, target):
        return {
            'trade_date': '2026-08-20',
            'plan_version': PLAN_VERSION,
            'target_version': 'v2-shadow-targets-conditional-0.3',
            'regime': {'label': 'neutral'},
            'stats': {'A': {'positions': 1}},
            'targets': {'A': [target]},
        }

    def _target(self):
        return {
            'symbol': 'sh.600919',
            'raw_code': '600919',
            'name': '江苏银行',
            'industry': '银行',
            'industry_code': '801780',
            'target_weight': 0.054796,
            'v2_score': 86.35,
            'thesis': 'fixture thesis',
            'invalidation': 'fixture invalidation',
            'fundamental_ready': True,
            'limit_status': 'normal',
            'opportunity_score': 81.23,
            'setup': 'pullback',
            'trade_plan': {
                'plan_version': PLAN_VERSION,
                'decision_date': '2026-08-20',
                'setup': 'pullback',
                'entry': {
                    'mode': 'pullback',
                    'operator': '<=',
                    'trigger_price': 10.1,
                    'valid_min': 9.8,
                    'valid_max': 10.1,
                },
                'exit': {
                    'hard_stop_pct': 0.03,
                    'reward_risk': 1.65,
                    'trailing_drawdown_pct': 0.02,
                    'max_hold_days': 4,
                },
                'cancel_if_not_triggered_by_close': True,
            },
        }

    def test_pending_target_keeps_full_conditional_plan_and_execution_fields(self):
        original = self._target()
        pending = build_pending_decision('A', self._payload(original), {'source': 'fixture'})
        target = pending['targets'][0]

        self.assertEqual(target['trade_plan']['plan_version'], PLAN_VERSION)
        self.assertEqual(target['trade_plan']['entry']['mode'], 'pullback')
        self.assertEqual(target['opportunity_score'], 81.23)
        self.assertEqual(target['setup'], 'pullback')

        original['trade_plan']['entry']['trigger_price'] = 999.0
        self.assertEqual(target['trade_plan']['entry']['trigger_price'], 10.1)

    def test_conditional_payload_fails_closed_if_target_plan_is_missing(self):
        target = self._target()
        target.pop('trade_plan')
        with self.assertRaisesRegex(RuntimeError, 'would drop or mismatch conditional trade plans'):
            build_pending_decision('A', self._payload(target), {'source': 'fixture'})

    def test_conditional_payload_fails_closed_if_target_plan_version_mismatches(self):
        target = self._target()
        target['trade_plan']['plan_version'] = 'legacy-fixed-open'
        with self.assertRaisesRegex(RuntimeError, 'would drop or mismatch conditional trade plans'):
            build_pending_decision('A', self._payload(target), {'source': 'fixture'})


if __name__ == '__main__':
    unittest.main()
