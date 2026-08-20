from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from engine_v2.morning_sell_run import MAX_CHECKPOINT_LATENESS_MINUTES, _checkpoint_delay_minutes


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


if __name__ == '__main__':
    unittest.main()
