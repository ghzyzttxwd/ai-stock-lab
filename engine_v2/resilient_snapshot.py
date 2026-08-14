from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .snapshot import build_snapshot

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / 'state' / 'market_universe.json'


def _load_recovery_universe(trade_date: str, path: Path = CACHE_PATH) -> tuple[list[dict], dict]:
    """Load a recent production recovery universe without treating it as a full market universe."""
    if not path.exists():
        raise RuntimeError(f'V2 recovery cache missing: {path}')
    payload = json.loads(path.read_text(encoding='utf-8'))
    asof = str(payload.get('asof') or '')[:10]
    if not asof:
        raise RuntimeError('V2 recovery cache has no asof date')
    age = (date.fromisoformat(trade_date) - date.fromisoformat(asof)).days
    if age < 0 or age > 14:
        raise RuntimeError(f'V2 recovery cache stale/future: asof={asof} age={age}d')
    rows = [dict(x) for x in (payload.get('symbols') or []) if x.get('code')]
    if len(rows) < 50:
        raise RuntimeError(f'V2 recovery cache too small: {len(rows)}')
    return rows, {'asof': asof, 'age_days': age, 'symbols': len(rows)}


def _install_market_snapshot_recovery(trade_date: str, recovery_meta: dict) -> None:
    """Patch only this process: strict full snapshot first, cached universe + Tencent on failure."""
    from engine.real_market import AKShareMarket

    original = AKShareMarket.snapshot

    def resilient_snapshot(self):
        try:
            rows = original(self)
            recovery_meta.update({'mode': 'full', 'primary_error': None})
            return rows
        except Exception as exc:
            selected, cache = _load_recovery_universe(trade_date)
            histories = self.histories(selected, trade_date)
            rows = self.snapshot_from_histories(selected, histories, trade_date)
            required = max(40, int(len(histories) * 0.70))
            if len(rows) < required:
                raise RuntimeError(
                    f'V2 Tencent recovery coverage too low: {len(rows)}/{len(histories)}, require >= {required}'
                ) from exc
            recovery_meta.update({
                'mode': 'degraded-cache',
                'primary_error': f'{type(exc).__name__}: {exc}',
                'cache': cache,
                'current_rows': len(rows),
                'current_histories': len(histories),
            })
            print(
                f'[V2 SNAPSHOT] full-market unavailable; using DEGRADED cache+Tencent '
                f'cache={cache["symbols"]} histories={len(histories)} current={len(rows)} asof={cache["asof"]}'
            )
            return rows

    AKShareMarket.snapshot = resilient_snapshot


def _build_and_capture_sentiment(requested_date: str) -> tuple[dict, dict]:
    """Build once and preserve the exact sentiment detail already used by the snapshot.

    The base snapshot historically stored only aggregate sentiment counts. Downstream enrichment then
    fetched the same Eastmoney pools again, creating a needless second network failure point and a
    possible within-run data mismatch. Capture the first successful point-in-time payload instead.
    """
    import engine_v2.snapshot as snapshot_module

    original = snapshot_module._sentiment_snapshot
    captured: dict = {}

    def capture(ak, trade_date: str):
        result = original(ak, trade_date)
        captured.clear()
        captured.update(result)
        captured['trade_date'] = trade_date
        return result

    snapshot_module._sentiment_snapshot = capture
    try:
        snap = build_snapshot(requested_date)
    finally:
        snapshot_module._sentiment_snapshot = original
    return snap, captured


def build_resilient_snapshot(requested_date: str) -> dict:
    """Build the normal V2 snapshot, preserving strict decision-grade distinctions and one-shot inputs."""
    from engine.real_market import AKShareMarket

    trade_date = AKShareMarket().latest_trade_date(requested_date)
    meta: dict = {}
    _install_market_snapshot_recovery(trade_date, meta)
    snap, sentiment_detail = _build_and_capture_sentiment(requested_date)

    if not sentiment_detail or sentiment_detail.get('trade_date') != snap.get('trade_date'):
        raise RuntimeError('V2 snapshot failed to preserve same-session sentiment detail')
    snap['market']['sentiment_detail'] = sentiment_detail
    snap['source_notes']['sentiment_detail'] = 'persisted from the same one-shot point-in-time snapshot; downstream must not refetch'

    mode = str(meta.get('mode') or 'full')
    full = mode == 'full'
    snap['source_notes']['stock_universe_mode'] = mode
    snap['source_notes']['stock_universe_grade'] = 'full' if full else 'degraded'
    if not full:
        snap['source_notes']['recovery'] = meta
    snap['safety']['stock_universe_grade'] = 'full' if full else 'degraded'
    snap['safety']['eligible_for_shadow_decision'] = full
    snap['safety']['decision_block_reason'] = None if full else (
        'Full-market Eastmoney/Sina snapshot unavailable; cached production universe is allowed for '
        'pipeline continuity only and must not be treated as a complete cross-sectional decision universe.'
    )
    return snap


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    snap = build_resilient_snapshot(args.date)
    text = json.dumps(snap, ensure_ascii=False, indent=2)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + '\n', encoding='utf-8')
    print(json.dumps({
        'trade_date': snap['trade_date'],
        'stock_source': snap['source_notes']['stock_snapshot'],
        'stock_universe_mode': snap['source_notes']['stock_universe_mode'],
        'stock_universe_grade': snap['safety']['stock_universe_grade'],
        'eligible_for_shadow_decision': snap['safety']['eligible_for_shadow_decision'],
        'sentiment_detail': {
            'limit_up': len(snap['market']['sentiment_detail'].get('limit_up', [])),
            'broken_limit': len(snap['market']['sentiment_detail'].get('broken_limit', [])),
            'limit_down': len(snap['market']['sentiment_detail'].get('limit_down', [])),
        },
        'preselection_union': snap['preselection']['union_count'],
        'safety': snap['safety'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
