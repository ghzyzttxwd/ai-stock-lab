import tempfile
import unittest
from pathlib import Path

from engine_v3.contracts import (
    CONDITIONAL_EXECUTION_MODEL,
    CONDITIONAL_PLAN_VERSION,
    DECISION_SCHEMA_VERSION,
    decision_hash,
    validate_decision,
)
from engine_v3.ledger import execute_agent_portfolio, new_ledger
from engine_v3.session import run_decision


def decision(symbol="sh.600000"):
    payload = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "mode": "AUTONOMOUS_AI_PAPER",
        "requires_user_approval": False,
        "uses_future_data": False,
        "decision_date": "2026-08-21",
        "execute_on": "2026-08-24",
        "created_at": "2026-08-21T15:30:00+08:00",
        "market_view": "市场中性，保留现金并选择流动性充足的主板股票。",
        "brief_symbols": [symbol],
        "portfolios": {},
    }
    for fund_id in ("A", "B", "C", "D", "L"):
        payload["portfolios"][fund_id] = [{
            "symbol": symbol,
            "name": "浦发银行",
            "industry": "银行",
            "target_weight": 0.10,
            "thesis": "估值、流动性与价格状态匹配该虚拟组合。",
            "invalidation": "基本面或价格结构明显恶化。",
        }]
    payload["decision_sha256"] = decision_hash(payload)
    return payload


def conditional_decision(symbol="sh.600000", weight=0.10, mode="breakout"):
    payload = {
        "schema_version": DECISION_SCHEMA_VERSION,
        "mode": "AUTONOMOUS_AI_PAPER",
        "requires_user_approval": False,
        "uses_future_data": False,
        "decision_date": "2026-08-26",
        "execute_on": "2026-08-27",
        "created_at": "2026-08-26T20:20:00+08:00",
        "market_view": "市场允许有限风险敞口，但所有新增仓位必须由次日价格确认。",
        "risk_regime": "risk_on",
        "execution_model": CONDITIONAL_EXECUTION_MODEL,
        "brief_symbols": [symbol],
        "portfolios": {},
    }
    plan = {
        "plan_version": CONDITIONAL_PLAN_VERSION,
        "entry": {
            "mode": mode,
            "trigger_price": 10.0,
            "valid_min": 10.0 if mode == "breakout" else 9.8,
            "valid_max": 10.1,
        },
        "max_gap_up_pct": 0.03,
        "cancel_if_not_triggered_by_close": True,
    }
    for fund_id in ("A", "B", "C", "D", "L"):
        payload["portfolios"][fund_id] = [{
            "symbol": symbol,
            "name": "浦发银行",
            "industry": "银行",
            "target_weight": weight,
            "thesis": "AI提出交易假设，等待次日价格确认。",
            "invalidation": "价格确认失败或逻辑失效。",
            "trade_plan": plan,
        }]
    payload["decision_sha256"] = decision_hash(payload)
    return payload


