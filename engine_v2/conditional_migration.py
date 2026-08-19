from __future__ import annotations

import argparse
import json
from pathlib import Path

from .conditional_plan import (
    EXECUTION_MODEL,
    PLAN_VERSION,
    attach_plans,
    pending_is_conditional,
    refresh_rotation_flags,
)
from .shadow_ledger import (
    AUDIT_SCHEMA_VERSION,
    EXECUTION_POLICY_VERSION,
    FUND_NAMES,
    immutable_write,
    ledger_content_hash,
    load_ledger,
    save_ledger,
    sha256_json,
    validate_ledger,
)


def _already_migrated(state_root: Path, trade_date: str, audit_path: Path) -> dict | None:
    if not audit_path.exists():
        return None
    event=json.loads(audit_path.read_text(encoding='utf-8'))
    event_hash=event.get('event_hash')
    for fund_id in FUND_NAMES:
        state=load_ledger(state_root/'ledgers'/f'{fund_id}.json',fund_id,trade_date)
        if state.get('audit_head')!=event_hash:
            raise RuntimeError(f'conditional migration head mismatch for {fund_id}')
        expected=((event.get('funds') or {}).get(fund_id) or {}).get('closing_ledger_content_sha256')
        if ledger_content_hash(state)!=expected:
            raise RuntimeError(f'conditional migration content mismatch for {fund_id}')
    return {'status':'already_migrated','trade_date':trade_date,'event_hash':event_hash,'audit_path':str(audit_path)}


def migrate_current_session(state_root: Path, trade_date: str) -> dict:
    """Replace only future pending instructions; fills, cash, positions and equity history are preserved."""
    audit_path=state_root/'audit'/f'{trade_date}~conditional-plan-migration.json'
    existing=_already_migrated(state_root,trade_date,audit_path)
    if existing:
        return existing

    states={
        fund_id:load_ledger(state_root/'ledgers'/f'{fund_id}.json',fund_id,trade_date)
        for fund_id in FUND_NAMES
    }
    dates={str(state.get('last_processed_date') or '')[:10] for state in states.values()}
    if dates!={trade_date}:
        raise RuntimeError(f'V2 migration requires all ledgers processed on {trade_date}, got {dates}')
    heads={state.get('audit_head') for state in states.values()}
    if len(heads)!=1:
        raise RuntimeError(f'V2 migration requires aligned audit heads, got {heads}')

    previous_heads={fund_id:state.get('audit_head') for fund_id,state in states.items()}
    opening_hashes={fund_id:ledger_content_hash(state) for fund_id,state in states.items()}
    fund_events={}
    total_old=0; total_new=0

    for fund_id,state in states.items():
        pending=state.get('pending_decision')
        old_targets=list((pending or {}).get('targets') or [])
        total_old+=len(old_targets)
        if pending:
            if pending.get('plan_version')==PLAN_VERSION or pending_is_conditional(pending):
                new_targets=old_targets
            else:
                new_targets=attach_plans(fund_id,old_targets,trade_date)
            pending['targets']=new_targets
            pending['plan_version']=PLAN_VERSION
            pending['execution_model']=EXECUTION_MODEL
            pending['execute_on']='next_session_conditional_entry_and_exit_checks'
            pending.setdefault('source_ref',{})['conditional_plan_migration']=True
        else:
            new_targets=[]
        total_new+=len(new_targets)
        refresh_rotation_flags(state,new_targets,trade_date)
        state['execution_model']=EXECUTION_MODEL
        state['plan_version']=PLAN_VERSION
        state['conditional_plan_migrated_on']=trade_date
        validate_ledger(state)
        fund_events[fund_id]={
            'opening_ledger_content_sha256':opening_hashes[fund_id],
            'old_pending_targets':len(old_targets),
            'new_conditional_targets':len(new_targets),
            'cash_before_after':float(state.get('cash') or 0.0),
            'positions_count':len(state.get('positions') or {}),
            'fills_count':len(state.get('fills') or []),
            'equity_curve_points':len(state.get('equity_curve') or []),
            'closing_ledger_content_sha256':ledger_content_hash(state),
        }

    event={
        'schema_version':AUDIT_SCHEMA_VERSION,
        'event_kind':'conditional_plan_migration',
        'trade_date':trade_date,
        'execution_policy_version':EXECUTION_POLICY_VERSION,
        'source_ref':{
            'reason':'replace future fixed-price pending instructions with pre-declared conditional plans',
            'execution_model':EXECUTION_MODEL,
            'plan_version':PLAN_VERSION,
            'economics_mutated':False,
            'historical_fills_rewritten':False,
            'note':'Cash, existing positions, fill history and equity history are preserved; only future plan metadata and protective exit plans are added.',
        },
        'regime':None,
        'target_diagnostics':None,
        'previous_event_hashes':previous_heads,
        'funds':fund_events,
        'safety':{
            'calls_sol':False,'reads_v1_ledger':False,'writes_v1_ledger':False,
            'executes_orders':False,'rewrites_historical_fills':False,'state_root':'shadow_state/v2',
        },
    }
    event_hash=sha256_json(event); event['event_hash']=event_hash
    for state in states.values():
        state['audit_head']=event_hash
    immutable_write(audit_path,event)
    for fund_id,state in states.items():
        save_ledger(state_root/'ledgers'/f'{fund_id}.json',state)
    return {
        'status':'migrated','trade_date':trade_date,'event_hash':event_hash,'audit_path':str(audit_path),
        'old_pending_targets':total_old,'new_conditional_targets':total_new,
        'execution_model':EXECUTION_MODEL,'plan_version':PLAN_VERSION,'safety':event['safety'],
    }


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',required=True)
    parser.add_argument('--state-root',default='shadow_state/v2')
    parser.add_argument('--report',required=True)
    args=parser.parse_args()
    report=migrate_current_session(Path(args.state_root),str(args.date)[:10])
    Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
