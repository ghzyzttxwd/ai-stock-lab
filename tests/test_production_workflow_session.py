from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'production.yml'


class ProductionWorkflowSessionTests(unittest.TestCase):
    def test_recovery_acceptance_uses_one_exchange_session_date(self):
        text = WORKFLOW.read_text(encoding='utf-8')

        resolve_start = text.index('- name: Resolve target exchange session for this run')
        guard_start = text.index('- name: Skip redundant retry if target session already has a conditional plan')
        resolve_block = text[resolve_start:guard_start]

        self.assertIn('id: marketday', resolve_block)
        self.assertIn('python -m engine.schedule_guard production', resolve_block)
        self.assertNotIn("github.event_name == 'schedule'", resolve_block)

        session_binding = 'SETTLEMENT_DATE: ${{ steps.marketday.outputs.latest_trade_date }}'
        self.assertGreaterEqual(text.count(session_binding), 4)
        self.assertIn("target = os.environ['SETTLEMENT_DATE']", text)
        self.assertIn('python -m engine.v1_production_gate --date "$SETTLEMENT_DATE"', text)
        self.assertIn('python -m engine.v1_freshness --date "$SETTLEMENT_DATE"', text)
        self.assertIn("expected_date=os.environ['SETTLEMENT_DATE']", text)

        # Weekend/holiday recovery must never compare a resolved Friday settlement to the
        # Saturday/Sunday wall-clock date at any acceptance layer.
        self.assertNotIn('TZ=Asia/Shanghai date +%F', text)
        self.assertNotIn("expected_date=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()", text)


if __name__ == '__main__':
    unittest.main()
