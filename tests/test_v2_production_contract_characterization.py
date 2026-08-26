from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MODEL = "V2_CONDITIONAL_PLAN_V1"
EXPECTED_PLAN = "v2-conditional-plan-v1"
MAX_CHECKPOINT_LATENESS_MINUTES = 10.0
RESET_DATE = "2026-08-26"


def _load_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _public_funds(payload: dict) -> dict[str, dict]:
    funds = payload.get("funds") or {}
    if isinstance(funds, dict):
        return funds
    return {str(row.get("fund_id") or row.get("id")): row for row in funds if isinstance(row, dict)}


class V2ProductionContractCharacterizationTests(unittest.TestCase):
    """Freeze current safety/public semantics before refactoring implementation details."""

    def test_public_artifact_keeps_conditional_execution_contract(self):
        payload = _load_json("web/v2/data.json")
        self.assertEqual(payload.get("execution_model"), EXPECTED_MODEL)
        self.assertEqual(payload.get("plan_version"), EXPECTED_PLAN)

        source = payload.get("source_ref") or {}
        if source.get("execution_model") is not None:
            self.assertEqual(source.get("execution_model"), EXPECTED_MODEL)
        if source.get("plan_version") is not None:
            self.assertEqual(source.get("plan_version"), EXPECTED_PLAN)

    def test_public_artifact_never_exposes_negative_cash(self):
        payload = _load_json("web/v2/data.json")
        funds = _public_funds(payload)
        self.assertTrue(funds, "public V2 artifact must expose at least one fund")
        for fund_id, fund in funds.items():
            metrics = fund.get("metrics") or {}
            cash = metrics.get("cash", fund.get("cash"))
            if cash is not None:
                self.assertGreaterEqual(float(cash), 0.0, f"{fund_id} has negative cash")

    def test_verified_checkpoint_requires_freshness_and_public_byte_equality(self):
        receipt = _load_json("state/v2_checkpoint_guard_verification.json")
        if receipt.get("status") != "verified":
            self.skipTest("latest checkpoint receipt is not a verified checkpoint")

        self.assertTrue(receipt.get("checkpoint_valid"))
        self.assertTrue(receipt.get("checkpoint_durable"))
        self.assertTrue(receipt.get("end_to_end_verified"))
        self.assertEqual(receipt.get("execution_model"), EXPECTED_MODEL)
        self.assertEqual(receipt.get("plan_version"), EXPECTED_PLAN)
        self.assertFalse(receipt.get("forced_clock_sell"))
        self.assertLessEqual(float(receipt.get("delay_minutes", 9999)), MAX_CHECKPOINT_LATENESS_MINUTES)
        self.assertEqual(receipt.get("expected_sha256"), receipt.get("public_sha256"))

    def test_live_conditional_sell_fills_are_reasoned_and_time_attributed(self):
        payload = _load_json("web/v2/data.json")
        checked = 0
        for fund_id, fund in _public_funds(payload).items():
            for fill in fund.get("recent_fills") or []:
                fill_date = str(fill.get("trade_date") or fill.get("date") or "")[:10]
                self.assertGreaterEqual(fill_date, RESET_DATE, f"{fund_id} exposes a pre-reset fill")
                if fill.get("side") != "SELL":
                    continue
                if fill.get("plan_version") != EXPECTED_PLAN:
                    continue
                if fill.get("execution_price_field") != "live_conditional":
                    continue
                checked += 1
                self.assertTrue(fill.get("exit_reason"), f"{fund_id} conditional sell missing exit_reason")
                self.assertTrue(fill.get("scheduled_time"), f"{fund_id} conditional sell missing scheduled_time")
                self.assertTrue(fill.get("actual_clock"), f"{fund_id} conditional sell missing actual_clock")
                self.assertTrue(fill.get("actual_execution_time"), f"{fund_id} conditional sell missing actual_execution_time")

        if checked == 0:
            source = payload.get("source_ref") or {}
            if source.get("reset_version") == "paper-reset-20260826":
                audit = payload.get("audit_verification") or {}
                self.assertEqual(audit.get("status"), "PASS")
                self.assertEqual(audit.get("first_date"), "2026-08-26~paper-reset")
                self.assertGreaterEqual(int(audit.get("events", 0)), 1)
                for fund_id, fund in _public_funds(payload).items():
                    metrics = fund.get("metrics") or {}
                    self.assertEqual(int(metrics.get("fills", 0)), 0, fund_id)
                    self.assertEqual(fund.get("recent_fills") or [], [], fund_id)
                    self.assertGreaterEqual(float(metrics.get("cash", 0)), 0.0, fund_id)
                return

        self.assertGreater(checked, 0, "expected at least one characterized live conditional V2 sell fill")

    def test_historical_terminal_failure_cannot_masquerade_as_current_health(self):
        status = _load_json("state/v2_terminal_recheck_status.json")
        self.assertEqual(status.get("historical_status"), "failed")
        self.assertEqual(status.get("status"), "superseded")
        self.assertEqual(status.get("current_status"), "verified")
        superseded_by = status.get("superseded_by") or {}
        self.assertTrue(superseded_by.get("terminal_receipt"))
        self.assertTrue(superseded_by.get("pages_full_verification"))
        self.assertTrue(superseded_by.get("latest_verified_public_sha256"))


if __name__ == "__main__":
    unittest.main()