class V3AgentPaperTests(unittest.TestCase):
    def test_contract_rejects_chinext(self):
        payload = decision("sz.300001")
        with self.assertRaisesRegex(ValueError, "non-main-board"):
            validate_decision(payload)

    def test_late_processing_uses_declared_open_and_is_idempotent(self):
        payload = decision()
        bars = {
            "sh.600000": {
                "open": 10.0, "high": 10.4, "low": 9.8, "close": 10.2,
                "preclose": 9.9, "tradestatus": "1",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = run_decision(payload, bars, root)
            second = run_decision(payload, bars, root)
            self.assertEqual(first["status"], "executed")
            self.assertEqual(second["status"], "already_executed")
            self.assertEqual(first["fills"], {"A": 1, "B": 1, "C": 1, "D": 1, "L": 1})
            state = __import__("json").loads((root / "ledgers/A.json").read_text())
            self.assertEqual(state["fills"][0]["open_price"], 10.0)
            self.assertEqual(state["last_processed_date"], "2026-08-24")
            self.assertIn("legacy", state["fills"][0]["note"].lower())

    def test_incomplete_bars_do_not_create_partial_state(self):
        payload = decision()
        payload["portfolios"]["A"].append({
            "symbol": "sh.600001", "name": "邯郸钢铁", "target_weight": 0.05,
            "thesis": "测试", "invalidation": "测试",
        })
        payload["brief_symbols"].append("sh.600001")
        payload["decision_sha256"] = decision_hash(payload)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "bars incomplete"):
                run_decision(payload, {"sh.600000": {"open": 10}}, root)
            self.assertFalse((root / "ledgers/A.json").exists())

    def test_cutover_requires_conditional_plan(self):
        payload = conditional_decision()
        payload["portfolios"]["A"][0].pop("trade_plan")
        payload["decision_sha256"] = decision_hash(payload)
        with self.assertRaisesRegex(ValueError, "trade_plan.plan_version"):
            validate_decision(payload)

    def test_breakout_waits_for_intraday_confirmation(self):
        payload = conditional_decision()
        bars = {
            "sh.600000": {
                "open": 9.90, "high": 10.05, "low": 9.85, "close": 10.02,
                "preclose": 9.90, "tradestatus": "1",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_decision(payload, bars, root)
            self.assertEqual(result["fills"], {"A": 1, "B": 1, "C": 1, "D": 1, "L": 1})
            state = __import__("json").loads((root / "ledgers/A.json").read_text())
            fill = state["fills"][0]
            self.assertEqual(fill["open_price"], 10.0)
            self.assertEqual(fill["execution_price_field"], "conditional_trigger")
            self.assertEqual(fill["conditional_reason"], "intraday_breakout_confirmed")

    def test_breakout_refuses_open_above_max_chase(self):
        payload = conditional_decision()
        bars = {
            "sh.600000": {
                "open": 10.20, "high": 10.30, "low": 10.15, "close": 10.25,
                "preclose": 10.00, "tradestatus": "1",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_decision(payload, bars, root)
            self.assertEqual(result["fills"], {"A": 0, "B": 0, "C": 0, "D": 0, "L": 0})
            state = __import__("json").loads((root / "ledgers/A.json").read_text())
            gate = state["decisions"][-1]["v3_gate"]["entries"][0]
            self.assertEqual(gate["status"], "blocked")
            self.assertEqual(gate["reason"], "open_above_max_chase")

    def test_drawdown_blocks_new_exposure(self):
        payload = validate_decision(conditional_decision())
        state = new_ledger("A", "2026-08-21")
        state["cash"] = 900000.0
        state["equity_curve"] = [{"date": "2026-08-25", "equity": 900000.0, "cash": 900000.0, "market_value": 0.0, "fees": 0.0}]
        bars = {
            "sh.600000": {
                "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1,
                "preclose": 9.9, "tradestatus": "1",
            }
        }
        result = execute_agent_portfolio(state, payload, bars)
        self.assertEqual(result["execution"]["fills"], [])
        self.assertEqual(result["v3_gate"]["entries"][0]["reason"], "portfolio_drawdown_no_expansion")

    def test_drawdown_still_allows_reduction(self):
        payload = validate_decision(conditional_decision(weight=0.05))
        state = new_ledger("A", "2026-08-21")
        state["cash"] = 810000.0
        state["positions"] = {
            "sh.600000": {
                "name": "浦发银行", "qty": 10000, "avg_cost": 10.0,
                "acquired_date": "2026-08-25", "last_price": 9.0,
            }
        }
        state["equity_curve"] = [{"date": "2026-08-25", "equity": 900000.0, "cash": 810000.0, "market_value": 90000.0, "fees": 0.0}]
        bars = {
            "sh.600000": {
                "open": 9.0, "high": 9.2, "low": 8.9, "close": 9.1,
                "preclose": 9.1, "tradestatus": "1",
            }
        }
        result = execute_agent_portfolio(state, payload, bars)
        sells = [x for x in result["execution"]["fills"] if x["side"] == "SELL"]
        self.assertEqual(len(sells), 1)
        self.assertEqual(result["v3_gate"]["entries"][0]["status"], "reduce")


if __name__ == "__main__":
    unittest.main()
