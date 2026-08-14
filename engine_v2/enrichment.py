from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from statistics import mean, pstdev


def _num(value, default=None):
    try:
        if value is None:
            return default
        x = float(value)
        return default if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return default


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _pct(a: float, b: float) -> float:
    return b / a - 1.0 if a and a > 0 else 0.0


def _max_drawdown(values: list[float]) -> float:
    peak = 0.0
    worst = 0.0
    for value in values:
        if value <= 0:
            continue
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst


def _technical_raw(rows: list[dict], trade_date: str) -> dict | None:
    """Build point-in-time stock features from ascending adjusted daily bars only."""
    usable = [r for r in rows if str(r.get('date', ''))[:10] <= trade_date]
    usable.sort(key=lambda r: str(r.get('date', ''))[:10])
    if len(usable) < 61 or str(usable[-1].get('date', ''))[:10] != trade_date:
        return None

    closes = [_num(r.get('close'), 0.0) or 0.0 for r in usable]
    highs = [_num(r.get('high'), c) or c for r, c in zip(usable, closes)]
    lows = [_num(r.get('low'), c) or c for r, c in zip(usable, closes)]
    opens = [_num(r.get('open'), c) or c for r, c in zip(usable, closes)]
    amounts = [_num(r.get('amount'), 0.0) or 0.0 for r in usable]
    if min(closes[-61:]) <= 0:
        return None

    c = closes[-1]
    r5 = _pct(closes[-6], c)
    r10 = _pct(closes[-11], c)
    r20 = _pct(closes[-21], c)
    r60 = _pct(closes[-61], c)
    ma10 = mean(closes[-10:])
    ma20 = mean(closes[-20:])
    ma60 = mean(closes[-60:])
    daily20 = [_pct(closes[i - 1], closes[i]) for i in range(len(closes) - 19, len(closes))]
    vol20 = pstdev(daily20) if len(daily20) > 1 else 0.0
    high20 = max(highs[-20:])
    high60 = max(highs[-60:])
    low20 = min(lows[-20:])
    amount20 = mean(amounts[-20:])
    prev_amount20 = mean(amounts[-21:-1]) if len(amounts) >= 21 else amount20
    amount_ratio = amounts[-1] / prev_amount20 if prev_amount20 > 0 else 1.0
    prev_close = closes[-2]
    gap = _pct(prev_close, opens[-1])
    drawdown60 = c / max(closes[-60:]) - 1.0
    maxdd60 = _max_drawdown(closes[-60:])
    range20 = mean((highs[i] - lows[i]) / closes[i - 1] for i in range(len(closes) - 19, len(closes)) if closes[i - 1] > 0)

    alignment = 1 if c > ma10 > ma20 > ma60 else -1 if c < ma10 < ma20 < ma60 else 0
    extension20 = c / ma20 - 1.0 if ma20 > 0 else 0.0
    near_high60 = c / high60 if high60 > 0 else 0.0
    near_high20 = c / high20 if high20 > 0 else 0.0

    trend = _clip(50 + 145 * r20 + 70 * r60 + 10 * alignment + (5 if c > ma20 else -5))
    momentum = _clip(50 + 150 * r10 + 95 * r20 + 35 * r5 - max(0.0, r5 - 0.16) * 180)
    breakout_quality = _clip(
        35 + 45 * near_high60 + 8 * min(2.0, max(0.0, amount_ratio))
        + (8 if c > ma20 else -8) - max(0.0, extension20 - 0.18) * 100
    )
    crowding_score = _clip(
        35 + 115 * max(0.0, r20) + 85 * max(0.0, r5)
        + 8 * max(0.0, amount_ratio - 1.0) + 80 * max(0.0, extension20 - 0.12)
    )

    return {
        'date': trade_date,
        'close': round(c, 4),
        'r5': round(r5, 6), 'r10': round(r10, 6),
        'r20': round(r20, 6), 'r60': round(r60, 6),
        'ma10': round(ma10, 4), 'ma20': round(ma20, 4), 'ma60': round(ma60, 4),
        'vol20': round(vol20, 6), 'range20': round(range20, 6),
        'drawdown60': round(drawdown60, 6), 'max_drawdown60': round(maxdd60, 6),
        'high20_distance': round(near_high20 - 1.0, 6),
        'high60_distance': round(near_high60 - 1.0, 6),
        'low20_distance': round(c / low20 - 1.0, 6) if low20 > 0 else None,
        'amount20': round(amount20, 2), 'amount_ratio': round(amount_ratio, 4),
        'gap': round(gap, 6), 'extension20': round(extension20, 6),
        'trend': round(trend, 2), 'momentum': round(momentum, 2),
        'breakout_quality': round(breakout_quality, 2),
        'crowding_score': round(crowding_score, 2),
    }


