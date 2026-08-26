import json
import unittest
from pathlib import Path

from engine_v2.shadow_reporting import verify_audit_chain


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / 'shadow_state' / 'v2'


class V2PaperResetTests(unittest.TestCase):
    def test_all_v2_funds_exclude_retired_pre_reset_activity(self):
        heads = set()
        for fund_id in ('A', 'B', 'C', 'D', 'L'):
            state = json.loads((STATE_ROOT / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8'))
            self.assertEqual(float(state['initial_cash']), 1_000_000.0, fund_id)
            for fill in state.get('fills') or []:
                self.assertGreaterEqual(str(fill.get('trade_date') or fill.get('date') or '')[:10], '2026-08-26', fund_id)
            for point in state.get('equity_curve') or []:
                self.assertGreaterEqual(str(point.get('date') or '')[:10], '2026-08-26', fund_id)
            for decision in state.get('decisions') or []:
                self.assertGreaterEqual(str(decision.get('decision_date') or '')[:10], '2026-08-26', fund_id)
            pending = state.get('pending_decision')
            if pending:
                self.assertGreaterEqual(str(pending.get('decision_date') or '')[:10], '2026-08-26', fund_id)
            processed = str(state.get('last_processed_date') or '')[:10]
            if processed:
                self.assertGreaterEqual(processed, '2026-08-26', fund_id)
            heads.add(state.get('audit_head'))
        self.assertEqual(len(heads), 1)
        self.assertNotIn(None, heads)

    def test_active_chain_is_rooted_at_reset_and_contains_no_retired_activity(self):
        files = sorted(path.name for path in (STATE_ROOT / 'audit').glob('*.json'))
        self.assertIn('2026-08-26~paper-reset.json', files)
        self.assertFalse(any(name[:10] < '2026-08-26' for name in files))
        verification = verify_audit_chain(STATE_ROOT)
        self.assertEqual(verification['status'], 'PASS')
        self.assertGreaterEqual(verification['events'], 1)
        self.assertEqual(verification['first_date'], '2026-08-26~paper-reset')
        for path in (STATE_ROOT / 'audit').glob('*.json'):
            event = json.loads(path.read_text(encoding='utf-8'))
            if event.get('event_kind') == 'conditional_exit_scan':
                for fund in (event.get('funds') or {}).values():
                    self.assertEqual(((fund.get('execution') or {}).get('fills') or []), [])

    def test_summary_only_exposes_current_experiment(self):
        summary = json.loads((STATE_ROOT / 'summary.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(summary['updated_at'], '2026-08-26')
        self.assertEqual(summary['execution_model'], 'V2_CONDITIONAL_PLAN_V1')
        self.assertEqual(summary['plan_version'], 'v2-conditional-plan-v1')
        self.assertIn(summary.get('audit_event_kind'), {
            'paper_reset', 'conditional_exit_scan', 'conditional_entries_and_decision',
            'execution_catchup', 'buy_price_correction',
        })
        for fund in summary['funds'].values():
            self.assertEqual(fund['execution_model'], 'V2_CONDITIONAL_PLAN_V1')
            self.assertEqual(fund['plan_version'], 'v2-conditional-plan-v1')
            self.assertGreaterEqual(float((fund.get('metrics') or {}).get('cash') or 0.0), -1e-6)
            for fill in fund.get('recent_fills') or []:
                self.assertGreaterEqual(str(fill.get('trade_date') or fill.get('date') or '')[:10], '2026-08-26')
            for point in fund.get('equity_curve') or []:
                self.assertGreaterEqual(str(point.get('date') or '')[:10], '2026-08-26')
            pending = fund.get('pending_decision')
            if pending:
                self.assertGreaterEqual(str(pending.get('decision_date') or '')[:10], '2026-08-26')


if __name__ == '__main__':
    unittest.main()
