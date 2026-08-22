from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from engine_v2.shadow_ledger import (
    DEFAULT_POLICY,
    EXECUTION_POLICY_VERSION,
    execute_pending,
    mark_to_market,
    normalize_symbol,
    validate_ledger,
)


LEDGER_SCHEMA_VERSION = "v3-agent-paper-ledger-1.0"
FUND_NAMES = {
    "A": "V3 AI保守稳健虚拟基金",
    "B": "V3 AI趋势进攻虚拟基金",
    "C": "V3 AI短线机会虚拟基金",
    "D": "V3 AI综合判断虚拟基金",
    "L": "V3 AI长线价值虚拟基金",
}


def new_ledger(fund_id: str, created_date: str) -> dict:
    if fund_id not in FUND_NAMES:
        raise ValueError(f"unknown V3 fund {fund_id}")
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "fund_id": fund_id,
        "name": FUND_NAMES[fund_id],
        "mode": "AUTONOMOUS_AI_PAPER",
        "initial_cash": DEFAULT_POLICY.initial_cash,
        "cash": DEFAULT_POLICY.initial_cash,
        "positions": {},
        "fills": [],
        "rejected_orders": [],
        "equity_curve": [],
        "decisions": [],
        "executed_decision_ids": [],
        "created_date": created_date,
        "last_processed_date": None,
        "audit_head": None,
        "execution_policy_version": EXECUTION_POLICY_VERSION,
    }


def load_ledger(path: Path, fund_id: str, created_date: str) -> dict:
    if not path.exists():
        return new_ledger(fund_id, created_date)
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != LEDGER_SCHEMA_VERSION or state.get("fund_id") != fund_id:
        raise RuntimeError(f"V3 ledger identity/schema mismatch: {path}")
    if float(state.get("initial_cash") or 0) != DEFAULT_POLICY.initial_cash:
        raise RuntimeError(f"V3 initial cash invariant violated: {path}")
    validate_ledger(state)
    return state


def save_ledger(path: Path, state: dict) -> None:
    validate_ledger(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def execute_agent_portfolio(state: dict, decision: dict, bars: dict[str, dict]) -> dict:
    decision_id = str(decision["decision_sha256"])
    if decision_id in set(state.get("executed_decision_ids") or []):
        return {"status": "already_executed", "decision_sha256": decision_id, "fills": [], "rejected_orders": []}

    targets = deepcopy((decision.get("portfolios") or {}).get(state["fund_id"]) or [])
    critical = set(state.get("positions") or {}) | {normalize_symbol(x.get("symbol")) for x in targets}
    normalized_bars = {normalize_symbol(k): dict(v) for k, v in bars.items()}
    missing = sorted(x for x in critical if x and x not in normalized_bars)
    if missing:
        raise RuntimeError(f"V3 execution bars incomplete for {state['fund_id']}: {missing}")

    pending = {
        "decision_date": decision["decision_date"],
        "strategy_version": "chatgpt-autonomous-agent-v3",
        "targets": targets,
    }
    result = execute_pending(state, pending, normalized_bars, decision["execute_on"])
    close = mark_to_market(state, normalized_bars, decision["execute_on"], result["fees"])
    state.setdefault("decisions", []).append({
        "decision_sha256": decision_id,
        "decision_date": decision["decision_date"],
        "execute_on": decision["execute_on"],
        "market_view": decision.get("market_view"),
        "targets": targets,
    })
    state.setdefault("executed_decision_ids", []).append(decision_id)
    state["last_processed_date"] = decision["execute_on"]
    return {"status": "executed", "decision_sha256": decision_id, "execution": result, "close": close}

