from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine_v2.board_policy import filter_mainboard_candidates
from engine_v2.shadow_ledger import sha256_json

from .contracts import DECISION_SCHEMA_VERSION, FUND_IDS
from .ledger import load_ledger


FEATURE_FIELDS = (
    "symbol", "raw_code", "name", "industry", "open", "high", "low", "close", "preclose",
    "pctChg", "amount", "turn", "peTTM", "pbMRQ", "r5", "r10", "r20", "r60", "vol20",
    "max_drawdown60", "amount_ratio", "trend", "momentum", "breakout_quality", "crowding_score",
    "risk", "liquidity", "valuation_score", "industry_score", "leader_score", "theme_score",
    "sentiment_score", "limit_status", "fundamental_ready", "financial_distress",
)


def _rank_union(rows: list[dict], limit: int = 180) -> list[dict]:
    """Build a broad retrieval set, not a trading decision.

    Multiple unrelated lenses prevent one hand-written score from silently becoming the strategy.
    """
    lenses = (
        ("amount", True), ("liquidity", True), ("trend", True), ("momentum", True),
        ("leader_score", True), ("valuation_score", True), ("risk", True),
    )
    picked: dict[str, dict] = {}
    per_lens = max(30, limit // 4)
    for field, reverse in lenses:
        ranked = sorted(rows, key=lambda x: float(x.get(field) or -1e30), reverse=reverse)
        for item in ranked[:per_lens]:
            picked[str(item.get("symbol"))] = item
    return list(picked.values())[:limit]


def build_brief(snapshot: dict, enriched: dict, state_root: Path, next_trade_date: str) -> dict:
    trade_date = str(enriched.get("trade_date") or "")[:10]
    if str(snapshot.get("trade_date") or "")[:10] != trade_date:
        raise RuntimeError("V3 brief snapshot/enrichment date mismatch")
    safety = enriched.get("safety") or {}
    if not safety.get("ready_for_strategy_targets"):
        raise RuntimeError("V3 refuses to brief the agent from degraded market data")
    candidates, excluded = filter_mainboard_candidates(list(enriched.get("candidates") or []))
    selected = _rank_union(candidates)
    ledgers = {
        fund_id: load_ledger(state_root / "ledgers" / f"{fund_id}.json", fund_id, trade_date)
        for fund_id in FUND_IDS
    }
    held = set()
    for state in ledgers.values():
        held.update(state.get("positions") or {})
    by_symbol = {str(x.get("symbol")): x for x in candidates}
    for symbol in held:
        if symbol in by_symbol and all(str(x.get("symbol")) != symbol for x in selected):
            selected.append(by_symbol[symbol])
    compact = [{k: row.get(k) for k in FEATURE_FIELDS if k in row} for row in selected]
    return {
        "brief_version": "v3-agent-brief-1.0",
        "trade_date": trade_date,
        "next_trade_date": next_trade_date,
        "market": enriched.get("market"),
        "coverage": enriched.get("coverage"),
        "candidate_retrieval": {
            "purpose": "broad context retrieval only; no row is an automatic buy",
            "eligible_mainboard": len(candidates),
            "included": len(compact),
            "excluded_non_mainboard": len(excluded),
        },
        "candidates": compact,
        "portfolios": {
            fund_id: {
                "cash": state["cash"],
                "positions": state["positions"],
                "last_processed_date": state.get("last_processed_date"),
            }
            for fund_id, state in ledgers.items()
        },
        "source_sha256": {
            "snapshot": sha256_json(snapshot),
            "enriched": sha256_json(enriched),
        },
        "decision_contract": {
            "schema_version": DECISION_SCHEMA_VERSION,
            "mode": "AUTONOMOUS_AI_PAPER",
            "requires_user_approval": False,
            "uses_future_data": False,
            "funds": list(FUND_IDS),
            "execution": "target weights are settled at next_trade_date open; late processing replays that historical open",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--enriched", required=True)
    ap.add_argument("--state-root", default="agent_state/v3")
    ap.add_argument("--next-trade-date", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    result = build_brief(
        json.loads(Path(args.snapshot).read_text(encoding="utf-8")),
        json.loads(Path(args.enriched).read_text(encoding="utf-8")),
        Path(args.state_root),
        args.next_trade_date,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"trade_date": result["trade_date"], "next_trade_date": result["next_trade_date"], "candidates": len(result["candidates"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()

