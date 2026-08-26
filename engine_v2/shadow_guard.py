from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .shadow_ledger import FUND_NAMES, ledger_content_hash, sha256_json


_NON_TERMINAL_ACCOUNTING_KINDS = {
    'execution_catchup',
    'conditional_exit_scan',
    'morning_sell',
}


def _valid_event(path: Path) -> dict | None:
    try:
        event = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    claimed = event.get('event_hash')
    if not claimed:
        return None
    body = dict(event)
    body.pop('event_hash', None)
    if sha256_json(body) != claimed:
        return None
    return event


def _event_for_hash(state_root: Path, event_hash: str) -> tuple[Path, dict] | None:
    for path in (state_root / 'audit').glob('*.json'):
        event = _valid_event(path)
        if event and event.get('event_hash') == event_hash:
            return path, event
    return None


def processed_session(state_root: Path, trade_date: str) -> dict | None:
    """Return a completed session only when the persisted ledgers prove its terminal event.

    Completion is derived from the aligned ledger head and that event's content hashes.  This
    intentionally supports append-only correction/restatement events instead of hard-coding one
    correction filename.  Execution-only/checkpoint events can never masquerade as a full day.
    """
    audit_path = state_root / 'audit' / f'{trade_date}.json'
    audit = _valid_event(audit_path) if audit_path.exists() else None
    if not audit or str(audit.get('trade_date') or '')[:10] != trade_date:
        return None

    states: dict[str, dict] = {}
    heads = set()
    for fund_id in FUND_NAMES:
        path = state_root / 'ledgers' / f'{fund_id}.json'
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        if str(state.get('last_processed_date') or '')[:10] != trade_date:
            return None
        head = state.get('audit_head')
        if not head:
            return None
        states[fund_id] = state
        heads.add(head)

    if len(heads) != 1:
        return None
    expected_head = next(iter(heads))
    terminal_match = _event_for_hash(state_root, expected_head)
    if not terminal_match:
        return None
    terminal_path, terminal = terminal_match
    if str(terminal.get('trade_date') or '')[:10] != trade_date:
        return None
    if terminal.get('event_kind') in _NON_TERMINAL_ACCOUNTING_KINDS:
        return None

    for fund_id, state in states.items():
        expected_content = ((terminal.get('funds') or {}).get(fund_id) or {}).get('closing_ledger_content_sha256')
        if not expected_content or expected_content != ledger_content_hash(state):
            return None

    base_event_hash = audit.get('event_hash')
    corrected = expected_head != base_event_hash
    return {
        'status': 'already_processed',
        'trade_date': trade_date,
        'event_hash': expected_head,
        'base_event_hash': base_event_hash,
        'correction_event_hash': expected_head if corrected else None,
        'terminal_event_kind': terminal.get('event_kind'),
        'audit_path': str(terminal_path if corrected else audit_path),
        'safety': terminal.get('safety') or {},
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
    state_root = Path(args.state_root)
    reset_path = state_root / 'reset_epoch.json'
    report = None
    if reset_path.exists():
        reset = json.loads(reset_path.read_text(encoding='utf-8'))
        reset_date = str(reset.get('reset_date') or '')[:10]
        if reset_date and args.trade_date < reset_date:
            heads = {
                json.loads((state_root / 'ledgers' / f'{fund_id}.json').read_text(encoding='utf-8')).get('audit_head')
                for fund_id in FUND_NAMES
            }
            if len(heads) != 1 or None in heads:
                raise RuntimeError('V2 reset guard: ledgers do not share the paper_reset audit head')
            report = {
                'status': 'before_reset_noop',
                'trade_date': args.trade_date,
                'reset_date': reset_date,
                'event_hash': next(iter(heads)),
                'audit_path': str(state_root / 'audit' / f'{reset_date}~paper-reset.json'),
                'fills': {},
                'rejected_orders': {},
                'concentration_flags': {},
                'missing_critical_execution_bars': [],
                'safety': {
                    'calls_sol': False,
                    'reads_v1_ledger': False,
                    'writes_v1_ledger': False,
                    'state_root': 'shadow_state/v2',
                    'paper_reset': True,
                    'retroactive_fills_forbidden': True,
                },
            }
    if report is None:
        report = processed_session(state_root, args.trade_date)
    done = report is not None
    _github_output('done', str(done).lower())
    if report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'trade_date': args.trade_date, 'already_processed': done}, ensure_ascii=False))


if __name__ == '__main__':
    main()
