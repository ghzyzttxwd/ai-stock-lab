from __future__ import annotations
from datetime import date, timedelta


def test_tencent_history():
    import akshare as ak
    end=date.today()
    start=end-timedelta(days=30)
    df=ak.stock_zh_a_hist_tx(
        symbol='sz000001',
        start_date=start.strftime('%Y%m%d'),
        end_date=end.strftime('%Y%m%d'),
        adjust='',
        timeout=25,
    )
    if df is None or df.empty:
        raise RuntimeError('Tencent history returned no rows')
    print(f'[OK] Tencent daily history rows={len(df)} latest={df.iloc[-1]["date"]}')


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
    test_tencent_history()
    test_snapshot()
    test_ai()
    print('[OK] ALL CONNECTION TESTS PASSED')


if __name__=='__main__':
    main()
