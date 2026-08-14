from __future__ import annotations

import argparse
import json
from pathlib import Path

from .enrichment import enrich_snapshot


def enrich_resilient(snapshot: dict) -> dict:
    """Preserve upstream universe safety when adding Tencent historical features."""
    enriched = enrich_snapshot(snapshot)
    base_safety = dict(snapshot.get('safety') or {})
    grade = str(base_safety.get('stock_universe_grade') or 'full')
    base_eligible = bool(base_safety.get('eligible_for_shadow_decision', grade == 'full'))
    technical_ready = bool(enriched.get('safety', {}).get('ready_for_strategy_targets'))
    final_ready = technical_ready and grade == 'full' and base_eligible

    enriched['safety']['stock_universe_grade'] = grade
    enriched['safety']['base_eligible_for_shadow_decision'] = base_eligible
    enriched['safety']['technical_coverage_ready'] = technical_ready
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
        'stock_universe_grade': enriched['safety']['stock_universe_grade'],
        'technical_coverage_ready': enriched['safety']['technical_coverage_ready'],
        'ready_for_strategy_targets': enriched['safety']['ready_for_strategy_targets'],
        'decision_block_reason': enriched['safety']['decision_block_reason'],
        'candidate_count': len(enriched['candidates']),
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
