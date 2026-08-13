from __future__ import annotations
import argparse
import json
import os
from datetime import date
from pathlib import Path
from .broker import execute_target_weights
from .demo import generate_history, NAMES
from .pipeline import build_candidates, market_temperature, targets_for
from .reporting import mark_to_market, metrics
from .state import load_state, save_state

ROOT=Path(__file__).resolve().parents[1]
STATE_ROOT=Path(os.getenv('FUND_STATE_DIR', str(ROOT/'state')))
FUNDS={
    'D_MAIN':'AI 综合判断基金',
    'A':'保守稳健基金',
    'B':'趋势追强基金',
    'C':'短线快攻基金',
    'D':'综合判断基金',
    'L':'长线价值基金',
}


def enrich_real_candidates(candidates, snapshot_rows):
    smap={x['code']:x for x in snapshot_rows}
    for c in candidates:
        s=smap.get(c['symbol'],{})
        pe=float(s.get('peTTM') or 0); pb=float(s.get('pbMRQ') or 0)
        if pe>0 and pb>0:
            val=max(0,min(100,100 - max(0,pe-8)*1.15 - max(0,pb-1)*4.5))
        else:
            val=45.0
        c['valuation']=round(val,2)
        c['peTTM']=round(pe,2); c['pbMRQ']=round(pb,2)
        c['score_d']=round(.30*c['trend']+.20*c['quality']+.20*c['momentum']+.15*c['valuation']+.15*c['risk'],2)
    candidates.sort(key=lambda x:x['score_d'],reverse=True)
    return candidates


def _previous_trade_date(histories: dict[str,list[dict]], trade_date: str) -> str | None:
    dates={
        str(row.get('date',''))[:10]
        for rows in histories.values()
        for row in rows
        if str(row.get('date',''))[:10] and str(row.get('date',''))[:10] < trade_date
    }
    return max(dates) if dates else None


def _pending_decision_date(state: dict) -> str | None:
    explicit=state.get('pending_decision_date')
    if explicit:
        return str(explicit)[:10]
    pending=state.get('pending_targets') or []
    for d in reversed(state.get('decisions',[]) or []):
        if d.get('targets') == pending and d.get('date'):
            return str(d['date'])[:10]
    decisions=state.get('decisions') or []
    if decisions and decisions[-1].get('date'):
        return str(decisions[-1]['date'])[:10]
    return None


def _pending_is_fresh(state: dict, previous_trade_date: str | None) -> bool:
    if not state.get('pending_targets') or not previous_trade_date:
        return False
    return _pending_decision_date(state) == previous_trade_date


def run_all(trade_date: str, histories, names, bars, use_ai=True, snapshot_rows=None):
    candidates=build_candidates(histories,names)
    if snapshot_rows is not None:
        candidates=enrich_real_candidates(candidates,snapshot_rows)
    mscore=market_temperature(candidates)
    previous_trade_date=_previous_trade_date(histories,trade_date)
    snapshots={}
    for fid,name in FUNDS.items():
        sid='D' if fid=='D_MAIN' else fid
        path=STATE_ROOT/f'{fid}.json'
        st=load_state(path,fid,name)
        st['name']=name
        if st.get('last_processed_date') == trade_date:
            print(f'[skip] {fid} already processed {trade_date}')
            mtm={'equity':st['equity_curve'][-1]['equity'] if st['equity_curve'] else st['cash'],'holdings':[]}
            snapshots[fid]={'state':st,'mtm':mtm,'metrics':metrics(st['equity_curve'],st['initial_cash']),'diary':'当日已处理'}
            save_state(path,st)
            continue
        if st.get('pending_targets'):
            if _pending_is_fresh(st,previous_trade_date):
                fills=execute_target_weights(st,st['pending_targets'],bars,trade_date)
                st['fills'].extend(fills)
            else:
                decision_date=_pending_decision_date(st)
                print(f'[expire] {fid} pending targets from {decision_date} not valid for {trade_date}; previous trade date={previous_trade_date}')
                st['pending_targets']=[]
            st.pop('pending_decision_date',None)
        mtm=mark_to_market(st,bars,trade_date)
        targets,diary=targets_for(sid,candidates,mscore,st,use_ai=(use_ai and sid=='D'))
        st['pending_targets']=targets
        st['pending_decision_date']=trade_date
        st['decisions'].append({'date':trade_date,'market_score':mscore,'targets':targets,'diary':diary})
        st['last_processed_date']=trade_date
        save_state(path,st)
        snapshots[fid]={'state':st,'mtm':mtm,'metrics':metrics(st['equity_curve'],st['initial_cash']),'diary':diary}
    return candidates,mscore,snapshots


