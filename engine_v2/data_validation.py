from __future__ import annotations

import argparse
import json
import signal
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


@dataclass
class Probe:
    name: str
    status: str
    scope: str
    rows: int = 0
    asof: str | None = None
    latency_s: float = 0.0
    details: dict | None = None
    error: str | None = None


def _call_with_timeout(seconds: int, fn: Callable):
    if not hasattr(signal, "SIGALRM"):
        return fn()
    old = signal.getsignal(signal.SIGALRM)

    def alarm(_signum, _frame):
        raise TimeoutError(f"provider call exceeded {seconds}s")

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _cols(df) -> set[str]:
    return {str(x) for x in getattr(df, "columns", [])}


def _missing(df, required: set[str]) -> list[str]:
    return sorted(required - _cols(df))


def _date_text(value) -> str | None:
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except Exception:
        return None


def _probe_df(name: str, scope: str, fn: Callable, required: set[str], *, min_rows: int = 1, timeout: int = 45) -> tuple[Probe, object | None]:
    started = time.monotonic()
    try:
        df = _call_with_timeout(timeout, fn)
        elapsed = time.monotonic() - started
        rows = 0 if df is None else len(df)
        missing = _missing(df, required) if df is not None else sorted(required)
        if df is None:
            return Probe(name, "FAIL", scope, latency_s=round(elapsed, 2), error="provider returned None"), None
        if missing:
            return Probe(name, "FAIL", scope, rows=rows, latency_s=round(elapsed, 2), details={"missing_columns": missing}), df
        status = "PASS" if rows >= min_rows else "DEGRADED"
        return Probe(name, status, scope, rows=rows, latency_s=round(elapsed, 2)), df
    except Exception as exc:
        return Probe(name, "FAIL", scope, latency_s=round(time.monotonic() - started, 2), error=f"{type(exc).__name__}: {exc}"), None


def _quarter_end_on_or_before(day: date) -> date:
    ends = [date(day.year, 3, 31), date(day.year, 6, 30), date(day.year, 9, 30), date(day.year, 12, 31)]
    candidates = [x for x in ends if x <= day]
    if candidates:
        return max(candidates)
    return date(day.year - 1, 12, 31)


def _safe_requested_date(now_cn: datetime) -> date:
    if now_cn.weekday() < 5 and now_cn.time() >= dt_time(15, 20):
        return now_cn.date()
    return now_cn.date() - timedelta(days=1)


def _activity_asof(df) -> str | None:
    if df is None or df.empty or not {"item", "value"}.issubset(_cols(df)):
        return None
    match = df[df["item"].astype(str) == "统计日期"]
    if match.empty:
        return None
    return _date_text(match.iloc[0]["value"])


def _latest_highlow_asof(df, trade_date: str) -> tuple[str | None, dict]:
    if df is None or df.empty or "date" not in _cols(df):
        return None, {}
    work = df.copy()
    work["_date"] = work["date"].astype(str).str[:10]
    work = work[work["_date"] <= trade_date]
    if work.empty:
        return None, {}
    row = work.sort_values("_date").iloc[-1]
    details = {k: int(row[k]) for k in ("high20", "low20", "high60", "low60") if k in row and str(row[k]) != "nan"}
    return str(row["_date"]), details


def _financial_available_rows(df, trade_date: str) -> tuple[int, str | None]:
    if df is None or df.empty or "最新公告日期" not in _cols(df):
        return 0, None
    work = df.copy()
    work["_announce"] = work["最新公告日期"].astype(str).str[:10]
    work = work[(work["_announce"] >= "2000-01-01") & (work["_announce"] <= trade_date)]
    if work.empty:
        return 0, None
    return len(work), str(work["_announce"].max())


