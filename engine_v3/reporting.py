from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import FUND_IDS
from .ledger import load_ledger


def build_summary(state_root: Path, trade_date: str) -> dict:
    funds = {}
    for fund_id in FUND_IDS:
        state = load_ledger(state_root / "ledgers" / f"{fund_id}.json", fund_id, trade_date)
        last = (state.get("equity_curve") or [{}])[-1]
        equity = float(last.get("equity") or state["cash"])
        funds[fund_id] = {
            "fund_id": fund_id,
            "name": state["name"],
            "initial_cash": state["initial_cash"],
            "cash": state["cash"],
            "equity": equity,
            "return_pct": round((equity / state["initial_cash"] - 1) * 100, 4),
            "last_processed_date": state.get("last_processed_date"),
            "positions": [
                {"symbol": symbol, **position}
                for symbol, position in sorted((state.get("positions") or {}).items())
            ],
            "recent_fills": (state.get("fills") or [])[-20:],
            "recent_rejections": (state.get("rejected_orders") or [])[-20:],
            "audit_head": state.get("audit_head"),
        }
    decisions = sorted((state_root / "decisions").glob("*.json")) if (state_root / "decisions").exists() else []
    latest_decision = json.loads(decisions[-1].read_text(encoding="utf-8")) if decisions else None
    return {
        "summary_version": "v3-agent-paper-summary-1.0",
        "updated_at": trade_date,
        "mode": "AUTONOMOUS_AI_PAPER",
        "scope": "SH_SZ_MAINBOARD_ONLY",
        "requires_user_approval": False,
        "github_role": "code_and_mirror_not_execution_clock",
        "latest_decision": latest_decision,
        "funds": funds,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-root", default="agent_state/v3")
    ap.add_argument("--trade-date", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    result = build_summary(Path(args.state_root), args.trade_date)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"trade_date": args.trade_date, "funds": list(result["funds"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()

