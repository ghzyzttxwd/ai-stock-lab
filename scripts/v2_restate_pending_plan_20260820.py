from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--v2-root', required=True)
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--enriched', required=True)
    ap.add_argument('--artifact-id', required=True)
    ap.add_argument('--artifact-name', required=True)
    ap.add_argument('--report', required=True)
    args = ap.parse_args()

    repo = Path(args.v2_root).resolve()
    sys.path.insert(0, str(repo))

    from engine_v2.conditional_plan import EXECUTION_MODEL, PLAN_VERSION
    from engine_v2.shadow_guard import processed_session
    from engine_v2.shadow_ledger import (
        FUND_NAMES,
        build_pending_decision,
        canonical_json,
        immutable_write,
        ledger_content_hash,
        save_ledger,
        sha256_json,
        validate_ledger,
    )
    from engine_v2.shadow_reporting import verify_audit_chain
    from engine_v2.targets import build_shadow_targets

    td = '2026-08-20'
    root = repo / 'shadow_state' / 'v2'
    base_path = root / 'audit' / f'{td}~conditional-execution-restatement.json'
    correction_path = root / 'audit' / f'{td}~conditional-plan-restatement.json'
    bad_catchup_hash = 'ec7b6cda2f19d1b66ab42f916ee5aff07c8f890f77e2b8aafbe0dea9431ac1c7'

    def load_valid(path: Path) -> dict:
        event = json.loads(path.read_text(encoding='utf-8'))
        body = dict(event)
        claimed = body.pop('event_hash', None)
        if not claimed or sha256_json(body) != claimed:
            raise SystemExit(f'invalid audit hash: {path}')
        return event

    base = load_valid(base_path)
    if base.get('event_kind') != 'conditional_execution_restatement':
        raise SystemExit('unexpected base restatement event kind')
    if base.get('corrects_event_hash') != bad_catchup_hash:
        raise SystemExit('base restatement does not correct the known invalid catch-up')
    base_hash = base['event_hash']

    snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    enriched = json.loads(Path(args.enriched).read_text(encoding='utf-8'))
    if str(snapshot.get('trade_date') or '')[:10] != td or str(enriched.get('trade_date') or '')[:10] != td:
        raise SystemExit('artifact does not contain exact 2026-08-20 inputs')

    source_ref = base.get('source_ref') or {}
    expected_snapshot_hash = source_ref.get('snapshot_sha256')
    expected_enrichment_hash = source_ref.get('enrichment_sha256')
    actual_snapshot_hash = sha256_json(snapshot)
    actual_enrichment_hash = sha256_json(enriched)
    if actual_snapshot_hash != expected_snapshot_hash:
        raise SystemExit(
            f'artifact snapshot hash mismatch: {actual_snapshot_hash} != {expected_snapshot_hash}'
        )
    if actual_enrichment_hash != expected_enrichment_hash:
        raise SystemExit(
            f'artifact enrichment hash mismatch: {actual_enrichment_hash} != {expected_enrichment_hash}'
        )

    drawdowns = {
        fid: float(((base.get('funds') or {}).get(fid) or {}).get('drawdown') or 0.0)
        for fid in FUND_NAMES
    }
    targets_payload = build_shadow_targets(enriched, fund_drawdowns=drawdowns)
    if targets_payload.get('plan_version') != PLAN_VERSION:
        raise SystemExit('rebuilt targets are not conditional-plan-v1')
    if not ((targets_payload.get('safety') or {}).get('targets_valid')):
        raise SystemExit(f'rebuilt targets failed validation: {targets_payload.get("validation_errors")}')

    diagnostics = base.get('target_diagnostics') or {}
    for key in ('stats', 'overlap_jaccard', 'high_overlap_pairs', 'concentration_flags', 'board_policy'):
        if canonical_json(targets_payload.get(key)) != canonical_json(diagnostics.get(key)):
            raise SystemExit(f'historical target reproduction mismatch in {key}')

    current_states = {
        fid: json.loads((root / 'ledgers' / f'{fid}.json').read_text(encoding='utf-8'))
        for fid in FUND_NAMES
    }
    heads = {state.get('audit_head') for state in current_states.values()}
    if len(heads) != 1:
        raise SystemExit(f'ledger heads are not aligned: {heads}')
    current_head = next(iter(heads))

    def require_pending_semantics(state: dict, fid: str) -> None:
        pending = state.get('pending_decision')
        if not isinstance(pending, dict) or str(pending.get('decision_date') or '')[:10] != td:
            raise SystemExit(f'{fid}: expected 2026-08-20 pending decision is missing')
        if pending.get('execution_model') != EXECUTION_MODEL or pending.get('plan_version') != PLAN_VERSION:
            raise SystemExit(f'{fid}: outer pending model/version mismatch')
        for target in pending.get('targets') or []:
            plan = target.get('trade_plan') or {}
            if plan.get('plan_version') != PLAN_VERSION:
                raise SystemExit(f'{fid}: target {target.get("symbol")} still lacks a conditional trade plan')
            if str(plan.get('decision_date') or '')[:10] != td:
                raise SystemExit(f'{fid}: target {target.get("symbol")} trade-plan date mismatch')
            if not plan.get('entry') or not plan.get('exit'):
                raise SystemExit(f'{fid}: target {target.get("symbol")} trade plan is incomplete')

    if correction_path.exists():
        correction = load_valid(correction_path)
        if current_head != correction.get('event_hash'):
            raise SystemExit('existing pending-plan restatement is not the current ledger head')
        for fid, state in current_states.items():
            require_pending_semantics(state, fid)
        result = verify_audit_chain(root)
        if result.get('status') != 'PASS':
            raise SystemExit(f'audit chain failed after existing pending-plan restatement: {result}')
        report = {
            'status': 'already_corrected',
            'trade_date': td,
            'event_hash': correction['event_hash'],
            'base_event_hash': base_hash,
            'artifact_id': args.artifact_id,
            'artifact_name': args.artifact_name,
            'snapshot_sha256': actual_snapshot_hash,
            'enrichment_sha256': actual_enrichment_hash,
            'audit_head': result['head'],
            'audit_events': result['events'],
        }
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if current_head != base_hash:
        raise SystemExit(f'ledger head moved unexpectedly before pending-plan restatement: {current_head}')

    corrected_states: dict[str, dict] = {}
    fund_events: dict[str, dict] = {}

    for fid, state in current_states.items():
        if str(state.get('last_processed_date') or '')[:10] != td:
            raise SystemExit(f'{fid}: last_processed_date mismatch')
        if str(state.get('last_execution_date') or '')[:10] != td:
            raise SystemExit(f'{fid}: last_execution_date mismatch')
        if float(state.get('cash') or 0.0) < -1e-6:
            raise SystemExit(f'{fid}: negative cash before pending-plan repair')
        for fill in state.get('fills') or []:
            if str(fill.get('trade_date') or '')[:10] == td and fill.get('note') == 'V2 目标仓位再平衡':
                raise SystemExit(f'{fid}: invalid legacy catch-up economics survived before plan repair')

        old_pending = state.get('pending_decision')
        if not isinstance(old_pending, dict) or str(old_pending.get('decision_date') or '')[:10] != td:
            raise SystemExit(f'{fid}: current next-session decision missing')
        if old_pending.get('execution_model') != EXECUTION_MODEL or old_pending.get('plan_version') != PLAN_VERSION:
            raise SystemExit(f'{fid}: current outer pending model/version mismatch')

        rebuilt = build_pending_decision(fid, targets_payload, old_pending.get('source_ref') or source_ref)
        rebuilt['execute_on'] = 'next_session_conditional_entry_and_exit_checks'
        rebuilt['execution_model'] = EXECUTION_MODEL
        rebuilt['plan_version'] = PLAN_VERSION

        old_targets = list(old_pending.get('targets') or [])
        new_targets = list(rebuilt.get('targets') or [])
        if len(old_targets) != len(new_targets):
            raise SystemExit(f'{fid}: target count changed during historical reproduction')
        new_by_symbol = {x.get('symbol'): x for x in new_targets}
        for old in old_targets:
            symbol = old.get('symbol')
            new = new_by_symbol.get(symbol)
            if not new:
                raise SystemExit(f'{fid}: historical target disappeared: {symbol}')
            for key, value in old.items():
                if key in {'trade_plan', 'opportunity_score', 'setup'}:
                    continue
                if canonical_json(new.get(key)) != canonical_json(value):
                    raise SystemExit(f'{fid}: target {symbol} changed field {key}')
            plan = new.get('trade_plan') or {}
            if plan.get('plan_version') != PLAN_VERSION or str(plan.get('decision_date') or '')[:10] != td:
                raise SystemExit(f'{fid}: rebuilt target {symbol} has invalid conditional plan')

        before_economics = {
            k: copy.deepcopy(v)
            for k, v in state.items()
            if k not in {'audit_head', 'pending_decision', 'decisions'}
        }
        old_pending_hash = sha256_json(old_pending)
        state['pending_decision'] = rebuilt

        matching = [
            item for item in (state.get('decisions') or [])
            if str(item.get('decision_date') or '')[:10] == td
        ]
        if len(matching) != 1:
            raise SystemExit(f'{fid}: expected exactly one 2026-08-20 decision history entry, got {len(matching)}')
        matching[0]['targets'] = copy.deepcopy(rebuilt['targets'])
        matching[0]['execution_model'] = EXECUTION_MODEL
        matching[0]['plan_version'] = PLAN_VERSION
        matching[0]['allow_cash'] = True

        after_economics = {
            k: copy.deepcopy(v)
            for k, v in state.items()
            if k not in {'audit_head', 'pending_decision', 'decisions'}
        }
        if canonical_json(before_economics) != canonical_json(after_economics):
            raise SystemExit(f'{fid}: economic state changed during pending-plan-only restatement')

        validate_ledger(state)
        corrected_states[fid] = state
        fund_events[fid] = {
            'previous_pending_decision_sha256': old_pending_hash,
            'corrected_pending_decision_sha256': sha256_json(rebuilt),
            'target_count': len(rebuilt.get('targets') or []),
            'economics_unchanged': True,
            'closing_ledger_content_sha256': ledger_content_hash(state),
        }

    event_source = copy.deepcopy(source_ref)
    event_source['pending_plan_restatement'] = {
        'reason': 'build_pending_decision compacted away per-target conditional trade_plan metadata',
        'base_execution_restatement_hash': base_hash,
        'artifact_id': str(args.artifact_id),
        'artifact_name': args.artifact_name,
        'snapshot_sha256': actual_snapshot_hash,
        'enrichment_sha256': actual_enrichment_hash,
        'target_payload_sha256': sha256_json(targets_payload),
        'historical_target_diagnostics_exact_match': True,
        'economics_unchanged': True,
        'history_preserved_append_only': True,
    }

    event = {
        'schema_version': base.get('schema_version'),
        'event_kind': 'conditional_plan_restatement',
        'trade_date': td,
        'previous_trade_date': base.get('previous_trade_date'),
        'execution_policy_version': base.get('execution_policy_version'),
        'source_ref': event_source,
        'regime': copy.deepcopy(base.get('regime')),
        'target_diagnostics': copy.deepcopy(base.get('target_diagnostics')),
        'previous_event_hashes': {fid: base_hash for fid in FUND_NAMES},
        'funds': fund_events,
        'supersedes_event_hash': base_hash,
        'corrects_pending_plan_loss': True,
        'safety': {
            'calls_sol': False,
            'reads_v1_ledger': False,
            'writes_v1_ledger': False,
            'forced_clock_buy': False,
            'forced_clock_sell': False,
            'changes_economic_history': False,
            'pending_plan_only': True,
            'repairs_missing_trade_plan': True,
            'history_preserved_append_only': True,
            'state_root': 'shadow_state/v2',
        },
    }
    event_hash = sha256_json(event)
    event['event_hash'] = event_hash
    immutable_write(correction_path, event)

    for fid, state in corrected_states.items():
        state['audit_head'] = event_hash
        save_ledger(root / 'ledgers' / f'{fid}.json', state)

    result = verify_audit_chain(root)
    if result.get('status') != 'PASS' or result.get('head') != event_hash:
        raise SystemExit(f'corrected audit chain failed: {result}')
    session = processed_session(root, td)
    if not session or session.get('event_hash') != event_hash or session.get('terminal_event_kind') != 'conditional_plan_restatement':
        raise SystemExit(f'corrected session is not terminally recognized: {session}')

    for fid, state in corrected_states.items():
        require_pending_semantics(state, fid)
        expected = ((event.get('funds') or {}).get(fid) or {}).get('closing_ledger_content_sha256')
        if ledger_content_hash(state) != expected:
            raise SystemExit(f'{fid}: closing ledger hash mismatch after pending-plan restatement')

    report = {
        'status': 'corrected',
        'trade_date': td,
        'event_hash': event_hash,
        'base_event_hash': base_hash,
        'artifact_id': args.artifact_id,
        'artifact_name': args.artifact_name,
        'snapshot_sha256': actual_snapshot_hash,
        'enrichment_sha256': actual_enrichment_hash,
        'target_payload_sha256': sha256_json(targets_payload),
        'audit_head': result['head'],
        'audit_events': result['events'],
        'economics_unchanged': True,
        'history_preserved_append_only': True,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
