from dataclasses import dataclass


@dataclass(frozen=True)
class TradingConfig:
    initial_cash: float = 1_000_000.0
    lot_size: int = 100
    commission_rate: float = 0.0003   # configurable brokerage assumption
    min_commission: float = 5.0
    stamp_duty_sell_rate: float = 0.0005
    slippage_bps: float = 5.0
    max_single_weight_d: float = 0.15
    max_total_weight_d: float = 0.90
    max_positions_d: int = 10
    max_new_position_weight_d: float = 0.10


CONFIG = TradingConfig()
