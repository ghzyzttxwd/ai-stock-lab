from __future__ import annotations

import unittest

from engine_v2.ops import (
    build_checkpoint_status,
    build_checkpoint_verification_receipt,
    validate_checkpoint_report,
)


class V2OpsTests(unittest.TestCase):
    def _report(self, **overrides):
        report = {
            "status": "conditional_exit_processed",
            "event_hash": "evt",
            "scheduled_time": "10:30",
            "executed_at": "2026-08-21T10:38:25+08:00",
            "actual_clock": "10:38",
            "delay_minutes": 7.38,
            "execution_model": "V2_CONDITIONAL_PLAN_V1",
            "plan_version": "v2-conditional-plan-v1",
            "safety": {
                "calls_sol": False,
                "reads_v1_ledger": False,
                "writes_v1_ledger": False,
                "forced_clock_sell": False,
            },
        }
        report.update(overrides)
        return report

    def test_validate_checkpoint_accepts_current_allowed_statuses(self):
        for status in (
            "conditional_exit_processed",
            "already_conditional_exit_scan",
            "already_processed",
            "not_exchange_session",
        ):
            validate_checkpoint_report(self._report(status=status))

    def test_validate_checkpoint_rejects_stale_and_outside_window(self):
        for status in ("stale_checkpoint_skipped", "outside_session_window", None):
            with self.assertRaises(ValueError):
                validate_checkpoint_report(self._report(status=status))

    def test_validate_checkpoint_rejects_isolation_or_forced_clock_sell(self):
        for key in ("calls_sol", "reads_v1_ledger", "writes_v1_ledger", "forced_clock_sell"):
            report = self._report()
            report["safety"] = dict(report["safety"])
            report["safety"][key] = True
            with self.assertRaises(ValueError):
                validate_checkpoint_report(report)

    def test_verification_receipt_matches_current_workflow_contract(self):
        receipt = build_checkpoint_verification_receipt(
            self._report(),
            expected_sha256="abc",
            public_sha256="abc",
            trade_date="2026-08-21",
            workflow_run_id="123",
            workflow_run_attempt="1",
            verified_at_utc="2026-08-21T02:39:56+00:00",
        )
        self.assertEqual(
            receipt,
            {
                "status": "verified",
                "checkpoint_valid": True,
                "checkpoint_durable": True,
                "end_to_end_verified": True,
                "trade_date": "2026-08-21",
                "conditional_exit_status": "conditional_exit_processed",
                "event_hash": "evt",
                "scheduled_time": "10:30",
                "executed_at": "2026-08-21T10:38:25+08:00",
                "actual_clock": "10:38",
                "delay_minutes": 7.38,
                "execution_model": "V2_CONDITIONAL_PLAN_V1",
                "plan_version": "v2-conditional-plan-v1",
                "forced_clock_sell": False,
                "expected_sha256": "abc",
                "public_sha256": "abc",
                "workflow_run_id": "123",
                "workflow_run_attempt": "1",
                "verified_at_utc": "2026-08-21T02:39:56+00:00",
            },
        )

    def test_verification_receipt_rejects_public_hash_mismatch(self):
        with self.assertRaises(ValueError):
            build_checkpoint_verification_receipt(
                self._report(),
                expected_sha256="abc",
                public_sha256="def",
                trade_date="2026-08-21",
                workflow_run_id="123",
                workflow_run_attempt="1",
            )

    def test_guard_status_matches_current_workflow_contract(self):
        status = build_checkpoint_status(
            self._report(),
            job_status="success",
            checkpoint="10:30",
            durable=True,
            workflow_run_id="123",
            workflow_run_attempt="1",
            event_name="schedule",
            source_sha="deadbeef",
            finished_at_utc="2026-08-21T02:39:58+00:00",
        )
        self.assertEqual(
            status,
            {
                "status": "success",
                "checkpoint_valid": True,
                "checkpoint_evaluated_fresh": True,
                "checkpoint_durable": True,
                "end_to_end_verified": True,
                "checkpoint_status": "conditional_exit_processed",
                "workflow_run_id": "123",
                "workflow_run_attempt": "1",
                "event_name": "schedule",
                "source_sha": "deadbeef",
                "scheduled_time": "10:30",
                "actual_clock": "10:38",
                "delay_minutes": 7.38,
                "event_hash": "evt",
                "finished_at_utc": "2026-08-21T02:39:58+00:00",
            },
        )

    def test_guard_status_failure_does_not_claim_end_to_end(self):
        status = build_checkpoint_status(
            self._report(status="stale_checkpoint_skipped"),
            job_status="failure",
            checkpoint="09:40",
            durable=False,
            workflow_run_id="123",
            workflow_run_attempt="1",
            event_name="schedule",
            source_sha="deadbeef",
        )
        self.assertFalse(status["checkpoint_valid"])
        self.assertFalse(status["checkpoint_evaluated_fresh"])
        self.assertFalse(status["checkpoint_durable"])
        self.assertFalse(status["end_to_end_verified"])


if __name__ == "__main__":
    unittest.main()
