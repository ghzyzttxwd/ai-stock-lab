from __future__ import annotations

import argparse
import json
from pathlib import Path

from .v1_production_gate import assert_v1_production_gate

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / 'state'
WEB_ROOT = ROOT / 'web'
FUND_IDS = ('D_MAIN', 'A', 'B', 'C', 'D', 'L')
EXPECTED_MODEL = 'CONDITIONAL_PLAN_V1'
EXPECTED_PLAN = 'conditional-plan-v1'


def _date(value) -> str:
    return str(value or '')[:10]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def assert_v1_regression_smoke(
    state_root: Path = STATE_ROOT,
    web_root: Path = WEB_ROOT,
) -> dict:
    """Validate persisted V1 safely both after settlement and during an intraday refresh.

    The strict production gate remains authoritative for a settled snapshot. During the next
    session's intraday exit scans, public data is intentionally dated today while
    last_processed_date/conditional_plan_date remain the most recent completed settlement.
    In that state require all ledgers to agree on the same settlement date and same current
    conditional scan, and require both public artifacts to agree on the intraday date/model.
    """
    states = {fid: _load(state_root / f'{fid}.json') for fid in FUND_IDS}
    settled_dates = {_date(st.get('last_processed_date')) for st in states.values()}
    plan_dates = {_date(st.get('conditional_plan_date')) for st in states.values()}

    web = {page: _load(web_root / page / 'data.json') for page in ('d', 'e')}
    web_dates = {_date(payload.get('updated_at')) for payload in web.values()}
    if len(web_dates) != 1 or '' in web_dates:
        raise RuntimeError(f'V1 regression smoke: D/E updated_at disagree: {sorted(web_dates)}')
    web_date = next(iter(web_dates))

    for page, payload in web.items():
        if payload.get('execution_model') != EXPECTED_MODEL:
            raise RuntimeError(
                f'V1 regression smoke: web/{page} execution_model={payload.get("execution_model")!r}'
            )
        if payload.get('plan_version') != EXPECTED_PLAN:
            raise RuntimeError(
                f'V1 regression smoke: web/{page} plan_version={payload.get("plan_version")!r}'
            )

    # A reset epoch deliberately has no settled or plan date until the first
    # post-reset close. Treat it as a distinct production state and prove that
    # no retired experiment state leaked into the new ledgers.
    reset_path = state_root / 'paper_reset_epoch.json'
    if settled_dates == {''} and plan_dates == {''} and reset_path.exists():
        reset = _load(reset_path)
        reset_date = _date(reset.get('reset_date'))
        initial_cash = float(reset.get('initial_cash_per_fund') or reset.get('initial_cash') or 0.0)
        if not reset_date or web_date != reset_date:
            raise RuntimeError(
                f'V1 regression smoke: reset date {reset_date!r} does not match public date {web_date!r}'
            )
        if abs(initial_cash - 1_000_000.0) > 1e-6:
            raise RuntimeError(f'V1 regression smoke: invalid reset initial_cash {initial_cash}')
        for fid, st in states.items():
            if st.get('execution_model') != EXPECTED_MODEL:
                raise RuntimeError(
                    f'V1 regression smoke: {fid} execution_model={st.get("execution_model")!r}'
                )
            if abs(float(st.get('initial_cash') or 0.0) - initial_cash) > 1e-6:
                raise RuntimeError(f'V1 regression smoke: {fid} initial cash differs from reset epoch')
            if abs(float(st.get('cash') or 0.0) - initial_cash) > 1e-6:
                raise RuntimeError(f'V1 regression smoke: {fid} cash is not reset-clean')
            for field in ('positions', 'fills', 'equity_curve', 'pending_targets', 'decisions'):
                if st.get(field):
                    raise RuntimeError(f'V1 regression smoke: {fid} reset leaked non-empty {field}')
        print(
            f'[v1-regression-smoke] PASS mode=paper_reset date={reset_date} '
            f'funds={len(FUND_IDS)}'
        )
        return {
            'mode': 'paper_reset',
            'reset_date': reset_date,
            'web_date': web_date,
        }

    if len(settled_dates) != 1 or '' in settled_dates:
        raise RuntimeError(f'V1 regression smoke: inconsistent last_processed_date values: {sorted(settled_dates)}')
    if len(plan_dates) != 1 or '' in plan_dates:
        raise RuntimeError(f'V1 regression smoke: inconsistent conditional_plan_date values: {sorted(plan_dates)}')

    settled_date = next(iter(settled_dates))
    plan_date = next(iter(plan_dates))
    if plan_date != settled_date:
        raise RuntimeError(
            f'V1 regression smoke: conditional plan date {plan_date} differs from settled date {settled_date}'
        )

    if web_date == settled_date:
        assert_v1_production_gate(settled_date, state_root, web_root)
        return {
            'mode': 'settled',
            'settled_date': settled_date,
            'web_date': web_date,
        }

    if web_date < settled_date:
        raise RuntimeError(
            f'V1 regression smoke: public data regressed to {web_date} behind settled date {settled_date}'
        )

    scan_keys = {str(st.get('last_conditional_scan_key') or '') for st in states.values()}
    if len(scan_keys) != 1 or '' in scan_keys:
        raise RuntimeError(f'V1 regression smoke: intraday scan keys disagree: {sorted(scan_keys)}')
    scan_key = next(iter(scan_keys))
    scan_date = _date(scan_key)
    if scan_date != web_date:
        raise RuntimeError(
            f'V1 regression smoke: intraday web date {web_date} is not backed by ledger scan {scan_key}'
        )

    for fid, st in states.items():
        if st.get('execution_model') != EXPECTED_MODEL:
            raise RuntimeError(
                f'V1 regression smoke: {fid} execution_model={st.get("execution_model")!r}'
            )
        if float(st.get('cash', 0.0)) < -1e-6:
            raise RuntimeError(f'V1 regression smoke: {fid} has negative cash {st.get("cash")}')
        for target in st.get('pending_targets') or []:
            plan = target.get('trade_plan') or {}
            if plan.get('plan_version') != EXPECTED_PLAN:
                raise RuntimeError(
                    f'V1 regression smoke: {fid} has pending target without current conditional plan'
                )
        for symbol, plan in (st.get('exit_plans') or {}).items():
            if plan.get('plan_version') != EXPECTED_PLAN:
                raise RuntimeError(
                    f'V1 regression smoke: {fid} exit plan {symbol} has wrong plan version'
                )

    print(
        f'[v1-regression-smoke] PASS mode=intraday settled={settled_date} '
        f'web={web_date} scan={scan_key} funds={len(FUND_IDS)}'
    )
    return {
        'mode': 'intraday',
        'settled_date': settled_date,
        'web_date': web_date,
        'scan_key': scan_key,
    }


def main() -> None:
    argparse.ArgumentParser().parse_args()
    assert_v1_regression_smoke()


if __name__ == '__main__':
    main()
