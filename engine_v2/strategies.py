from __future__ import annotations

from .regime import MarketRegime
from .sizing import risk_budget_weights


def _f(x: dict, key: str, default: float = 50.0) -> float:
    try:
        value=x.get(key, default)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _drawdown_brake(exposure: float, fund_drawdown: float) -> float:
    """Prototype portfolio brake; thresholds must be validated by the shadow experiment."""
    dd = float(fund_drawdown or 0.0)
    factor = 1.0
    if dd <= -0.18:
        factor = 0.35
    elif dd <= -0.12:
        factor = 0.55
    elif dd <= -0.08:
        factor = 0.75
    return max(0.0, min(1.0, exposure * factor))


def _exposure(regime: MarketRegime, table: dict[str, float], fund_drawdown: float) -> float:
    """Map market regime to exposure without a hard neutral/risk-on cliff.

    The regime classifier flips from neutral to risk_on at score 64. Previously that
    single threshold could jump a fund from its neutral exposure straight to the full
    risk-on exposure. Blend continuously between the two tables from score 58 to 70,
    so a borderline strong market increases risk gradually instead of in one step.
    Risk-off and panic-rebound behaviour is intentionally unchanged.
    """
    if regime.label in {"neutral", "risk_on"}:
        neutral = float(table.get("neutral", 0.5))
        risk_on = float(table.get("risk_on", neutral))
        score = float(regime.score)
        blend = max(0.0, min(1.0, (score - 58.0) / 12.0))
        base = neutral + (risk_on - neutral) * blend
    else:
        base = float(table.get(regime.label, table.get("neutral", 0.5)))
    return _drawdown_brake(base, fund_drawdown)


def _decorate(candidate: dict, score: float, thesis: str, invalidation: str) -> dict:
    return {
        **candidate,
        "v2_score": round(score, 2),
        "thesis": thesis,
        "invalidation": invalidation,
    }


def strategy_a_v2(candidates: list[dict], regime: MarketRegime, fund_drawdown: float = 0.0) -> list[dict]:
    """A: verified financial quality + low risk + defensive trend."""
    eligible = [
        x for x in candidates
        if bool(x.get("fundamental_ready"))
        and _f(x, "quality_score") >= 55
        and _f(x, "cashflow_score") >= 50
        and not bool(x.get("financial_distress"))
    ]
    ranked = []
    for x in eligible:
        score = (
            0.34 * _f(x, "quality_score")
            + 0.24 * _f(x, "risk")
            + 0.16 * _f(x, "cashflow_score")
            + 0.14 * _f(x, "trend")
            + 0.07 * _f(x, "valuation_score")
            + 0.05 * _f(x, "industry_score")
        )
        ranked.append(_decorate(
            x, score,
            "已验证财务期的盈利/现金流质量合格，波动可控且价格趋势未明显恶化",
            "财务质量跌破门槛、经营现金流转负、风险显著上升或中期趋势破坏",
        ))
    ranked.sort(key=lambda x: x["v2_score"], reverse=True)
    exposure = _exposure(regime, {
        "risk_on": 0.75, "neutral": 0.55, "panic_rebound": 0.35, "risk_off": 0.20,
    }, fund_drawdown)
    return risk_budget_weights(ranked[:10], exposure, 0.10, 0.25, 0.20)


def strategy_b_v2(candidates: list[dict], regime: MarketRegime, fund_drawdown: float = 0.0) -> list[dict]:
    """B: strong industry first, healthy stock trend second."""
    eligible = [
        x for x in candidates
        if _f(x, "industry_score") >= 52
        and _f(x, "trend") >= 55
        and _f(x, "liquidity") >= 45
    ]
    ranked = []
    for x in eligible:
        crowding_penalty = max(0.0, _f(x, "crowding_score", 50) - 75.0) * 0.25
        score = (
            0.32 * _f(x, "industry_score")
            + 0.28 * _f(x, "trend")
            + 0.15 * _f(x, "breakout_quality")
            + 0.10 * _f(x, "momentum")
            + 0.10 * _f(x, "liquidity")
            + 0.05 * _f(x, "leader_score")
            - crowding_penalty
        )
        ranked.append(_decorate(
            x, score,
            "强行业中的健康上升趋势，突破与成交持续性共同确认",
            "行业转弱、趋势跌破关键结构或拥挤度升高且动量衰减",
        ))
    ranked.sort(key=lambda x: x["v2_score"], reverse=True)
    exposure = _exposure(regime, {
        "risk_on": 0.90, "neutral": 0.65, "panic_rebound": 0.35, "risk_off": 0.20,
    }, fund_drawdown)
    return risk_budget_weights(ranked[:6], exposure, 0.18, 0.38, 0.30)


