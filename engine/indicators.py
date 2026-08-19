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


def _cap(v: float) -> float:
    return round(max(0.0, min(100.0, v)), 2)


def score_history(rows: list[dict]) -> dict:
    """Compute explainable factors with the next 1-3 sessions as the primary horizon."""
    if len(rows) < 60:
        return {'eligible': False, 'reason': 'history_lt_60'}

    closes = [_safe_float(r.get('close')) for r in rows]
    highs = [_safe_float(r.get('high'), closes[i]) for i, r in enumerate(rows)]
    lows = [_safe_float(r.get('low'), closes[i]) for i, r in enumerate(rows)]
    amounts = [_safe_float(r.get('amount')) for r in rows]
    turns = [_safe_float(r.get('turn')) for r in rows]
    if min(closes[-60:]) <= 0:
        return {'eligible': False, 'reason': 'invalid_price'}

    c = closes[-1]
    ma5 = mean(closes[-5:])
    ma10 = mean(closes[-10:])
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:])
    r1 = pct_return(closes[-2], c)
    r3 = pct_return(closes[-4], c)
    r5 = pct_return(closes[-6], c)
    r20 = pct_return(closes[-21], c)
    r60 = pct_return(closes[-60], c)
    avg_amt3 = mean(amounts[-3:])
    avg_amt20 = mean(amounts[-20:])
    avg_turn20 = mean(turns[-20:])

    daily = [pct_return(closes[i-1], closes[i]) for i in range(len(closes)-19, len(closes))]
    vol20 = pstdev(daily) if len(daily) > 1 else 0.0
    drawdown60 = c / max(closes[-60:]) - 1.0

    true_ranges = []
    for i in range(max(1, len(rows)-14), len(rows)):
        prev = closes[i-1]
        tr = max(highs[i] - lows[i], abs(highs[i] - prev), abs(lows[i] - prev))
        if prev > 0:
            true_ranges.append(tr / prev)
    atr14_pct = mean(true_ranges) if true_ranges else max(vol20, 0.02)

    today_high = highs[-1]
    today_low = lows[-1]
    close_position = (c - today_low) / (today_high - today_low) if today_high > today_low else 0.5
    amount_ratio = avg_amt3 / avg_amt20 if avg_amt20 > 0 else 1.0
    high3 = max(highs[-3:])
    low3 = min(lows[-3:])

    # Long trend is retained as context, but no longer dominates a 1-3 day decision.
    trend = 50 + 110 * r5 + 80 * r20 + 40 * r60 + (8 if c > ma5 > ma10 else 0)
    momentum = 50 + 280 * r1 + 150 * r3 + 90 * r5 + min(12, max(-8, (amount_ratio - 1.0) * 8))
    liquidity = 50 + min(30, math.log10(max(avg_amt20, 1)) * 7 - 45)
    risk = 88 - vol20 * 650 + drawdown60 * 80 - max(0.0, atr14_pct - 0.04) * 240

    # Penalize the classic "looks strong because it already exploded" trap.
    overheat = 0.0
    if r3 > 0.10:
        overheat += min(35.0, (r3 - 0.10) * 180)
    if r5 > 0.16:
        overheat += min(30.0, (r5 - 0.16) * 140)
    distance_ma5 = c / ma5 - 1.0 if ma5 > 0 else 0.0
    if distance_ma5 > 0.07:
        overheat += min(30.0, (distance_ma5 - 0.07) * 220)
    if close_position < 0.28 and r1 > 0:
        overheat += 12.0
    overheat = _cap(overheat)

    # "quality" in V1 is now an observed trading-quality/stability measure, not a fake constant 70.
    quality = _cap(0.50 * _cap(risk) + 0.25 * _cap(liquidity) + 0.25 * (100.0 - overheat))

    return {
        'eligible': avg_amt20 >= 20_000_000,
        'close': round(c, 4),
        'ma5': round(ma5, 4),
        'ma10': round(ma10, 4),
        'trend': _cap(trend),
        'momentum': _cap(momentum),
        'liquidity': _cap(liquidity),
        'risk': _cap(risk),
        'quality': quality,
        'r1': round(r1, 6),
        'r3': round(r3, 6),
        'r5': round(r5, 6),
        'r20': round(r20, 6),
        'r60': round(r60, 6),
        'vol20': round(vol20, 6),
        'atr14_pct': round(atr14_pct, 6),
        'drawdown60': round(drawdown60, 6),
        'close_position': round(close_position, 6),
        'amount_ratio_3_20': round(amount_ratio, 6),
        'distance_ma5': round(distance_ma5, 6),
        'recent_high_3': round(high3, 4),
        'recent_low_3': round(low3, 4),
        'overheat_score': overheat,
        'amount20': round(avg_amt20, 2),
    }
