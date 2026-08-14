from __future__ import annotations
from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo
import math
import signal
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


def _call_with_timeout(seconds: int, fn):
    """Bound providers that do not expose a timeout argument on Linux runners."""
    if not hasattr(signal, 'SIGALRM'):
        return fn()
    old_handler=signal.getsignal(signal.SIGALRM)
    def _alarm(_signum, _frame):
        raise TimeoutError(f'provider call exceeded {seconds}s')
    signal.signal(signal.SIGALRM,_alarm)
    signal.setitimer(signal.ITIMER_REAL,seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,old_handler)


def _tx_amount_mode(raw_amount: float, close: float, reference_amount: float = 0.0) -> str:
    """Detect whether Tencent's ambiguous `amount` currently behaves like yuan or hands.

    AKShare documents the field as hands, but upstream formats can drift. When a full-market
    snapshot is available, compare both interpretations with its same-day turnover amount.
    """
    raw=max(0.0,_f(raw_amount))
    px=max(0.0,_f(close))
    ref=max(0.0,_f(reference_amount))
    if raw <= 0:
        return 'yuan'
    if ref > 0 and px > 0:
        as_yuan=raw
        as_hands_yuan=raw*100.0*px
        distance_yuan=abs(math.log(max(as_yuan,1.0)/ref))
        distance_hands=abs(math.log(max(as_hands_yuan,1.0)/ref))
        return 'yuan' if distance_yuan <= distance_hands else 'hands'
    # Recovery mode may only have an old cached reference. For liquid main-board names,
    # values already above 10m are much more likely turnover-yuan than lot counts.
    return 'yuan' if raw >= 10_000_000 else 'hands'


def _tx_amount_and_volume(raw_amount: float, close: float, mode: str) -> tuple[float,float]:
    raw=max(0.0,_f(raw_amount))
    px=max(0.0,_f(close))
    if mode == 'hands':
        shares=raw*100.0
        return shares*px,shares
    amount_yuan=raw
    approx_shares=(raw/px) if px > 0 else 0.0
    return amount_yuan,approx_shares