def _percentile(values: dict[str, float | None], higher_better: bool = True) -> dict[str, float | None]:
    valid = sorted((float(v), k) for k, v in values.items() if v is not None and math.isfinite(float(v)))
    result = {k: None for k in values}
    n = len(valid)
    if n == 0:
        return result
    i = 0
    while i < n:
        j = i + 1
        while j < n and valid[j][0] == valid[i][0]:
            j += 1
        rank = 50.0 if n == 1 else 100.0 * ((i + j - 1) / 2) / (n - 1)
        if not higher_better:
            rank = 100.0 - rank
        for _, key in valid[i:j]:
            result[key] = round(rank, 2)
        i = j
    return result


def _sentiment_signals(sentiment: dict) -> dict[str, dict]:
    signals: dict[str, dict] = {}
    for item in sentiment.get('limit_up', []):
        signals[item['code']] = {
            'status': 'limit_up',
            'boards': int(item.get('boards') or 1),
            'breaks': int(item.get('breaks') or 0),
            'leader_seed': float(item.get('leader_score') or 50),
            'theme_seed': float(item.get('theme_score') or 50),
            'short_industry': item.get('short_industry'),
        }
    for item in sentiment.get('broken_limit', []):
        signals.setdefault(item['code'], {
            'status': 'broken_limit', 'boards': 0, 'breaks': 1,
            'leader_seed': 38.0, 'theme_seed': 45.0,
            'short_industry': item.get('short_industry'),
        })
    for item in sentiment.get('limit_down', []):
        signals[item['code']] = {
            'status': 'limit_down', 'boards': 0, 'breaks': 0,
            'leader_seed': 5.0, 'theme_seed': 20.0, 'short_industry': None,
        }
    return signals


