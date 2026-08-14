from __future__ import annotations
from .config import CONFIG


def clamp_d_targets(targets: list[dict], state: dict | None = None) -> tuple[list[dict], list[str]]:
    notes: list[str] = []
    cleaned = []
    seen = set()
    positions=(state or {}).get('positions') or {}
    apply_new_cap=state is not None
    for x in sorted(targets, key=lambda z: float(z.get('target_weight', 0)), reverse=True):
        symbol = x['symbol']
        if symbol in seen:
            continue
        seen.add(symbol)
        requested=float(x.get('target_weight', 0))
        cap=CONFIG.max_single_weight_d
        if apply_new_cap and symbol not in positions:
            cap=min(cap,CONFIG.max_new_position_weight_d)
        w=max(0.0,min(requested,cap))
        if w != requested:
            if apply_new_cap and symbol not in positions and cap == CONFIG.max_new_position_weight_d:
                notes.append(f'{symbol} 新开仓被压到 {w:.0%}')
            else:
                notes.append(f'{symbol} 单股仓位被压到 {w:.0%}')
        if w > 0:
            cleaned.append({**x, 'target_weight': w})
        if len(cleaned) >= CONFIG.max_positions_d:
            break

    total = sum(x['target_weight'] for x in cleaned)
    if total > CONFIG.max_total_weight_d and total > 0:
        scale = CONFIG.max_total_weight_d / total
        for x in cleaned:
            x['target_weight'] = round(x['target_weight'] * scale, 6)
        notes.append(f'组合总仓位按比例压到 {CONFIG.max_total_weight_d:.0%}')
    return cleaned, notes