class AKShareMarket:
    """Real-market loader with independent upstreams and conservative fallbacks.

    Full-market snapshot: Eastmoney first, Sina fallback, both with bounded retries.
    Historical bars and recovery bars: Tencent Securities via AKShare.
    Trading-date detection: Tencent A-share daily first, Tencent index daily fallback.
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
        last_error=None
        for attempt in (1,2):
            try:
                df=self.ak.stock_zh_a_hist_tx(symbol='sz000001',start_date=start,end_date=end,adjust='',timeout=20)
                if df is None or df.empty:
                    raise RuntimeError('empty Tencent stock calendar response')
                td=str(df.iloc[-1]['date'])[:10]
                print(f'[market] trading-date source=tencent-stock date={td}')
                return td
            except Exception as e:
                last_error=e
                print(f'[market] Tencent stock trading-date attempt {attempt}/2 failed: {e}')
                if attempt == 1:
                    time.sleep(3)

        try:
            df=_call_with_timeout(35,lambda:self.ak.stock_zh_index_daily_tx(symbol='sh000001'))
            if df is None or df.empty:
                raise RuntimeError('empty Tencent index calendar response')
            dates=[str(x)[:10] for x in df['date'].tolist()]
            valid=[x for x in dates if x and x <= requested.isoformat()]
            if not valid:
                raise RuntimeError(f'no index trading date on or before {requested.isoformat()}')
            td=max(valid)
            print(f'[market] trading-date source=tencent-index-fallback date={td}')
            return td
        except Exception as index_error:
            raise RuntimeError(
                f'Cannot determine latest trading date; Tencent stock path failed ({last_error}); '
                f'Tencent index fallback failed ({index_error})'
            ) from index_error

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

    def _try_snapshot_provider(self, label: str, fn) -> list[dict] | None:
        for attempt in (1,2):
            try:
                rows=_call_with_timeout(45,fn)
                if len(rows) < 500:
                    raise RuntimeError(f'suspiciously small snapshot: {len(rows)} rows')
                print(f'[market] snapshot source={label} rows={len(rows)} attempt={attempt}')
                return rows
            except Exception as e:
                print(f'[market] {label} snapshot attempt {attempt}/2 failed: {e}')
                if attempt == 1:
                    time.sleep(3)
        return None

    def snapshot(self) -> list[dict]:
        rows=self._try_snapshot_provider('eastmoney',self._snapshot_eastmoney)
        if rows:
            return rows
        rows=self._try_snapshot_provider('sina',self._snapshot_sina)
        if rows:
            return rows
        raise RuntimeError('Eastmoney and Sina full-market snapshots both failed after retries')

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
        stale_count=0
        amount_modes={'yuan':0,'hands':0}
        for x in selected:
            try:
                df=self.ak.stock_zh_a_hist_tx(
                    symbol=_tx_symbol(x['code']),
                    start_date=start,
                    end_date=end,
                    adjust='qfq',
                    timeout=20,
                )
                tail=df.tail(self.history_limit)
                if tail is None or tail.empty:
                    continue
                last=tail.iloc[-1]
                mode=_tx_amount_mode(last.get('amount'),last.get('close'),x.get('amount',0))
                amount_modes[mode]+=1
                rows=[]
                for _,r in tail.iterrows():
                    close=_f(r.get('close'))
                    amount_yuan,volume_shares=_tx_amount_and_volume(r.get('amount'),close,mode)
                    rows.append({
                        'date':str(r.get('date'))[:10],
                        'code':x['code'],'name':x.get('name',x['code']),
                        'open':_f(r.get('open')),'high':_f(r.get('high')),
                        'low':_f(r.get('low')),'close':close,
                        'volume':volume_shares,'amount':amount_yuan,
                        'turn':0.0,'pctChg':0.0,'tradestatus':'1','isST':'0',
                    })
                if rows and rows[-1]['date'] == trade_date:
                    out[x['code']]=rows
                elif rows:
                    stale_count+=1
            except Exception as e:
                print(f'[market] tencent history failed {x["code"]}: {e}')
        if not selected:
            raise RuntimeError('No symbols supplied for Tencent histories')
        required=max(1,math.ceil(len(selected)*0.75))
        if len(out) < required:
            raise RuntimeError(f'Tencent current-history coverage too low: {len(out)}/{len(selected)}, require >= {required}; stale={stale_count}')
        print(
            f'[market] historical source=tencent current_symbols={len(out)}/{len(selected)} '
            f'stale_or_suspended={stale_count} amount_mode_yuan={amount_modes["yuan"]} '
            f'amount_mode_hands={amount_modes["hands"]}'
        )
        return out

    def snapshot_from_histories(self, selected: list[dict], histories: dict[str,list[dict]], trade_date: str) -> list[dict]:
        """Build a degraded-but-current snapshot from cached universe + Tencent daily bars."""
        meta={x['code']:x for x in selected}
        rows=[]
        for sym,hist in histories.items():
            if not hist or str(hist[-1].get('date',''))[:10] != trade_date:
                continue
            cur=hist[-1]
            prev=hist[-2] if len(hist) >= 2 else cur
            m=meta.get(sym,{})
            close=_f(cur.get('close'))
            if close <= 0 or _f(cur.get('open')) <= 0:
                continue
            r60=0.0
            if len(hist) >= 61 and _f(hist[-61].get('close')) > 0:
                r60=close/_f(hist[-61].get('close'))-1
            rows.append({
                'code':sym,'raw_code':sym[-6:],'name':m.get('name',cur.get('name',sym)),'source':'tencent-cache',
                'open':_f(cur.get('open')),'high':_f(cur.get('high'),close),'low':_f(cur.get('low'),close),'close':close,
                'preclose':_f(prev.get('close'),close),'amount':_f(cur.get('amount')),'turn':0.0,
                'pctChg':(close/_f(prev.get('close'),close)-1)*100 if _f(prev.get('close'),close)>0 else 0.0,
                'peTTM':_f(m.get('peTTM')),'pbMRQ':_f(m.get('pbMRQ')),'r60_snapshot':r60,
                'tradestatus':'1','isST':'0'
            })
        return rows

    def execution_bars(self, symbols: dict[str,str], trade_date: str) -> dict[str,dict]:
        """Fetch unadjusted Tencent bars for holdings/pending symbols in recovery mode."""
        if not symbols:
            return {}
        d=date.fromisoformat(trade_date)
        start=(d-timedelta(days=12)).strftime('%Y%m%d')
        end=d.strftime('%Y%m%d')
        out={}
        for sym,name in symbols.items():
            for attempt in (1,2):
                try:
                    df=self.ak.stock_zh_a_hist_tx(symbol=_tx_symbol(sym),start_date=start,end_date=end,adjust='',timeout=15)
                    if df is None or df.empty:
                        raise RuntimeError('empty')
                    records=list(df.to_dict('records'))
                    current_index=next((i for i,r in enumerate(records) if str(r.get('date'))[:10]==trade_date),None)
                    if current_index is None:
                        break
                    r=records[current_index]
                    prev=records[current_index-1] if current_index>0 else r
                    close=_f(r.get('close'))
                    if close<=0 or _f(r.get('open'))<=0:
                        break
                    mode=_tx_amount_mode(r.get('amount'),close,0)
                    amount_yuan,_=_tx_amount_and_volume(r.get('amount'),close,mode)
                    out[sym]={
                        'code':sym,'raw_code':sym[-6:],'name':name,'source':'tencent-execution',
                        'open':_f(r.get('open')),'high':_f(r.get('high'),close),'low':_f(r.get('low'),close),'close':close,
                        'preclose':_f(prev.get('close'),close),'amount':amount_yuan,'turn':0.0,
                        'pctChg':0.0,'peTTM':0.0,'pbMRQ':0.0,'r60_snapshot':0.0,
                        'tradestatus':'1','isST':'0'
                    }
                    break
                except Exception as e:
                    print(f'[market] critical Tencent bar {sym} attempt {attempt}/2 failed: {e}')
                    if attempt == 1:
                        time.sleep(1)
        missing=sorted(set(symbols)-set(out))
        if missing:
            print(f'[market] critical bars unavailable/suspended count={len(missing)} symbols={",".join(missing[:12])}')
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
