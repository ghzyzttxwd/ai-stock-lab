from __future__ import annotations

from collections import defaultdict


def _volatility(candidate: dict) -> float:
    """Daily volatility proxy with a floor so missing/zero data never gets infinite weight."""
    return max(0.012, min(0.08, float(candidate.get("vol20") or 0.03)))


def risk_budget_weights(
    picks: list[dict],
    exposure: float,
    max_weight: float,
    industry_cap: float,
    cluster_cap: float | None = None,
) -> list[dict]:
    """Allocate exposure approximately inverse to volatility, then enforce group caps.

    This is intentionally deterministic and explainable. It is not a portfolio optimizer.
    """
    if not picks or exposure <= 0:
        return []
    exposure = max(0.0, min(1.0, float(exposure)))
    max_weight = max(0.0, min(1.0, float(max_weight)))
    industry_cap = max(max_weight, min(1.0, float(industry_cap)))
    if cluster_cap is not None:
        cluster_cap = max(max_weight, min(1.0, float(cluster_cap)))

    raw = [1.0 / _volatility(x) for x in picks]
    raw_total = sum(raw) or 1.0
    weights = [min(max_weight, exposure * r / raw_total) for r in raw]

    def redistribute_group_cap(group_key: str, cap: float | None) -> None:
        nonlocal weights
        if cap is None:
            return
        for _ in range(8):
            groups: dict[str, list[int]] = defaultdict(list)
            for i, p in enumerate(picks):
                value = str(p.get(group_key) or "UNKNOWN")
                groups[value].append(i)
            excess = 0.0
            capped_indices: set[int] = set()
            for idxs in groups.values():
                group_weight = sum(weights[i] for i in idxs)
                if group_weight > cap + 1e-12:
                    scale = cap / group_weight
                    for i in idxs:
                        old = weights[i]
                        weights[i] *= scale
                        excess += old - weights[i]
                        capped_indices.add(i)
            if excess <= 1e-9:
                break
            recipients = [
                i for i in range(len(picks))
                if i not in capped_indices and weights[i] < max_weight - 1e-12
            ]
            if not recipients:
                break
            capacity = sum(max_weight - weights[i] for i in recipients)
            if capacity <= 1e-12:
                break
            for i in recipients:
                room = max_weight - weights[i]
                add = min(room, excess * room / capacity)
                weights[i] += add

    redistribute_group_cap("industry", industry_cap)
    redistribute_group_cap("correlation_cluster", cluster_cap)

    total = sum(weights)
    if total > exposure and total > 0:
        scale = exposure / total
        weights = [w * scale for w in weights]

    result = []
    for p, w in zip(picks, weights):
        if w < 0.005:
            continue
        result.append({**p, "target_weight": round(w, 6)})
    return result
