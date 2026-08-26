from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from engine_v2.board_policy import is_retail_buyable_symbol
from engine_v2.shadow_ledger import normalize_symbol


DECISION_SCHEMA_VERSION = "v3-agent-decision-1.0"
FUND_IDS = ("A", "B", "C", "D", "L")
CONDITIONAL_CUTOVER_DATE = "2026-08-26"
CONDITIONAL_EXECUTION_MODEL = "V3_CONDITIONAL_AI_PLAN_V1"
CONDITIONAL_PLAN_VERSION = "v3-ai-conditional-plan-v1"
RISK_REGIMES = {"risk_off", "neutral", "risk_on"}
RISK_EXPOSURE_CAPS = {
    "risk_off": {"A": 0.40, "B": 0.30, "C": 0.25, "D": 0.35, "L": 0.50},
    "neutral": {"A": 0.60, "B": 0.55, "C": 0.45, "D": 0.60, "L": 0.70},
    "risk_on": {"A": 0.75, "B": 0.75, "C": 0.65, "D": 0.75, "L": 0.80},
}


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decision_hash(payload: dict) -> str:
    value = deepcopy(payload)
    value.pop("decision_sha256", None)
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_trade_plan(fund_id: str, symbol: str, item: dict, errors: list[str]) -> None:
    plan = item.get("trade_plan") or {}
    if plan.get("plan_version") != CONDITIONAL_PLAN_VERSION:
        errors.append(f"{fund_id} {symbol} trade_plan.plan_version must be {CONDITIONAL_PLAN_VERSION}")
        return
    entry = plan.get("entry") or {}
    mode = str(entry.get("mode") or "")
    if mode not in {"breakout", "pullback", "range"}:
        errors.append(f"{fund_id} {symbol} invalid entry mode {mode}")
        return
    try:
        trigger = float(entry.get("trigger_price"))
        low = float(entry.get("valid_min"))
        high = float(entry.get("valid_max"))
    except (TypeError, ValueError):
        errors.append(f"{fund_id} {symbol} entry prices must be numeric")
        return
    if min(trigger, low, high) <= 0 or low > high or not (low <= trigger <= high):
        errors.append(f"{fund_id} {symbol} invalid entry price band")
    if plan.get("cancel_if_not_triggered_by_close") is not True:
        errors.append(f"{fund_id} {symbol} cancel_if_not_triggered_by_close must be true")
    try:
        max_gap = float(plan.get("max_gap_up_pct", 0.018))
    except (TypeError, ValueError):
        max_gap = -1.0
    if not 0 <= max_gap <= 0.05:
        errors.append(f"{fund_id} {symbol} max_gap_up_pct must be between 0 and 0.05")


def validate_decision(payload: dict, *, allowed_symbols: set[str] | None = None) -> dict:
    """Validate the agent's complete target portfolios.

    Decisions made before 2026-08-26 retain the original open-rebalance contract.
    From the cutover date onward, every desired position must carry an auditable
    next-session conditional entry plan; exits/reductions remain executable without
    needing a fresh buy trigger.
    """
    errors: list[str] = []
    if payload.get("schema_version") != DECISION_SCHEMA_VERSION:
        errors.append(f"schema_version must be {DECISION_SCHEMA_VERSION}")
    decision_date = str(payload.get("decision_date") or "")[:10]
    execute_on = str(payload.get("execute_on") or "")[:10]
    if len(decision_date) != 10:
        errors.append("decision_date must be YYYY-MM-DD")
    if len(execute_on) != 10 or (decision_date and execute_on <= decision_date):
        errors.append("execute_on must be a later exchange session")
    if payload.get("mode") != "AUTONOMOUS_AI_PAPER":
        errors.append("mode must be AUTONOMOUS_AI_PAPER")
    if payload.get("requires_user_approval") is not False:
        errors.append("requires_user_approval must be false")
    if payload.get("uses_future_data") is not False:
        errors.append("uses_future_data must be false")
    if not str(payload.get("market_view") or "").strip():
        errors.append("market_view is required")

    conditional = bool(decision_date and decision_date >= CONDITIONAL_CUTOVER_DATE)
    risk_regime = str(payload.get("risk_regime") or "")
    if conditional:
        if payload.get("execution_model") != CONDITIONAL_EXECUTION_MODEL:
            errors.append(f"execution_model must be {CONDITIONAL_EXECUTION_MODEL} after {CONDITIONAL_CUTOVER_DATE}")
        if risk_regime not in RISK_REGIMES:
            errors.append("risk_regime must be risk_off, neutral, or risk_on")

    portfolios = payload.get("portfolios") or {}
    if set(portfolios) != set(FUND_IDS):
        errors.append(f"portfolios must contain exactly {','.join(FUND_IDS)}")
    normalized_portfolios: dict[str, list[dict]] = {}
    normalized_allowed = {normalize_symbol(x) for x in (allowed_symbols or set())}
    for fund_id in FUND_IDS:
        rows = portfolios.get(fund_id) or []
        if not isinstance(rows, list):
            errors.append(f"{fund_id} portfolio must be a list")
            continue
        if len(rows) > 20:
            errors.append(f"{fund_id} has more than 20 positions")
        seen: set[str] = set()
        total = 0.0
        clean: list[dict] = []
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict):
                errors.append(f"{fund_id}[{index}] must be an object")
                continue
            item = deepcopy(raw)
            symbol = normalize_symbol(item.get("symbol") or item.get("code"))
            try:
                weight = float(item.get("target_weight"))
            except (TypeError, ValueError):
                weight = -1.0
            if symbol in seen:
                errors.append(f"{fund_id} duplicate symbol {symbol}")
            seen.add(symbol)
            if not is_retail_buyable_symbol(symbol):
                errors.append(f"{fund_id} non-main-board symbol {symbol}")
            if normalized_allowed and symbol not in normalized_allowed:
                errors.append(f"{fund_id} symbol absent from decision brief {symbol}")
            if not 0 < weight <= 1:
                errors.append(f"{fund_id} invalid target_weight for {symbol}: {weight}")
            total += max(0.0, weight)
            for field in ("name", "thesis", "invalidation"):
                if not str(item.get(field) or "").strip():
                    errors.append(f"{fund_id} {symbol} missing {field}")
            if conditional:
                _validate_trade_plan(fund_id, symbol, item, errors)
            item["symbol"] = symbol
            item["target_weight"] = round(weight, 6)
            clean.append(item)
        if total > 1.000001:
            errors.append(f"{fund_id} total target weight exceeds 100%: {total:.6f}")
        if conditional and risk_regime in RISK_EXPOSURE_CAPS:
            cap = RISK_EXPOSURE_CAPS[risk_regime][fund_id]
            if total > cap + 1e-6:
                errors.append(f"{fund_id} target exposure {total:.6f} exceeds {risk_regime} cap {cap:.2f}")
        normalized_portfolios[fund_id] = clean

    expected = decision_hash(payload)
    supplied = payload.get("decision_sha256")
    if supplied and supplied != expected:
        errors.append("decision_sha256 mismatch")
    if errors:
        raise ValueError("invalid V3 agent decision: " + "; ".join(errors))
    output = deepcopy(payload)
    output["decision_date"] = decision_date
    output["execute_on"] = execute_on
    output["portfolios"] = normalized_portfolios
    output["decision_sha256"] = decision_hash(output)
    return output
