from __future__ import annotations

import argparse
import copy
import json
import subprocess
from pathlib import Path


TRADE_DATE = '2026-08-19'
V1_BASE_COMMIT = 'f0aa1f1c73f3fa89001cd8bfe4b4cde4ad0b8908'
V2_BASE_COMMIT = 'c5d078a686fb20f6d0eb3769e9e66c9b61ec13d0'
EXECUTION_MODEL = '09:40_SELL_15:10_OPEN_BUY'


def _git_json(ref: str, path: str) -> dict:
    raw = subprocess.check_output(['git', 'show', f'{ref}:{path}'])
    return json.loads(raw.decode('utf-8'))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _critical_from_v1(states: dict[str, dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for state in states.values():
        for symbol, pos in (state.get('positions') or {}).items():
            out[symbol] = pos.get('name') or symbol
        for target in state.get('pending_targets') or []:
            symbol = target.get('symbol')
            if symbol:
                out[symbol] = target.get('name') or symbol
    return out


def _critical_from_v2(states: dict[str, dict]) -> dict[str, str]:
    from engine_v2.shadow_ledger import normalize_symbol

    out: dict[str, str] = {}
    for state in states.values():
        for symbol, pos in (state.get('positions') or {}).items():
            symbol = normalize_symbol(symbol)
            out[symbol] = pos.get('name') or symbol
        pending = state.get('pending_decision') or {}
        for target in pending.get('targets') or []:
            symbol = normalize_symbol(target.get('symbol'))
            if symbol:
                out[symbol] = target.get('name') or symbol
    return out


def _fetch_bars(critical: dict[str, str]) -> dict[str, dict]:
    from engine.real_market import AKShareMarket

    bars = AKShareMarket().execution_bars(critical, TRADE_DATE)
    missing = sorted(set(critical) - set(bars))
    if missing:
        raise RuntimeError(f'correction refused: missing 2026-08-19 execution bars: {missing}')
    bad = sorted(symbol for symbol, bar in bars.items() if float(bar.get('open') or 0.0) <= 0)
    if bad:
        raise RuntimeError(f'correction refused: invalid 2026-08-19 open price: {bad}')
    return bars


def _current_v1_decision(current: dict) -> dict:
    found = [x for x in (current.get('decisions') or []) if str(x.get('date') or '')[:10] == TRADE_DATE]
    if len(found) != 1:
        raise RuntimeError(f'expected exactly one V1 {TRADE_DATE} decision, got {len(found)} for {current.get("fund_id")}')
    return copy.deepcopy(found[0])


def repair_v1(root: Path) -> dict:
    from engine.broker import execute_target_weights
    from engine.daily_run import FUNDS
    from engine.reporting import mark_to_market

    current = {
        fid: json.loads((root / 'state' / f'{fid}.json').read_text(encoding='utf-8'))
        for fid in FUNDS
    }
    base = {
        fid: _git_json(V1_BASE_COMMIT, f'state/{fid}.json')
        for fid in FUNDS
    }
    for fid, state in base.items():
        if str(state.get('morning_sell_date') or '')[:10] != TRADE_DATE:
            raise RuntimeError(f'V1 base is not post-morning-sell for {fid}')
        if str(state.get('pending_decision_date') or '')[:10] != '2026-08-18':
            raise RuntimeError(f'V1 base pending decision is not 2026-08-18 for {fid}')

    bars = _fetch_bars(_critical_from_v1(base))
    report = {'mode': 'v1', 'trade_date': TRADE_DATE, 'funds': {}, 'bar_source': 'tencent-execution'}

    for fid, state in base.items():
        pending = copy.deepcopy(state.get('pending_targets') or [])
        fills = execute_target_weights(
            state,
            pending,
            bars,
            TRADE_DATE,
            sides=('BUY',),
            price_field='open',
            note='上一交易日决策 · 15:10结算买入/加仓（参考当日开盘价）',
        )
        state.setdefault('fills', []).extend(fills)
        mtm = mark_to_market(state, bars, TRADE_DATE)

        cur = current[fid]
        state['pending_targets'] = copy.deepcopy(cur.get('pending_targets') or [])
        state['pending_decision_date'] = TRADE_DATE
        state.setdefault('decisions', []).append(_current_v1_decision(cur))
        state['last_processed_date'] = TRADE_DATE
        state['split_execution_date'] = TRADE_DATE
        state['split_execution_phase'] = '15:10_OPEN_BUY_COMPLETE_CORRECTED'
        state['open_buy_reference'] = 'session_open'
        state['execution_model'] = EXECUTION_MODEL
        state['data_corrections'] = list(cur.get('data_corrections') or []) + [{
            'trade_date': TRADE_DATE,
            'kind': 'BUY_PRICE_BASIS_CORRECTION',
            'from': 'session_close',
            'to': 'session_open',
            'reason': '2026-08-19 close-price BUY settlement was incorrect',
        }]
        _write_json(root / 'state' / f'{fid}.json', state)

        wrong = [
            x for x in state.get('fills') or []
            if x.get('side') == 'BUY' and str(x.get('trade_date') or '')[:10] == TRADE_DATE
            and x.get('execution_price_field') != 'open'
        ]
        if wrong:
            raise RuntimeError(f'V1 correction left non-open BUY fills for {fid}: {wrong}')
        report['funds'][fid] = {
            'buy_fills': len(fills),
            'buy_symbols': [x.get('symbol') for x in fills],
            'buy_prices': {x.get('symbol'): x.get('price') for x in fills},
            'cash': state.get('cash'),
            'equity': mtm.get('equity'),
        }

    _write_json(root / 'state' / '20260819_open_buy_correction_v1.json', report)
    return report


def _current_v2_decision(current: dict) -> dict:
    found = [x for x in (current.get('decisions') or []) if str(x.get('decision_date') or '')[:10] == TRADE_DATE]
    if len(found) != 1:
        raise RuntimeError(f'expected exactly one V2 {TRADE_DATE} decision, got {len(found)} for {current.get("fund_id")}')
    item = copy.deepcopy(found[0])
    item['execution_model'] = EXECUTION_MODEL
    source_ref = dict(item.get('source_ref') or {})
    source_ref['execution_model'] = EXECUTION_MODEL
    item['source_ref'] = source_ref
    return item


def _current_v2_pending(current: dict) -> dict:
    pending = copy.deepcopy(current.get('pending_decision') or {})
    if str(pending.get('decision_date') or '')[:10] != TRADE_DATE:
        raise RuntimeError(f'V2 current pending is not {TRADE_DATE} for {current.get("fund_id")}')
    pending['execute_on'] = 'next_session_0940_sell_1510_open_buy'
    pending['execution_model'] = EXECUTION_MODEL
    source_ref = dict(pending.get('source_ref') or {})
    source_ref['execution_model'] = EXECUTION_MODEL
    pending['source_ref'] = source_ref
    return pending


def repair_v2(root: Path) -> dict:
    from engine_v2.shadow_ledger import (
        AUDIT_SCHEMA_VERSION,
        EXECUTION_POLICY_VERSION,
        FUND_NAMES,
        immutable_write,
        ledger_content_hash,
        mark_to_market,
        save_ledger,
        sha256_json,
        validate_ledger,
    )
    from engine_v2.split_execution import execute_pending_side

    ledger_dir = root / 'shadow_state' / 'v2' / 'ledgers'
    current = {
        fid: json.loads((ledger_dir / f'{fid}.json').read_text(encoding='utf-8'))
        for fid in FUND_NAMES
    }
    base = {
        fid: _git_json(V2_BASE_COMMIT, f'shadow_state/v2/ledgers/{fid}.json')
        for fid in FUND_NAMES
    }
    daily_audit_path = root / 'shadow_state' / 'v2' / 'audit' / f'{TRADE_DATE}.json'
    daily_audit = json.loads(daily_audit_path.read_text(encoding='utf-8'))
    corrected_event_path = root / 'shadow_state' / 'v2' / 'audit' / f'{TRADE_DATE}-buy-price-correction.json'

    for fid, state in base.items():
        if str(state.get('morning_sell_date') or '')[:10] != TRADE_DATE:
            raise RuntimeError(f'V2 base is not post-morning-sell for {fid}')
        pending = state.get('pending_decision') or {}
        if str(pending.get('decision_date') or '')[:10] != '2026-08-18':
            raise RuntimeError(f'V2 base pending decision is not 2026-08-18 for {fid}')

    bars = _fetch_bars(_critical_from_v2(base))
    previous_heads = {fid: current[fid].get('audit_head') for fid in FUND_NAMES}
    opening_hashes = {fid: ledger_content_hash(current[fid]) for fid in FUND_NAMES}
    corrected: dict[str, dict] = {}
    executions: dict[str, dict] = {}
    closes: dict[str, dict] = {}

    for fid, state in base.items():
        pending = copy.deepcopy(state.get('pending_decision') or {})
        execution = execute_pending_side(
            state,
            pending,
            bars,
            TRADE_DATE,
            side='BUY',
            price_field='open',
            note='V2 上一交易日决策 · 15:10结算买入/加仓（参考当日开盘价）',
        )
        close = mark_to_market(state, bars, TRADE_DATE, execution.get('fees', 0.0))
        cur = current[fid]
        state['pending_decision'] = _current_v2_pending(cur)
        state.setdefault('decisions', []).append(_current_v2_decision(cur))
        state['last_processed_date'] = TRADE_DATE
        state['last_execution_date'] = TRADE_DATE
        state['execution_model'] = EXECUTION_MODEL
        state['open_buy_reference'] = 'session_open'
        state['data_corrections'] = list(cur.get('data_corrections') or []) + [{
            'trade_date': TRADE_DATE,
            'kind': 'BUY_PRICE_BASIS_CORRECTION',
            'from': 'session_close',
            'to': 'session_open',
            'reason': '2026-08-19 close-price BUY settlement was incorrect',
        }]
        validate_ledger(state)
        wrong = [
            x for x in state.get('fills') or []
            if x.get('side') == 'BUY' and str(x.get('trade_date') or '')[:10] == TRADE_DATE
            and x.get('execution_price_field') != 'open'
        ]
        if wrong:
            raise RuntimeError(f'V2 correction left non-open BUY fills for {fid}: {wrong}')
        corrected[fid] = state
        executions[fid] = execution
        closes[fid] = close

    event = {
        'schema_version': AUDIT_SCHEMA_VERSION,
        'event_kind': 'buy_price_correction',
        'trade_date': TRADE_DATE,
        'execution_policy_version': EXECUTION_POLICY_VERSION,
        'corrects_event_hash': daily_audit.get('event_hash'),
        'previous_event_hashes': previous_heads,
        'source_ref': {
            'reason': '2026-08-19 BUY legs were incorrectly settled from close prices',
            'old_buy_reference': 'session_close',
            'correct_buy_reference': 'session_open',
            'accounting_time': '15:10 Asia/Shanghai',
            'execution_model': EXECUTION_MODEL,
            'bar_source': 'tencent-execution',
        },
        'funds': {},
        'safety': {
            'calls_sol': False,
            'reads_v1_ledger': False,
            'writes_v1_ledger': False,
            'state_root': 'shadow_state/v2',
        },
    }
    for fid in FUND_NAMES:
        event['funds'][fid] = {
            'opening_ledger_content_sha256': opening_hashes[fid],
            'corrected_execution': executions[fid],
            'corrected_close': closes[fid],
            'closing_ledger_content_sha256': ledger_content_hash(corrected[fid]),
        }
    event_hash = sha256_json(event)
    event['event_hash'] = event_hash
    immutable_write(corrected_event_path, event)

    report = {
        'mode': 'v2',
        'trade_date': TRADE_DATE,
        'status': 'corrected',
        'corrects_event_hash': daily_audit.get('event_hash'),
        'correction_event_hash': event_hash,
        'funds': {},
    }
    for fid, state in corrected.items():
        state['audit_head'] = event_hash
        save_ledger(ledger_dir / f'{fid}.json', state)
        fills = executions[fid].get('fills') or []
        report['funds'][fid] = {
            'buy_fills': len(fills),
            'buy_symbols': [x.get('symbol') for x in fills],
            'buy_prices': {x.get('symbol'): x.get('price') for x in fills},
            'cash': state.get('cash'),
            'equity': closes[fid].get('equity'),
            'ledger_content_sha256': ledger_content_hash(state),
        }

    _write_json(root / 'shadow_state' / 'v2' / '20260819_open_buy_correction.json', report)
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', required=True, choices=('v1', 'v2'))
    ap.add_argument('--root', default='.')
    args = ap.parse_args()
    root = Path(args.root).resolve()
    result = repair_v1(root) if args.mode == 'v1' else repair_v2(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