def strategy_c_v2(candidates: list[dict], regime: MarketRegime, fund_drawdown: float = 0.0) -> list[dict]:
    """C: end-of-day main-theme / leader / sentiment strategy; never pretends to intraday hit limits."""
    if regime.label == "risk_off":
        return []
    eligible = [
        x for x in candidates
        if _f(x, "leader_score") >= 55
        and _f(x, "industry_score") >= 55
        and _f(x, "liquidity") >= 50
        and not bool(x.get("one_word_limit"))
    ]
    ranked = []
    for x in eligible:
        score = (
            0.30 * _f(x, "leader_score")
            + 0.22 * _f(x, "theme_score")
            + 0.18 * _f(x, "sentiment_score")
            + 0.14 * _f(x, "momentum")
            + 0.10 * _f(x, "industry_score")
            + 0.06 * _f(x, "liquidity")
        )
        ranked.append(_decorate(
            x, score,
            "主线板块具有扩散度，个股具备板块核心辨识度和短线资金承接",
            "主线退潮、龙头地位丢失、炸板/亏钱效应恶化或板块共振消失",
        ))
    ranked.sort(key=lambda x: x["v2_score"], reverse=True)
    exposure = _exposure(regime, {
        "risk_on": 0.85, "neutral": 0.45, "panic_rebound": 0.20, "risk_off": 0.0,
    }, fund_drawdown)
    return risk_budget_weights(ranked[:4], exposure, 0.22, 0.45, 0.40)


def strategy_d_fallback_v2(candidates: list[dict], regime: MarketRegime, fund_drawdown: float = 0.0) -> list[dict]:
    """D deterministic fallback for shadow mode. Sol integration is a later stage."""
    ranked = []
    for x in candidates:
        score = (
            0.18 * _f(x, "trend")
            + 0.14 * _f(x, "momentum")
            + 0.14 * _f(x, "quality_score")
            + 0.11 * _f(x, "cashflow_score")
            + 0.10 * _f(x, "valuation_score")
            + 0.11 * _f(x, "industry_score")
            + 0.08 * _f(x, "leader_score")
            + 0.08 * _f(x, "risk")
            + 0.06 * _f(x, "liquidity")
        )
        ranked.append(_decorate(
            x, score,
            "技术、行业地位、已验证财务质量、估值和风险共同支持，作为D的规则兜底候选",
            "综合评分显著下降、原投资逻辑失效或组合风险约束要求降仓",
        ))
    ranked.sort(key=lambda x: x["v2_score"], reverse=True)
    exposure = _exposure(regime, {
        "risk_on": 0.85, "neutral": 0.60, "panic_rebound": 0.35, "risk_off": 0.20,
    }, fund_drawdown)
    return risk_budget_weights(ranked[:8], exposure, 0.15, 0.32, 0.28)


def strategy_l_v2(candidates: list[dict], regime: MarketRegime, fund_drawdown: float = 0.0) -> list[dict]:
    """L: verified quality/value with cash-flow discipline and a trend sanity check.

    Balance-sheet leverage is intentionally not scored yet: V2 will only add it after a
    disclosure-timed leverage source passes validation rather than substituting a fake neutral value.
    """
    eligible = [
        x for x in candidates
        if bool(x.get("fundamental_ready"))
        and _f(x, "quality_score") >= 60
        and _f(x, "cashflow_score") >= 55
        and _f(x, "trend") >= 35
        and not bool(x.get("financial_distress"))
    ]
    ranked = []
    for x in eligible:
        score = (
            0.34 * _f(x, "quality_score")
            + 0.22 * _f(x, "cashflow_score")
            + 0.22 * _f(x, "valuation_score")
            + 0.10 * _f(x, "trend")
            + 0.07 * _f(x, "risk")
            + 0.05 * _f(x, "industry_score")
        )
        ranked.append(_decorate(
            x, score,
            "已验证财务期的盈利质量和经营现金流良好，估值具有相对吸引力",
            "盈利/现金流恶化、估值失去安全边际或基本面逻辑改变",
        ))
    ranked.sort(key=lambda x: x["v2_score"], reverse=True)
    exposure = _exposure(regime, {
        "risk_on": 0.75, "neutral": 0.65, "panic_rebound": 0.50, "risk_off": 0.35,
    }, fund_drawdown)
    return risk_budget_weights(ranked[:8], exposure, 0.12, 0.30, 0.24)