def enrich_snapshot(snapshot: dict) -> dict:
    import akshare as ak
    from engine.real_market import AKShareMarket
    from .snapshot import _sentiment_snapshot

    trade_date = str(snapshot['trade_date'])
    selected = list(snapshot.get('preselection', {}).get('rows', []))
    if not selected:
        raise RuntimeError('V2 snapshot contains no preselection rows')

    market = AKShareMarket(history_limit=120)
    cache_root = os.getenv('V2_HISTORY_CACHE_DIR')
    if cache_root:
        from .history_cache import load_histories_cached
        histories, history_diagnostics = load_histories_cached(
            market, selected, trade_date, Path(cache_root),
        )
    else:
        histories = market.histories(selected, trade_date)
        history_diagnostics = {
            'cache_version': None, 'workers': 1, 'selected': len(selected),
            'current_histories': len(histories), 'cache_hits': 0,
            'incremental_fetches': 0, 'full_fetches': len(selected),
            'failures': max(0, len(selected) - len(histories)), 'elapsed_s': None,
            'mode': 'legacy_serial_no_persistent_cache',
        }
    sentiment = _sentiment_snapshot(ak, trade_date)
    signals = _sentiment_signals(sentiment)

    raw_by_code: dict[str, dict] = {}
    row_by_code = {str(x.get('raw_code') or ''): x for x in selected}
    for code, item in row_by_code.items():
        symbol = str(item.get('code') or '')
        rows = histories.get(symbol)
        if not rows:
            continue
        technical = _technical_raw(rows, trade_date)
        if technical is not None:
            raw_by_code[code] = technical

    vol_scores = _percentile({k: v['vol20'] for k, v in raw_by_code.items()}, higher_better=False)
    dd_scores = _percentile({k: v['max_drawdown60'] for k, v in raw_by_code.items()}, higher_better=True)
    liq_scores = _percentile({k: math.log10(max(v['amount20'], 1.0)) for k, v in raw_by_code.items()})
    pb_scores = _percentile(
        {k: _num(row_by_code[k].get('valuation_pb_disclosed')) for k in raw_by_code},
        higher_better=False,
    )

    market_emotion = _clip(
        50 + 0.55 * (sentiment.get('limit_up_count', 0) - sentiment.get('limit_down_count', 0))
        - 32 * float(sentiment.get('limit_break_rate', 0.0))
    )
    candidates = []
    for code, technical in raw_by_code.items():
        base = dict(row_by_code[code])
        risk = 0.65 * (vol_scores.get(code) or 50.0) + 0.35 * (dd_scores.get(code) or 50.0)
        industry_score = _num(base.get('industry_score'), 50.0) or 50.0
        leader_proxy = (
            0.34 * technical['momentum'] + 0.30 * technical['breakout_quality']
            + 0.22 * industry_score + 0.14 * technical['trend']
        )
        signal = signals.get(code)
        status = signal.get('status') if signal else 'normal'
        if signal:
            if status == 'limit_up':
                leader_score = max(leader_proxy, signal['leader_seed'])
                theme_score = max(industry_score, signal['theme_seed'])
                status_bonus = 18.0
            elif status == 'broken_limit':
                leader_score = min(leader_proxy, signal['leader_seed'] + 12.0)
                theme_score = max(35.0, min(industry_score, signal['theme_seed'] + 8.0))
                status_bonus = -8.0
            else:
                leader_score = signal['leader_seed']
                theme_score = signal['theme_seed']
                status_bonus = -30.0
        else:
            leader_score = leader_proxy
            theme_score = industry_score
            status_bonus = 0.0

        pct_change = _num(base.get('pctChg'), 0.0) or 0.0
        high = _num(base.get('high'), 0.0) or 0.0
        low = _num(base.get('low'), 0.0) or 0.0
        close = _num(base.get('close'), 0.0) or 0.0
        one_word_limit = bool(
            pct_change >= 9.5 and close > 0 and high > 0 and low > 0
            and (high - low) / close <= 0.0015
        )

        candidates.append({
            **base,
            **technical,
            'symbol': base.get('code'),
            'risk': round(_clip(risk), 2),
            'liquidity': round(_clip(liq_scores.get(code) or 50.0), 2),
            'valuation_score': None if pb_scores.get(code) is None else round(pb_scores[code], 2),
            'leader_score': round(_clip(leader_score), 2),
            'theme_score': round(_clip(theme_score), 2),
            'sentiment_score': round(_clip(market_emotion + status_bonus), 2),
            'limit_status': status,
            'one_word_limit': one_word_limit,
            'correlation_cluster': base.get('industry'),
            'correlation_cluster_source': 'industry_proxy_until_price-correlation-module',
            'leader_score_source': 'limit-pool+relative-strength' if signal else 'relative-strength-proxy',
            'theme_score_source': 'limit-pool+sw-industry' if signal else 'sw-industry-strength-proxy',
        })

    requested = len(selected)
    history_coverage = len(histories) / requested if requested else 0.0
    feature_coverage = len(candidates) / requested if requested else 0.0
    ready = history_coverage >= 0.85 and feature_coverage >= 0.80 and len(candidates) >= 100
    return {
        'enrichment_version': 'v2-history-0.1',
        'trade_date': trade_date,
        'base_snapshot_version': snapshot.get('snapshot_version'),
        'base_stock_source': snapshot.get('source_notes', {}).get('stock_snapshot'),
        'market': snapshot.get('market'),
        'industry': snapshot.get('industry'),
        'fundamentals': snapshot.get('fundamentals'),
        'coverage': {
            'preselected': requested,
            'tencent_current_histories': len(histories),
            'eligible_technical_features': len(candidates),
            'history_ratio': round(history_coverage, 4),
            'feature_ratio': round(feature_coverage, 4),
            'history_cache': history_diagnostics,
        },
        'factor_notes': {
            'price_history': 'Tencent qfq daily bars ending exactly on trade_date',
            'history_cache': 'persistent content cache with bounded incremental Tencent refresh when V2_HISTORY_CACHE_DIR is set',
            'valuation_score': 'cross-sectional inverse rank of disclosed common-period PB; missing stays missing',
            'risk_score': 'cross-sectional low-volatility and max-drawdown rank',
            'liquidity_score': 'cross-sectional 20-session turnover-amount rank',
            'leader_theme': 'limit-pool evidence where present; otherwise transparent relative-strength/SW-industry proxy',
            'correlation_cluster': 'industry proxy only; no claim of measured price correlation yet',
        },
        'candidates': candidates,
        'safety': {
            'writes_ledgers': False,
            'calls_sol': False,
            'historical_backtest_grade': False,
            'ready_for_strategy_targets': ready,
            'ready_for_shadow_accounting': False,
            'next_required_stage': 'generate deterministic V2 shadow targets and validate them before any shadow execution',
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snapshot', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    enriched = enrich_snapshot(snapshot)
    text = json.dumps(enriched, ensure_ascii=False, indent=2)
    Path(args.output).write_text(text + '\n', encoding='utf-8')
    print(json.dumps({
        'trade_date': enriched['trade_date'],
        'coverage': enriched['coverage'],
        'ready_for_strategy_targets': enriched['safety']['ready_for_strategy_targets'],
        'candidate_count': len(enriched['candidates']),
        'limit_status_counts': {
            key: sum(1 for x in enriched['candidates'] if x.get('limit_status') == key)
            for key in ('limit_up', 'broken_limit', 'limit_down', 'normal')
        },
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
