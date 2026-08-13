from __future__ import annotations
from datetime import date, timedelta
import math
import time
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


class AKShareMarket:
    """Efficient V1 real-market loader.

    One full-market snapshot is used to filter the main board. Only a bounded
    preselection receives 90-day historical requests, reducing upstream load.
    """
    def __init__(self, history_limit: int = 140, sleep_s: float = 0.08):
        import akshare as ak
        self.ak = ak
        self.history_limit = history_limit
        self.sleep_s = sleep_s

    def latest_trade_date(self, today: str) -> str:
        d = date.fromisoformat(today)
        start = (d - timedelta(days=14)).strftime('%Y%m%d')
        end = d.strftime('%Y%m%d')
        df = self.ak.stock_zh_a_hist(symbol='000001', period='daily', start_date=start, end_date=end, adjust='')
        if df is None or df.empty:
            raise RuntimeError('Cannot determine latest trading date')
        return str(df.iloc[-1]['日期'])[:10]

    def snapshot(self) -> list[dict]:
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
                'code':sym,'raw_code':code,'name':name,
                'open':op,'high':_f(r.get('最高'),last),'low':_f(r.get('最低'),last),'close':last,
                'preclose':_f(r.get('昨收'),last),'amount':amount,'turn':_f(r.get('换手率')),
                'pctChg':_f(r.get('涨跌幅')),'peTTM':_f(r.get('市盈率-动态')),'pbMRQ':_f(r.get('市净率')),
                'r60_snapshot':_f(r.get('60日涨跌幅'))/100.0,
                'tradestatus':'1','isST':'0'
            })
        return rows

    def preselect(self, rows: list[dict], max_symbols: int = 140) -> list[dict]:
        if not rows:
            return []
        by_amt=sorted(rows,key=lambda x:x['amount'],reverse=True)[:80]
        momentum=[x for x in rows if -0.05 <= x['r60_snapshot'] <= 0.70]
        by_mom=sorted(momentum,key=lambda x:x['r60_snapshot'],reverse=True)[:55]
        sane=[x for x in rows if (0 < x['peTTM'] < 80) and (0 < x['pbMRQ'] < 12)]
        by_val=sorted(sane,key=lambda x:(x['peTTM'] + 4*x['pbMRQ']))[:45]
        union={x['code']:x for x in by_amt+by_mom+by_val}
        vals=list(union.values())
        vals.sort(key=lambda x:(math.log10(max(x['amount'],1))*5 + x['r60_snapshot']*60 - max(0,x['turn']-18)), reverse=True)
        return vals[:max_symbols]

    def histories(self, selected: list[dict], trade_date: str) -> dict[str,list[dict]]:
        d=date.fromisoformat(trade_date)
        start=(d-timedelta(days=210)).strftime('%Y%m%d')
        end=d.strftime('%Y%m%d')
        out={}
        for i,x in enumerate(selected,1):
            try:
                df=self.ak.stock_zh_a_hist(symbol=x['raw_code'],period='daily',start_date=start,end_date=end,adjust='qfq')
                rows=[]
                for _,r in df.tail(self.history_limit).iterrows():
                    rows.append({'date':str(r['日期'])[:10],'code':x['code'],'name':x['name'],
                                 'open':_f(r['开盘']),'high':_f(r['最高']),'low':_f(r['最低']),'close':_f(r['收盘']),
                                 'volume':_f(r['成交量'])*100,'amount':_f(r['成交额']),'turn':_f(r['换手率']),
                                 'pctChg':_f(r['涨跌幅']),'tradestatus':'1','isST':'0'})
                if rows:
                    out[x['code']]=rows
            except Exception as e:
                print(f'[market] history failed {x["code"]}: {e}')
            if self.sleep_s:
                time.sleep(self.sleep_s)
        return out
