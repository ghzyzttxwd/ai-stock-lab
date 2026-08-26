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

from .contracts import CONDITIONAL_CUTOVER_DATE, CONDITIONAL_EXECUTION_MODEL


LEDGER_SCHEMA_VERSION = "v3-agent-paper-ledger-1.0"
MIN_REBALANCE_WEIGHT = 0.02
DRAWDOWN_NO_EXPANSION = -0.08
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


def _opening_equity(state: dict, bars: dict[str, dict]) -> tuple[float, dict[str, float]]:
    equity = float(state.get("cash") or 0.0)
    values: dict[str, float] = {}
    for symbol, position in (state.get("positions") or {}).items():
        bar = bars.get(symbol) or {}
        opening = float(bar.get("open") or position.get("last_price") or position.get("avg_cost") or 0.0)
        value = float(position.get("qty") or 0) * opening
        values[symbol] = value
        equity += value
    return equity, values


def _opening_drawdown(state: dict, opening_equity: float) -> float:
    history = [float(x.get("equity") or 0.0) for x in state.get("equity_curve") or []]
    history.append(float(state.get("initial_cash") or DEFAULT_POLICY.initial_cash))
    peak = max(history) if history else opening_equity
    return opening_equity / peak - 1.0 if peak > 0 else 0.0


def _entry_gate(target: dict, bar: dict) -> tuple[bool, float | None, str]:
    plan = target.get("trade_plan") or {}
    entry = plan.get("entry") or {}
    mode = str(entry.get("mode") or "")
    trigger = float(entry.get("trigger_price") or 0.0)
    valid_min = float(entry.get("valid_min") or 0.0)
    valid_max = float(entry.get("valid_max") or 0.0)
    opening = float(bar.get("open") or 0.0)
    high = float(bar.get("high") or opening)
    low = float(bar.get("low") or opening)
    preclose = float(bar.get("preclose") or 0.0)
    max_gap = float(plan.get("max_gap_up_pct", 0.018))

    if opening <= 0 or min(trigger, valid_min, valid_max) <= 0:
        return False, None, "invalid_execution_bar_or_plan"
    if preclose > 0 and opening / preclose - 1.0 > max_gap:
        return False, None, "gap_up_above_plan"

    if mode == "breakout":
        if opening > valid_max:
            return False, None, "open_above_max_chase"
        if trigger <= opening <= valid_max:
            return True, opening, "open_confirmed_breakout"
        if opening < trigger and high >= trigger and trigger <= valid_max:
            return True, trigger, "intraday_breakout_confirmed"
        return False, None, "breakout_not_triggered"

    if mode == "pullback":
        if opening < valid_min:
            return False, None, "gap_down_below_valid_band"
        if valid_min <= opening <= trigger:
            return True, opening, "open_inside_pullback_band"
        if opening > trigger and low <= trigger:
            if low < valid_min:
                return False, None, "pullback_fell_through_valid_band"
            return True, trigger, "intraday_pullback_confirmed"
        return False, None, "pullback_not_triggered"

    if mode == "range":
        if opening < valid_min:
            return False, None, "open_below_range"
        if valid_min <= opening <= valid_max:
            return True, opening, "open_inside_range"
        if opening > valid_max and low <= valid_max:
            if low < valid_min:
                return False, None, "range_fell_through_valid_band"
            return True, valid_max, "intraday_range_entry"
        return False, None, "range_not_triggered"

    return False, None, "unknown_entry_mode"


