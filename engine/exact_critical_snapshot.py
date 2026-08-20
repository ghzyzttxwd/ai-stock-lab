from __future__ import annotations

from .tencent_full_market import _critical_symbols


def overlay_exact_critical_rows(
    rows: list[dict],
    exact: dict[str, dict],
    trade_date: str,
    critical: dict[str, str],
) -> list[dict]:
    """Overlay exact-date completed daily bars onto every critical V1 symbol.

    Full-market spot tables are useful for cross-sectional features but do not carry a
    trustworthy session date in the normalized row. Holdings and pending orders are
    accounting/execution-critical, so their OHLC/preclose/amount must come from the daily
    record that ``execution_bars`` matched to ``trade_date``.
    """
    missing = sorted(set(critical) - set(exact))
    if missing:
        raise RuntimeError(
            'Exact-date critical bar coverage incomplete: '
            f'{len(exact)}/{len(critical)} for {trade_date}; '
            f'missing={",".join(missing[:16])}'
        )

    by_code = {row.get('code'): dict(row) for row in rows if row.get('code')}
    for sym, bar in exact.items():
        base = by_code.get(sym, {'code': sym, 'raw_code': sym[-6:], 'name': critical.get(sym, sym)})
        for key in ('open', 'high', 'low', 'close', 'preclose', 'amount', 'tradestatus'):
            if key in bar:
                base[key] = bar[key]
        for key in ('name', 'raw_code'):
            if not base.get(key) and bar.get(key):
                base[key] = bar[key]
        base['exact_bar_source'] = str(bar.get('source') or 'tencent-execution')
        base['exact_bar_date'] = trade_date
        base['exact_bar_date_evidence'] = 'execution_bars_exact_date_match'
        by_code[sym] = base
    return list(by_code.values())


def install() -> None:
    """Wrap V1 snapshots so critical accounting bars are always exact-date daily bars."""
    from .real_market import AKShareMarket

    if getattr(AKShareMarket.snapshot, '_v1_exact_critical_installed', False):
        return

    original_snapshot = AKShareMarket.snapshot

    def snapshot(self):
        rows = original_snapshot(self)
        trade_date = str(getattr(self, '_resolved_trade_date', '') or '')[:10]
        critical = _critical_symbols()
        if not critical:
            return rows
        if not trade_date:
            # Snapshot-only exploratory callers may not have resolved a session yet. Do not
            # invent a date; production paths always call latest_trade_date first.
            print('[market] exact critical overlay skipped: no resolved trade date')
            return rows

        exact = self.execution_bars(critical, trade_date)
        overlaid = overlay_exact_critical_rows(rows, exact, trade_date, critical)
        print(
            f'[market] exact critical overlay trade_date={trade_date} '
            f'critical={len(critical)} exact={len(exact)}'
        )
        return overlaid

    snapshot._v1_exact_critical_installed = True
    AKShareMarket.snapshot = snapshot
