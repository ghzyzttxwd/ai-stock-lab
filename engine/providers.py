from __future__ import annotations
from datetime import date, timedelta


class BaoStockProvider:
    """BaoStock adapter. Internet access is required at runtime."""
    fields = 'date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus,pctChg,peTTM,pbMRQ,isST'

    def __init__(self):
        import baostock as bs
        self.bs = bs
        self.logged_in = False

    def __enter__(self):
        lg = self.bs.login()
        if lg.error_code != '0':
            raise RuntimeError(f'BaoStock login failed: {lg.error_msg}')
        self.logged_in = True
        return self

    def __exit__(self, *args):
        if self.logged_in:
            self.bs.logout()

    def bars(self, symbol: str, end_date: str, lookback_days: int = 140) -> list[dict]:
        end = date.fromisoformat(end_date)
        start = end - timedelta(days=lookback_days * 2)
        rs = self.bs.query_history_k_data_plus(
            symbol, self.fields,
            start_date=start.isoformat(), end_date=end.isoformat(),
            frequency='d', adjustflag='3')
        out = []
        while rs.error_code == '0' and rs.next():
            out.append(dict(zip(rs.fields, rs.get_row_data())))
        return out[-lookback_days:]

    def all_stocks(self, trade_date: str) -> list[dict]:
        rs = self.bs.query_all_stock(day=trade_date)
        out = []
        while rs.error_code == '0' and rs.next():
            out.append(dict(zip(rs.fields, rs.get_row_data())))
        return out
