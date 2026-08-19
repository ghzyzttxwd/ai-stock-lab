from __future__ import annotations

from datetime import date


PLAN_VERSION = 'conditional-plan-v1'
EXECUTION_MODEL = 'CONDITIONAL_PLAN_V1'


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_conditional_target(target: dict) -> bool:
    plan = target.get('trade_plan') or {}
    return plan.get('plan_version') == PLAN_VERSION and bool(plan.get('entry')) and bool(plan.get('exit'))


def pending_is_conditional(targets: list[dict] | None) -> bool:
    rows = list(targets or [])
    return bool(rows) and all(is_conditional_target(x) for x in rows)


def opportunity_score(candidate: dict) -> float:
    """Score the next 1-3 session opportunity, not the past 20/60-day beauty contest."""
    rs1 = _f(candidate.get('market_relative_1'))
    rs3 = _f(candidate.get('market_relative_3'))
    rs5 = _f(candidate.get('market_relative_5'))
    close_pos = _f(candidate.get('close_position'), 0.5)
    amount_ratio = _f(candidate.get('amount_ratio_3_20'), 1.0)
    risk = _f(candidate.get('risk'), 50.0)
    overheat = _f(candidate.get('overheat_score'), 0.0)
    r1 = _f(candidate.get('r1'))

    score = (
        50.0
        + 520.0 * rs1
        + 260.0 * rs3
        + 120.0 * rs5
        + (close_pos - 0.5) * 13.0
        + _clamp((amount_ratio - 1.0) * 8.0, -8.0, 12.0)
        + (risk - 50.0) * 0.12
        - overheat * 0.32
    )
    # A large one-day collapse is not a "cheap entry" signal by itself.
    if r1 <= -0.045:
        score -= 12.0
    return round(_clamp(score, 0.0, 100.0), 2)


def _setup_for(fund_id: str, candidate: dict) -> str:
    fid = 'D' if fund_id == 'D_MAIN' else fund_id
    rs3 = _f(candidate.get('market_relative_3'))
    close_pos = _f(candidate.get('close_position'), 0.5)
    amount_ratio = _f(candidate.get('amount_ratio_3_20'), 1.0)
    overheat = _f(candidate.get('overheat_score'))

    if fid in {'B', 'C'}:
        return 'breakout'
    if fid in {'A', 'L'}:
        return 'pullback'
    if rs3 >= 0.012 and close_pos >= 0.68 and amount_ratio >= 1.05 and overheat < 45:
        return 'breakout'
    if close_pos <= 0.48 or overheat >= 35:
        return 'pullback'
    return 'range'


def _threshold_for(fund_id: str) -> float:
    fid = 'D' if fund_id == 'D_MAIN' else fund_id
    return {
        'A': 52.0,
        'B': 57.0,
        'C': 61.0,
        'D': 55.0,
        'L': 50.0,
    }.get(fid, 55.0)


def _max_hold_days(fund_id: str) -> int:
    fid = 'D' if fund_id == 'D_MAIN' else fund_id
    return {'A': 4, 'B': 3, 'C': 2, 'D': 3, 'L': 5}.get(fid, 3)


def _build_plan(fund_id: str, candidate: dict, trade_date: str, opp: float) -> dict:
    close = _f(candidate.get('close'))
    if close <= 0:
        raise ValueError('candidate close must be positive')
    setup = _setup_for(fund_id, candidate)
    atr_pct = _clamp(_f(candidate.get('atr14_pct'), 0.03), 0.012, 0.07)
    ma5 = _f(candidate.get('ma5'), close) or close
    high3 = _f(candidate.get('recent_high_3'), close) or close

    if setup == 'breakout':
        trigger = max(close * 1.004, high3 * 1.001)
        max_chase = trigger * (1.0 + _clamp(atr_pct * 0.38, 0.008, 0.018))
        entry = {
            'mode': 'breakout',
            'operator': '>=',
            'trigger_price': round(trigger, 3),
            'valid_min': round(trigger, 3),
            'valid_max': round(max_chase, 3),
            'explanation': '达到突破确认价才买；跳空超过最高追价则放弃',
        }
        estimate = trigger
    elif setup == 'pullback':
        trigger = min(close * 0.994, ma5 * 1.002)
        floor = trigger * (1.0 - _clamp(atr_pct * 0.75, 0.018, 0.04))
        entry = {
            'mode': 'pullback',
            'operator': '<=',
            'trigger_price': round(trigger, 3),
            'valid_min': round(floor, 3),
            'valid_max': round(trigger, 3),
            'explanation': '回踩到计划价才买；直接跌穿有效区间则取消',
        }
        estimate = trigger
    else:
        half_width = _clamp(atr_pct * 0.25, 0.006, 0.013)
        lower = close * (1.0 - half_width)
        upper = close * (1.0 + _clamp(atr_pct * 0.12, 0.003, 0.006))
        entry = {
            'mode': 'range',
            'operator': 'inside',
            'trigger_price': round(upper, 3),
            'valid_min': round(lower, 3),
            'valid_max': round(upper, 3),
            'explanation': '只在计划价格区间内买，不追高也不接失控下跌',
        }
        estimate = (lower + upper) / 2.0

    risk_pct = _clamp(atr_pct * 1.15, 0.025, 0.055)
    reward_risk = 1.8 if setup == 'breakout' else 1.65
    trailing = _clamp(atr_pct * 0.75, 0.018, 0.035)
    stop_est = estimate * (1.0 - risk_pct)
    take_est = estimate * (1.0 + risk_pct * reward_risk)
    return {
        'plan_version': PLAN_VERSION,
        'decision_date': trade_date,
        'setup': setup,
        'opportunity_score': round(opp, 2),
        'entry': entry,
        'exit': {
            'hard_stop_pct': round(risk_pct, 5),
            'reward_risk': reward_risk,
            'trailing_activation_rr': 1.0,
            'trailing_drawdown_pct': round(trailing, 5),
            'max_hold_days': _max_hold_days(fund_id),
            'estimated_stop_price': round(stop_est, 3),
            'estimated_take_profit_price': round(take_est, 3),
        },
        'cancel_if_not_triggered_by_close': True,
        'estimated_entry_price': round(estimate, 3),
    }


