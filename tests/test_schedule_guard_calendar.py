import unittest

import pandas as pd

from engine.schedule_guard import exchange_calendar_latest_session


class _FakeAk:
    def __init__(self, dates):
        self._dates = dates

    def tool_trade_date_hist_sina(self):
        return pd.DataFrame({'trade_date': self._dates})


class ScheduleGuardCalendarTests(unittest.TestCase):
    def test_requested_session_wins_even_if_quotes_could_lag(self):
        ak = _FakeAk(['2026-08-18', '2026-08-19', '2026-08-20'])
        self.assertEqual(
            exchange_calendar_latest_session('2026-08-20', ak),
            '2026-08-20',
        )

    def test_weekday_holiday_resolves_previous_exchange_session(self):
        ak = _FakeAk(['2026-09-30', '2026-10-09'])
        self.assertEqual(
            exchange_calendar_latest_session('2026-10-01', ak),
            '2026-09-30',
        )

    def test_calendar_without_requested_range_fails_closed(self):
        ak = _FakeAk(['2026-08-18', '2026-08-19'])
        with self.assertRaisesRegex(RuntimeError, 'cannot classify 2026-08-20'):
            exchange_calendar_latest_session('2026-08-20', ak)


if __name__ == '__main__':
    unittest.main()
