from __future__ import annotations

import json
import math
import os
import signal
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FUND_IDS = ('D_MAIN', 'A', 'B', 'C', 'D', 'L')


def _f(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return default
        return float(value)
    except Exception:
        return default


def _bounded(seconds: int, fn):
    if not hasattr(signal, 'SIGALRM'):
        return fn()
    old = signal.getsignal(signal.SIGALRM)

    def alarm(_sig, _frame):
        raise TimeoutError(f'Tencent full-market call exceeded {seconds}s')

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _symbol(raw: str) -> str:
    text = str(raw or '').strip().lower().replace('.', '')
    if text.startswith('sh') and len(text) >= 8:
        return 'sh.' + text[-6:]
    if text.startswith('sz') and len(text) >= 8:
        return 'sz.' + text[-6:]
    return ''


def _mainboard(sym: str) -> bool:
    code = sym[-6:]
    return (sym.startswith('sh.') and code.startswith(('600', '601', '603', '605'))) or (
        sym.startswith('sz.') and code.startswith(('000', '001', '002', '003'))
    )


def _critical_symbols() -> dict[str, str]:
    """Read V1 holdings + pending targets without importing daily_run (avoids cycles)."""
    state_root = Path(os.getenv('FUND_STATE_DIR', str(ROOT / 'state')))
    out: dict[str, str] = {}
    for fid in FUND_IDS:
        path = state_root / f'{fid}.json'
        if not path.exists():
            continue
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        for sym, pos in (state.get('positions') or {}).items():
            out[sym] = pos.get('name', sym)
        for target in state.get('pending_targets') or []:
            sym = target.get('symbol')
            if sym:
                out[sym] = target.get('name', out.get(sym, sym))
    return out


def fetch_tencent_full_rows(ak) -> list[dict]:
    """Return a broad liquid Shanghai/Shenzhen main-board cross-section from Tencent.

    The Tencent spot table has no OHLC. Non-critical rows therefore keep OHLC unavailable;
    histories drive candidate features, while holdings/pending symbols are overlaid with
    unadjusted Tencent daily bars before V1 accounting can execute anything.
    """
    last_error = None
    for attempt in (1, 2):
        try:
            df = _bounded(60, ak.stock_zh_a_spot_tx)
            if df is None or df.empty:
                raise RuntimeError('empty Tencent full-market dataframe')
            rows = []
            for _, row in df.iterrows():
                sym = _symbol(row.get('code'))
                if not sym or not _mainboard(sym):
                    continue
                name = str(row.get('name') or '').strip()
                if not name or 'ST' in name.upper() or '退' in name:
                    continue
                close = _f(row.get('zxj'))
                pct = _f(row.get('zdf'))
                amount = _f(row.get('turnover')) * 10000.0
                if close <= 0 or amount < 20_000_000:
                    continue
                preclose = close / (1.0 + pct / 100.0) if abs(100.0 + pct) > 1e-9 else close
                pe = _f(row.get('pe_ttm'))
                turn = _f(row.get('hsl'))
                r60 = _f(row.get('zdf_d60')) / 100.0
                rows.append({
                    'code': sym,
                    'raw_code': sym[-6:],
                    'name': name,
                    'source': 'tencent-full',
                    'open': 0.0,
                    'high': 0.0,
                    'low': 0.0,
                    'close': close,
                    'preclose': preclose,
                    'amount': amount,
                    'turn': turn,
                    'pctChg': pct,
                    'peTTM': pe if pe > 0 else 0.0,
                    'pbMRQ': 0.0,
                    'r60_snapshot': r60,
                    'tradestatus': '1',
                    'isST': '0',
                })
            if len(rows) < 1500:
                raise RuntimeError(f'Tencent full-market coverage suspiciously low after filters: {len(rows)}')
            print(f'[market] snapshot source=tencent-full rows={len(rows)} raw_rows={len(df)} attempt={attempt}')
            return rows
        except Exception as exc:
            last_error = exc
            print(f'[market] tencent-full snapshot attempt {attempt}/2 failed: {exc}')
            if attempt == 1:
                time.sleep(3)
    raise RuntimeError(f'Tencent full-market snapshot failed after retries: {last_error}')


def _overlay_execution_bars(rows: list[dict], execution_bars: dict[str, dict]) -> list[dict]:
    """Overlay real unadjusted OHLC for symbols that can be executed today.

    Preserve Tencent full-market cross-sectional fields (PE/60d/turnover/source) for rows that
    already exist. Missing critical symbols are appended using their execution bar.
    """
    by_code = {row['code']: dict(row) for row in rows if row.get('code')}
    for sym, bar in execution_bars.items():
        if sym in by_code:
            base = by_code[sym]
            for key in ('open', 'high', 'low', 'close', 'preclose', 'tradestatus'):
                if key in bar:
                    base[key] = bar[key]
            # Daily-bar amount is preferred for execution/valuation consistency, while all
            # cross-sectional valuation/momentum fields remain from the full-market table.
            if _f(bar.get('amount')) > 0:
                base['amount'] = bar['amount']
            base['source'] = 'tencent-full'
            by_code[sym] = base
        else:
            by_code[sym] = dict(bar)
    return list(by_code.values())


def install() -> None:
    """Install Tencent as V1's third full-market source after Eastmoney and Sina."""
    from .real_market import AKShareMarket

    if getattr(AKShareMarket.snapshot, '_v1_tencent_full_installed', False):
        return

    original_latest = AKShareMarket.latest_trade_date
    original_snapshot = AKShareMarket.snapshot

    def latest_trade_date(self, today: str):
        trade_date = original_latest(self, today)
        self._resolved_trade_date = trade_date
        return trade_date

    def snapshot(self):
        try:
            return original_snapshot(self)
        except Exception as primary_error:
            print(f'[market] Eastmoney/Sina full-market path failed; trying Tencent full-market: {primary_error}')
            rows = fetch_tencent_full_rows(self.ak)
            trade_date = getattr(self, '_resolved_trade_date', None)
            critical = _critical_symbols()
            if critical and trade_date:
                execution = self.execution_bars(critical, trade_date)
                rows = _overlay_execution_bars(rows, execution)
                print(
                    f'[market] Tencent full-market execution overlay '
                    f'critical={len(critical)} bars={len(execution)} trade_date={trade_date}'
                )
            elif critical:
                raise RuntimeError('Tencent full-market fallback has critical V1 symbols but no resolved trade date')
            return rows

    latest_trade_date._v1_tencent_full_installed = True
    snapshot._v1_tencent_full_installed = True
    AKShareMarket.latest_trade_date = latest_trade_date
    AKShareMarket.snapshot = snapshot
