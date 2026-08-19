from __future__ import annotations
from statistics import median

from .indicators import score_history
from .risk import clamp_d_targets
from .strategies import strategy_a, strategy_b, strategy_c, strategy_d, strategy_l
from .ai_manager import decide_with_api
from .trading_plan import opportunity_score
from .universe import is_main_board


def _cap(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def build_candidates(histories: dict[str, list[dict]], names: dict[str,str] | None=None) -> list[dict]:
    names = names or {}
    out=[]
    for sym, rows in histories.items():
        # Recovery mode can overlay old holdings/pending symbols outside the normal universe.
        # Never let those symbols re-enter strategy selection for this retail account.
        if not is_main_board(sym):
            continue
        sc=score_history(rows)
        if not sc.get('eligible'):
            continue
        last=rows[-1]
        if str(last.get('isST','0')) == '1' or str(last.get('tradestatus','1')) != '1':
            continue
        sc.update({'symbol':sym,'name':names.get(sym,last.get('name',sym))})
        sc['valuation']=50.0
        out.append(sc)

    if not out:
        return []

    # Cross-sectional main-board medians are a robust market proxy when the benchmark provider is unavailable.
    med1=median([float(x.get('r1') or 0.0) for x in out])
    med3=median([float(x.get('r3') or 0.0) for x in out])
    med5=median([float(x.get('r5') or 0.0) for x in out])
    for sc in out:
        sc['market_relative_1']=round(float(sc.get('r1') or 0.0)-med1,6)
        sc['market_relative_3']=round(float(sc.get('r3') or 0.0)-med3,6)
        sc['market_relative_5']=round(float(sc.get('r5') or 0.0)-med5,6)
        sc['opportunity_score']=opportunity_score(sc)
        sc['score_d']=round(
            0.30*sc['opportunity_score'] +
            0.16*sc['trend'] +
            0.14*sc['momentum'] +
            0.12*sc['quality'] +
            0.13*sc['valuation'] +
            0.15*sc['risk'],
            2,
        )
    return sorted(out,key=lambda x:x['score_d'],reverse=True)


def market_temperature(candidates: list[dict]) -> float:
    if not candidates:
        return 0.0
    top=candidates[:min(120,len(candidates))]
    breadth1=sum(1 for x in top if float(x.get('r1') or 0.0)>0)/len(top)
    breadth5=sum(1 for x in top if float(x.get('r5') or 0.0)>0)/len(top)
    avg_opp=sum(float(x.get('opportunity_score') or 0.0) for x in top)/len(top)
    # A strong market lifts the ceiling; it never forces the portfolio to fill that ceiling.
    return _cap(0.35*avg_opp + 35*breadth1 + 30*breadth5)


def targets_for(fund_id: str, candidates: list[dict], market_score: float, state: dict, use_ai=True):
    # Defense in depth: callers/tests/manual recovery data cannot feed ChiNext/STAR names into
    # any target generator even if they bypass build_candidates().
    candidates=[x for x in candidates if is_main_board(str(x.get('symbol') or ''))]
    if fund_id == 'A':
        return strategy_a(candidates,market_score), '稳健规则策略A：短周期机会先过关，再强调风险和流动性'
    if fund_id == 'B':
        return strategy_b(candidates,market_score), '趋势规则策略B：相对强势、突破质量和短周期动量优先'
    if fund_id == 'C':
        return strategy_c(candidates,market_score), '短线规则策略C：1-3日相对强弱/量价/过热惩罚优先'
    if fund_id == 'L':
        return strategy_l(candidates,market_score,state), '长线规则策略L：估值/风险/质量优先，买点仍需条件触发'
    if fund_id == 'D':
        if use_ai:
            ai = decide_with_api(candidates, {'cash':state.get('cash'), 'positions':state.get('positions',{})}, market_score)
            if ai and isinstance(ai.get('targets'),list):
                allowed={x['symbol'] for x in candidates}
                proposed=[x for x in ai['targets'] if x.get('symbol') in allowed]
                rejected=len(ai['targets'])-len(proposed)
                clean, notes=clamp_d_targets(proposed,state)
                if rejected:
                    notes.append(f'拒绝 {rejected} 个候选池外代码')
                return clean, ai.get('diary','AI综合决策') + (('；'+'；'.join(notes)) if notes else '')
            clean, notes=clamp_d_targets(strategy_d(candidates,market_score),state)
            return clean, 'AI调用失败，已启用短周期D规则兜底' + (('；'+'；'.join(notes)) if notes else '')
        clean, notes=clamp_d_targets(strategy_d(candidates,market_score),state)
        return clean, 'D短周期规则' + (('；'+'；'.join(notes)) if notes else '')
    raise ValueError(f'unknown fund {fund_id}')
