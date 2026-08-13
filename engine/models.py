from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Literal

Side = Literal['BUY', 'SELL']


@dataclass
class Order:
    id: str
    fund_id: str
    symbol: str
    name: str
    side: Side
    target_weight: float
    created_date: str
    status: str = 'PENDING'
    reason: str = ''

    def to_dict(self):
        return asdict(self)


@dataclass
class Fill:
    order_id: str
    fund_id: str
    symbol: str
    name: str
    side: Side
    trade_date: str
    price: float
    qty: int
    gross: float
    fees: float
    net_cash_change: float
    note: str = ''

    def to_dict(self):
        return asdict(self)
