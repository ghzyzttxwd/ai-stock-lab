from __future__ import annotations


def _normalize(cands: list[dict], n: int, max_weight: float, total_weight: float) -> list[dict]:
    picks = cands[:n]
    if not picks or total_weight <= 0:
        return []
    w = min(max_weight, total_weight / len(picks))
    return [{**x, 'target_weight': round(w, 6)} for x in picks]


def _opp(x: dict) -> float:
    value = x.get('opportunity_score')
    return 50.0 if value is None else float(value)


def strategy_a(candidates: list[dict], market_score: float) -> list[dict]:
    """稳健：只做横向排名靠前的机会，弱市主动留现金。"""
    # Keep the first-stage universe slightly wider than the final conditional-plan
    # threshold (62) so board/risk filters still receive viable main-board names.
    eligible = [x for x in candidates if _opp(x) >= 60]
    ranked = sorted(
        eligible,
        key=lambda x: (0.34*x['risk'] + 0.24*x['quality'] + 0.22*_opp(x) + 0.20*x['liquidity']),
        reverse=True,
    )
    exposure = 0.60 if market_score >= 60 else 0.40 if market_score >= 45 else 0.20 if market_score >= 30 else 0.10
    return _normalize(ranked, 8, 0.09, exposure)


def strategy_b(candidates: list[dict], market_score: float) -> list[dict]:
    """趋势：弱市不追，只有横向排名和相对强度都足够高才参与。"""
    eligible = [
        x for x in candidates
        if _opp(x) >= 72
        and float(x.get('market_relative_3') or 0) > 0
        and float(x.get('overheat_score') or 0) < 68
        and float(x.get('r1') or 0) <= 0.045
    ]
    ranked = sorted(
        eligible,
        key=lambda x: (0.42*_opp(x) + 0.27*x['momentum'] + 0.19*x['trend'] + 0.12*x['liquidity']),
        reverse=True,
    )
    exposure = 0.75 if market_score >= 70 else 0.50 if market_score >= 58 else 0.25 if market_score >= 48 else 0.0
    return _normalize(ranked, 5, 0.16, exposure)


def strategy_c(candidates: list[dict], market_score: float) -> list[dict]:
    """短线：只做当天横向前列且不过热的1-3日机会。"""
    eligible = [
        x for x in candidates
        if _opp(x) >= 78
        and float(x.get('overheat_score') or 0) < 62
        and float(x.get('liquidity') or 0) >= 50
        and float(x.get('r1') or 0) <= 0.04
    ]
    ranked = sorted(
        eligible,
        key=lambda x: (0.50*_opp(x) + 0.22*x['momentum'] + 0.14*x['liquidity'] + 0.09*x['trend'] + 0.05*x['risk']),
        reverse=True,
    )
    exposure = 0.70 if market_score >= 70 else 0.45 if market_score >= 58 else 0.20 if market_score >= 48 else 0.0
    return _normalize(ranked, 4, 0.18, exposure)


def strategy_d(candidates: list[dict], market_score: float) -> list[dict]:
    """综合：横向机会排名为主，趋势/质量/估值/风险为确认层。"""
    eligible = [x for x in candidates if _opp(x) >= 65]
    ranked = sorted(eligible, key=lambda x: x['score_d'], reverse=True)
    exposure = 0.75 if market_score >= 75 else 0.55 if market_score >= 60 else 0.35 if market_score >= 45 else 0.15 if market_score >= 30 else 0.0
    return _normalize(ranked, 7, 0.13, exposure)


def strategy_l(candidates: list[dict], market_score: float, state: dict | None = None) -> list[dict]:
    """长线：保留估值/稳定性和持仓连续性，但弱市不硬塞仓位。"""
    current = set((state or {}).get('positions', {}).keys())

    def score(x: dict) -> float:
        keep_bonus = 8.0 if x['symbol'] in current else 0.0
        return (
            0.27*x['valuation'] + 0.22*x['quality'] + 0.18*x['risk'] +
            0.15*_opp(x) + 0.10*x['trend'] + 0.08*x['liquidity'] + keep_bonus
        )

    eligible = [x for x in candidates if _opp(x) >= (55 if x['symbol'] in current else 62)]
    ranked = sorted(eligible, key=score, reverse=True)
    exposure = 0.65 if market_score >= 65 else 0.50 if market_score >= 50 else 0.30 if market_score >= 35 else 0.15
    return _normalize(ranked, 7, 0.10, exposure)
