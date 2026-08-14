from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .regime import MarketRegime
from .strategies import (
    strategy_a_v2,
    strategy_b_v2,
    strategy_c_v2,
    strategy_d_fallback_v2,
    strategy_l_v2,
)


POLICY = {
    'A': {'max_weight': 0.10, 'industry_cap': 0.25, 'max_positions': 10},
    'B': {'max_weight': 0.18, 'industry_cap': 0.38, 'max_positions': 6},
    'C': {'max_weight': 0.22, 'industry_cap': 0.45, 'max_positions': 4},
    'D': {'max_weight': 0.15, 'industry_cap': 0.32, 'max_positions': 8},
    'L': {'max_weight': 0.12, 'industry_cap': 0.30, 'max_positions': 8},
}


def _regime(payload: dict) -> MarketRegime:
    r = dict((payload.get('market') or {}).get('regime') or {})
    return MarketRegime(
        str(r.get('label') or 'neutral'),
        float(r.get('score') or 50.0),
        float(r.get('confidence') or 0.0),
        tuple(r.get('reasons') or ()),
    )


def _code(x: dict) -> str:
    return str(x.get('raw_code') or x.get('symbol') or x.get('code') or '')[-6:]


def _portfolio_stats(targets: list[dict]) -> dict:
    industry = defaultdict(float)
    for x in targets:
        industry[str(x.get('industry') or 'UNKNOWN')] += float(x.get('target_weight') or 0.0)
    return {
        'positions': len(targets),
        'exposure': round(sum(float(x.get('target_weight') or 0.0) for x in targets), 6),
        'max_position': round(max((float(x.get('target_weight') or 0.0) for x in targets), default=0.0), 6),
        'industry_weights': dict(sorted(
            ((k, round(v, 6)) for k, v in industry.items()),
            key=lambda kv: kv[1], reverse=True,
        )),
        'max_industry': round(max(industry.values(), default=0.0), 6),
    }


def _jaccard(a: list[dict], b: list[dict]) -> float:
    sa = {_code(x) for x in a if _code(x)}
    sb = {_code(x) for x in b if _code(x)}
    union = sa | sb
    return round(len(sa & sb) / len(union), 4) if union else 0.0


def _validate_one(label: str, targets: list[dict], regime: MarketRegime) -> list[str]:
    p = POLICY[label]
    errors: list[str] = []
    codes = [_code(x) for x in targets]
    if len(codes) != len(set(codes)):
        errors.append('duplicate stock code')
    if len(targets) > p['max_positions']:
        errors.append(f'positions {len(targets)} > {p["max_positions"]}')
    if sum(float(x.get('target_weight') or 0.0) for x in targets) > 1.000001:
        errors.append('exposure > 100%')
    for x in targets:
        w = float(x.get('target_weight') or 0.0)
        if w <= 0 or w > p['max_weight'] + 1e-6:
            errors.append(f'{_code(x)} weight {w:.4f} outside policy')
        if not str(x.get('thesis') or '').strip():
            errors.append(f'{_code(x)} missing thesis')
        if not str(x.get('invalidation') or '').strip():
            errors.append(f'{_code(x)} missing invalidation')
    industries = defaultdict(float)
    for x in targets:
        industries[str(x.get('industry') or 'UNKNOWN')] += float(x.get('target_weight') or 0.0)
    if max(industries.values(), default=0.0) > p['industry_cap'] + 1e-6:
        errors.append(f'industry cap exceeded: {max(industries.values()):.4f}')

    if label == 'A':
        for x in targets:
            if not x.get('fundamental_ready') or x.get('financial_distress'):
                errors.append(f'{_code(x)} violates A fundamental gate')
    elif label == 'B':
        for x in targets:
            if float(x.get('industry_score') or 0) < 52 or float(x.get('trend') or 0) < 55:
                errors.append(f'{_code(x)} violates B trend/industry gate')
    elif label == 'C':
        if regime.label == 'risk_off' and targets:
            errors.append('C must be flat in risk_off')
        for x in targets:
            if x.get('one_word_limit'):
                errors.append(f'{_code(x)} one-word limit is not executable by C')
            if float(x.get('leader_score') or 0) < 55:
                errors.append(f'{_code(x)} violates C leader gate')
    elif label == 'L':
        for x in targets:
            if not x.get('fundamental_ready') or x.get('financial_distress'):
                errors.append(f'{_code(x)} violates L fundamental gate')
            if x.get('valuation_score') is None:
                errors.append(f'{_code(x)} L missing valuation score')
    return errors


def build_shadow_targets(enriched: dict, fund_drawdowns: dict | None = None) -> dict:
    safety = dict(enriched.get('safety') or {})
    if not safety.get('ready_for_strategy_targets'):
        raise RuntimeError(
            'V2 target generation blocked by upstream safety: '
            + str(safety.get('decision_block_reason') or 'not ready')
        )
    candidates = list(enriched.get('candidates') or [])
    if len(candidates) < 100:
        raise RuntimeError(f'V2 target generation requires >=100 candidates, got {len(candidates)}')
    regime = _regime(enriched)
    dds = dict(fund_drawdowns or {})

    targets = {
        'A': strategy_a_v2(candidates, regime, float(dds.get('A') or 0.0)),
        'B': strategy_b_v2(candidates, regime, float(dds.get('B') or 0.0)),
        'C': strategy_c_v2(candidates, regime, float(dds.get('C') or 0.0)),
        'D': strategy_d_fallback_v2(candidates, regime, float(dds.get('D') or 0.0)),
        'L': strategy_l_v2(candidates, regime, float(dds.get('L') or 0.0)),
    }

    validation = {k: _validate_one(k, v, regime) for k, v in targets.items()}
    overlap = {}
    labels = list(targets)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            overlap[f'{a}-{b}'] = _jaccard(targets[a], targets[b])

    # Distinctness is diagnostic, not a hard optimization target. A high overlap is surfaced for
    # review rather than silently tuning parameters to make the experiment look diverse.
    high_overlap = {k: v for k, v in overlap.items() if v >= 0.60}
    all_errors = {k: v for k, v in validation.items() if v}
    return {
        'target_version': 'v2-shadow-targets-0.1',
        'trade_date': enriched.get('trade_date'),
        'decision_for': 'next_trading_session_open',
        'regime': {
            'label': regime.label,
            'score': regime.score,
            'confidence': regime.confidence,
            'reasons': list(regime.reasons),
        },
        'fund_drawdowns_used': {k: float(dds.get(k) or 0.0) for k in labels},
        'targets': targets,
        'stats': {k: _portfolio_stats(v) for k, v in targets.items()},
        'overlap_jaccard': overlap,
        'high_overlap_pairs': high_overlap,
        'validation_errors': all_errors,
        'safety': {
            'writes_ledgers': False,
            'calls_sol': False,
            'executes_orders': False,
            'ready_for_shadow_accounting': False,
            'targets_valid': not bool(all_errors),
            'd_mode': 'deterministic_fallback_only',
            'next_required_stage': (
                'review live target distinctness and execution feasibility; then build separate V2 shadow ledgers'
            ),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--enriched', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    enriched = json.loads(Path(args.enriched).read_text(encoding='utf-8'))
    result = build_shadow_targets(enriched)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'trade_date': result['trade_date'],
        'regime': result['regime'],
        'stats': result['stats'],
        'overlap_jaccard': result['overlap_jaccard'],
        'high_overlap_pairs': result['high_overlap_pairs'],
        'validation_errors': result['validation_errors'],
        'safety': result['safety'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
