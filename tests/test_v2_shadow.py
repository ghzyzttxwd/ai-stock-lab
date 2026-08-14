import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from engine_v2.data_validation import (
    _financial_available_rows,
    _quarter_end_on_or_before,
    _safe_requested_date,
)
from engine_v2.fundamentals import (
    _announced_mainboard_rows,
    score_quality,
    select_scoring_period,
)
from engine_v2.regime import MarketRegime, classify_market_regime
from engine_v2.sizing import risk_budget_weights
from engine_v2.snapshot import _normalize_market_row
from engine_v2.strategies import (
    strategy_a_v2,
    strategy_b_v2,
    strategy_c_v2,
    strategy_d_fallback_v2,
    strategy_l_v2,
)


def candidate(i: int, industry: str = "行业A", **kw):
    base = {
        "symbol": f"sh.600{i:03d}",
        "name": f"股票{i}",
        "industry": industry,
        "correlation_cluster": industry,
        "vol20": 0.02 + i * 0.001,
        "trend": 70,
        "momentum": 68,
        "liquidity": 75,
        "risk": 72,
        "quality_score": 72,
        "cashflow_score": 70,
        "fundamental_ready": True,
        "financial_distress": False,
        "valuation_score": 65,
        "industry_score": 70,
        "leader_score": 68,
        "theme_score": 66,
        "sentiment_score": 65,
        "breakout_quality": 67,
        "crowding_score": 55,
    }
    base.update(kw)
    return base


