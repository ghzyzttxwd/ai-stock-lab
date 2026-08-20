from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / 'state'
WEB_ROOT = ROOT / 'web'
FUND_IDS = ('D_MAIN', 'A', 'B', 'C', 'D', 'L')


def _date(value) -> str:
    return str(value or '')[:10]


def assert_v1_freshness(
    expected_date: str,
    state_root: Path = STATE_ROOT,
    web_root: Path = WEB_ROOT,
) -> dict:
    """Fail closed unless every persisted V1 ledger and public snapshot is current.

    This invariant is intentionally stronger than hash equality: a stale repository snapshot
    must never be accepted merely because GitHub Pages serves the same stale bytes.
    """
    errors: list[str] = []
    fund_dates: dict[str, dict[str, str]] = {}

    for fid in FUND_IDS:
        path = state_root / f'{fid}.json'
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'{fid}: unreadable state: {exc}')
            continue

        processed = _date(state.get('last_processed_date'))
        planned = _date(state.get('conditional_plan_date'))
        fund_dates[fid] = {
            'last_processed_date': processed,
            'conditional_plan_date': planned,
        }
        if processed != expected_date:
            errors.append(
                f'{fid}: last_processed_date={processed or "missing"}, expected={expected_date}'
            )
        if planned != expected_date:
            errors.append(
                f'{fid}: conditional_plan_date={planned or "missing"}, expected={expected_date}'
            )

    web_dates: dict[str, str] = {}
    for page in ('d', 'e'):
        path = web_root / page / 'data.json'
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'web/{page}/data.json: unreadable: {exc}')
            continue
        updated = _date(payload.get('updated_at'))
        web_dates[page] = updated
        if updated != expected_date:
            errors.append(
                f'web/{page}/data.json: updated_at={updated or "missing"}, expected={expected_date}'
            )

    if errors:
        raise RuntimeError('V1 freshness invariant failed:\n- ' + '\n- '.join(errors))

    result = {
        'expected_date': expected_date,
        'fund_dates': fund_dates,
        'web_dates': web_dates,
    }
    print(f'[v1-freshness] PASS expected_date={expected_date} funds={len(FUND_IDS)} web=d,e')
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True, help='Expected exchange session date, YYYY-MM-DD')
    args = parser.parse_args()
    assert_v1_freshness(args.date)


if __name__ == '__main__':
    main()