def build_conditional_targets(
    fund_id: str,
    targets: list[dict],
    candidates: list[dict],
    trade_date: str,
    state: dict | None = None,
) -> list[dict]:
    """Attach executable price conditions and allow cash when no setup is good enough."""
    cmap = {str(x.get('symbol') or ''): x for x in candidates}
    current = set(((state or {}).get('positions') or {}).keys())
    threshold = _threshold_for(fund_id)
    result = []
    for target in targets:
        symbol = str(target.get('symbol') or '')
        candidate = cmap.get(symbol)
        if not candidate:
            continue
        opp = _f(candidate.get('opportunity_score'), opportunity_score(candidate))
        # Existing holdings get a small continuity allowance to avoid pointless churn.
        required = threshold - (6.0 if symbol in current else 0.0)
        if opp < required:
            continue
        plan = _build_plan(fund_id, candidate, trade_date, opp)
        result.append({
            **target,
            'trade_plan': plan,
            'opportunity_score': round(opp, 2),
            'setup': plan['setup'],
            'reason': target.get('reason') or (
                f"{plan['setup']}条件计划；机会分{opp:.1f}，未触发则保持现金"
            ),
        })
    return result


def build_exit_plan_from_fill(fill_price: float, trade_plan: dict, trade_date: str) -> dict:
    exit_spec = dict((trade_plan or {}).get('exit') or {})
    risk_pct = _clamp(_f(exit_spec.get('hard_stop_pct'), 0.035), 0.015, 0.08)
    rr = max(1.5, _f(exit_spec.get('reward_risk'), 1.65))
    trailing = _clamp(_f(exit_spec.get('trailing_drawdown_pct'), 0.025), 0.012, 0.06)
    return {
        'plan_version': PLAN_VERSION,
        'opened_date': trade_date,
        'entry_price': round(fill_price, 4),
        'hard_stop_price': round(fill_price * (1.0 - risk_pct), 3),
        'take_profit_price': round(fill_price * (1.0 + risk_pct * rr), 3),
        'trailing_activation_price': round(fill_price * (1.0 + risk_pct), 3),
        'trailing_drawdown_pct': round(trailing, 5),
        'highest_price': round(fill_price, 4),
        'partial_taken': False,
        'max_hold_days': int(exit_spec.get('max_hold_days') or 3),
        'sessions_held': 0,
        'rotation_exit': False,
        'rotation_min_price': None,
        'setup': (trade_plan or {}).get('setup'),
    }


def refresh_exit_plans(state: dict, targets: list[dict], candidates: list[dict], trade_date: str) -> None:
    """Maintain protective exits for holdings and mark obsolete holdings for conditional rotation."""
    plans = state.setdefault('exit_plans', {})
    positions = state.get('positions') or {}
    target_map = {x.get('symbol'): x for x in targets}
    candidate_map = {x.get('symbol'): x for x in candidates}

    for symbol in list(plans):
        if symbol not in positions:
            plans.pop(symbol, None)

    for symbol, position in positions.items():
        last = _f((candidate_map.get(symbol) or {}).get('close'), _f(position.get('last_price'), _f(position.get('avg_cost'))))
        avg = _f(position.get('avg_cost'), last)
        acquired = str(position.get('acquired_date') or '')[:10]
        plan = plans.get(symbol)
        if not plan:
            # Migration/default protection: do not rewrite historical cost, only create future exit rules.
            anchor = last if last > 0 else avg
            risk_pct = 0.045
            plan = {
                'plan_version': PLAN_VERSION,
                'opened_date': acquired or trade_date,
                'entry_price': round(avg, 4),
                'hard_stop_price': round(anchor * (1.0 - risk_pct), 3),
                'take_profit_price': round(max(avg * 1.02, anchor * 1.035), 3),
                'trailing_activation_price': round(max(avg * 1.01, anchor * 1.02), 3),
                'trailing_drawdown_pct': 0.025,
                'highest_price': round(max(anchor, avg), 4),
                'partial_taken': False,
                'max_hold_days': 3,
                'sessions_held': 0,
                'rotation_exit': False,
                'rotation_min_price': None,
                'setup': 'legacy-migrated',
            }
            plans[symbol] = plan

        if acquired and acquired < trade_date:
            plan['sessions_held'] = int(plan.get('sessions_held') or 0) + 1

        if symbol in target_map:
            plan['rotation_exit'] = False
            plan['rotation_min_price'] = None
            tp = target_map[symbol].get('trade_plan') or {}
            if tp:
                plan['setup'] = tp.get('setup', plan.get('setup'))
                plan['max_hold_days'] = int((tp.get('exit') or {}).get('max_hold_days') or plan.get('max_hold_days') or 3)
        else:
            # The new decision no longer wants this name. Seek a small rebound, but hard stop remains superior.
            anchor = last if last > 0 else avg
            plan['rotation_exit'] = True
            if anchor > 0:
                plan['rotation_min_price'] = round(min(max(avg * 0.997, anchor * 1.003), anchor * 1.012), 3)


def days_between(start: str, end: str) -> int:
    try:
        return max(0, (date.fromisoformat(end) - date.fromisoformat(start)).days)
    except Exception:
        return 0
