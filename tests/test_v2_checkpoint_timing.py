from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from engine_v2.morning_sell_run import (
    MAX_CHECKPOINT_LATENESS_MINUTES,
    _checkpoint_delay_minutes,
    run_morning_sell,
)
from engine_v2.shadow_ledger import sha256_json


class V2CheckpointTimingTests(unittest.TestCase):
    def test_checkpoint_within_ten_minutes_is_fresh(self):
        now = datetime(2026, 8, 20, 9, 49, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertLessEqual(
            _checkpoint_delay_minutes(now, '09:40'),
            MAX_CHECKPOINT_LATENESS_MINUTES,
        )

    def test_checkpoint_over_ten_minutes_is_stale(self):
        now = datetime(2026, 8, 20, 10, 42, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertGreater(
            _checkpoint_delay_minutes(now, '09:40'),
            MAX_CHECKPOINT_LATENESS_MINUTES,
        )

    def test_future_checkpoint_is_negative_delay(self):
        now = datetime(2026, 8, 20, 10, 20, tzinfo=ZoneInfo('Asia/Shanghai'))
        self.assertLess(_checkpoint_delay_minutes(now, '10:30'), 0)

    def test_late_duplicate_is_noop_and_never_claims_fresh_evaluation(self):
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 8, 20, 15, 14, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
                return value if tz is None else value.astimezone(tz)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_dir = root / 'audit'
            audit_dir.mkdir(parents=True)
            event = {
                'schema_version': 'v2-shadow-audit-1.0',
                'event_kind': 'conditional_exit_scan',
                'trade_date': '2026-08-20',
                'source_ref': {
                    'scheduled_time': '14:30',
                    'executed_at': '2026-08-20T14:38:18+08:00',
                    'actual_clock': '14:38',
                    'delay_minutes': 7.3,
                    'execution_model': 'V2_CONDITIONAL_PLAN_V1',
                    'plan_version': 'v2-conditional-plan-v1',
                },
                'previous_event_hashes': {'A': 'parent'},
                'funds': {},
            }
            event['event_hash'] = sha256_json(event)
            (audit_dir / '2026-08-20-execution-1430.json').write_text(
                json.dumps(event, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
            )

            with patch('engine_v2.morning_sell_run.datetime', FixedDateTime):
                result = run_morning_sell('2026-08-20', root, '14:30')

        self.assertEqual(result['status'], 'late_duplicate_noop')
        self.assertEqual(result['scheduled_time'], '14:30')
        self.assertEqual(result['actual_clock'], '15:14')
        self.assertEqual(result['delay_minutes'], 44.0)
        self.assertEqual(result['event_hash'], event['event_hash'])
        self.assertEqual(result['existing_checkpoint']['actual_clock'], '14:38')
        self.assertFalse(result['safety']['reads_market_quotes'])
        self.assertFalse(result['safety']['mutates_v2_ledger'])
        self.assertFalse(result['safety']['forced_clock_sell'])


if __name__ == '__main__':
    unittest.main()
