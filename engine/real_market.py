from __future__ import annotations
from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
import math
from .universe import is_main_board


def _f(v, default=0.0):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except Exception:
        return default


def _symbol(code: str) -> str:
    return ('sh.' if code.startswith(('600','601','603','605')) else 'sz.') + code


def _tx_symbol(symbol: str) -> str:
    s=symbol.lower().strip()
    if s.startswith('sh.'):
        return 'sh'+s[3:]
    if s.startswith('sz.'):
        return 'sz'+s[3:]
    return s


def _period_return(vals: list[float], sessions: int):
    if len(vals) <= sessions:
        return None
    base=vals[-1-sessions]
    if base <= 0:
        return None
    return round((vals[-1]/base-1)*100,2)


class AKShareMarket:
    """V1 real-market loader with multiple independent upstreams.

    Full-market snapshot: Eastmoney first, Sina fallback.
    Historical bars, trading-date detection and benchmark histories: Tencent Securities via AKShare.
    This avoids making BaoStock availability a hard dependency on GitHub runners.
    """
    def __init__(self, history_limit: int = 120):
        import akshare as ak
        self.ak = ak
        self.history_limit = history_limit

    def latest_trade_date(self, today: str) -> str:
        requested = date.fromisoformat(today)
        now_cn = datetime.now(ZoneInfo('Asia/Shanghai'))
        if requested == now_cn.date() and now_cn.time() < dt_time(15, 20):
            raise RuntimeError('A股尚未收盘。正式日频虚拟盘请在北京时间15:20之后运行；盘中请运行“连接测试（行情 + AI）”。')

        start=(requested-timedelta(days=20)).strftime('%Y%m%d')
        end=requested.strftime('%Y%m%d')
        df=self.ak.stock_zh_a_hist_tx(symbol='sz000001',start_date=start,end_date=end,adjust='',timeout=25)
        if df is None or df.empty:
            raise RuntimeError('Cannot determine latest trading date from Tencent')
        return str(df.iloc[-1]['date'])[:10]

    def _snapshot_eastmoney(self) -> list[dict]:
        df = self.ak.stock_zh_a_spot_em()
        rows=[]
        for _,r in df.iterrows():
            code=str(r.get('代码','')).zfill(6)
            sym=_symbol(code) if len(code)==6 else ''
            name=str(r.get('名称',''))
            if not sym or not is_main_board(sym):
                continue
            if 'ST' in name.upper() or '退' in name:
                continue
            last=_f(r.get('最新价')); op=_f(r.get('今开')); amount=_f(r.get('成交额'))
            if last<=0 or op<=0 or amount<20_000_000:
                continue
            rows.append({
                'code':sym,'raw_code':code,'name':name,'source':'eastmoney',
                'open':op,'high':_f(r.get('最高'),last),'low':_f(r.get('最低'),last),'close':last,
                'preclose':_f(r.get('昨收'),last),'amount':amount,'turn':_f(r.get('换手率')),
                'pctChg':_f(r.get('涨跌幅')),'peTTM':_f(r.get('市盈率-动态')),'pbMRQ':_f(r.get('市净率')),
                'r60_snapshot':_f(r.get('60日涨跌幅'))/100.0,
                'tradestatus':'1','isST':'0'
            })
        return rows

    def _snapshot_sina(self) -> list[dict]:
        df = self.ak.stock_zh_a_spot()
        rows=[]
        for _,r in df.iterrows():
            raw=str(r.get('代码','')).lower().strip()
            code=raw[-6:] if len(raw) >= 6 else ''
            if raw.startswith('sh'):
                sym='sh.'+code
            elif raw.startswith('sz'):
                sym='sz.'+code
            else:
                sym=_symbol(code) if len(code)==6 else ''
            name=str(r.get('名称',''))
            if not sym or not is_main_board(sym):
                continue
            if 'ST' in name.upper() or '退' in name:
                continue
            last=_f(r.get('最新价')); op=_f(r.get('今开')); amount=_f(r.get('成交额'))
            if last<=0 or op<=0 or amount<20_000_000:
                continue
            rows.append({
                'code':sym,'raw_code':code,'name':name,'source':'sina',
                'open':op,'high':_f(r.get('最高'),last),'low':_f(r.get('最低'),last),'close':last,
                'preclose':_f(r.get('昨收'),last),'amount':amount,'turn':0.0,
                'pctChg':_f(r.get('涨跌幅')),'peTTM':0.0,'pbMRQ':0.0,'r60_snapshot':0.0,
                'tradestatus':'1','isST':'0'
            })
        return rows

    def snapshot(self) -> list[dict]:
        try:
            rows=self._snapshot_eastmoney()
            if rows:
                print(f'[market] snapshot source=eastmoney rows={len(rows)}')
                return rows
        except Exception as e:
            print(f'[market] eastmoney snapshot failed, fallback to sina: {e}')
        rows=self._snapshot_sina()
        if not rows:
            raise RuntimeError('Both Eastmoney and Sina snapshots returned no usable A-share rows')
        print(f'[market] snapshot source=sina rows={len(rows)}')
        return rows

    def preselect(self, rows: list[dict], max_symbols: int = 100) -> list[dict]:
        if not rows:
            return []
        if all(abs(x.get('r60_snapshot',0.0)) < 1e-12 for x in rows):
            return sorted(rows,key=lambda x:x['amount'],reverse=True)[:max_symbols]

        by_amt=sorted(rows,key=lambda x:x['amount'],reverse=True)[:60]
        momentum=[x for x in rows if -0.05 <= x['r60_snapshot'] <= 0.70]
        by_mom=sorted(momentum,key=lambda x:x['r60_snapshot'],reverse=True)[:45]
        sane=[x for x in rows if (0 < x['peTTM'] < 80) and (0 < x['pbMRQ'] < 12)]
        by_val=sorted(sane,key=lambda x:(x['peTTM'] + 4*x['pbMRQ']))[:35]
        union={x['code']:x for x in by_amt+by_mom+by_val}
        vals=list(union.values())
        vals.sort(key=lambda x:(math.log10(max(x['amount'],1))*5 + x['r60_snapshot']*60 - max(0,x['turn']-18)), reverse=True)
        return vals[:max_symbols]

    def histories(self, selected: list[dict], trade_date: str) -> dict[str,list[dict]]:
        d=date.fromisoformat(trade_date)
        start=(d-timedelta(days=240)).strftime('%Y%m%d')
        end=d.strftime('%Y%m%d')
        out={}
        for x in selected:
            try:
                df=self.ak.stock_zh_a_hist_tx(
                    symbol=_tx_symbol(x['code']),
                    start_date=start,
                    end_date=end,
                    adjust='qfq',
                    timeout=25,
                )
                rows=[]
                for _,r in df.tail(self.history_limit).iterrows():
                    close=_f(r.get('close'))
                    hands=_f(r.get('amount'))
                    amount_yuan=max(0.0,hands*100.0*close)
                    rows.append({
                        'date':str(r.get('date'))[:10],
                        'code':x['code'],'name':x['name'],
                        'open':_f(r.get('open')),'high':_f(r.get('high')),
                        'low':_f(r.get('low')),'close':close,
                        'volume':hands*100.0,'amount':amount_yuan,
                        'turn':0.0,'pctChg':0.0,'tradestatus':'1','isST':'0',
                    })
                if rows:
                    out[x['code']]=rows
            except Exception as e:
                print(f'[market] tencent history failed {x["code"]}: {e}')
        if not out:
            raise RuntimeError('Tencent returned no historical data for the preselected universe')
        print(f'[market] historical source=tencent symbols={len(out)}')
        return out

    def benchmarks(self, start_date: str, trade_date: str) -> list[dict]:
        specs=[('沪深300','sh000300'),('中证500','sh000905'),('中证1000','sh000852')]
        result=[]
        for name,symbol in specs:
            item={'name':name,'symbol':symbol,'return_pct':None,'return_5d_pct':None,'return_20d_pct':None,'return_60d_pct':None,'curve':[]}
            try:
                df=self.ak.stock_zh_index_daily_tx(symbol=symbol)
                if df is None or df.empty:
                    result.append(item); continue
                df=df.copy()
                df['date']=df['date'].astype(str).str[:10]
                df=df[(df['date']>=start_date)&(df['date']<=trade_date)]
                vals=[_f(x) for x in df['close'].tolist() if _f(x)>0]
                dates=df['date'].tolist()[-len(vals):] if vals else []
                if vals:
                    base=vals[0]
                    item['return_pct']=round((vals[-1]/base-1)*100,2)
                    item['return_5d_pct']=_period_return(vals,5)
                    item['return_20d_pct']=_period_return(vals,20)
                    item['return_60d_pct']=_period_return(vals,60)
                    item['curve']=[{'date':d,'equity':round(1_000_000*v/base,2)} for d,v in zip(dates,vals)]
            except Exception as e:
                print(f'[market] benchmark failed {symbol}: {e}')
            result.append(item)
        return result
