import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import runtime_metrics as rm


class RuntimeMetricsTests(unittest.TestCase):
    def setUp(self):
        rm.reset()

    def tearDown(self):
        rm.reset()

    def test_stage_records_success_and_summary(self):
        with patch('engine.runtime_metrics.time.monotonic', side_effect=[10.0, 10.125]):
            with rm.stage('unit.stage'):
                pass
        events = rm.snapshot()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['name'], 'unit.stage')
        self.assertEqual(events[0]['status'], 'ok')
        self.assertEqual(events[0]['elapsed_s'], 0.125)
        text = rm.render_summary()
        self.assertIn('unit.stage=0.125s/1x/ok', text)
        self.assertIn('[PERF SLOWEST]', text)

    def test_stage_records_error_and_reraises(self):
        with patch('engine.runtime_metrics.time.monotonic', side_effect=[20.0, 20.5]):
            with self.assertRaisesRegex(ValueError, 'boom'):
                with rm.stage('unit.error'):
                    raise ValueError('boom')
        event = rm.snapshot()[0]
        self.assertEqual(event['status'], 'error')
        self.assertEqual(event['elapsed_s'], 0.5)
        self.assertIn('ValueError', event['detail'])

    def test_wrapped_method_preserves_success_and_exception(self):
        class Fake:
            def ok(self, value):
                return value + 1

            def bad(self):
                raise RuntimeError('nope')

        rm._wrap_method(Fake, 'ok', 'fake.ok')
        rm._wrap_method(Fake, 'bad', 'fake.bad')
        obj = Fake()
        self.assertEqual(obj.ok(4), 5)
        with self.assertRaisesRegex(RuntimeError, 'nope'):
            obj.bad()
        events = rm.snapshot()
        by_name = {x['name']: x for x in events}
        self.assertEqual(by_name['fake.ok']['status'], 'ok')
        self.assertEqual(by_name['fake.bad']['status'], 'error')

    def test_write_github_summary_appends_markdown(self):
        rm.record('market.snapshot', 1.25)
        rm.record('market.snapshot', 0.75)
        rm.record('job.run_real', 3.5)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'summary.md'
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(path)}, clear=False):
                rm.write_github_summary()
            text = path.read_text(encoding='utf-8')
        self.assertIn('### V1 runtime timing', text)
        self.assertIn('| `market.snapshot` | 2 | 2.000 | ok |', text)
        self.assertIn('| `job.run_real` | 1 | 3.500 | ok |', text)


if __name__ == '__main__':
    unittest.main()
