from __future__ import annotations
from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
import math
from .universe import is_main_board
from .providers import BaoStockProvider


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
    """V1 real-market loader with multiple upstreams.

    Full-market snapshot: Eastmoney first, Sina fallback.
    Historical bars and trade calendar: BaoStock, so a single Eastmoney outage
    cannot kill the daily portfolio run.
    """
    def __init__(self, history_limit: int = 140):
        import akshare as ak
        self.ak = ak
        self.history_limit = history_limit

    def latest_trade_date(self, today: str) -> str:
        requested = date.fromisoformat(today)
        now_cn = datetime.now(ZoneInfo('Asia/Shanghai'))
        # A daily strategy must never use an unfinished trading day.
        if requested == now_cn.date() and now_cn.time() < dt_time(15, 20):
            raise RuntimeError('A股尚未收盘。正式日频虚拟盘请在北京时间15:20之后运行；盘中请运行“连接测试（行情 + AI）”。')

        start = requested - timedelta(days=20)
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            raise RuntimeError(f'BaoStock login failed: {lg.error_msg}')
        try:
            rs = bs.query_trade_dates(start_date=start.isoformat(), end_date=requested.isoformat())
            trading=[]
            while rs.error_code == '0' and rs.next():
                row=dict(zip(rs.fields, rs.get_row_data()))
                if str(row.get('is_trading_day','0')) == '1':
                    trading.append(str(row.get('calendar_date',''))[:10])
            if not trading:
                raise RuntimeError('Cannot determine latest trading date from BaoStock')
            return trading[-1]
        finally:
            bs.logout()

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

    def preselect(self, rows: list[dict], max_symbols: int = 140) -> list[dict]:
        if not rows:
            return []
        # Sina fallback has no valuation/60-day fields; in that case use liquid names
        # and let BaoStock historical bars do the real ranking afterwards.
        if all(abs(x.get('r60_snapshot',0.0)) < 1e-12 for x in rows):
            return sorted(rows,key=lambda x:x['amount'],reverse=True)[:max_symbols]

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
        out={}
        with BaoStockProvider() as provider:
            for x in selected:
                try:
                    rows=provider.bars(x['code'],trade_date,lookback_days=self.history_limit)
                    for r in rows:
                        r['name']=x['name']
                    if rows:
                        out[x['code']]=rows
                        last=rows[-1]
                        # Enrich the snapshot object so valuation/turnover survive even
                        # when the full-market snapshot came from Sina.
                        x['peTTM']=_f(last.get('peTTM'),x.get('peTTM',0.0))
                        x['pbMRQ']=_f(last.get('pbMRQ'),x.get('pbMRQ',0.0))
                        x['turn']=_f(last.get('turn'),x.get('turn',0.0))
                        x['tradestatus']=str(last.get('tradestatus','1'))
                        x['isST']=str(last.get('isST','0'))
                except Exception as e:
                    print(f'[market] baostock history failed {x["code"]}: {e}')
        if not out:
            raise RuntimeError('BaoStock returned no historical data for the preselected universe')
        print(f'[market] historical source=baostock symbols={len(out)}')
        return out
