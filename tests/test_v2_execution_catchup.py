import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine_v2.conditional_plan import EXECUTION_MODEL, PLAN_VERSION
from engine_v2.execution_catchup import run_execution_catchup
from engine_v2.shadow_ledger import FUND_NAMES, new_ledger, save_ledger


class V2ExecutionCatchupSafetyTests(unittest.TestCase):
    def _conditional_root(self, directory: str) -> Path:
        root = Path(directory) / 'shadow_state' / 'v2'
        for fund_id in FUND_NAMES:
            state = new_ledger(fund_id, '2026-08-19')
            state['execution_model'] = EXECUTION_MODEL
            state['plan_version'] = PLAN_VERSION
            state['pending_decision'] = {
                'decision_date': '2026-08-19',
                'execution_model': EXECUTION_MODEL,
                'plan_version': PLAN_VERSION,
                'targets': [],
            }
            save_ledger(root / 'ledgers' / f'{fund_id}.json', state)
        return root

    def test_conditional_state_refuses_legacy_catchup_before_market_call_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._conditional_root(directory)
            before = {
                fund_id: (root / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8')
                for fund_id in FUND_NAMES
            }
            with patch('engine.real_market.AKShareMarket.execution_bars') as market_call:
                with self.assertRaisesRegex(RuntimeError, 'legacy V2 execution catch-up is forbidden'):
                    run_execution_catchup('2026-08-20', root)
                market_call.assert_not_called()

            self.assertFalse((root / 'audit' / '2026-08-20-execution.json').exists())
            for fund_id in FUND_NAMES:
                self.assertEqual(
                    before[fund_id],
                    (root / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8'),
                )


if __name__ == '__main__':
    unittest.main()
