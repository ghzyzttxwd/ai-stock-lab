from __future__ import annotations
from datetime import date, timedelta


def test_baostock():
    import baostock as bs
    lg=bs.login()
    if lg.error_code!='0':
        raise RuntimeError(f'BaoStock login failed: {lg.error_msg}')
    try:
        end=date.today()
        start=end-timedelta(days=20)
        rs=bs.query_trade_dates(start_date=start.isoformat(), end_date=end.isoformat())
        days=[]
        while rs.error_code=='0' and rs.next():
            row=dict(zip(rs.fields,rs.get_row_data()))
            if row.get('is_trading_day')=='1':
                days.append(row.get('calendar_date'))
        if not days:
            raise RuntimeError('BaoStock trade calendar returned no trading day')
        print(f'[OK] BaoStock trade calendar latest={days[-1]}')
    finally:
        bs.logout()


def test_snapshot():
    import akshare as ak
    try:
        df=ak.stock_zh_a_spot_em()
        source='eastmoney'
    except Exception as e:
        print(f'[WARN] Eastmoney unavailable: {e}')
        df=ak.stock_zh_a_spot()
        source='sina'
    if df is None or df.empty:
        raise RuntimeError('AKShare snapshot returned no rows')
    print(f'[OK] AKShare snapshot source={source} rows={len(df)}')


def test_ai():
    from .ai_manager import smoke_test_api
    text=smoke_test_api()
    print(f'[OK] AI API responded: {text}')


def main():
    test_baostock()
    test_snapshot()
    test_ai()
    print('[OK] ALL CONNECTION TESTS PASSED')


if __name__=='__main__':
    main()