def _conditional_execution_inputs(
    state: dict,
    targets: list[dict],
    bars: dict[str, dict],
) -> tuple[list[dict], dict[str, dict], dict]:
    opening_equity, current_values = _opening_equity(state, bars)
    if opening_equity <= 0:
        raise RuntimeError(f"V3 opening equity invalid for {state['fund_id']}")
    drawdown = _opening_drawdown(state, opening_equity)
    current_weights = {symbol: value / opening_equity for symbol, value in current_values.items()}
    positions = state.get("positions") or {}
    effective: list[dict] = []
    execution_bars = {k: dict(v) for k, v in bars.items()}
    gate_rows: list[dict] = []
    wanted = {normalize_symbol(x.get("symbol")): x for x in targets}

    for symbol, target_raw in wanted.items():
        target = deepcopy(target_raw)
        desired = float(target.get("target_weight") or 0.0)
        current = float(current_weights.get(symbol, 0.0))
        delta = desired - current

        # Tiny rebalance differences only pay fees/slippage without materially changing
        # the portfolio. Hold the current opening weight instead.
        if abs(delta) < MIN_REBALANCE_WEIGHT:
            if symbol in positions:
                target["target_weight"] = round(current, 6)
                effective.append(target)
            gate_rows.append({"symbol": symbol, "status": "hold", "reason": "rebalance_delta_below_2pct", "current_weight": round(current, 6), "desired_weight": round(desired, 6)})
            continue

        # Reductions do not need a fresh entry trigger. The AI has already decided to
        # de-risk; execute_pending will reduce at the exact next-session open.
        if delta < 0:
            effective.append(target)
            gate_rows.append({"symbol": symbol, "status": "reduce", "reason": "target_weight_reduction", "current_weight": round(current, 6), "desired_weight": round(desired, 6)})
            continue

        # Portfolio drawdown protection: after -8%, no new/increased exposure until the
        # existing book recovers. Existing positions are held unless the AI reduces them.
        if drawdown <= DRAWDOWN_NO_EXPANSION:
            if symbol in positions:
                target["target_weight"] = round(current, 6)
                effective.append(target)
            gate_rows.append({"symbol": symbol, "status": "blocked", "reason": "portfolio_drawdown_no_expansion", "drawdown": round(drawdown, 6)})
            continue

        allowed, execution_price, reason = _entry_gate(target, execution_bars.get(symbol) or {})
        if not allowed or execution_price is None:
            if symbol in positions:
                target["target_weight"] = round(current, 6)
                effective.append(target)
            gate_rows.append({"symbol": symbol, "status": "blocked", "reason": reason, "current_weight": round(current, 6), "desired_weight": round(desired, 6)})
            continue

        effective.append(target)
        execution_bars[symbol]["open"] = round(float(execution_price), 6)
        gate_rows.append({"symbol": symbol, "status": "accepted", "reason": reason, "execution_reference_price": round(float(execution_price), 6), "current_weight": round(current, 6), "desired_weight": round(desired, 6)})

    return effective, execution_bars, {
        "execution_model": CONDITIONAL_EXECUTION_MODEL,
        "opening_equity": round(opening_equity, 2),
        "opening_drawdown": round(drawdown, 6),
        "min_rebalance_weight": MIN_REBALANCE_WEIGHT,
        "drawdown_no_expansion_threshold": DRAWDOWN_NO_EXPANSION,
        "entries": gate_rows,
    }


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

    conditional = (
        str(decision.get("decision_date") or "")[:10] >= CONDITIONAL_CUTOVER_DATE
        and decision.get("execution_model") == CONDITIONAL_EXECUTION_MODEL
    )
    gate = {"execution_model": "LEGACY_TARGET_OPEN_REBALANCE", "entries": []}
    execution_targets = targets
    execution_bars = normalized_bars
    if conditional:
        execution_targets, execution_bars, gate = _conditional_execution_inputs(
            state, targets, normalized_bars
        )

    pending = {
        "decision_date": decision["decision_date"],
        "strategy_version": "chatgpt-autonomous-agent-v3-conditional" if conditional else "chatgpt-autonomous-agent-v3",
        "targets": execution_targets,
    }
    before_fills = len(state.get("fills") or [])
    result = execute_pending(state, pending, execution_bars, decision["execute_on"])
    new_fills = (state.get("fills") or [])[before_fills:]
    accepted = {row["symbol"]: row for row in gate.get("entries") or [] if row.get("status") == "accepted"}
    for fill in new_fills:
        symbol = normalize_symbol(fill.get("symbol"))
        if conditional:
            if fill.get("side") == "BUY" and symbol in accepted:
                fill["note"] = "V3 AI条件计划 · 次日市场确认后买入/加仓"
                fill["execution_price_field"] = "conditional_trigger"
                fill["conditional_reason"] = accepted[symbol].get("reason")
            elif fill.get("side") == "SELL":
                fill["note"] = "V3 AI目标降仓/退出 · 次日开盘执行"
                fill["execution_price_field"] = "open"
        else:
            fill["note"] = "V3 legacy AI目标仓位再平衡"

    close = mark_to_market(state, normalized_bars, decision["execute_on"], result["fees"])
    state.setdefault("decisions", []).append({
        "decision_sha256": decision_id,
        "decision_date": decision["decision_date"],
        "execute_on": decision["execute_on"],
        "market_view": decision.get("market_view"),
        "risk_regime": decision.get("risk_regime"),
        "execution_model": decision.get("execution_model") or "LEGACY_TARGET_OPEN_REBALANCE",
        "targets": targets,
        "v3_gate": gate,
    })
    state.setdefault("executed_decision_ids", []).append(decision_id)
    state["last_processed_date"] = decision["execute_on"]
    return {
        "status": "executed",
        "decision_sha256": decision_id,
        "execution": result,
        "v3_gate": gate,
        "close": close,
    }
