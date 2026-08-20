import json
import tempfile
import unittest
from pathlib import Path

from engine.v1_freshness import FUND_IDS
from engine.v1_production_gate import assert_v1_production_gate


class V1ProductionGateTests(unittest.TestCase):
    def _fixture(self, root: Path, date: str = '2026-08-20') -> tuple[Path, Path]:
        state_root = root / 'state'
        web_root = root / 'web'
        state_root.mkdir(parents=True)
        for fid in FUND_IDS:
            (state_root / f'{fid}.json').write_text(
                json.dumps({
                    'last_processed_date': date,
                    'conditional_plan_date': date,
                    'execution_model': 'CONDITIONAL_PLAN_V1',
                    'cash': 100000.0,
                    'positions': {
                        'sh.600000': {'qty': 100, 'avg_cost': 10.0},
                    },
                    'pending_targets': [{
                        'symbol': 'sz.000001',
                        'trade_plan': {'plan_version': 'conditional-plan-v1'},
                    }],
                }),
                encoding='utf-8',
            )
        for page in ('d', 'e'):
            path = web_root / page
            path.mkdir(parents=True)
            (path / 'data.json').write_text(
                json.dumps({
                    'updated_at': date,
                    'execution_model': 'CONDITIONAL_PLAN_V1',
                    'plan_version': 'conditional-plan-v1',
                }),
                encoding='utf-8',
            )
        return state_root, web_root

    def _read_state(self, state_root: Path, fid: str = 'A') -> dict:
        return json.loads((state_root / f'{fid}.json').read_text(encoding='utf-8'))

    def _write_state(self, state_root: Path, state: dict, fid: str = 'A') -> None:
        (state_root / f'{fid}.json').write_text(json.dumps(state), encoding='utf-8')

    def test_valid_post_settlement_outputs_pass(self):
        with tempfile.TemporaryDirectory() as td:
            state_root, web_root = self._fixture(Path(td))
            result = assert_v1_production_gate('2026-08-20', state_root, web_root)
        self.assertEqual(result['funds']['A']['pending_targets'], 1)
        self.assertEqual(result['web']['d']['plan_version'], 'conditional-plan-v1')

    def test_negative_cash_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            state_root, web_root = self._fixture(Path(td))
            state = self._read_state(state_root)
            state['cash'] = -1.0
            self._write_state(state_root, state)
            with self.assertRaisesRegex(RuntimeError, 'negative cash'):
                assert_v1_production_gate('2026-08-20', state_root, web_root)

    def test_legacy_pending_plan_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            state_root, web_root = self._fixture(Path(td))
            state = self._read_state(state_root)
            state['pending_targets'][0]['trade_plan']['plan_version'] = 'fixed-open-v0'
            self._write_state(state_root, state)
            with self.assertRaisesRegex(RuntimeError, 'wrong plan_version'):
                assert_v1_production_gate('2026-08-20', state_root, web_root)

    def test_non_main_board_pending_buy_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            state_root, web_root = self._fixture(Path(td))
            state = self._read_state(state_root)
            state['pending_targets'][0]['symbol'] = 'sz.300001'
            self._write_state(state_root, state)
            with self.assertRaisesRegex(RuntimeError, 'outside main-board universe'):
                assert_v1_production_gate('2026-08-20', state_root, web_root)

    def test_wrong_public_model_or_plan_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            state_root, web_root = self._fixture(Path(td))
            target = web_root / 'e' / 'data.json'
            payload = json.loads(target.read_text(encoding='utf-8'))
            payload['execution_model'] = 'FIXED_OPEN'
            payload['plan_version'] = 'legacy'
            target.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'web/e/data.json: execution_model'):
                assert_v1_production_gate('2026-08-20', state_root, web_root)

    def test_freshness_is_still_a_hard_requirement(self):
        with tempfile.TemporaryDirectory() as td:
            state_root, web_root = self._fixture(Path(td))
            state = self._read_state(state_root)
            state['last_processed_date'] = '2026-08-19'
            self._write_state(state_root, state)
            with self.assertRaisesRegex(RuntimeError, 'V1 freshness invariant failed'):
                assert_v1_production_gate('2026-08-20', state_root, web_root)


if __name__ == '__main__':
    unittest.main()
