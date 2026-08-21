from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_CHECKPOINT_STATUSES = {
    "conditional_exit_processed",
    "already_conditional_exit_scan",
    "already_processed",
    "not_exchange_session",
}


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: dict) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_checkpoint_report(report: dict) -> dict:
    """Validate the current operational checkpoint contract without mutating state."""
    status = report.get("status")
    if status not in ALLOWED_CHECKPOINT_STATUSES:
        raise ValueError(f"V2 checkpoint is not operationally valid: {status}; report={report}")

    safety = report.get("safety") or {}
    if safety.get("calls_sol") or safety.get("reads_v1_ledger") or safety.get("writes_v1_ledger"):
        raise ValueError("V2 checkpoint isolation invariant violated")
    if safety.get("forced_clock_sell"):
        raise ValueError("V2 checkpoint attempted a forced clock sale")
    return report


def build_checkpoint_verification_receipt(
    report: dict,
    *,
    expected_sha256: str,
    public_sha256: str,
    trade_date: str,
    workflow_run_id: str,
    workflow_run_attempt: str | None,
    verified_at_utc: str | None = None,
) -> dict:
    validate_checkpoint_report(report)
    if expected_sha256 != public_sha256:
        raise ValueError("guarded checkpoint public hash mismatch")
    if verified_at_utc is None:
        verified_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "status": "verified",
        "checkpoint_valid": True,
        "checkpoint_durable": True,
        "end_to_end_verified": True,
        "trade_date": trade_date,
        "conditional_exit_status": report.get("status"),
        "event_hash": report.get("event_hash"),
        "scheduled_time": report.get("scheduled_time"),
        "executed_at": report.get("executed_at"),
        "actual_clock": report.get("actual_clock"),
        "delay_minutes": report.get("delay_minutes"),
        "execution_model": report.get("execution_model"),
        "plan_version": report.get("plan_version"),
        "forced_clock_sell": False,
        "expected_sha256": expected_sha256,
        "public_sha256": public_sha256,
        "workflow_run_id": workflow_run_id,
        "workflow_run_attempt": workflow_run_attempt,
        "verified_at_utc": verified_at_utc,
    }


def build_checkpoint_status(
    report: dict,
    *,
    job_status: str,
    checkpoint: str | None,
    durable: bool,
    workflow_run_id: str,
    workflow_run_attempt: str | None,
    event_name: str | None,
    source_sha: str | None,
    finished_at_utc: str | None = None,
) -> dict:
    if finished_at_utc is None:
        finished_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    checkpoint_status = report.get("status")
    operational = checkpoint_status in ALLOWED_CHECKPOINT_STATUSES
    return {
        "status": job_status,
        "checkpoint_valid": operational and durable,
        "checkpoint_evaluated_fresh": operational,
        "checkpoint_durable": durable,
        "end_to_end_verified": job_status == "success",
        "checkpoint_status": checkpoint_status,
        "workflow_run_id": workflow_run_id,
        "workflow_run_attempt": workflow_run_attempt,
        "event_name": event_name,
        "source_sha": source_sha,
        "scheduled_time": checkpoint,
        "actual_clock": report.get("actual_clock"),
        "delay_minutes": report.get("delay_minutes"),
        "event_hash": report.get("event_hash"),
        "finished_at_utc": finished_at_utc,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="V2 operational invariant helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-checkpoint")
    validate.add_argument("--report", required=True)

    receipt = sub.add_parser("write-checkpoint-receipt")
    receipt.add_argument("--report", required=True)
    receipt.add_argument("--output", required=True)
    receipt.add_argument("--expected-sha256", required=True)
    receipt.add_argument("--public-sha256", required=True)
    receipt.add_argument("--trade-date", required=True)
    receipt.add_argument("--workflow-run-id", required=True)
    receipt.add_argument("--workflow-run-attempt")

    status = sub.add_parser("write-checkpoint-status")
    status.add_argument("--report", required=True)
    status.add_argument("--output", required=True)
    status.add_argument("--job-status", required=True)
    status.add_argument("--checkpoint")
    status.add_argument("--durable", action="store_true")
    status.add_argument("--workflow-run-id", required=True)
    status.add_argument("--workflow-run-attempt")
    status.add_argument("--event-name")
    status.add_argument("--source-sha")

    args = parser.parse_args()
    report = load_json(args.report)

    try:
        if args.command == "validate-checkpoint":
            validate_checkpoint_report(report)
            print(
                "[V2 GUARD]",
                report.get("status"),
                report.get("scheduled_time"),
                report.get("actual_clock"),
                report.get("delay_minutes"),
            )
            return
        if args.command == "write-checkpoint-receipt":
            payload = build_checkpoint_verification_receipt(
                report,
                expected_sha256=args.expected_sha256,
                public_sha256=args.public_sha256,
                trade_date=args.trade_date,
                workflow_run_id=args.workflow_run_id,
                workflow_run_attempt=args.workflow_run_attempt,
            )
            write_json(args.output, payload)
            return
        payload = build_checkpoint_status(
            report,
            job_status=args.job_status,
            checkpoint=args.checkpoint,
            durable=args.durable,
            workflow_run_id=args.workflow_run_id,
            workflow_run_attempt=args.workflow_run_attempt,
            event_name=args.event_name,
            source_sha=args.source_sha,
        )
        write_json(args.output, payload)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    _main()
