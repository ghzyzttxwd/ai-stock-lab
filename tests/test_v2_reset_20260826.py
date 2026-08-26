import json
import unittest
from pathlib import Path

from engine_v2.shadow_reporting import verify_audit_chain


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / 'shadow_state' / 'v2'


class V2PaperResetTests(unittest.TestCase):
    def test_all_v2_funds_restart_clean_at_one_million(self):
        heads = set()
        for fund_id in ('A', 'B', 'C', 'D', 'L'):
            state = json.loads((STATE_ROOT / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8'))
            self.assertEqual(float(state['initial_cash']), 1_000_000.0, fund_id)
            self.assertEqual(float(state['cash']), 1_000_000.0, fund_id)
            self.assertEqual(state.get('positions') or {}, {}, fund_id)
            self.assertEqual(state.get('fills') or [], [], fund_id)
            self.assertEqual(state.get('rejected_orders') or [], [], fund_id)
            self.assertEqual(state.get('equity_curve') or [], [], fund_id)
            self.assertIsNone(state.get('pending_decision'), fund_id)
            self.assertEqual(state.get('decisions') or [], [], fund_id)
            heads.add(state.get('audit_head'))
        self.assertEqual(len(heads), 1)
        self.assertNotIn(None, heads)

    def test_reset_audit_is_the_only_active_chain(self):
        files = sorted(path.name for path in (STATE_ROOT / 'audit').glob('*.json'))
        self.assertEqual(files, ['2026-08-26~paper-reset.json'])
        verification = verify_audit_chain(STATE_ROOT)
        self.assertEqual(verification['status'], 'PASS')
        self.assertEqual(verification['events'], 1)

    def test_summary_is_zeroed(self):
        summary = json.loads((STATE_ROOT / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['updated_at'], '2026-08-26')
        for fund in summary['funds'].values():
            metrics = fund['metrics']
            self.assertEqual(float(metrics['equity']), 1_000_000.0)
            self.assertEqual(float(metrics['cash']), 1_000_000.0)
            self.assertEqual(float(metrics['return_pct']), 0.0)
            self.assertEqual(metrics['fills'], 0)
            self.assertEqual(fund['holdings'], [])
            self.assertIsNone(fund['pending_decision'])


if __name__ == '__main__':
    unittest.main()
