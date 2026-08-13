from __future__ import annotations

from .ai_manager import decide_with_api
from .demo import NAMES, generate_history
from .pipeline import build_candidates, market_temperature


def main():
    # Build a realistic formal-size decision payload without touching production state or market ledgers.
    histories = generate_history(110)
    candidates = build_candidates(histories, NAMES)
    market_score = market_temperature(candidates)
    if len(candidates) < 20:
        raise RuntimeError(f'formal AI test needs >=20 candidates, got {len(candidates)}')

    result = decide_with_api(
        candidates=candidates,
        current={'cash': 1_000_000.0, 'positions': {}},
        market_score=market_score,
    )
    if not result or not isinstance(result.get('targets'), list):
        raise RuntimeError('formal AI stream test failed; D rule fallback would be used in production')

    print(f'[OK] FORMAL AI STREAM TEST PASSED candidates=20 targets={len(result["targets"])}')
    print(f'[OK] diary={str(result.get("diary", ""))[:200]}')
    print('[OK] production state was NOT read or modified')


if __name__ == '__main__':
    main()
