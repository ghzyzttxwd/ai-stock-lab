from __future__ import annotations


def _normalize(cands: list[dict], n: int, max_weight: float, total_weight: float) -> list[dict]:
    picks = cands[:n]
    if not picks:
        return []
    w = min(max_weight, total_weight / len(picks))
    return [{**x, 'target_weight': round(w, 6)} for x in picks]


def _opp(x: dict) -> float:
    return float(x.get('opportunity_score') or 0.0)


def strategy_a(candidates: list[dict], market_score: float) -> list[dict]:
    """稳健：先有合格买点，再强调风险、稳定性与流动性。"""
    eligible=[x for x in candidates if _opp(x) >= 46]
    ranked = sorted(eligible, key=lambda x: (0.34*x['risk'] + 0.24*x['quality'] + 0.22*_opp(x) + 0.20*x['liquidity']), reverse=True)
    exposure = 0.70 if market_score >= 55 else 0.45 if market_score >= 40 else 0.20
    return _normalize(ranked, 10, 0.10, exposure)


def strategy_b(candidates: list[dict], market_score: float) -> list[dict]:
    """趋势：只追相对市场真正强、且短周期机会仍在的股票。"""
    eligible=[x for x in candidates if _opp(x) >= 52 and float(x.get('market_relative_3') or 0) > 0]
    ranked = sorted(eligible, key=lambda x: (0.42*_opp(x) + 0.27*x['momentum'] + 0.19*x['trend'] + 0.12*x['liquidity']), reverse=True)
    exposure = 0.95 if market_score >= 65 else 0.70 if market_score >= 50 else 0.30
    return _normalize(ranked, 6, 0.20, exposure)


def strategy_c(candidates: list[dict], market_score: float) -> list[dict]:
    """短线：目标就是未来1-3个交易日，而不是过去60日涨幅排行榜。"""
    eligible=[
        x for x in candidates
        if _opp(x) >= 56
        and float(x.get('overheat_score') or 0) < 65
        and float(x.get('liquidity') or 0) >= 45
    ]
    ranked = sorted(
        eligible,
        key=lambda x: (0.50*_opp(x) + 0.22*x['momentum'] + 0.14*x['liquidity'] + 0.09*x['trend'] + 0.05*x['risk']),
        reverse=True,
    )
    exposure = 0.95 if market_score >= 65 else 0.78 if market_score >= 50 else 0.45 if market_score >= 35 else 0.0
    return _normalize(ranked, 4, 0.22, exposure)


def strategy_d(candidates: list[dict], market_score: float) -> list[dict]:
    """综合：机会分为主，趋势/质量/估值/风险为确认层。"""
    eligible=[x for x in candidates if _opp(x) >= 48]
    ranked = sorted(eligible, key=lambda x: x['score_d'], reverse=True)
    exposure = 0.90 if market_score >= 80 else 0.75 if market_score >= 60 else 0.55 if market_score >= 40 else 0.30 if market_score >= 20 else 0.0
    return _normalize(ranked, 8, 0.15, exposure)


def strategy_l(candidates: list[dict], market_score: float, state: dict | None = None) -> list[dict]:
    """长线：保留估值/稳定性，但也不在明显坏买点硬塞仓位。"""
    current = set((state or {}).get('positions', {}).keys())

    def score(x: dict) -> float:
        keep_bonus = 8.0 if x['symbol'] in current else 0.0
        return (
            0.27*x['valuation'] + 0.22*x['quality'] + 0.18*x['risk'] +
            0.15*_opp(x) + 0.10*x['trend'] + 0.08*x['liquidity'] + keep_bonus
        )

    eligible=[x for x in candidates if _opp(x) >= (43 if x['symbol'] in current else 47)]
    ranked = sorted(eligible, key=score, reverse=True)
    exposure = 0.80 if market_score >= 65 else 0.65 if market_score >= 45 else 0.45 if market_score >= 25 else 0.20
    return _normalize(ranked, 8, 0.12, exposure)
