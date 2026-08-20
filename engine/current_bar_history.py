from __future__ import annotations

import math
from datetime import date, timedelta

from .real_market import _f, _tx_amount_and_volume, _tx_amount_mode, _tx_symbol


def _valid_current_bar(row: dict | None) -> bool:
    if not row:
        return False
    return all(_f(row.get(key)) > 0 for key in ('open', 'high', 'low', 'close', 'preclose'))


def _append_current_qfq(rows: list[dict], bar: dict, trade_date: str, history_limit: int) -> list[dict] | None:
    """Append a verified current unadjusted bar onto a stale qfq series.

    Tencent's qfq endpoint can lag the completed session even when its unadjusted/current
    market data is already available. The stale qfq series' final close and the current
    bar's preclose must describe the same prior close. Their ratio is used as the qfq scale.
    A >3% mismatch is treated as a possible corporate-action / basis mismatch and rejected.
    """
    if not rows or not _valid_current_bar(bar):
        return None
    if str(rows[-1].get('date') or '')[:10] >= trade_date:
        return rows

    qfq_prev = _f(rows[-1].get('close'))
    raw_prev = _f(bar.get('preclose'))
    if qfq_prev <= 0 or raw_prev <= 0:
        return None

    scale = qfq_prev / raw_prev
    if not 0.97 <= scale <= 1.03:
        print(
            f'[market] refuse current-bar qfq bridge {bar.get("code")}: '
            f'qfq_prev={qfq_prev:.4f} raw_preclose={raw_prev:.4f} scale={scale:.6f}'
        )
        return None

    raw_close = _f(bar.get('close'))
    amount = _f(bar.get('amount'))
    close = raw_close * scale
    current = {
        'date': trade_date,
        'code': bar.get('code') or rows[-1].get('code'),
        'name': bar.get('name') or rows[-1].get('name'),
        'open': _f(bar.get('open')) * scale,
        'high': _f(bar.get('high')) * scale,
        'low': _f(bar.get('low')) * scale,
        'close': close,
        'volume': amount / raw_close if raw_close > 0 and amount > 0 else 0.0,
        'amount': amount,
        'turn': _f(bar.get('turn')),
        'pctChg': (close / qfq_prev - 1.0) * 100.0 if qfq_prev > 0 else 0.0,
        'tradestatus': str(bar.get('tradestatus') or '1'),
        'isST': str(bar.get('isST') or '0'),
        'history_bridge_source': str(bar.get('source') or 'current-bar'),
        'history_bridge_scale': round(scale, 8),
    }
    merged = rows + [current]
    return merged[-history_limit:]


def install() -> None:
    """Install a fail-closed current-bar bridge for lagging Tencent qfq histories."""
    from .real_market import AKShareMarket

    if getattr(AKShareMarket.histories, '_v1_current_bar_bridge_installed', False):
        return

    def histories(self, selected: list[dict], trade_date: str) -> dict[str, list[dict]]:
        d = date.fromisoformat(trade_date)
        start = (d - timedelta(days=240)).strftime('%Y%m%d')
        end = d.strftime('%Y%m%d')
        out: dict[str, list[dict]] = {}
        stale: dict[str, tuple[dict, list[dict]]] = {}
        amount_modes = {'yuan': 0, 'hands': 0}

        for x in selected:
            try:
                df = self.ak.stock_zh_a_hist_tx(
                    symbol=_tx_symbol(x['code']),
                    start_date=start,
                    end_date=end,
                    adjust='qfq',
                    timeout=20,
                )
                tail = df.tail(self.history_limit)
                if tail is None or tail.empty:
                    continue
                last = tail.iloc[-1]
                mode = _tx_amount_mode(last.get('amount'), last.get('close'), x.get('amount', 0))
                amount_modes[mode] += 1
                rows = []
                for _, r in tail.iterrows():
                    close = _f(r.get('close'))
                    amount_yuan, volume_shares = _tx_amount_and_volume(r.get('amount'), close, mode)
                    rows.append({
                        'date': str(r.get('date'))[:10],
                        'code': x['code'],
                        'name': x.get('name', x['code']),
                        'open': _f(r.get('open')),
                        'high': _f(r.get('high')),
                        'low': _f(r.get('low')),
                        'close': close,
                        'volume': volume_shares,
                        'amount': amount_yuan,
                        'turn': 0.0,
                        'pctChg': 0.0,
                        'tradestatus': '1',
                        'isST': '0',
                    })
                if rows and rows[-1]['date'] == trade_date:
                    out[x['code']] = rows
                elif rows:
                    stale[x['code']] = (x, rows)
            except Exception as exc:
                print(f'[market] tencent qfq history failed {x["code"]}: {exc}')

        # Prefer the already-fetched completed-session snapshot when it contains full OHLC.
        # Tencent full-market spot rows contain only close for most symbols, so request an
        # unadjusted current daily bar only for stale symbols that still lack usable OHLC.
        need_execution: dict[str, str] = {}
        for sym, (meta, rows) in stale.items():
            if _valid_current_bar(meta):
                merged = _append_current_qfq(rows, meta, trade_date, self.history_limit)
                if merged and merged[-1]['date'] == trade_date:
                    out[sym] = merged
                    continue
            need_execution[sym] = meta.get('name', sym)

        execution = self.execution_bars(need_execution, trade_date) if need_execution else {}
        bridged = 0
        rejected = 0
        for sym, name in need_execution.items():
            item = stale.get(sym)
            if not item:
                continue
            _, rows = item
            bar = execution.get(sym)
            merged = _append_current_qfq(rows, bar, trade_date, self.history_limit) if bar else None
            if merged and merged[-1]['date'] == trade_date:
                out[sym] = merged
                bridged += 1
            else:
                rejected += 1

        if not selected:
            raise RuntimeError('No symbols supplied for Tencent histories')
        required = max(1, math.ceil(len(selected) * 0.75))
        if len(out) < required:
            raise RuntimeError(
                f'Current-history coverage too low after qfq bridge: {len(out)}/{len(selected)}, '
                f'require >= {required}; stale_qfq={len(stale)} execution_requested={len(need_execution)} '
                f'bridge_rejected={rejected}'
            )
        print(
            f'[market] historical source=tencent+current-bar current_symbols={len(out)}/{len(selected)} '
            f'stale_qfq={len(stale)} execution_requested={len(need_execution)} bridged={bridged} '
            f'bridge_rejected={rejected} amount_mode_yuan={amount_modes["yuan"]} '
            f'amount_mode_hands={amount_modes["hands"]}'
        )
        return out

    histories._v1_current_bar_bridge_installed = True
    AKShareMarket.histories = histories
