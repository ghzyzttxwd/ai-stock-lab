from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from engine_v2.shadow_ledger import immutable_write, sha256_json

from .contracts import FUND_IDS, validate_decision
from .ledger import execute_agent_portfolio, load_ledger, save_ledger


def run_decision(decision: dict, bars: dict[str, dict], state_root: Path) -> dict:
    allowed = {str(x) for x in (decision.get("brief_symbols") or [])}
    checked = validate_decision(decision, allowed_symbols=allowed or None)
    audit_path = state_root / "audit" / f"{checked['execute_on']}~{checked['decision_sha256'][:12]}.json"
    if audit_path.exists():
        event = json.loads(audit_path.read_text(encoding="utf-8"))
        return {"status": "already_executed", "event_hash": event["event_hash"], "audit_path": str(audit_path)}

    original = {
        fund_id: load_ledger(state_root / "ledgers" / f"{fund_id}.json", fund_id, checked["decision_date"])
        for fund_id in FUND_IDS
    }
    states = deepcopy(original)
    results = {
        fund_id: execute_agent_portfolio(states[fund_id], checked, bars)
        for fund_id in FUND_IDS
    }
    event = {
        "schema_version": "v3-agent-paper-audit-1.0",
        "decision_sha256": checked["decision_sha256"],
        "decision_date": checked["decision_date"],
        "execute_on": checked["execute_on"],
        "market_view": checked["market_view"],
        "results": results,
        "opening_ledger_sha256": {k: sha256_json(v) for k, v in original.items()},
        "closing_ledger_sha256": {k: sha256_json(v) for k, v in states.items()},
        "safety": {
            "paper_only": True,
            "requires_user_approval": False,
            "mainboard_only": True,
            "github_timing_changes_execution_price": False,
        },
    }
    event["event_hash"] = sha256_json(event)
    for state in states.values():
        state["audit_head"] = event["event_hash"]
    immutable_write(audit_path, event)
    for fund_id, state in states.items():
        save_ledger(state_root / "ledgers" / f"{fund_id}.json", state)
    return {
        "status": "executed",
        "event_hash": event["event_hash"],
        "audit_path": str(audit_path),
        "fills": {k: len(v["execution"]["fills"]) for k, v in results.items()},
        "rejected": {k: len(v["execution"]["rejected_orders"]) for k, v in results.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decision", required=True)
    ap.add_argument("--bars", required=True)
    ap.add_argument("--state-root", default="agent_state/v3")
    ap.add_argument("--report")
    args = ap.parse_args()
    decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    bars = json.loads(Path(args.bars).read_text(encoding="utf-8"))
    result = run_decision(decision, bars, Path(args.state_root))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()

