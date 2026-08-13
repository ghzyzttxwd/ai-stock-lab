from __future__ import annotations
from pathlib import Path
import json
from datetime import date, timedelta
from .demo import generate_history, NAMES
from .indicators import score_history

ROOT = Path(__file__).resolve().parents[1]


def main():
    histories = generate_history(110)
    candidates=[]
    for sym, rows in histories.items():
        sc=score_history(rows)
        if not sc.get('eligible'):
            continue
        sc.update({'symbol':sym,'name':NAMES[sym]})
        sc['score_d']=round(.30*sc['trend']+.25*sc['momentum']+.20*sc['risk']+.15*sc['liquidity']+.10*70,2)
        candidates.append(sc)
    candidates.sort(key=lambda x:x['score_d'], reverse=True)

    start=1_000_000
    dates=[]
    v=start
    for i in range(55):
        d=date.today()-timedelta(days=54-i)
        v*=1+(0.0007 + math_wave(i)*0.0024)
        dates.append({'date':d.isoformat(),'equity':round(v,2)})

    holdings=[]
    for i,c in enumerate(candidates[:5]):
        holdings.append({'symbol':c['symbol'],'name':c['name'],'weight':round(12.5-i*0.8,1),'pnl_pct':round((c['r20']*100),2),'score':c['score_d']})

    d_state={
      'updated_at': date.today().isoformat(), 'market_score':68, 'market_label':'偏强',
      'fund':{'name':'AI 综合基金 D','initial':start,'equity':round(v,2),'cash':342650.12,'position_pct':65.7},
      'metrics':{'return_pct':round((v/start-1)*100,2),'today_pct':0.62,'week_pct':1.84,'max_drawdown_pct':-3.21,'win_rate_pct':58.3,'profit_factor':1.42,'trades':24},
      'holdings':holdings,
      'decisions':[
        {'action':'买入','name':candidates[0]['name'],'symbol':candidates[0]['symbol'],'weight':'10%','reason':'趋势与动量同时位于候选池前列，组合行业集中度仍在限制内。'},
        {'action':'持有','name':candidates[1]['name'],'symbol':candidates[1]['symbol'],'weight':'9%','reason':'综合评分稳定，尚未触发退出条件。'},
        {'action':'减仓','name':candidates[-1]['name'],'symbol':candidates[-1]['symbol'],'weight':'3%','reason':'20日动量转弱，风险评分下降。'}
      ],
      'candidates':candidates[:10], 'equity_curve':dates,
      'diary':'市场偏强但没有进入高温区，组合维持中等偏高仓位。新增1只、减仓1只，保留约34%的现金缓冲。'
    }

    def curve(mult, phase):
        vv=start; out=[]
        for i in range(55):
            d=date.today()-timedelta(days=54-i)
            vv*=1+(0.00045*mult + math_wave(i+phase)*0.0026*mult)
            out.append({'date':d.isoformat(),'equity':round(vv,2)})
        return out
    e_funds=[]
    for fid,name,mult,phase,risk in [('A','稳健基金 A',0.65,0,'低'),('B','趋势基金 B',1.35,7,'高'),('D','综合基金 D',1.0,3,'中')]:
        cv=curve(mult,phase); eq=cv[-1]['equity']
        e_funds.append({'id':fid,'name':name,'equity':eq,'return_pct':round((eq/start-1)*100,2),'max_drawdown_pct':round(-3.8*mult,2),'risk':risk,'curve':cv})
    e_state={'updated_at':date.today().isoformat(),'funds':e_funds,'benchmarks':[{'name':'沪深300','return_pct':2.1},{'name':'中证500','return_pct':1.4},{'name':'中证1000','return_pct':0.7}]}

    (ROOT/'web/d/data.json').write_text(json.dumps(d_state,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'web/e/data.json').write_text(json.dumps(e_state,ensure_ascii=False,indent=2),encoding='utf-8')


def math_wave(i):
    import math
    return math.sin(i/5.0)+0.35*math.sin(i/2.7)

if __name__=='__main__':
    main()
