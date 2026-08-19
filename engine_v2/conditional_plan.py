from __future__ import annotations


PLAN_VERSION = 'v2-conditional-plan-v1'
EXECUTION_MODEL = 'V2_CONDITIONAL_PLAN_V1'


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def short_opportunity(candidate: dict) -> float:
    momentum=_f(candidate.get('momentum'),50)
    breakout=_f(candidate.get('breakout_quality'),50)
    industry=_f(candidate.get('industry_score'),50)
    leader=_f(candidate.get('leader_score'),50)
    risk=_f(candidate.get('risk'),50)
    liquidity=_f(candidate.get('liquidity'),50)
    crowd=_f(candidate.get('crowding_score'),50)
    extension=max(0.0,_f(candidate.get('extension20'))-0.12)
    score=(
        0.28*momentum+0.20*breakout+0.15*industry+0.10*leader+
        0.12*risk+0.10*liquidity+0.05*_f(candidate.get('trend'),50)
        -max(0.0,crowd-72.0)*0.32-extension*120.0
    )
    return round(_clamp(score,0,100),2)


def _setup(fund_id: str, candidate: dict) -> str:
    if fund_id in {'B','C'}:
        return 'breakout'
    if fund_id in {'A','L'}:
        return 'pullback'
    if _f(candidate.get('breakout_quality'),50)>=65 and _f(candidate.get('crowding_score'),50)<72:
        return 'breakout'
    if _f(candidate.get('extension20'))>0.08 or _f(candidate.get('gap'))>0.025:
        return 'pullback'
    return 'range'


def _threshold(fund_id: str) -> float:
    return {'A':50,'B':56,'C':59,'D':54,'L':48}.get(fund_id,54)


def _max_hold(fund_id: str) -> int:
    return {'A':4,'B':3,'C':2,'D':3,'L':5}.get(fund_id,3)


def build_plan(fund_id: str, candidate: dict, trade_date: str) -> dict | None:
    close=_f(candidate.get('close'))
    if close<=0:
        return None
    opp=short_opportunity(candidate)
    if opp<_threshold(fund_id):
        return None
    setup=_setup(fund_id,candidate)
    range20=_clamp(_f(candidate.get('range20'),0.03),0.012,0.075)
    high20_distance=_f(candidate.get('high20_distance'),-0.02)

    if setup=='breakout':
        # high20_distance = close/high20 - 1. A small negative distance means the breakout level is nearby.
        high20=close/(1.0+high20_distance) if (1.0+high20_distance)>0 else close
        trigger=max(close*1.004,high20*1.001)
        upper=trigger*(1.0+_clamp(range20*0.35,0.008,0.018))
        entry={'mode':'breakout','operator':'>=','trigger_price':round(trigger,3),'valid_min':round(trigger,3),'valid_max':round(upper,3)}
        estimate=trigger
    elif setup=='pullback':
        ma10=_f(candidate.get('ma10'),close) or close
        trigger=min(close*0.994,ma10*1.003)
        lower=trigger*(1.0-_clamp(range20*0.8,0.018,0.045))
        entry={'mode':'pullback','operator':'<=','trigger_price':round(trigger,3),'valid_min':round(lower,3),'valid_max':round(trigger,3)}
        estimate=trigger
    else:
        lower=close*(1.0-_clamp(range20*0.25,0.006,0.014))
        upper=close*(1.0+_clamp(range20*0.12,0.003,0.007))
        entry={'mode':'range','operator':'inside','trigger_price':round(upper,3),'valid_min':round(lower,3),'valid_max':round(upper,3)}
        estimate=(lower+upper)/2

    risk_pct=_clamp(range20*1.15,0.025,0.06)
    rr=1.85 if setup=='breakout' else 1.65
    trail=_clamp(range20*0.75,0.018,0.038)
    return {
        'plan_version':PLAN_VERSION,
        'decision_date':trade_date,
        'setup':setup,
        'opportunity_score':opp,
        'entry':entry,
        'exit':{
            'hard_stop_pct':round(risk_pct,5),
            'reward_risk':rr,
            'trailing_drawdown_pct':round(trail,5),
            'max_hold_days':_max_hold(fund_id),
            'estimated_stop_price':round(estimate*(1-risk_pct),3),
            'estimated_take_profit_price':round(estimate*(1+risk_pct*rr),3),
        },
        'cancel_if_not_triggered_by_close':True,
    }


