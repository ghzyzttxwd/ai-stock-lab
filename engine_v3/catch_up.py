from __future__ import annotations

import argparse
import json
from pathlib import Path

from engine.real_market import AKShareMarket
from engine_v2.shadow_ledger import normalize_symbol
from engine_v2.shadow_run import bars_from_enriched

from .contracts import FUND_IDS, validate_decision
from .ledger import load_ledger
from .session import run_decision


def _critical_symbols(decision: dict, state_root: Path) -> dict[str, str]:
    symbols: dict[str, str] = {}
    for fund_id in FUND_IDS:
        state = load_ledger(state_root / "ledgers" / f"{fund_id}.json", fund_id, decision["decision_date"])
        for symbol, position in (state.get("positions") or {}).items():
            symbols[normalize_symbol(symbol)] = str(position.get("name") or symbol)
        for target in (decision.get("portfolios") or {}).get(fund_id) or []:
            symbol = normalize_symbol(target.get("symbol"))
            symbols[symbol] = str(target.get("name") or symbol)
    return symbols


def catch_up(
    decisions_root: Path,
    state_root: Path,
    as_of: str,
    current_bars: dict[str, dict] | None = None,
    market: AKShareMarket | None = None,
) -> dict:
    reports = []
    current = {normalize_symbol(k): dict(v) for k, v in (current_bars or {}).items()}
    for path in sorted(decisions_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        allowed = set(payload.get("brief_symbols") or [])
        if not allowed:
            brief_path = state_root / "briefs" / f"{str(payload.get('decision_date') or '')[:10]}.json"
            if not brief_path.exists():
                raise RuntimeError(f"V3 decision has no matching immutable brief: {brief_path}")
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
            allowed = {str(x.get("symbol") or "") for x in brief.get("candidates") or []}
        decision = validate_decision(payload, allowed_symbols=allowed or None)
        if decision["execute_on"] > as_of:
            reports.append({"decision": path.name, "status": "not_due", "execute_on": decision["execute_on"]})
            continue

        critical = _critical_symbols(decision, state_root)
        bars = dict(current) if decision["execute_on"] == as_of else {}
        missing = {symbol: name for symbol, name in critical.items() if symbol not in bars}
        if missing:
            provider = market or AKShareMarket()
            bars.update(provider.execution_bars(missing, decision["execute_on"]))
        still_missing = sorted(set(critical) - set(bars))
        if still_missing:
            raise RuntimeError(
                f"V3 catch-up refuses partial execution for {decision['execute_on']}; missing bars={still_missing}"
            )
        result = run_decision(decision, bars, state_root)
        reports.append({"decision": path.name, **result})
    return {"status": "ok", "as_of": as_of, "decisions": reports}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions-root", default="agent_state/v3/decisions")
    ap.add_argument("--state-root", default="agent_state/v3")
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--enriched")
    ap.add_argument("--report")
    args = ap.parse_args()
    bars = {}
    if args.enriched:
        enriched = json.loads(Path(args.enriched).read_text(encoding="utf-8"))
        bars = bars_from_enriched(enriched)
    result = catch_up(Path(args.decisions_root), Path(args.state_root), args.as_of, bars)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