class V2ShadowTests(unittest.TestCase):
    def test_regime_uses_broad_market_inputs(self):
        strong = classify_market_regime({
            "index_trend_score": 78,
            "advancer_ratio": 0.68,
            "new_high_ratio": 0.06,
            "new_low_ratio": 0.01,
            "limit_up_count": 75,
            "limit_down_count": 4,
            "limit_break_rate": 0.18,
        })
        self.assertEqual(strong.label, "risk_on")
        weak = classify_market_regime({
            "index_trend_score": 22,
            "advancer_ratio": 0.25,
            "new_high_ratio": 0.005,
            "new_low_ratio": 0.08,
            "limit_up_count": 12,
            "limit_down_count": 55,
            "limit_break_rate": 0.58,
        })
        self.assertEqual(weak.label, "risk_off")

    def test_panic_rebound_is_not_normal_risk_on(self):
        r = classify_market_regime({
            "index_trend_score": 60,
            "advancer_ratio": 0.72,
            "limit_up_count": 80,
            "limit_down_count": 3,
            "limit_break_rate": 0.12,
            "index_drawdown20": -0.11,
            "index_return3": 0.06,
        })
        self.assertEqual(r.label, "panic_rebound")

    def test_risk_budget_respects_single_and_industry_caps(self):
        picks = [candidate(i, "行业A" if i < 4 else "行业B") for i in range(8)]
        out = risk_budget_weights(picks, exposure=0.8, max_weight=0.15, industry_cap=0.35)
        self.assertLessEqual(sum(x["target_weight"] for x in out), 0.800001)
        self.assertLessEqual(max(x["target_weight"] for x in out), 0.150001)
        a = sum(x["target_weight"] for x in out if x["industry"] == "行业A")
        self.assertLessEqual(a, 0.350001)

    def test_c_goes_flat_in_risk_off(self):
        r = MarketRegime("risk_off", 25, 80, ("市场宽度偏弱",))
        self.assertEqual(strategy_c_v2([candidate(i) for i in range(8)], r), [])

    def test_a_and_l_reject_bad_or_unverified_financial_quality(self):
        r = MarketRegime("neutral", 50, 50, ())
        bad = candidate(1, quality_score=30, cashflow_score=25)
        missing = candidate(3, fundamental_ready=False, quality_score=None, cashflow_score=None)
        good = candidate(2)
        self.assertTrue(strategy_a_v2([bad, missing, good], r))
        self.assertTrue(strategy_l_v2([bad, missing, good], r))
        for fn in (strategy_a_v2, strategy_l_v2):
            symbols={x["symbol"] for x in fn([bad, missing, good], r)}
            self.assertNotIn(bad["symbol"], symbols)
            self.assertNotIn(missing["symbol"], symbols)
            self.assertIn(good["symbol"], symbols)

    def test_all_v2_targets_record_thesis_and_invalidation(self):
        r = MarketRegime("risk_on", 72, 70, ())
        xs = [candidate(i, "行业A" if i % 2 else "行业B") for i in range(10)]
        outputs = [
            strategy_a_v2(xs, r),
            strategy_b_v2(xs, r),
            strategy_c_v2(xs, r),
            strategy_d_fallback_v2(xs, r),
            strategy_l_v2(xs, r),
        ]
        for out in outputs:
            self.assertTrue(out)
            for x in out:
                self.assertTrue(x.get("thesis"))
                self.assertTrue(x.get("invalidation"))

    def test_drawdown_brake_reduces_exposure(self):
        r = MarketRegime("risk_on", 75, 80, ())
        xs = [candidate(i, "行业A" if i % 2 else "行业B") for i in range(10)]
        normal = sum(x["target_weight"] for x in strategy_b_v2(xs, r, fund_drawdown=0.0))
        stressed = sum(x["target_weight"] for x in strategy_b_v2(xs, r, fund_drawdown=-0.13))
        self.assertLess(stressed, normal)

    def test_validator_uses_latest_completed_quarter(self):
        self.assertEqual(_quarter_end_on_or_before(date(2026, 8, 14)), date(2026, 6, 30))
        self.assertEqual(_quarter_end_on_or_before(date(2026, 2, 1)), date(2025, 12, 31))

    def test_validator_never_uses_partial_trading_day(self):
        before_close = datetime(2026, 8, 14, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        after_close = datetime(2026, 8, 14, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        saturday = datetime(2026, 8, 15, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(_safe_requested_date(before_close), date(2026, 8, 13))
        self.assertEqual(_safe_requested_date(after_close), date(2026, 8, 14))
        self.assertEqual(_safe_requested_date(saturday), date(2026, 8, 14))

    def test_financial_rows_are_filtered_by_announcement_date(self):
        df = pd.DataFrame([
            {"最新公告日期": "2026-08-10", "股票代码": "600001"},
            {"最新公告日期": "2026-08-20", "股票代码": "600002"},
        ])
        rows, latest = _financial_available_rows(df, "2026-08-14")
        self.assertEqual(rows, 1)
        self.assertEqual(latest, "2026-08-10")

    def test_fundamental_loader_filters_non_mainboard_future_reports_and_keeps_book_value(self):
        df = pd.DataFrame([
            {"股票代码":"600001","股票简称":"主板A","最新公告日期":"2026-08-10","每股收益":0.25,"每股净资产":5.5,"净资产收益率":8,"营业总收入-同比增长":10,"净利润-同比增长":12,"每股经营现金流量":0.5,"销售毛利率":25},
            {"股票代码":"688001","股票简称":"科创","最新公告日期":"2026-08-10","净资产收益率":8},
            {"股票代码":"000002","股票简称":"主板B","最新公告日期":"2026-08-20","净资产收益率":8},
        ])
        rows=_announced_mainboard_rows(df,"2026-08-14")
        self.assertEqual(set(rows),{"600001"})
        self.assertEqual(rows["600001"]["book_value_per_share"],5.5)
        self.assertEqual(rows["600001"]["eps_period"],0.25)

    def test_sina_missing_feature_sentinels_never_become_real_zero_factors(self):
        row=_normalize_market_row({
            "source":"sina","peTTM":0.0,"pbMRQ":0.0,"r60_snapshot":0.0,"turn":0.0,"close":10.0,
        })
        self.assertIsNone(row["peTTM"])
        self.assertIsNone(row["pbMRQ"])
        self.assertIsNone(row["r60_snapshot"])
        self.assertIsNone(row["turn"])

    def test_real_eastmoney_market_fields_are_preserved(self):
        row=_normalize_market_row({
            "source":"eastmoney","peTTM":12.0,"pbMRQ":1.4,"r60_snapshot":0.12,"turn":2.3,
        })
        self.assertEqual(row["peTTM"],12.0)
        self.assertEqual(row["pbMRQ"],1.4)
        self.assertEqual(row["r60_snapshot"],0.12)
        self.assertEqual(row["turn"],2.3)

    def test_quality_scoring_keeps_one_common_period_until_new_period_is_broad(self):
        previous={f"600{i:03d}":{} for i in range(100)}
        early_current={f"600{i:03d}":{} for i in range(20)}
        label, selected, coverage=select_scoring_period(early_current,previous,min_current_coverage=0.8)
        self.assertEqual(label,"previous")
        self.assertIs(selected,previous)
        self.assertAlmostEqual(coverage,0.2)
        broad_current={f"600{i:03d}":{} for i in range(85)}
        label, selected, coverage=select_scoring_period(broad_current,previous,min_current_coverage=0.8)
        self.assertEqual(label,"current")
        self.assertIs(selected,broad_current)
        self.assertAlmostEqual(coverage,0.85)

    def test_quality_score_requires_cashflow_and_multiple_real_fields(self):
        rows={
            "600001":{"roe":12,"revenue_yoy":15,"profit_yoy":18,"gross_margin":30,"operating_cashflow_per_share":0.5},
            "600002":{"roe":-5,"revenue_yoy":-10,"profit_yoy":-40,"gross_margin":15,"operating_cashflow_per_share":-0.2},
            "600003":{"roe":None,"revenue_yoy":None,"profit_yoy":10,"gross_margin":None,"operating_cashflow_per_share":0.1},
        }
        scored=score_quality(rows)
        self.assertTrue(scored["600001"]["fundamental_ready"])
        self.assertTrue(scored["600002"]["fundamental_ready"])
        self.assertTrue(scored["600002"]["financial_distress"])
        self.assertFalse(scored["600003"]["fundamental_ready"])
        self.assertIsNone(scored["600003"]["quality_score"])


if __name__ == "__main__":
    unittest.main()
