import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESET_DATE = "2026-08-26"


def _activity_date(item: dict) -> str | None:
    for key in ("trade_date", "execution_date", "decision_date", "date"):
        value = item.get(key)
        if value:
            return str(value)[:10]
    plan = item.get("trade_plan") or {}
    value = plan.get("decision_date")
    return str(value)[:10] if value else None


class V1PaperResetTests(unittest.TestCase):
    def test_all_v1_funds_respect_reset_boundary(self):
        for fund_id in ("A", "B", "C", "D", "D_MAIN", "L"):
            state = json.loads((ROOT / "state" / f"{fund_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(float(state["initial_cash"]), 1_000_000.0, fund_id)
            self.assertGreaterEqual(float(state["cash"]), 0.0, fund_id)
            self.assertEqual(state.get("execution_model"), "CONDITIONAL_PLAN_V1", fund_id)

            for collection in ("fills", "rejected_orders", "equity_curve", "pending_targets", "decisions"):
                for item in state.get(collection) or []:
                    activity_date = _activity_date(item)
                    self.assertIsNotNone(activity_date, f"{fund_id} {collection} item missing activity date")
                    self.assertGreaterEqual(activity_date, RESET_DATE, f"{fund_id} contains pre-reset {collection}")

            processed = state.get("last_processed_date")
            if processed:
                self.assertGreaterEqual(str(processed)[:10], RESET_DATE, fund_id)

    def test_reset_epoch_forbids_retroactive_fills(self):
        epoch = json.loads((ROOT / "state" / "paper_reset_epoch.json").read_text(encoding="utf-8"))
        self.assertEqual(epoch["reset_date"], RESET_DATE)
        self.assertEqual(float(epoch["initial_cash_per_fund"]), 1_000_000.0)
        self.assertEqual(epoch["reselect_from_close"], RESET_DATE)
        self.assertTrue(epoch["retroactive_fills_forbidden"])


if __name__ == "__main__":
    unittest.main()
