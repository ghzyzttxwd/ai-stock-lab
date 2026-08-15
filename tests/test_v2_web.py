import json
import tempfile
import unittest
from pathlib import Path

from engine_v2.shadow_reporting import build_summary


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / 'shadow_state' / 'v2'
WEB_ROOT = ROOT / 'web' / 'v2'


class V2MobileWebTests(unittest.TestCase):
    def test_summary_exposes_all_read_only_mobile_fields(self):
        summary = build_summary(STATE_ROOT)
        self.assertEqual(summary['summary_version'], 'v2-shadow-summary-1.1')
        self.assertEqual(set(summary['funds']), {'A', 'B', 'C', 'D', 'L'})
        self.assertEqual(summary['mode'], 'FORWARD_SHADOW_ONLY')
        self.assertFalse(summary['safety']['calls_sol'])
        self.assertFalse(summary['safety']['reads_v1_ledger'])
        self.assertFalse(summary['safety']['writes_v1_ledger'])
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
            self.assertIn('targets', fund['pending_decision'])

    def test_committed_fallback_is_exactly_the_v2_summary(self):
        expected = build_summary(STATE_ROOT)
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

