from __future__ import annotations

from engine.universe import is_main_board

from .shadow_ledger import normalize_symbol


BOARD_POLICY_VERSION = 'retail-mainboard-only-2026-08-19'


def is_retail_buyable_symbol(value: object) -> bool:
    """Return True only for Shanghai/Shenzhen main-board A shares.

    This explicitly excludes ChiNext (300/301), STAR (688/689), BSE and B shares.
    Existing non-eligible positions may still be sold; this predicate is for opening or
    increasing exposure.
    """
    return is_main_board(normalize_symbol(value))


def filter_mainboard_candidates(candidates: list[dict]) -> tuple[list[dict], list[str]]:
    eligible: list[dict] = []
    excluded: list[str] = []
    for raw in candidates:
        symbol = normalize_symbol(raw.get('symbol') or raw.get('code') or raw.get('raw_code'))
        if is_main_board(symbol):
            eligible.append(raw)
        else:
            excluded.append(symbol or str(raw.get('code') or raw.get('raw_code') or ''))
    return eligible, sorted(set(x for x in excluded if x))


def sanitize_pending_for_retail(pending: dict) -> tuple[dict, list[dict]]:
    """Clamp non-mainboard BUY exposure to zero at the execution boundary.

    This is deliberately applied even though the upstream universe is already main-board
    only, so stale caches, old pending decisions or manually injected targets cannot buy
    ChiNext/STAR shares.
    """
    safe = dict(pending)
    targets: list[dict] = []
    adjustments: list[dict] = []
    for raw in pending.get('targets') or []:
        item = dict(raw)
        symbol = normalize_symbol(item.get('symbol') or item.get('code') or item.get('raw_code'))
        requested = max(0.0, float(item.get('target_weight') or 0.0))
        if symbol:
            item['symbol'] = symbol
        if symbol and requested > 0 and not is_main_board(symbol):
            item['target_weight'] = 0.0
            adjustments.append({
                'symbol': symbol,
                'requested_weight': round(requested, 6),
                'applied_weight': 0.0,
                'reason': 'retail_mainboard_only',
                'policy_version': BOARD_POLICY_VERSION,
            })
        targets.append(item)
    safe['targets'] = targets
    return safe, adjustments
