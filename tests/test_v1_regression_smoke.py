from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine.v1_regression_smoke import FUND_IDS, assert_v1_regression_smoke


class V1RegressionSmokeTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, settled='2026-08-20', web='2026-08-20', scan=None):
        state_root = root / 'state'
        web_root = root / 'web'
        state_root.mkdir()
        (web_root / 'd').mkdir(parents=True)
        (web_root / 'e').mkdir(parents=True)
        for fid in FUND_IDS:
            payload = {
                'last_processed_date': settled,
                'conditional_plan_date': settled,
                'execution_model': 'CONDITIONAL_PLAN_V1',
                'cash': 1000.0,
                'positions': {},
                'pending_targets': [],
                'exit_plans': {},
            }
            if scan is not None:
                payload['last_conditional_scan_key'] = scan
            (state_root / f'{fid}.json').write_text(json.dumps(payload), encoding='utf-8')
        for page in ('d', 'e'):
            (web_root / page / 'data.json').write_text(
                json.dumps({
                    'updated_at': web,
                    'execution_model': 'CONDITIONAL_PLAN_V1',
                    'plan_version': 'conditional-plan-v1',
                }),
                encoding='utf-8',
            )
        return state_root, web_root

    def test_intraday_public_date_requires_same_scan_in_all_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root, web_root = self._write_fixture(
                Path(tmp), web='2026-08-21', scan='2026-08-21T11:20'
            )
            result = assert_v1_regression_smoke(state_root, web_root)
            self.assertEqual(result['mode'], 'intraday')
            self.assertEqual(result['scan_key'], '2026-08-21T11:20')

    def test_intraday_public_date_fails_without_ledger_scan_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root, web_root = self._write_fixture(Path(tmp), web='2026-08-21')
            with self.assertRaises(RuntimeError):
                assert_v1_regression_smoke(state_root, web_root)

    def test_intraday_scan_must_match_public_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root, web_root = self._write_fixture(
                Path(tmp), web='2026-08-21', scan='2026-08-20T14:55'
            )
            with self.assertRaises(RuntimeError):
                assert_v1_regression_smoke(state_root, web_root)

    def test_intraday_rejects_negative_cash(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root, web_root = self._write_fixture(
                Path(tmp), web='2026-08-21', scan='2026-08-21T11:20'
            )
            path = state_root / 'A.json'
            payload = json.loads(path.read_text())
            payload['cash'] = -1.0
            path.write_text(json.dumps(payload))
            with self.assertRaises(RuntimeError):
                assert_v1_regression_smoke(state_root, web_root)

    def test_intraday_rejects_wrong_web_execution_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root, web_root = self._write_fixture(
                Path(tmp), web='2026-08-21', scan='2026-08-21T11:20'
            )
            path = web_root / 'd' / 'data.json'
            payload = json.loads(path.read_text())
            payload['execution_model'] = 'FIXED_OPEN'
            path.write_text(json.dumps(payload))
            with self.assertRaises(RuntimeError):
                assert_v1_regression_smoke(state_root, web_root)


if __name__ == '__main__':
    unittest.main()
