from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import date
from pathlib import Path

from .snapshot import build_snapshot
from .shadow_ledger import sha256_json

ROOT = Path(__file__).resolve().parents[1]
V2_CACHE_PATH = ROOT / 'shadow_state' / 'v2' / 'cache' / 'market_universe.json'
V2_DECISION_SNAPSHOT_CACHE_PATH = ROOT / 'shadow_state' / 'v2' / 'cache' / 'normalized_snapshot.json'


def _cache_path() -> Path:
    return Path(os.getenv('V2_UNIVERSE_CACHE_PATH', str(V2_CACHE_PATH)))


def _decision_snapshot_cache_path() -> Path:
    return Path(os.getenv('V2_DECISION_SNAPSHOT_CACHE_PATH', str(V2_DECISION_SNAPSHOT_CACHE_PATH)))


def _save_decision_snapshot(snapshot: dict, path: Path | None = None) -> None:
    """Persist only a full, decision-grade V2 snapshot for exact-session recovery."""
    path = path or _decision_snapshot_cache_path()
    safety = dict(snapshot.get('safety') or {})
    if (
        safety.get('stock_universe_grade') != 'full'
        or not safety.get('eligible_for_shadow_decision')
        or safety.get('calls_sol')
        or safety.get('writes_ledgers')
    ):
        raise RuntimeError('refusing to cache a non-decision-grade V2 snapshot')
    trade_date = str(snapshot.get('trade_date') or '')[:10]
    detail_date = str(((snapshot.get('market') or {}).get('sentiment_detail') or {}).get('trade_date') or '')[:10]
    if not trade_date or detail_date != trade_date:
        raise RuntimeError('refusing to cache V2 snapshot without same-session sentiment detail')
    payload = {
        'cache_version': 'v2-normalized-snapshot-1',
        'trade_date': trade_date,
        'snapshot_sha256': sha256_json(snapshot),
        'snapshot': snapshot,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def _load_decision_snapshot(trade_date: str, path: Path | None = None) -> tuple[dict, dict]:
    """Load an exact-date, hashed, formerly decision-grade V2 snapshot; never use stale data."""
    path = path or _decision_snapshot_cache_path()
    if not path.exists():
        raise RuntimeError(f'V2 decision snapshot cache missing: {path}')
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('cache_version') != 'v2-normalized-snapshot-1':
        raise RuntimeError(f'unsupported V2 decision snapshot cache version: {payload.get("cache_version")}')
    cached_date = str(payload.get('trade_date') or '')[:10]
    if cached_date != trade_date:
        raise RuntimeError(f'V2 decision snapshot cache date mismatch: cached={cached_date} requested={trade_date}')
    snapshot = dict(payload.get('snapshot') or {})
    if str(snapshot.get('trade_date') or '')[:10] != trade_date:
        raise RuntimeError('V2 decision snapshot payload date mismatch')
    actual_hash = sha256_json(snapshot)
    if actual_hash != payload.get('snapshot_sha256'):
        raise RuntimeError('V2 decision snapshot cache hash mismatch')
    safety = dict(snapshot.get('safety') or {})
    detail_date = str(((snapshot.get('market') or {}).get('sentiment_detail') or {}).get('trade_date') or '')[:10]
    if (
        safety.get('stock_universe_grade') != 'full'
        or not safety.get('eligible_for_shadow_decision')
        or safety.get('calls_sol')
        or safety.get('writes_ledgers')
        or detail_date != trade_date
    ):
        raise RuntimeError('V2 decision snapshot cache failed safety validation')
    return copy.deepcopy(snapshot), {'path': str(path), 'trade_date': cached_date, 'snapshot_sha256': actual_hash}


def _load_recovery_universe(trade_date: str, path: Path | None = None) -> tuple[list[dict], dict]:
    """Load only the V2-owned recovery universe; V1 state is never consulted."""
    path = path or _cache_path()
    if not path.exists():
        raise RuntimeError(f'V2 recovery cache missing: {path}')
    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('cache_version') != 'v2-market-universe-1':
        raise RuntimeError(f'unsupported V2 recovery cache version: {payload.get("cache_version")}')
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


def _save_recovery_universe(snapshot: dict, path: Path | None = None) -> None:
    path = path or _cache_path()
    rows = []
    keep = ('code', 'raw_code', 'name', 'peTTM', 'pbMRQ', 'r60_snapshot', 'amount', 'turn')
    for item in (snapshot.get('preselection') or {}).get('rows') or []:
        if item.get('code'):
            rows.append({key: item.get(key) for key in keep})
    if len(rows) < 50:
        raise RuntimeError(f'refusing to persist undersized V2 recovery universe: {len(rows)}')
    payload = {
        'cache_version': 'v2-market-universe-1',
        'asof': snapshot.get('trade_date'),
        'source': 'V2 full decision-grade snapshot preselection',
        'symbols': rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


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
    try:
        snap, sentiment_detail = _build_and_capture_sentiment(requested_date)
    except Exception as first_error:
        try:
            cached, cache_meta = _load_decision_snapshot(trade_date)
        except Exception as cache_error:
            print(
                f'[V2 SNAPSHOT] first live build failed and exact-session cache is unavailable; '
                f'retrying once: live={type(first_error).__name__}: {first_error}; '
                f'cache={type(cache_error).__name__}: {cache_error}'
            )
            try:
                snap, sentiment_detail = _build_and_capture_sentiment(requested_date)
            except Exception as second_error:
                raise RuntimeError(
                    f'V2 snapshot failed twice with no valid exact-session cache: '
                    f'first={type(first_error).__name__}: {first_error}; '
                    f'second={type(second_error).__name__}: {second_error}'
                ) from second_error
        else:
            cached.setdefault('source_notes', {})['snapshot_recovery_mode'] = 'exact-session-v2-cache'
            cached['source_notes']['snapshot_cache'] = {
                **cache_meta,
                'live_error': f'{type(first_error).__name__}: {first_error}',
            }
            cached.setdefault('safety', {})['snapshot_cache_reused'] = True
            cached['safety']['reads_v1_ledger'] = False
            cached['safety']['writes_v1_ledger'] = False
            print(
                f'[V2 SNAPSHOT] live build failed; reusing hashed exact-session V2 cache '
                f'date={trade_date} sha256={cache_meta["snapshot_sha256"]}'
            )
            return cached

    if not sentiment_detail or sentiment_detail.get('trade_date') != snap.get('trade_date'):
        raise RuntimeError('V2 snapshot failed to preserve same-session sentiment detail')
    snap['market']['sentiment_detail'] = sentiment_detail
    snap['source_notes']['sentiment_detail'] = 'persisted from the same one-shot point-in-time snapshot; downstream must not refetch'

    mode = str(meta.get('mode') or 'full')
    full = mode == 'full'
    if full:
        _save_recovery_universe(snap)
    snap['source_notes']['stock_universe_mode'] = mode
    snap['source_notes']['stock_universe_grade'] = 'full' if full else 'degraded'
    if not full:
        snap['source_notes']['recovery'] = meta
    snap['safety']['stock_universe_grade'] = 'full' if full else 'degraded'
    snap['safety']['eligible_for_shadow_decision'] = full
    snap['safety']['decision_block_reason'] = None if full else (
        'Full-market Eastmoney/Sina snapshot unavailable; cached V2 universe is allowed for '
        'pipeline continuity only and must not be treated as a complete cross-sectional decision universe.'
    )
    snap['safety']['reads_v1_ledger'] = False
    snap['safety']['writes_v1_ledger'] = False
    snap['safety']['snapshot_cache_reused'] = False
    snap['source_notes']['snapshot_recovery_mode'] = 'live'
    if full:
        _save_decision_snapshot(snap)
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

