from __future__ import annotations
from datetime import date, timedelta
import math
import random

NAMES = {
    'sh.600519':'贵州茅台','sh.600036':'招商银行','sh.600276':'恒瑞医药','sh.601318':'中国平安',
    'sh.600900':'长江电力','sh.601012':'隆基绿能','sz.000333':'美的集团','sz.000858':'五粮液',
    'sz.002415':'海康威视','sz.002594':'比亚迪','sz.000651':'格力电器','sz.002475':'立讯精密'
}


def generate_history(days=100, seed=42):
    random.seed(seed)
    end = date.today()
    symbols = list(NAMES)
    data = {}
    for j, sym in enumerate(symbols):
        px = 20 + j*7 + random.random()*20
        rows=[]
        for i in range(days):
            d = end - timedelta(days=days-i)
            drift = 0.0005 + (j%4-1.5)*0.00015
            shock = random.gauss(0, 0.012)
            op = px * (1 + random.gauss(0,0.004))
            px *= 1 + drift + shock
            rows.append({'date':d.isoformat(),'code':sym,'name':NAMES[sym],'open':round(op,2),'high':round(max(op,px)*1.01,2),
                         'low':round(min(op,px)*0.99,2),'close':round(px,2),'preclose':round(px/(1+drift+shock),2),
                         'volume':'10000000','amount':str(2e8 + j*1e7),'turn':str(1.3+j*0.15),'tradestatus':'1','pctChg':'0','isST':'0'})
        data[sym]=rows
    return data
