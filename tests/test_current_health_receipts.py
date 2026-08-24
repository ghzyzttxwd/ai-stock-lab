import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


class CurrentHealthReceiptTests(unittest.TestCase):
    def test_v1_current_health_matches_terminal_receipts(self):
        health = load("state/current_health.json")
        v1 = health["v1"]
        run = load(v1["production_receipt"])
        live = load(v1["live_status"])
        pages = load(v1["pages_receipt"])

        self.assertEqual(health["status"], "verified")
        self.assertEqual(v1["status"], "verified")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["conclusion"], "success")
        self.assertEqual(str(live["run_id"]), str(run["run_id"]))
        self.assertEqual(live["status"], "completed")
        self.assertEqual(live["conclusion"], "success")
        self.assertEqual(pages["status"], "verified")
        self.assertEqual(pages["settlement_date"], v1["settlement_date"])
        self.assertEqual(pages["d_expected_sha256"], pages["d_public_sha256"])
        self.assertEqual(pages["e_expected_sha256"], pages["e_public_sha256"])
        self.assertEqual(v1["d_public_sha256"], pages["d_public_sha256"])
        self.assertEqual(v1["e_public_sha256"], pages["e_public_sha256"])

    def test_v2_1455_historical_failure_is_explicitly_superseded(self):
        health = load("state/current_health.json")
        v2 = health["v2"]
        pages = load(v2["pages_receipt"])
        terminal = load(v2["terminal_status"])

        self.assertEqual(v2["status"], "verified")
        self.assertEqual(v2["latest_checkpoint"], "14:55")

        # v2_checkpoint_guard_status.json is the mutable status of the newest
        # checkpoint. Do not pin this historical incident test to that live file:
        # a newer trading-day checkpoint must be allowed to replace it.
        self.assertEqual(pages["status"], "verified")
        self.assertTrue(pages["historical_guard_superseded"])
        self.assertEqual(pages["historical_guard_run_id"], v2["historical_guard_run_id"])
        self.assertEqual(pages["historical_guard_expected_sha256"], pages["public_sha256"])
        self.assertEqual(pages["expected_sha256"], pages["public_sha256"])
        self.assertEqual(v2["current_public_sha256"], pages["public_sha256"])

        self.assertEqual(terminal["status"], "superseded")
        self.assertEqual(terminal["historical_status"], "failed")
        self.assertEqual(terminal["historical_workflow_run_id"], v2["historical_guard_run_id"])
        self.assertEqual(terminal["current_status"], "verified")
        self.assertEqual(terminal["current_trade_date"], v2["trade_date"])
        self.assertTrue(terminal["superseded_by"]["public_byte_equality"])
        self.assertEqual(
            terminal["superseded_by"]["latest_verified_public_sha256"],
            v2["current_public_sha256"],
        )

    def test_historical_v1_diagnostic_cannot_override_current_health(self):
        health = load("state/current_health.json")
        v1 = health["v1"]
        diagnostic = load(v1["historical_diagnostic"])

        self.assertEqual(v1["historical_diagnostic_status"], "superseded")
        self.assertLessEqual(diagnostic["requested_date"], v1["settlement_date"])
        self.assertEqual(v1["status"], "verified")
        self.assertEqual(load(v1["production_receipt"])["conclusion"], "success")
        self.assertEqual(load(v1["pages_receipt"])["status"], "verified")


if __name__ == "__main__":
    unittest.main()