def attach_plans(fund_id: str, targets: list[dict], trade_date: str) -> list[dict]:
    result=[]
    for target in targets:
        plan=build_plan(fund_id,target,trade_date)
        if not plan:
            continue
        result.append({**target,'trade_plan':plan,'opportunity_score':plan['opportunity_score'],'setup':plan['setup']})
    return result


def pending_is_conditional(pending: dict | None) -> bool:
    if not pending:
        return False
    targets=list(pending.get('targets') or [])
    return bool(targets) and all(((x.get('trade_plan') or {}).get('plan_version')==PLAN_VERSION) for x in targets)


def build_exit_plan(entry_price: float, trade_plan: dict, trade_date: str) -> dict:
    spec=dict((trade_plan or {}).get('exit') or {})
    risk=_clamp(_f(spec.get('hard_stop_pct'),0.035),0.015,0.08)
    rr=max(1.5,_f(spec.get('reward_risk'),1.65))
    trail=_clamp(_f(spec.get('trailing_drawdown_pct'),0.025),0.012,0.06)
    return {
        'plan_version':PLAN_VERSION,'opened_date':trade_date,'entry_price':round(entry_price,4),
        'hard_stop_price':round(entry_price*(1-risk),3),
        'take_profit_price':round(entry_price*(1+risk*rr),3),
        'trailing_activation_price':round(entry_price*(1+risk),3),
        'trailing_drawdown_pct':round(trail,5),'highest_price':round(entry_price,4),
        'partial_taken':False,'max_hold_days':int(spec.get('max_hold_days') or 3),'sessions_held':0,
        'rotation_exit':False,'rotation_min_price':None,'setup':trade_plan.get('setup'),
    }


def ensure_exit_plans(state: dict, trade_date: str) -> None:
    plans=state.setdefault('exit_plans',{})
    positions=state.get('positions') or {}
    for symbol in list(plans):
        if symbol not in positions:
            plans.pop(symbol,None)
    for symbol,pos in positions.items():
        if symbol in plans and plans[symbol].get('plan_version')==PLAN_VERSION:
            continue
        last=_f(pos.get('last_price'),_f(pos.get('avg_cost')))
        avg=_f(pos.get('avg_cost'),last)
        anchor=last or avg
        plans[symbol]={
            'plan_version':PLAN_VERSION,'opened_date':str(pos.get('opened_date') or pos.get('acquired_date') or trade_date)[:10],
            'entry_price':round(avg,4),'hard_stop_price':round(anchor*0.955,3),
            'take_profit_price':round(max(avg*1.02,anchor*1.035),3),
            'trailing_activation_price':round(max(avg*1.01,anchor*1.02),3),
            'trailing_drawdown_pct':0.025,'highest_price':round(max(anchor,avg),4),
            'partial_taken':False,'max_hold_days':3,'sessions_held':0,
            'rotation_exit':False,'rotation_min_price':None,'setup':'legacy-migrated',
        }


def refresh_rotation_flags(state: dict, next_targets: list[dict], trade_date: str) -> None:
    ensure_exit_plans(state,trade_date)
    wanted={str(x.get('symbol') or '') for x in next_targets}
    for symbol,pos in (state.get('positions') or {}).items():
        plan=state['exit_plans'][symbol]
        acquired=str(pos.get('acquired_date') or '')[:10]
        if acquired and acquired<trade_date:
            plan['sessions_held']=int(plan.get('sessions_held') or 0)+1
        if symbol in wanted:
            plan['rotation_exit']=False; plan['rotation_min_price']=None
        else:
            last=_f(pos.get('last_price'),_f(pos.get('avg_cost')))
            avg=_f(pos.get('avg_cost'),last)
            plan['rotation_exit']=True
            if last>0:
                plan['rotation_min_price']=round(min(max(avg*0.997,last*1.003),last*1.012),3)
