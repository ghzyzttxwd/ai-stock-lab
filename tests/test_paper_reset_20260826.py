import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V1PaperResetTests(unittest.TestCase):
    def test_all_v1_funds_restart_clean_at_one_million(self):
        for fund_id in ('A', 'B', 'C', 'D', 'D_MAIN', 'L'):
            state = json.loads((ROOT / 'state' / f'{fund_id}.json').read_text(encoding='utf-8'))
            self.assertEqual(float(state['initial_cash']), 1_000_000.0, fund_id)
            self.assertEqual(float(state['cash']), 1_000_000.0, fund_id)
            self.assertEqual(state.get('positions') or {}, {}, fund_id)
            self.assertEqual(state.get('fills') or [], [], fund_id)
            self.assertEqual(state.get('rejected_orders') or [], [], fund_id)
            self.assertEqual(state.get('equity_curve') or [], [], fund_id)
            self.assertEqual(state.get('pending_targets') or [], [], fund_id)
            self.assertEqual(state.get('decisions') or [], [], fund_id)
            self.assertEqual(state.get('execution_model'), 'CONDITIONAL_PLAN_V1', fund_id)

    def test_reset_epoch_forbids_retroactive_fills(self):
        epoch = json.loads((ROOT / 'state' / 'paper_reset_epoch.json').read_text(encoding='utf-8'))
        self.assertEqual(epoch['reset_date'], '2026-08-26')
        self.assertEqual(float(epoch['initial_cash_per_fund']), 1_000_000.0)
        self.assertEqual(epoch['reselect_from_close'], '2026-08-26')
        self.assertTrue(epoch['retroactive_fills_forbidden'])


if __name__ == '__main__':
    unittest.main()
