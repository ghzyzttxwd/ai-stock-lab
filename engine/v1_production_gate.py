from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .universe import is_main_board
from .v1_freshness import FUND_IDS, STATE_ROOT, WEB_ROOT, assert_v1_freshness

EXPECTED_EXECUTION_MODEL = 'CONDITIONAL_PLAN_V1'
EXPECTED_PLAN_VERSION = 'conditional-plan-v1'


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def assert_v1_production_gate(
    expected_date: str,
    state_root: Path = STATE_ROOT,
    web_root: Path = WEB_ROOT,
) -> dict:
    """Validate settlement outputs that are safety-critical before persistence.

    Unlike the full unit-test suite, this gate inspects the actual state and web files produced
    by the current settlement run. It deliberately keeps fail-closed invariants close to the
    production data path while code-level regressions run separately in CI.
    """
    freshness = assert_v1_freshness(expected_date, state_root, web_root)
    errors: list[str] = []
    fund_summary: dict[str, dict] = {}

    for fid in FUND_IDS:
        path = state_root / f'{fid}.json'
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'{fid}: unreadable state: {exc}')
            continue

        if state.get('execution_model') != EXPECTED_EXECUTION_MODEL:
            errors.append(
                f'{fid}: execution_model={state.get("execution_model")!r}, '
                f'expected={EXPECTED_EXECUTION_MODEL}'
            )

        cash = _number(state.get('cash'))
        if cash is None:
            errors.append(f'{fid}: cash is not a finite number')
        elif cash < -1e-6:
            errors.append(f'{fid}: negative cash={cash}')

        positions = state.get('positions') or {}
        if not isinstance(positions, dict):
            errors.append(f'{fid}: positions is not an object')
            positions = {}
        for symbol, position in positions.items():
            if not isinstance(position, dict):
                errors.append(f'{fid}: position {symbol} is not an object')
                continue
            qty = _number(position.get('qty'))
            avg_cost = _number(position.get('avg_cost'))
            if qty is None or qty < 0:
                errors.append(f'{fid}: invalid qty for {symbol}: {position.get("qty")!r}')
            if qty is not None and qty > 0 and (avg_cost is None or avg_cost <= 0):
                errors.append(
                    f'{fid}: invalid avg_cost for live position {symbol}: '
                    f'{position.get("avg_cost")!r}'
                )

        pending = state.get('pending_targets') or []
        if not isinstance(pending, list):
            errors.append(f'{fid}: pending_targets is not a list')
            pending = []
        for index, target in enumerate(pending):
            if not isinstance(target, dict):
                errors.append(f'{fid}: pending_targets[{index}] is not an object')
                continue
            symbol = str(target.get('symbol') or '')
            if not is_main_board(symbol):
                errors.append(f'{fid}: pending target outside main-board universe: {symbol or "missing"}')
            trade_plan = target.get('trade_plan') or {}
            if not isinstance(trade_plan, dict) or trade_plan.get('plan_version') != EXPECTED_PLAN_VERSION:
                errors.append(
                    f'{fid}: pending target {symbol or index} has wrong plan_version='
                    f'{trade_plan.get("plan_version") if isinstance(trade_plan, dict) else None!r}'
                )

        fund_summary[fid] = {
            'cash': cash,
            'positions': len(positions),
            'pending_targets': len(pending),
        }

    web_summary: dict[str, dict] = {}
    for page in ('d', 'e'):
        path = web_root / page / 'data.json'
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'web/{page}/data.json: unreadable: {exc}')
            continue
        model = payload.get('execution_model')
        plan_version = payload.get('plan_version')
        if model != EXPECTED_EXECUTION_MODEL:
            errors.append(
                f'web/{page}/data.json: execution_model={model!r}, '
                f'expected={EXPECTED_EXECUTION_MODEL}'
            )
        if plan_version != EXPECTED_PLAN_VERSION:
            errors.append(
                f'web/{page}/data.json: plan_version={plan_version!r}, '
                f'expected={EXPECTED_PLAN_VERSION}'
            )
        web_summary[page] = {
            'execution_model': model,
            'plan_version': plan_version,
        }

    if errors:
        raise RuntimeError('V1 production safety gate failed:\n- ' + '\n- '.join(errors))

    print(
        f'[v1-production-gate] PASS date={expected_date} funds={len(FUND_IDS)} '
        f'web=d,e model={EXPECTED_EXECUTION_MODEL}'
    )
    return {
        'expected_date': expected_date,
        'freshness': freshness,
        'funds': fund_summary,
        'web': web_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True, help='Expected exchange session date, YYYY-MM-DD')
    args = parser.parse_args()
    assert_v1_production_gate(args.date)


if __name__ == '__main__':
    main()