def run_demo(trade_date: str):
    histories=generate_history(110)
    bars={sym:rows[-1] for sym,rows in histories.items()}
    return run_all(trade_date,histories,NAMES,bars,use_ai=False)


def run_real(requested_date: str):
    from .real_market import AKShareMarket
    market=AKShareMarket()
    trade_date=market.latest_trade_date(requested_date)
    snapshot=market.snapshot()
    selected=market.preselect(snapshot)
    print(f'[market] trade_date={trade_date} mainboard_liquid={len(snapshot)} preselected={len(selected)}')
    histories=market.histories(selected,trade_date)
    names={x['code']:x['name'] for x in snapshot}
    bars={x['code']:x for x in snapshot}
    candidates,mscore,snapshots=run_all(trade_date,histories,names,bars,use_ai=True,snapshot_rows=snapshot)
    first_dates=[x['state']['equity_curve'][0]['date'] for x in snapshots.values() if x['state'].get('equity_curve')]
    start_date=min(first_dates) if first_dates else trade_date
    benchmarks=market.benchmarks(start_date,trade_date)
    return trade_date,candidates,mscore,snapshots,benchmarks


def _activity(state: dict, trade_date: str) -> dict:
    today=[x for x in state.get('fills',[]) if x.get('trade_date')==trade_date]
    buys=[x for x in today if x.get('side')=='BUY']
    sells=[x for x in today if x.get('side')=='SELL']
    return {
        'buy_count':len(buys), 'sell_count':len(sells),
        'buy_amount':round(sum(float(x.get('gross',0)) for x in buys),2),
        'sell_amount':round(sum(float(x.get('gross',0)) for x in sells),2),
        'pending_count':len(state.get('pending_targets',[])),
    }


def _benchmark_lookup(benchmarks, name='沪深300'):
    return next((x for x in (benchmarks or []) if x.get('name')==name),None)


def _score(met: dict, excess):
    if met.get('trading_days',0) < 20:
        return None
    ex=float(excess or 0)
    return round(float(met.get('return_pct',0)) + 1.2*ex - 0.45*abs(float(met.get('max_drawdown_pct',0))) - 0.05*float(met.get('volatility_pct',0)),2)


