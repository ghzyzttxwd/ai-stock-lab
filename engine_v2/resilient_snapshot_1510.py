from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.real_market import AKShareMarket


_ORIGINAL_LATEST_TRADE_DATE = AKShareMarket.latest_trade_date


def _latest_trade_date_1510(self: AKShareMarket, requested_text: str) -> str:
    """Permit the 15:10 production slot without ever silently falling back to yesterday.

    engine.real_market historically blocked current-day daily processing until 15:20. For
    the new 15:10 accounting slot, query through tomorrow only during 15:05-15:20 so the
    provider can reveal today's completed bar. If today's bar is not available yet, fail
    explicitly and let the 15:20/15:30 retry run instead of processing a stale session.
    """
    requested = date.fromisoformat(requested_text)
    now_cn = datetime.now(ZoneInfo('Asia/Shanghai'))
    if (
        requested == now_cn.date()
        and now_cn.weekday() < 5
        and dt_time(15, 5) <= now_cn.time() < dt_time(15, 20)
    ):
        proxy = (requested + timedelta(days=1)).isoformat()
        resolved = _ORIGINAL_LATEST_TRADE_DATE(self, proxy)
        if resolved != requested.isoformat():
            raise RuntimeError(
                f'15:10 session bar is not ready yet: requested={requested.isoformat()} resolved={resolved}; '
                'refusing stale-session accounting; wait for scheduled retry'
            )
        return resolved
    return _ORIGINAL_LATEST_TRADE_DATE(self, requested_text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    AKShareMarket.latest_trade_date = _latest_trade_date_1510
    from .resilient_snapshot import build_resilient_snapshot

    snap = build_resilient_snapshot(args.date)
    if str(snap.get('trade_date') or '')[:10] != args.date:
        raise RuntimeError(
            f'15:10 V2 snapshot date mismatch: requested={args.date} actual={snap.get("trade_date")}'
        )
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'trade_date': snap.get('trade_date'),
        'stock_source': (snap.get('source_notes') or {}).get('stock_snapshot'),
        'stock_universe_grade': (snap.get('safety') or {}).get('stock_universe_grade'),
        'eligible_for_shadow_decision': (snap.get('safety') or {}).get('eligible_for_shadow_decision'),
        'slot': '15:10',
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
