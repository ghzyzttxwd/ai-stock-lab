from __future__ import annotations


def _normalize(cands: list[dict], n: int, max_weight: float, total_weight: float) -> list[dict]:
    picks = cands[:n]
    if not picks:
        return []
    w = min(max_weight, total_weight / len(picks))
    return [{**x, 'target_weight': round(w, 6)} for x in picks]


def strategy_a(candidates: list[dict], market_score: float) -> list[dict]:
    """稳健：优先风险控制、趋势确认与流动性。"""
    ranked = sorted(candidates, key=lambda x: (0.45*x['risk'] + 0.30*x['trend'] + 0.25*x['liquidity']), reverse=True)
    exposure = 0.70 if market_score >= 55 else 0.45 if market_score >= 40 else 0.20
    return _normalize(ranked, 10, 0.10, exposure)


def strategy_b(candidates: list[dict], market_score: float) -> list[dict]:
    """趋势：追随中短期强势方向。"""
    ranked = sorted(candidates, key=lambda x: (0.55*x['trend'] + 0.35*x['momentum'] + 0.10*x['liquidity']), reverse=True)
    exposure = 0.95 if market_score >= 65 else 0.70 if market_score >= 50 else 0.30
    return _normalize(ranked, 6, 0.20, exposure)


def strategy_c(candidates: list[dict], market_score: float) -> list[dict]:
    """短线：高换手、高弹性，主要看动量、突破趋势和成交活跃度。

    仍然只做沪深主板普通股票；成交由 broker 统一执行，受 T+1、涨跌停、
    100 股整数、手续费与滑点约束。目标持有周期设计为数个交易日量级，
    但不会为了交易而强制每天换仓。
    """
    ranked = sorted(
        candidates,
        key=lambda x: (0.48*x['momentum'] + 0.32*x['trend'] + 0.15*x['liquidity'] + 0.05*x['risk']),
        reverse=True,
    )
    exposure = 0.95 if market_score >= 65 else 0.78 if market_score >= 50 else 0.45 if market_score >= 35 else 0.0
    return _normalize(ranked, 4, 0.22, exposure)


def strategy_d(candidates: list[dict], market_score: float) -> list[dict]:
    """综合：多维评分后再由 AI/规则决定组合。"""
    ranked = sorted(candidates, key=lambda x: x['score_d'], reverse=True)
    exposure = 0.90 if market_score >= 80 else 0.75 if market_score >= 60 else 0.55 if market_score >= 40 else 0.30 if market_score >= 20 else 0.0
    return _normalize(ranked, 8, 0.15, exposure)


def strategy_l(candidates: list[dict], market_score: float, state: dict | None = None) -> list[dict]:
    """长线：偏估值、风险、质量与中期趋势，并给现有持仓保留奖励降低换手。

    V1 的财务质量数据还比较基础；接入真实财报层后会继续增强这一策略。
    """
    current = set((state or {}).get('positions', {}).keys())

    def score(x: dict) -> float:
        keep_bonus = 10.0 if x['symbol'] in current else 0.0
        return (
            0.30*x['valuation'] + 0.23*x['risk'] + 0.20*x['trend'] +
            0.17*x['quality'] + 0.10*x['liquidity'] + keep_bonus
        )

    ranked = sorted(candidates, key=score, reverse=True)
    exposure = 0.80 if market_score >= 65 else 0.65 if market_score >= 45 else 0.45 if market_score >= 25 else 0.20
    return _normalize(ranked, 8, 0.12, exposure)