def export_web(trade_date: str,candidates,mscore,s,benchmarks=None):
    hs300=_benchmark_lookup(benchmarks)
    d=s['D_MAIN']; st=d['state']; mtm=d['mtm']; met=d['metrics']
    if not mtm.get('holdings'):
        mtm['holdings']=[]
    excess=round(met['return_pct']-hs300['return_pct'],2) if hs300 and hs300.get('return_pct') is not None else None
    d_json={
      'mode': 'REAL' if getattr(export_web, '_real_mode', False) else 'DEMO',
      'updated_at':trade_date,'market_score':mscore,
      'market_label':'强势' if mscore>=80 else '偏强' if mscore>=60 else '震荡' if mscore>=40 else '偏弱' if mscore>=20 else '高风险',
      'fund':{'name':st['name'],'initial':st['initial_cash'],'equity':mtm['equity'],'cash':st['cash'],'position_pct':round((mtm['equity']-st['cash'])/mtm['equity']*100,1) if mtm['equity'] else 0},
      'metrics':{**met,'week_pct':met.get('return_5d_pct'),'win_rate_pct':0,'profit_factor':0,'trades':len(st['fills']),'excess_hs300_pct':excess},
      'activity':_activity(st,trade_date),
      'holdings':[{**x,'weight':round(x['market_value']/mtm['equity']*100,1),'score':next((c['score_d'] for c in candidates if c['symbol']==x['symbol']),0)} for x in mtm['holdings']],
      'decisions':[{'action':'待执行','name':x.get('name',x['symbol']),'symbol':x['symbol'],'weight':f"{x['target_weight']*100:.1f}%",'reason':x.get('reason','组合目标仓位'),'timing':'下一交易日开盘模拟执行'} for x in st.get('pending_targets',[])],
      'recent_fills':st.get('fills',[])[-10:],
      'benchmark':hs300,
      'candidates':candidates[:10], 'equity_curve':st['equity_curve'], 'diary':d['diary'],
      'plain_explanation':'不押单一风格：趋势、公司质量、估值、动量和风险一起看，再由 AI 决定组合。'}
    (ROOT/'web/d/data.json').write_text(json.dumps(d_json,ensure_ascii=False,indent=2),encoding='utf-8')

    funds=[]
    risk_map={'A':'低','B':'高','C':'很高','D':'中','L':'中低'}
    style_map={'A':'保守','B':'追强','C':'短线','D':'综合','L':'长线'}
    desc_map={
        'A':'少折腾，先控制亏损和回撤，再考虑赚钱。',
        'B':'专找最近明显走强的股票，顺势买入；转弱就换。',
        'C':'偏热点、动量和活跃股，通常几天级别，换手最快。',
        'D':'趋势、公司、估值、动量和风险都看，属于综合均衡型。',
        'L':'更看重估值和公司质量，买入后倾向拿得更久。',
    }
    for fid in ('A','B','C','D','L'):
        x=s[fid]; st2=x['state']; mtm2=x['mtm']; met2=x['metrics']
        bx=round(met2['return_pct']-hs300['return_pct'],2) if hs300 and hs300.get('return_pct') is not None else None
        funds.append({
            'id':fid,'name':st2['name'],'style':style_map[fid],'description':desc_map[fid],
            'equity':mtm2['equity'],'return_pct':met2['return_pct'],
            'today_pct':met2.get('today_pct',0),'return_5d_pct':met2.get('return_5d_pct'),
            'return_20d_pct':met2.get('return_20d_pct'),'return_60d_pct':met2.get('return_60d_pct'),
            'max_drawdown_pct':met2['max_drawdown_pct'],'volatility_pct':met2.get('volatility_pct',0),
            'trading_days':met2.get('trading_days',0),'losing_streak_days':met2.get('losing_streak_days',0),
            'health':met2.get('health','观察期'),'health_reason':met2.get('health_reason',''),
            'excess_hs300_pct':bx,'composite_score':_score(met2,bx),
            'risk':risk_map[fid],'trades':len(st2.get('fills',[])),'curve':st2['equity_curve'],
            'activity':_activity(st2,trade_date),
            'holdings':mtm2.get('holdings',[]),
            'pending_targets':st2.get('pending_targets',[]),
            'recent_fills':st2.get('fills',[])[-8:],
            'diary':x.get('diary',''),
        })
    default_b=[{'name':'沪深300','return_pct':None,'curve':[]},{'name':'中证500','return_pct':None,'curve':[]},{'name':'中证1000','return_pct':None,'curve':[]}]
    bs=benchmarks or default_b
    available=[f for f in funds if f.get('return_5d_pct') is not None]
    if available:
        best=max(available,key=lambda x:x['return_5d_pct'])
        worst=min(available,key=lambda x:x['return_5d_pct'])
        weekly=f"近5个交易日最好：{best['name']} {best['return_5d_pct']:+.2f}%；最弱：{worst['name']} {worst['return_5d_pct']:+.2f}%。"
    else:
        weekly='运行不足6个交易日，周战报将在样本够后自动生成。'
    e_json={
        'mode':'REAL' if getattr(export_web,'_real_mode',False) else 'DEMO',
        'updated_at':trade_date,'experiment_days':max((f['trading_days'] for f in funds),default=0),
        'funds':funds,'benchmarks':bs,'weekly_report':weekly,
        'evaluation_note':'至少看20/60个交易日；若连续几个月跑输并出现明显负收益，系统会直接标记为长期表现差。'
    }
    (ROOT/'web/e/data.json').write_text(json.dumps(e_json,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--date',default=date.today().isoformat())
    mode=ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--demo',action='store_true')
    mode.add_argument('--real',action='store_true')
    args=ap.parse_args()
    if args.demo:
        export_web._real_mode=False
        c,m,s=run_demo(args.date); td=args.date; b=None
    else:
        export_web._real_mode=True
        td,c,m,s,b=run_real(args.date)
    export_web(td,c,m,s,b)
    print(f'updated D/E web snapshots for {td}, market_score={m}')

if __name__=='__main__':
    main()
