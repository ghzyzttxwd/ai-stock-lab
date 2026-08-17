from __future__ import annotations

import math
import signal
import time


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
    return sym.startswith('sh.') and code.startswith(('600', '601', '603', '605')) or (
        sym.startswith('sz.') and code.startswith(('000', '001', '002', '003'))
    )


def fetch_tencent_full_rows(ak) -> list[dict]:
    """Return a full decision-grade main-board cross-section from Tencent.

    Tencent's spot table has no OHLC. V2 uses this only for same-session cross-sectional
    preselection; final indicators and any execution prices still come from Tencent daily bars.
    """
    last_error = None
    for attempt in (1, 2):
        try:
            df = _bounded(60, ak.stock_zh_a_spot_tx)
            if df is None or df.empty:
                raise RuntimeError('empty Tencent full-market dataframe')
            rows = []
            for _, r in df.iterrows():
                sym = _symbol(r.get('code'))
                if not sym or not _mainboard(sym):
                    continue
                name = str(r.get('name') or '').strip()
                if not name or 'ST' in name.upper() or '退' in name:
                    continue
                close = _f(r.get('zxj'))
                pct = _f(r.get('zdf'))
                amount = _f(r.get('turnover')) * 10000.0
                if close <= 0 or amount < 20_000_000:
                    continue
                preclose = close / (1.0 + pct / 100.0) if abs(100.0 + pct) > 1e-9 else close
                pe = _f(r.get('pe_ttm'))
                turn = _f(r.get('hsl'))
                r60 = _f(r.get('zdf_d60')) / 100.0
                rows.append({
                    'code': sym,
                    'raw_code': sym[-6:],
                    'name': name,
                    'source': 'tencent-full',
                    # The Tencent full-market endpoint does not expose OHLC. Keep these explicitly
                    # unavailable instead of faking open=close. V2 execution uses daily bars elsewhere.
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
            # After the same liquidity/ST/main-board filters used by the old providers, this must
            # still be a broad market cross-section, not a cached candidate list.
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


def install() -> None:
    """Patch only the current process: Eastmoney/Sina first, Tencent-full third."""
    from engine.real_market import AKShareMarket

    if getattr(AKShareMarket.snapshot, '_v2_tencent_full_installed', False):
        return
    original = AKShareMarket.snapshot

    def snapshot(self):
        try:
            return original(self)
        except Exception as primary_error:
            print(f'[market] Eastmoney/Sina full-market path failed; trying Tencent full-market: {primary_error}')
            return fetch_tencent_full_rows(self.ak)

    snapshot._v2_tencent_full_installed = True
    AKShareMarket.snapshot = snapshot
