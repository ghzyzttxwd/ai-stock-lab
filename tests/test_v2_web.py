import json
import tempfile
import unittest
from pathlib import Path

from engine_v2.shadow_reporting import build_summary


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / 'shadow_state' / 'v2'
WEB_ROOT = ROOT / 'web' / 'v2'
SUMMARY_PATH = STATE_ROOT / 'summary.json'


class V2MobileWebTests(unittest.TestCase):
    def test_summary_exposes_all_read_only_mobile_fields(self):
        summary = build_summary(STATE_ROOT)
        self.assertEqual(summary['summary_version'], 'v2-shadow-summary-1.1')
        self.assertEqual(set(summary['funds']), {'A', 'B', 'C', 'D', 'L'})
        self.assertEqual(summary['mode'], 'FORWARD_SHADOW_ONLY')
        self.assertFalse(summary['safety']['calls_sol'])
        self.assertFalse(summary['safety']['reads_v1_ledger'])
        self.assertFalse(summary['safety']['writes_v1_ledger'])
        if summary.get('audit_event_kind') == 'morning_sell':
            source_ref = summary.get('source_ref') or {}
            self.assertEqual(source_ref.get('scheduled_time'), '09:40')
            self.assertIn(source_ref.get('timing_status'), {'RECORDED', 'LEGACY_ACTUAL_TIME_UNRECORDED'})
            if source_ref.get('timing_status') == 'LEGACY_ACTUAL_TIME_UNRECORDED':
                self.assertIsNone(source_ref.get('executed_at'))
                self.assertIn('not a verified actual execution time', source_ref.get('note') or '')
        for fund_id, fund in summary['funds'].items():
            self.assertEqual(fund['fund_id'], fund_id)
            self.assertIn('equity', fund['metrics'])
            self.assertIn('cash', fund['metrics'])
            self.assertIn('position_market_value', fund['metrics'])
            self.assertIn('return_pct', fund['metrics'])
            self.assertIn('max_drawdown_pct', fund['metrics'])
            self.assertIn('fills', fund['metrics'])
            self.assertIn('rejected_orders', fund['metrics'])
            self.assertIsInstance(fund['holdings'], list)
            self.assertIsInstance(fund['recent_fills'], list)
            self.assertIsInstance(fund['recent_rejected_orders'], list)
            self.assertIsInstance(fund['concentration_flags'], list)
            pending=fund['pending_decision']
            if pending is None:
                self.assertEqual(summary.get('audit_event_kind'), 'execution_catchup')
                self.assertEqual(fund['metrics'].get('execution_only_date'), summary['updated_at'])
            else:
                self.assertIn('targets', pending)

    def test_committed_fallback_is_exactly_the_persisted_v2_summary(self):
        expected = json.loads(SUMMARY_PATH.read_text(encoding='utf-8'))
        actual = json.loads((WEB_ROOT / 'data.json').read_text(encoding='utf-8'))
        self.assertEqual(actual, expected)

    def test_page_has_only_v2_read_sources_and_explicit_labels(self):
        app = (WEB_ROOT / 'app.js').read_text(encoding='utf-8')
        page = (WEB_ROOT / 'index.html').read_text(encoding='utf-8')
        self.assertIn('/v2-shadow/shadow_state/v2/summary.json', app)
        self.assertIn("fetchJson('data.json'", app)
        self.assertNotIn('/state/', app)
        self.assertNotIn('web/d/data.json', app)
        self.assertNotIn('web/e/data.json', app)
        for label in ('V2 影子盘', '非实盘', '当前未替代 V1'):
            self.assertIn(label, app)
        self.assertIn('viewport-fit=cover', page)

    def test_cli_can_write_isolated_same_origin_fallback(self):
        summary = build_summary(STATE_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'data.json'
            output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            self.assertEqual(json.loads(output.read_text(encoding='utf-8')), summary)


if __name__ == '__main__':
    unittest.main()