def validate_data_sources(trade_date: str | None = None) -> dict:
    import akshare as ak
    from engine.real_market import AKShareMarket

    now_cn = datetime.now(ZoneInfo("Asia/Shanghai"))
    requested = date.fromisoformat(trade_date) if trade_date else _safe_requested_date(now_cn)
    market = AKShareMarket()
    latest = market.latest_trade_date(requested.isoformat())
    td = date.fromisoformat(latest)
    d8 = latest.replace("-", "")
    probes: list[Probe] = []

    p, limit_up = _probe_df(
        "limit_up_pool", "forward_and_recent_only",
        lambda: ak.stock_zt_pool_em(date=d8),
        {"代码", "名称", "炸板次数", "连板数", "所属行业", "首次封板时间", "最后封板时间"},
        min_rows=1,
    )
    p.asof = latest
    if limit_up is not None and not limit_up.empty:
        p.details = {
            "max_board": int(limit_up["连板数"].max()),
            "limit_up_count": len(limit_up),
            "open_break_events": int(limit_up["炸板次数"].fillna(0).astype(float).sum()),
        }
    probes.append(p)

    p, broken = _probe_df(
        "broken_limit_pool", "forward_and_recent_only",
        lambda: ak.stock_zt_pool_zbgc_em(date=d8),
        {"代码", "名称", "炸板次数", "所属行业"},
        min_rows=0,
    )
    p.asof = latest
    p.details = {"broken_count": 0 if broken is None else len(broken)}
    probes.append(p)

    p, limit_down = _probe_df(
        "limit_down_pool", "forward_and_recent_only",
        lambda: ak.stock_zt_pool_dtgc_em(date=d8),
        {"代码", "名称", "连续跌停", "开板次数", "所属行业"},
        min_rows=0,
    )
    p.asof = latest
    p.details = {"limit_down_count": 0 if limit_down is None else len(limit_down)}
    probes.append(p)

    p, activity = _probe_df(
        "market_activity", "forward_only",
        ak.stock_market_activity_legu,
        {"item", "value"},
        min_rows=8,
    )
    p.asof = _activity_asof(activity)
    if p.status != "FAIL" and p.asof != latest:
        p.status = "DEGRADED"
        p.details = {"expected_trade_date": latest, "note": "current-only feed did not report the latest completed session"}
    probes.append(p)

    p, highlow = _probe_df(
        "high_low_breadth", "historical_point_in_time",
        lambda: ak.stock_a_high_low_statistics(symbol="all"),
        {"date", "high20", "low20", "high60", "low60"},
        min_rows=100,
    )
    asof, breadth_details = _latest_highlow_asof(highlow, latest)
    p.asof = asof
    p.details = breadth_details
    if p.status != "FAIL":
        if asof is None:
            p.status = "FAIL"
        else:
            lag = (td - date.fromisoformat(asof)).days
            p.details = {**(p.details or {}), "calendar_lag_days": lag}
            if lag > 5:
                p.status = "FAIL"
            elif asof != latest:
                p.status = "DEGRADED"
    probes.append(p)

    # Industry layer: probe Eastmoney, independent Tonghuashun flow, and CNInfo dated taxonomy changes.
    p, industries = _probe_df(
        "industry_board_eastmoney", "forward_only",
        ak.stock_board_industry_name_em,
        {"板块名称", "板块代码", "涨跌幅", "上涨家数", "下跌家数"},
        min_rows=50,
    )
    p.asof = latest if p.status != "FAIL" else None
    probes.append(p)

    p, industry_flow_em = _probe_df(
        "industry_fund_flow_eastmoney", "forward_only",
        lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"),
        {"名称", "今日涨跌幅", "今日主力净流入-净额"},
        min_rows=50,
    )
    p.asof = latest if p.status != "FAIL" else None
    probes.append(p)

    p, industry_flow_ths = _probe_df(
        "industry_fund_flow_ths", "forward_only",
        lambda: ak.stock_fund_flow_industry(symbol="即时"),
        {"行业", "行业指数", "行业-涨跌幅", "净额", "公司家数"},
        min_rows=50,
        timeout=60,
    )
    p.asof = latest if p.status != "FAIL" else None
    probes.append(p)

    p, industry_change = _probe_df(
        "industry_membership_history_cninfo", "historical_point_in_time",
        lambda: ak.stock_industry_change_cninfo(
            symbol="002594",
            start_date="20100101",
            end_date=d8,
        ),
        {"行业中类", "行业大类", "分类标准", "证券代码", "变更日期"},
        min_rows=1,
        timeout=60,
    )
    if industry_change is not None and not industry_change.empty:
        change_dates = [_date_text(x) for x in industry_change["变更日期"].tolist()]
        change_dates = [x for x in change_dates if x and x <= latest]
        p.asof = max(change_dates) if change_dates else None
    p.details = {"sample": "002594", "purpose": "prove dated industry membership history is available"}
    probes.append(p)

    if industries is not None and not industries.empty and {"板块名称"}.issubset(_cols(industries)):
        sample_industry = str(industries.iloc[0]["板块名称"])
        p, industry_hist = _probe_df(
            "industry_price_history", "historical_prices_current_taxonomy",
            lambda: ak.stock_board_industry_hist_em(
                symbol=sample_industry,
                start_date=(td - timedelta(days=120)).strftime("%Y%m%d"),
                end_date=d8,
                period="日k",
                adjust="",
            ),
            {"日期", "收盘", "涨跌幅", "成交额"},
            min_rows=20,
        )
        p.asof = _date_text(industry_hist.iloc[-1]["日期"]) if industry_hist is not None and not industry_hist.empty else None
        p.details = {"sample_industry": sample_industry, "warning": "historical prices are usable; current taxonomy/membership must not be back-projected"}
        probes.append(p)

    period = _quarter_end_on_or_before(td)
    p, report = _probe_df(
        "financial_report_with_announcement_date", "historical_point_in_time_after_announcement",
        lambda: ak.stock_yjbb_em(date=period.strftime("%Y%m%d")),
        {"股票代码", "股票简称", "营业总收入-同比增长", "净利润-同比增长", "净资产收益率", "每股经营现金流量", "销售毛利率", "最新公告日期"},
        min_rows=100,
        timeout=60,
    )
    available_rows, latest_announcement = _financial_available_rows(report, latest)
    p.asof = latest_announcement
    p.details = {
        "report_period": period.isoformat(),
        "rows_announced_by_trade_date": available_rows,
        "raw_rows": 0 if report is None else len(report),
    }
    if p.status != "FAIL" and available_rows < 50:
        p.status = "DEGRADED"
    probes.append(p)

    p, rich = _probe_df(
        "rich_financial_ratios", "research_only_without_disclosure_join",
        lambda: ak.stock_financial_analysis_indicator(symbol="600519", start_year=str(max(2010, td.year - 2))),
        {"日期", "资产负债率(%)"},
        min_rows=4,
        timeout=60,
    )
    p.details = {
        "sample": "600519",
        "rule": "must join a verified disclosure timestamp before historical use",
    }
    probes.append(p)

    p, disclosure = _probe_df(
        "cninfo_disclosure_metadata", "historical_point_in_time",
        lambda: ak.stock_zh_a_disclosure_report_cninfo(
            symbol="000001",
            market="沪深京",
            keyword="",
            category="",
            start_date=date(td.year, 1, 1).strftime("%Y%m%d"),
            end_date=d8,
        ),
        {"代码", "简称", "公告标题", "公告时间"},
        min_rows=1,
        timeout=60,
    )
    if disclosure is not None and not disclosure.empty:
        dates = [_date_text(x) for x in disclosure["公告时间"].tolist()]
        dates = [x for x in dates if x and x <= latest]
        p.asof = max(dates) if dates else None
    p.details = {"sample": "000001", "purpose": "audit financial disclosure timestamps"}
    probes.append(p)

    by_name = {p.name: p for p in probes}
    sentiment_ok = all(by_name[x].status in {"PASS", "DEGRADED"} for x in ("limit_up_pool", "broken_limit_pool", "limit_down_pool"))
    breadth_ok = by_name["high_low_breadth"].status in {"PASS", "DEGRADED"}
    eastmoney_industry_ok = by_name["industry_board_eastmoney"].status == "PASS" and by_name["industry_fund_flow_eastmoney"].status in {"PASS", "DEGRADED"}
    fallback_industry_ok = by_name["industry_fund_flow_ths"].status in {"PASS", "DEGRADED"} and by_name["industry_membership_history_cninfo"].status in {"PASS", "DEGRADED"}
    industry_ok = eastmoney_industry_ok or fallback_industry_ok
    fundamentals_ok = by_name["financial_report_with_announcement_date"].status in {"PASS", "DEGRADED"}

    return {
        "validator_version": "v2-data-0.2",
        "generated_at": now_cn.isoformat(),
        "trade_date": latest,
        "report_period": period.isoformat(),
        "probes": [asdict(x) for x in probes],
        "readiness": {
            "sentiment_forward": sentiment_ok,
            "breadth_forward": breadth_ok,
            "industry_forward": industry_ok,
            "industry_primary_eastmoney": eastmoney_industry_ok,
            "industry_fallback_ths_cninfo": fallback_industry_ok,
            "fundamentals_forward": fundamentals_ok,
            "forward_shadow_ready": sentiment_ok and breadth_ok and industry_ok and fundamentals_ok,
            "historical_backtest_production_grade": False,
            "historical_blockers": [
                "涨停/炸板/跌停接口只提供近期数据，需从影子盘启用日起自行归档",
                "东方财富行业实时成份属于当前快照；历史回测需使用巨潮带变更日期的行业归属",
                "rich_financial_ratios 必须与公告时间连接后才能用于历史时点因子",
                "历史股票池还需解决上市/退市/ST/停牌的逐日 point-in-time 状态",
            ],
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD; omitted means latest completed A-share session")
    ap.add_argument("--output")
    args = ap.parse_args()
    report = validate_data_sources(args.date)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
