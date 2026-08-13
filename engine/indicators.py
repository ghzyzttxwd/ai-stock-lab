from __future__ import annotations
import math
from statistics import mean, pstdev


def _safe_float(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def pct_return(a: float, b: float) -> float:
    if not a:
        return 0.0
    return b / a - 1.0


def score_history(rows: list[dict]) -> dict:
    """Compute lightweight, explainable V1 factors from ascending daily bars."""
    if len(rows) < 60:
        return {'eligible': False, 'reason': 'history_lt_60'}

    closes = [_safe_float(r.get('close')) for r in rows]
    amounts = [_safe_float(r.get('amount')) for r in rows]
    turns = [_safe_float(r.get('turn')) for r in rows]
    if min(closes[-60:]) <= 0:
        return {'eligible': False, 'reason': 'invalid_price'}

    c = closes[-1]
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:])
    r20 = pct_return(closes[-21], c)
    r60 = pct_return(closes[-60], c)
    avg_amt20 = mean(amounts[-20:])
    avg_turn20 = mean(turns[-20:])

    daily = [pct_return(closes[i-1], closes[i]) for i in range(len(closes)-19, len(closes))]
    vol20 = pstdev(daily) if len(daily) > 1 else 0.0
    drawdown60 = c / max(closes[-60:]) - 1.0

    trend = 50 + 180 * r20 + 90 * r60 + (12 if c > ma20 > ma60 else 0)
    momentum = 50 + 220 * r20 + min(15, max(-10, (avg_turn20 - 1.5) * 3))
    liquidity = 50 + min(30, math.log10(max(avg_amt20, 1)) * 7 - 45)
    risk = 85 - vol20 * 700 + drawdown60 * 100

    def cap(v): return round(max(0.0, min(100.0, v)), 2)

    return {
        'eligible': avg_amt20 >= 20_000_000,
        'close': round(c, 4),
        'trend': cap(trend),
        'momentum': cap(momentum),
        'liquidity': cap(liquidity),
        'risk': cap(risk),
        'r20': round(r20, 6),
        'r60': round(r60, 6),
        'vol20': round(vol20, 6),
        'drawdown60': round(drawdown60, 6),
        'amount20': round(avg_amt20, 2),
    }
