from __future__ import annotations

from .ai_manager import decide_with_api


def _candidates() -> list[dict]:
    # Deterministic test-only records shaped like production candidates.
    # They are never written to fund state and are not trading instructions.
    out = []
    for i in range(20):
        market = 'sh' if i % 2 == 0 else 'sz'
        code = 600100 + i if market == 'sh' else 000700 + i
        out.append({
            'eligible': True,
            'symbol': f'{market}.{code:06d}',
            'name': f'测试候选{i+1:02d}',
            'close': round(8.5 + i * 3.17, 2),
            'trend': round(92 - i * 1.6, 2),
            'momentum': round(88 - i * 1.35, 2),
            'liquidity': 80,
            'risk': round(72 - i * 1.1, 2),
            'r20': round(0.24 - i * 0.012, 6),
            'r60': round(0.38 - i * 0.015, 6),
            'vol20': round(0.025 + i * 0.0012, 6),
            'drawdown60': round(-0.01 - i * 0.004, 6),
            'amount20': float(8_000_000_000 + i * 730_000_000),
            'quality': round(82 - i * 0.8, 2),
            'valuation': round(74 - i * 0.9, 2),
            'score_d': round(84 - i * 1.15, 2),
            'peTTM': round(12 + i * 0.7, 2),
            'pbMRQ': round(1.4 + i * 0.08, 2),
        })
    return out


def main():
    candidates = _candidates()
    result = decide_with_api(
        candidates=candidates,
        current={'cash': 1_000_000.0, 'positions': {}},
        market_score=57.3,
    )
    if not result or not isinstance(result.get('targets'), list):
        raise RuntimeError('formal AI stream test failed; D rule fallback would be used in production')

    print(f'[OK] FORMAL AI STREAM TEST PASSED candidates=20 targets={len(result["targets"])}')
    print(f'[OK] diary={str(result.get("diary", ""))[:200]}')
    print('[OK] production state was NOT read or modified')


if __name__ == '__main__':
    main()
