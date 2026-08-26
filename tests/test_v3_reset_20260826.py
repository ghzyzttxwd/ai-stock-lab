import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / 'agent_state' / 'v3'


class V3PaperResetTests(unittest.TestCase):
    def test_all_v3_funds_restart_clean_at_one_million(self):
        for fund_id in ('A', 'B', 'C', 'D', 'L'):
            state = json.loads((STATE_ROOT / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8'))
            self.assertEqual(float(state['initial_cash']), 1_000_000.0, fund_id)
            self.assertEqual(float(state['cash']), 1_000_000.0, fund_id)
            self.assertEqual(state.get('positions') or {}, {}, fund_id)
            self.assertEqual(state.get('fills') or [], [], fund_id)
            self.assertEqual(state.get('rejected_orders') or [], [], fund_id)
            self.assertEqual(state.get('equity_curve') or [], [], fund_id)
            self.assertEqual(state.get('decisions') or [], [], fund_id)
            self.assertEqual(state.get('executed_decision_ids') or [], [], fund_id)
            self.assertIsNone(state.get('last_processed_date'), fund_id)
            self.assertIsNone(state.get('audit_head'), fund_id)

    def test_no_pre_reset_decision_is_active(self):
        active = sorted(path.name for path in (STATE_ROOT / 'decisions').glob('*.json'))
        self.assertEqual(active, [])
        self.assertFalse((STATE_ROOT / 'latest_brief.json').exists())
        self.assertFalse((STATE_ROOT / 'public_verification.json').exists())

    def test_reset_epoch_starts_new_selection_at_aug26_close(self):
        epoch = json.loads((STATE_ROOT / 'reset_epoch.json').read_text(encoding='utf-8'))
        self.assertEqual(epoch['reset_date'], '2026-08-26')
        self.assertEqual(epoch['ignore_decisions_before'], '2026-08-26')
        self.assertEqual(epoch['first_new_decision_date'], '2026-08-26')
        self.assertEqual(epoch['first_possible_execution_date'], '2026-08-27')
        self.assertTrue(epoch['retroactive_fills_forbidden'])


if __name__ == '__main__':
    unittest.main()
