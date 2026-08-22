import tempfile
import unittest
from pathlib import Path

from engine_v3.contracts import DECISION_SCHEMA_VERSION, decision_hash, validate_decision
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


if __name__ == "__main__":
    unittest.main()
