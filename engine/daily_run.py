from __future__ import annotations
import argparse
import json
from datetime import date
from pathlib import Path
from .broker import execute_target_weights
from .demo import generate_history, NAMES
from .pipeline import build_candidates, market_temperature, targets_for
from .reporting import mark_to_market, metrics
from .state import load_state, save_state

ROOT=Path(__file__).resolve().parents[1]
FUNDS={
    'D_MAIN':'AI 综合基金 D',
    'A':'稳健基金 A',
    'B':'趋势基金 B',
    'C':'短线基金 C',
    'D':'综合基金 D',
    'L':'长线基金 L',
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


def run_all(trade_date: str, histories, names, bars, use_ai=True, snapshot_rows=None):
    candidates=build_candidates(histories,names)
    if snapshot_rows is not None:
        candidates=enrich_real_candidates(candidates,snapshot_rows)
    mscore=market_temperature(candidates)
    snapshots={}
    for fid,name in FUNDS.items():
        sid='D' if fid=='D_MAIN' else fid
        path=ROOT/'state'/f'{fid}.json'
        st=load_state(path,fid,name)
        if st.get('last_processed_date') == trade_date:
            print(f'[skip] {fid} already processed {trade_date}')
            mtm={'equity':st['equity_curve'][-1]['equity'] if st['equity_curve'] else st['cash'],'holdings':[]}
            snapshots[fid]={'state':st,'mtm':mtm,'metrics':metrics(st['equity_curve'],st['initial_cash']),'diary':'当日已处理'}
            continue
        if st.get('pending_targets'):
            fills=execute_target_weights(st,st['pending_targets'],bars,trade_date)
            st['fills'].extend(fills)
        mtm=mark_to_market(st,bars,trade_date)
        targets,diary=targets_for(sid,candidates,mscore,st,use_ai=(use_ai and sid=='D'))
        st['pending_targets']=targets
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
    return trade_date,*run_all(trade_date,histories,names,bars,use_ai=True,snapshot_rows=snapshot)


def export_web(trade_date: str,candidates,mscore,s,benchmarks=None):
    d=s['D_MAIN']; st=d['state']; mtm=d['mtm']; met=d['metrics']
    if not mtm.get('holdings'):
        mtm['holdings']=[]
    d_json={
      'mode': 'REAL' if getattr(export_web, '_real_mode', False) else 'DEMO', 'updated_at':trade_date,'market_score':mscore,'market_label':'强势' if mscore>=80 else '偏强' if mscore>=60 else '震荡' if mscore>=40 else '偏弱' if mscore>=20 else '高风险',
      'fund':{'name':st['name'],'initial':st['initial_cash'],'equity':mtm['equity'],'cash':st['cash'],'position_pct':round((mtm['equity']-st['cash'])/mtm['equity']*100,1) if mtm['equity'] else 0},
      'metrics':{**met,'today_pct':0,'week_pct':0,'win_rate_pct':0,'profit_factor':0,'trades':len(st['fills'])},
      'holdings':[{**x,'weight':round(x['market_value']/mtm['equity']*100,1),'score':next((c['score_d'] for c in candidates if c['symbol']==x['symbol']),0)} for x in mtm['holdings']],
      'decisions':[{'action':'目标','name':x.get('name',x['symbol']),'symbol':x['symbol'],'weight':f"{x['target_weight']*100:.1f}%",'reason':x.get('reason','组合目标仓位')} for x in st.get('pending_targets',[])],
      'candidates':candidates[:10], 'equity_curve':st['equity_curve'], 'diary':d['diary']}
    (ROOT/'web/d/data.json').write_text(json.dumps(d_json,ensure_ascii=False,indent=2),encoding='utf-8')

    funds=[]
    risk_map={'A':'低','B':'高','C':'很高','D':'中','L':'中低'}
    style_map={'A':'稳健','B':'趋势','C':'短线','D':'综合','L':'长线'}
    for fid in ('A','B','C','D','L'):
        x=s[fid]; st2=x['state']; mtm2=x['mtm']; met2=x['metrics']
        funds.append({
            'id':fid,
            'name':st2['name'],
            'style':style_map[fid],
            'equity':mtm2['equity'],
            'return_pct':met2['return_pct'],
            'max_drawdown_pct':met2['max_drawdown_pct'],
            'risk':risk_map[fid],
            'trades':len(st2.get('fills',[])),
            'curve':st2['equity_curve'],
        })
    default_b=[{'name':'沪深300','return_pct':None},{'name':'中证500','return_pct':None},{'name':'中证1000','return_pct':None}]
    e_json={'mode': 'REAL' if getattr(export_web, '_real_mode', False) else 'DEMO', 'updated_at':trade_date,'funds':funds,'benchmarks':benchmarks or default_b}
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
        c,m,s=run_demo(args.date); td=args.date
    else:
        export_web._real_mode=True
        td,c,m,s=run_real(args.date)
    export_web(td,c,m,s)
    print(f'updated D/E web snapshots for {td}, market_score={m}')

if __name__=='__main__':
    main()
