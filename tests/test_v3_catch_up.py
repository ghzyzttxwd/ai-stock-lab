import tempfile
import unittest
from pathlib import Path

from engine_v3.calendar import next_trade_session
from engine_v3.catch_up import catch_up
from tests.test_v3_agent_paper import decision


class FakeMarket:
    def execution_bars(self, symbols, trade_date):
        return {
            symbol: {
                "open": 10.0, "high": 10.3, "low": 9.8, "close": 10.1,
                "preclose": 9.9, "tradestatus": "1",
            }
            for symbol in symbols
        }


class V3CatchUpTests(unittest.TestCase):
    def test_calendar_uses_exchange_sessions_not_weekday_guess(self):
        self.assertEqual(
            next_trade_session("2026-09-30", ["2026-09-30", "2026-10-09"]),
            "2026-10-09",
        )

    def test_due_decision_can_be_replayed_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decisions = root / "decisions"
            decisions.mkdir()
            import json
            (decisions / "2026-08-21.json").write_text(json.dumps(decision(), ensure_ascii=False))
            result = catch_up(decisions, root, "2026-08-25", market=FakeMarket())
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["decisions"][0]["status"], "executed")
            state = json.loads((root / "ledgers/A.json").read_text())
            self.assertEqual(state["last_processed_date"], "2026-08-24")


if __name__ == "__main__":
    unittest.main()

