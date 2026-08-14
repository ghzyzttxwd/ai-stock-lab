from __future__ import annotations

from dataclasses import dataclass


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(x)))


@dataclass(frozen=True)
class MarketRegime:
    label: str
    score: float
    confidence: float
    reasons: tuple[str, ...]


def classify_market_regime(features: dict) -> MarketRegime:
    """Classify broad A-share market state from *market-wide* inputs.

    Expected inputs are already point-in-time features. Ratios use 0..1, scores 0..100.
    This function intentionally knows nothing about the selected stock candidate pool.
    """
    index_trend = _clip(features.get("index_trend_score", 50.0))
    adv = _clip(float(features.get("advancer_ratio", 0.5)) * 100.0)
    new_high = _clip(float(features.get("new_high_ratio", 0.02)) * 1000.0)
    new_low = _clip(float(features.get("new_low_ratio", 0.02)) * 1000.0)
    limit_up = max(0.0, float(features.get("limit_up_count", 0.0)))
    limit_down = max(0.0, float(features.get("limit_down_count", 0.0)))
    break_rate = _clip(float(features.get("limit_break_rate", 0.25)) * 100.0)
    drawdown20 = float(features.get("index_drawdown20", 0.0))
    rebound3 = float(features.get("index_return3", 0.0))

    breadth_score = 0.58 * adv + 0.42 * _clip(50.0 + new_high - new_low)
    emotion_score = _clip(50.0 + 0.7 * (limit_up - limit_down) - 0.35 * break_rate)
    score = _clip(0.48 * index_trend + 0.34 * breadth_score + 0.18 * emotion_score)

    reasons: list[str] = []
    if adv >= 58:
        reasons.append("上涨家数占优")
    elif adv <= 38:
        reasons.append("市场宽度偏弱")
    if limit_down >= max(10.0, limit_up * 0.55):
        reasons.append("跌停压力较高")
    if break_rate >= 45:
        reasons.append("炸板率偏高")
    if index_trend >= 65:
        reasons.append("主要指数趋势向上")
    elif index_trend <= 35:
        reasons.append("主要指数趋势向下")

    panic_rebound = drawdown20 <= -0.08 and rebound3 >= 0.045
    if panic_rebound:
        label = "panic_rebound"
        reasons.append("深回撤后出现快速反弹")
    elif score >= 64:
        label = "risk_on"
    elif score <= 36:
        label = "risk_off"
    else:
        label = "neutral"

    distance = abs(score - 50.0)
    signal_count = sum(
        1 for x in (
            abs(index_trend - 50.0) >= 15,
            abs(adv - 50.0) >= 12,
            abs(emotion_score - 50.0) >= 15,
        ) if x
    )
    confidence = _clip(45.0 + distance * 0.7 + signal_count * 8.0)
    return MarketRegime(label, round(score, 2), round(confidence, 2), tuple(reasons))
