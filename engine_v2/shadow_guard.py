from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .shadow_ledger import FUND_NAMES


def processed_session(state_root: Path, trade_date: str) -> dict | None:
    audit_path = state_root / 'audit' / f'{trade_date}.json'
    if not audit_path.exists():
        return None
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    event_hash = audit.get('event_hash')
    if not event_hash:
        return None
    for fund_id in FUND_NAMES:
        path = state_root / 'ledgers' / f'{fund_id}.json'
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        if str(state.get('last_processed_date') or '')[:10] != trade_date:
            return None
        if state.get('audit_head') != event_hash:
            return None
    return {
        'status': 'already_processed', 'trade_date': trade_date,
        'event_hash': event_hash, 'audit_path': str(audit_path),
        'safety': audit.get('safety') or {},
    }


def _github_output(key: str, value: str) -> None:
    path = os.getenv('GITHUB_OUTPUT')
    if path:
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(f'{key}={value}\n')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-root', default='shadow_state/v2')
    parser.add_argument('--trade-date', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    report = processed_session(Path(args.state_root), args.trade_date)
    done = report is not None
    _github_output('done', str(done).lower())
    if report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'trade_date': args.trade_date, 'already_processed': done}, ensure_ascii=False))


if __name__ == '__main__':
    main()
