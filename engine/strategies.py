from __future__ import annotations


def _normalize(cands: list[dict], n: int, max_weight: float, total_weight: float) -> list[dict]:
    picks = cands[:n]
    if not picks:
        return []
    w = min(max_weight, total_weight / len(picks))
    return [{**x, 'target_weight': round(w, 6)} for x in picks]


def strategy_a(candidates: list[dict], market_score: float) -> list[dict]:
    ranked = sorted(candidates, key=lambda x: (0.45*x['risk'] + 0.30*x['trend'] + 0.25*x['liquidity']), reverse=True)
    exposure = 0.70 if market_score >= 55 else 0.45 if market_score >= 40 else 0.20
    return _normalize(ranked, 10, 0.10, exposure)


def strategy_b(candidates: list[dict], market_score: float) -> list[dict]:
    ranked = sorted(candidates, key=lambda x: (0.55*x['trend'] + 0.35*x['momentum'] + 0.10*x['liquidity']), reverse=True)
    exposure = 0.95 if market_score >= 65 else 0.70 if market_score >= 50 else 0.30
    return _normalize(ranked, 6, 0.20, exposure)


def strategy_d(candidates: list[dict], market_score: float) -> list[dict]:
    ranked = sorted(candidates, key=lambda x: x['score_d'], reverse=True)
    exposure = 0.90 if market_score >= 80 else 0.75 if market_score >= 60 else 0.55 if market_score >= 40 else 0.30 if market_score >= 20 else 0.0
    return _normalize(ranked, 8, 0.15, exposure)
