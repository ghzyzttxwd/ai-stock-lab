from __future__ import annotations
from .indicators import score_history
from .risk import clamp_d_targets
from .strategies import strategy_a, strategy_b, strategy_d
from .ai_manager import decide_with_api


def build_candidates(histories: dict[str, list[dict]], names: dict[str,str] | None=None) -> list[dict]:
    names = names or {}
    out=[]
    for sym, rows in histories.items():
        sc=score_history(rows)
        if not sc.get('eligible'):
            continue
        last=rows[-1]
        if str(last.get('isST','0')) == '1' or str(last.get('tradestatus','1')) != '1':
            continue
        sc.update({'symbol':sym,'name':names.get(sym,last.get('name',sym))})
        sc['quality']=70.0
        sc['valuation']=70.0
        sc['score_d']=round(.30*sc['trend']+.20*sc['quality']+.20*sc['momentum']+.15*sc['valuation']+.15*sc['risk'],2)
        out.append(sc)
    return sorted(out,key=lambda x:x['score_d'],reverse=True)


def market_temperature(candidates: list[dict]) -> float:
    if not candidates:
        return 0.0
    top=candidates[:min(100,len(candidates))]
    breadth=sum(1 for x in top if x.get('r20',0)>0)/len(top)
    avg_trend=sum(x['trend'] for x in top)/len(top)
    return round(max(0,min(100,0.55*avg_trend+45*breadth)),2)


def targets_for(fund_id: str, candidates: list[dict], market_score: float, state: dict, use_ai=True):
    if fund_id == 'A':
        return strategy_a(candidates,market_score), '规则策略A'
    if fund_id == 'B':
        return strategy_b(candidates,market_score), '规则策略B'
    if fund_id == 'D':
        if use_ai:
            ai = decide_with_api(candidates, {'cash':state.get('cash'), 'positions':state.get('positions',{})}, market_score)
            if ai and isinstance(ai.get('targets'),list):
                allowed={x['symbol'] for x in candidates}
                proposed=[x for x in ai['targets'] if x.get('symbol') in allowed]
                rejected=len(ai['targets'])-len(proposed)
                clean, notes=clamp_d_targets(proposed)
                if rejected:
                    notes.append(f'拒绝 {rejected} 个候选池外代码')
                return clean, ai.get('diary','AI综合决策') + (('；'+'；'.join(notes)) if notes else '')
        clean, notes=clamp_d_targets(strategy_d(candidates,market_score))
        return clean, 'D规则兜底' + (('；'+'；'.join(notes)) if notes else '')
    raise ValueError(f'unknown fund {fund_id}')
