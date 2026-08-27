import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / 'agent_state' / 'v3'
RESET_DATE = '2026-08-26'
FIRST_EXECUTION_DATE = '2026-08-27'


def _event_date(item: dict, *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value:
            return str(value)[:10]
    return None


class V3PaperResetTests(unittest.TestCase):
    def test_all_v3_funds_preserve_reset_epoch_boundary(self):
        for fund_id in ('A', 'B', 'C', 'D', 'L'):
            state = json.loads((STATE_ROOT / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8'))
            self.assertEqual(float(state['initial_cash']), 1_000_000.0, fund_id)
            self.assertGreaterEqual(float(state.get('cash') or 0.0), -0.01, fund_id)

            for fill in state.get('fills') or []:
                event_date = _event_date(fill, 'date', 'trade_date', 'execution_date', 'executed_on')
                self.assertIsNotNone(event_date, (fund_id, fill))
                self.assertGreaterEqual(event_date, FIRST_EXECUTION_DATE, (fund_id, fill))

            for rejection in state.get('rejected_orders') or []:
                event_date = _event_date(rejection, 'date', 'trade_date', 'execution_date', 'executed_on')
                if event_date is not None:
                    self.assertGreaterEqual(event_date, FIRST_EXECUTION_DATE, (fund_id, rejection))

            for point in state.get('equity_curve') or []:
                event_date = _event_date(point, 'date', 'trade_date')
                self.assertIsNotNone(event_date, (fund_id, point))
                self.assertGreaterEqual(event_date, RESET_DATE, (fund_id, point))

            for decision in state.get('decisions') or []:
                decision_date = _event_date(decision, 'decision_date', 'date')
                self.assertIsNotNone(decision_date, (fund_id, decision))
                self.assertGreaterEqual(decision_date, RESET_DATE, (fund_id, decision))

            processed = state.get('last_processed_date')
            if processed is not None:
                self.assertGreaterEqual(str(processed)[:10], FIRST_EXECUTION_DATE, fund_id)

    def test_no_pre_reset_decision_or_brief_is_active(self):
        for path in (STATE_ROOT / 'decisions').glob('*.json'):
            payload = json.loads(path.read_text(encoding='utf-8'))
            decision_date = str(payload.get('decision_date') or path.stem)[:10]
            self.assertGreaterEqual(decision_date, RESET_DATE, path.name)

        latest_brief = STATE_ROOT / 'latest_brief.json'
        if latest_brief.exists():
            brief = json.loads(latest_brief.read_text(encoding='utf-8'))
            self.assertGreaterEqual(str(brief.get('trade_date') or '')[:10], RESET_DATE)
            self.assertGreaterEqual(str(brief.get('next_trade_date') or '')[:10], FIRST_EXECUTION_DATE)

        verification = STATE_ROOT / 'public_verification.json'
        if verification.exists():
            payload = json.loads(verification.read_text(encoding='utf-8'))
            evidence_date = payload.get('trade_date') or payload.get('settlement_date') or payload.get('verified_trade_date')
            if evidence_date:
                self.assertGreaterEqual(str(evidence_date)[:10], RESET_DATE)

    def test_reset_epoch_starts_new_selection_at_aug26_close(self):
        epoch = json.loads((STATE_ROOT / 'reset_epoch.json').read_text(encoding='utf-8'))
        self.assertEqual(epoch['reset_date'], RESET_DATE)
        self.assertEqual(epoch['ignore_decisions_before'], RESET_DATE)
        self.assertEqual(epoch['first_new_decision_date'], RESET_DATE)
        self.assertEqual(epoch['first_possible_execution_date'], FIRST_EXECUTION_DATE)
        self.assertTrue(epoch['retroactive_fills_forbidden'])


if __name__ == '__main__':
    unittest.main()
