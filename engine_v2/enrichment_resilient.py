from __future__ import annotations

import argparse
import json
from pathlib import Path

from .enrichment import enrich_snapshot


def _install_persisted_sentiment(snapshot: dict):
    """Temporarily make enrichment reuse the exact sentiment payload already used by the snapshot."""
    import engine_v2.snapshot as snapshot_module

    detail = dict((snapshot.get('market') or {}).get('sentiment_detail') or {})
    trade_date = str(snapshot.get('trade_date') or '')
    if not detail or str(detail.get('trade_date') or '') != trade_date:
        raise RuntimeError('V2 enrichment requires same-session persisted sentiment detail; refusing a second network fetch')

    original = snapshot_module._sentiment_snapshot

    def persisted(_ak, requested_trade_date: str):
        if str(requested_trade_date) != trade_date:
            raise RuntimeError(
                f'persisted sentiment date mismatch: requested={requested_trade_date} stored={trade_date}'
            )
        return detail

    snapshot_module._sentiment_snapshot = persisted
    return snapshot_module, original, detail


def enrich_resilient(snapshot: dict) -> dict:
    """Preserve upstream universe safety and reuse one-shot point-in-time inputs during enrichment."""
    snapshot_module, original_sentiment, sentiment_detail = _install_persisted_sentiment(snapshot)
    try:
        enriched = enrich_snapshot(snapshot)
    finally:
        snapshot_module._sentiment_snapshot = original_sentiment

    base_safety = dict(snapshot.get('safety') or {})
    grade = str(base_safety.get('stock_universe_grade') or 'full')
    base_eligible = bool(base_safety.get('eligible_for_shadow_decision', grade == 'full'))
    technical_ready = bool(enriched.get('safety', {}).get('ready_for_strategy_targets'))
    final_ready = technical_ready and grade == 'full' and base_eligible

    enriched.setdefault('factor_notes', {})['sentiment_detail'] = (
        'reused exact limit-up/broken-limit/limit-down detail persisted by the same normalized snapshot; no second provider request'
    )
    enriched['sentiment_detail_provenance'] = {
        'trade_date': sentiment_detail.get('trade_date'),
        'source': 'persisted-same-snapshot',
        'limit_up': len(sentiment_detail.get('limit_up') or []),
        'broken_limit': len(sentiment_detail.get('broken_limit') or []),
        'limit_down': len(sentiment_detail.get('limit_down') or []),
    }

    enriched['safety']['stock_universe_grade'] = grade
    enriched['safety']['base_eligible_for_shadow_decision'] = base_eligible
    enriched['safety']['technical_coverage_ready'] = technical_ready
    enriched['safety']['sentiment_detail_grade'] = 'same-snapshot-persisted'
    enriched['safety']['ready_for_strategy_targets'] = final_ready
    if not final_ready:
        if grade != 'full' or not base_eligible:
            enriched['safety']['decision_block_reason'] = base_safety.get('decision_block_reason') or (
                'Upstream stock universe is degraded; Tencent history enrichment cannot restore missing '
                'cross-sectional stocks, so strategy targets remain blocked.'
            )
        else:
            enriched['safety']['decision_block_reason'] = 'Tencent history/feature coverage below decision threshold.'
    else:
        enriched['safety']['decision_block_reason'] = None
    return enriched


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    enriched = enrich_resilient(snapshot)
    text = json.dumps(enriched, ensure_ascii=False, indent=2)
    Path(args.output).write_text(text + '\n', encoding='utf-8')
    print(json.dumps({
        'trade_date': enriched['trade_date'],
        'coverage': enriched['coverage'],
        'sentiment_detail': enriched['sentiment_detail_provenance'],
        'stock_universe_grade': enriched['safety']['stock_universe_grade'],
        'technical_coverage_ready': enriched['safety']['technical_coverage_ready'],
        'ready_for_strategy_targets': enriched['safety']['ready_for_strategy_targets'],
        'decision_block_reason': enriched['safety']['decision_block_reason'],
        'candidate_count': len(enriched['candidates']),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
