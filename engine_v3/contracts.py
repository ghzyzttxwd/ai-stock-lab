from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from engine_v2.board_policy import is_retail_buyable_symbol
from engine_v2.shadow_ledger import normalize_symbol


DECISION_SCHEMA_VERSION = "v3-agent-decision-1.0"
FUND_IDS = ("A", "B", "C", "D", "L")


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def decision_hash(payload: dict) -> str:
    value = deepcopy(payload)
    value.pop("decision_sha256", None)
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_decision(payload: dict, *, allowed_symbols: set[str] | None = None) -> dict:
    """Validate the agent's complete target portfolios.

    This contract intentionally contains no stock-picking thresholds. It only rejects
    malformed, unauditable, future-dated, or non-main-board orders.
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
            item["symbol"] = symbol
            item["target_weight"] = round(weight, 6)
            clean.append(item)
        if total > 1.000001:
            errors.append(f"{fund_id} total target weight exceeds 100%: {total:.6f}")
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

