from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from .provider import bounded_call


def normalize_code(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            value = int(value)
    text = str(value).strip()
    if text.endswith('.0') and text[:-2].isdigit():
        text = text[:-2]
    digits = ''.join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    return digits[-6:].zfill(6)


def is_mainboard_code(code: str | None) -> bool:
    return bool(code) and str(code).startswith(('600','601','603','605','000','001','002','003'))


def _num(value):
    try:
        if value is None:
            return None
        x = float(value)
        return None if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return None


def _quarter_pair(day: date) -> tuple[date, date]:
    ends = [date(day.year - 1, 12, 31), date(day.year, 3, 31), date(day.year, 6, 30), date(day.year, 9, 30), date(day.year, 12, 31)]
    eligible = [x for x in ends if x <= day]
    current = max(eligible)
    previous = max(x for x in ends if x < current)
    return current, previous


def _announced_mainboard_rows(df, asof: str) -> dict[str, dict]:
    """Return one report row per eligible main-board issuer, using only public announcements by `asof`."""
    out: dict[str, dict] = {}
    if df is None or getattr(df, 'empty', True):
        return out
    for _, row in df.iterrows():
        code = normalize_code(row.get('股票代码'))
        if not is_mainboard_code(code):
            continue
        announced = str(row.get('最新公告日期') or '')[:10]
        if not ('2000-01-01' <= announced <= asof):
            continue
        out[code] = {
            'code': code,
            'name': str(row.get('股票简称') or '').strip(),
            'announcement_date': announced,
            'roe': _num(row.get('净资产收益率')),
            'revenue_yoy': _num(row.get('营业总收入-同比增长')),
            'profit_yoy': _num(row.get('净利润-同比增长')),
            'operating_cashflow_per_share': _num(row.get('每股经营现金流量')),
            'gross_margin': _num(row.get('销售毛利率')),
        }
    return out


def select_scoring_period(current_rows: dict[str, dict], previous_rows: dict[str, dict], min_current_coverage: float = 0.80) -> tuple[str, dict[str, dict], float]:
    """Use one common reporting period for cross-sectional quality ranking.

    Mixing early half-year reporters with first-quarter rows creates horizon mismatch in ROE/cash-flow.
    The newer period becomes the scoring baseline only after it reaches broad coverage; otherwise the
    previous broadly complete period remains the quality baseline. Fresh newer reports can still be
    carried separately as event information later.
    """
    previous_count = len(previous_rows)
    coverage = len(current_rows) / previous_count if previous_count else 0.0
    if previous_count and coverage >= min_current_coverage:
        return 'current', current_rows, coverage
    return 'previous', previous_rows, coverage


def _percentile(values: dict[str, float | None]) -> dict[str, float | None]:
    valid = sorted((float(v), k) for k, v in values.items() if v is not None)
    if not valid:
        return {k: None for k in values}
    n = len(valid)
    result = {k: None for k in values}
    i = 0
    while i < n:
        j = i + 1
        while j < n and valid[j][0] == valid[i][0]:
            j += 1
        avg_rank = (i + (j - 1)) / 2
        score = 50.0 if n == 1 else 100.0 * avg_rank / (n - 1)
        for _, key in valid[i:j]:
            result[key] = round(score, 2)
        i = j
    return result


def _group_percentiles(rows: dict[str, dict], field: str, industry_by_code: dict[str, str] | None) -> dict[str, float | None]:
    global_rank = _percentile({code: row.get(field) for code, row in rows.items()})
    if not industry_by_code:
        return global_rank
    groups: dict[str, dict[str, float | None]] = defaultdict(dict)
    for code, row in rows.items():
        industry = industry_by_code.get(code)
        if industry:
            groups[industry][code] = row.get(field)
    out = dict(global_rank)
    for group in groups.values():
        usable = sum(v is not None for v in group.values())
        if usable < 5:
            continue
        out.update(_percentile(group))
    return out


def score_quality(rows: dict[str, dict], industry_by_code: dict[str, str] | None = None) -> dict[str, dict]:
    """Explainable quality factors. No debt/balance-sheet score until disclosure-timed leverage data passes validation."""
    ranks = {
        field: _group_percentiles(rows, field, industry_by_code)
        for field in ('roe', 'revenue_yoy', 'profit_yoy', 'gross_margin')
    }
    out = {}
    for code, row in rows.items():
        available = [ranks[f].get(code) for f in ranks if ranks[f].get(code) is not None]
        ocf = row.get('operating_cashflow_per_share')
        cashflow_score = 75.0 if ocf is not None and ocf > 0 else 25.0 if ocf is not None and ocf < 0 else None
        ready = len(available) >= 3 and cashflow_score is not None
        if ready:
            weights = {'roe': 0.40, 'profit_yoy': 0.25, 'revenue_yoy': 0.20, 'gross_margin': 0.15}
            weighted = [(ranks[f].get(code), w) for f, w in weights.items() if ranks[f].get(code) is not None]
            quality = sum(v * w for v, w in weighted) / sum(w for _, w in weighted)
        else:
            quality = None
        roe = row.get('roe')
        profit = row.get('profit_yoy')
        distress = bool(
            (roe is not None and roe < 0 and profit is not None and profit < 0)
            or (ocf is not None and ocf < 0 and profit is not None and profit < -30)
        )
        out[code] = {
            **row,
            'industry': industry_by_code.get(code) if industry_by_code else None,
            'quality_score': round(quality, 2) if quality is not None else None,
            'cashflow_score': cashflow_score,
            'financial_distress': distress,
            'fundamental_ready': ready,
            'quality_components': {f: ranks[f].get(code) for f in ranks},
            'balance_sheet_score': None,
        }
    return out


def load_point_in_time_fundamentals(trade_date: str, industry_by_code: dict[str, str] | None = None, min_current_coverage: float = 0.80) -> dict:
    import akshare as ak

    td = date.fromisoformat(trade_date)
    current_period, previous_period = _quarter_pair(td)
    current_df = bounded_call(
        75,
        lambda: ak.stock_yjbb_em(date=current_period.strftime('%Y%m%d')),
        f'financial report {current_period.isoformat()}',
    )
    previous_df = bounded_call(
        75,
        lambda: ak.stock_yjbb_em(date=previous_period.strftime('%Y%m%d')),
        f'financial report {previous_period.isoformat()}',
    )
    current_rows = _announced_mainboard_rows(current_df, trade_date)
    previous_rows = _announced_mainboard_rows(previous_df, trade_date)
    selected_label, selected_rows, current_coverage = select_scoring_period(
        current_rows, previous_rows, min_current_coverage=min_current_coverage
    )
    selected_period = current_period if selected_label == 'current' else previous_period
    scored = score_quality(selected_rows, industry_by_code=industry_by_code)

    fresh_reports = {
        code: row for code, row in current_rows.items()
        if selected_label != 'current'
    }
    return {
        'trade_date': trade_date,
        'selected_period': selected_period.isoformat(),
        'current_period': current_period.isoformat(),
        'previous_period': previous_period.isoformat(),
        'current_mainboard_announced': len(current_rows),
        'previous_mainboard_announced': len(previous_rows),
        'current_coverage_vs_previous': round(current_coverage, 4),
        'min_current_coverage': min_current_coverage,
        'score_period_reason': (
            'current period reached broad coverage' if selected_label == 'current'
            else 'newer period is still incomplete; keep one common prior period for cross-sectional quality scores'
        ),
        'stocks': scored,
        'fresh_report_events': fresh_reports,
        'quality_fields': ['roe', 'revenue_yoy', 'profit_yoy', 'gross_margin', 'operating_cashflow_per_share'],
        'not_yet_used': ['debt_ratio/balance_sheet_score: requires disclosure-timed source validation'],
    }
