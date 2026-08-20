import json
import tempfile
import unittest
from pathlib import Path

from engine.v1_freshness import FUND_IDS, assert_v1_freshness


class V1FreshnessTests(unittest.TestCase):
    def _fixture(self, root: Path, date: str = '2026-08-20') -> tuple[Path, Path]:
        state_root = root / 'state'
        web_root = root / 'web'
        state_root.mkdir(parents=True)
        for fid in FUND_IDS:
            (state_root / f'{fid}.json').write_text(
                json.dumps({
                    'last_processed_date': date,
                    'conditional_plan_date': date,
                }),
                encoding='utf-8',
            )
        for page in ('d', 'e'):
            path = web_root / page
            path.mkdir(parents=True)
            (path / 'data.json').write_text(
                json.dumps({'updated_at': date}),
                encoding='utf-8',
            )
        return state_root, web_root

    def test_current_state_and_web_pass(self):
        with tempfile.TemporaryDirectory() as td:
            state_root, web_root = self._fixture(Path(td))
            result = assert_v1_freshness('2026-08-20', state_root, web_root)
        self.assertEqual(result['web_dates'], {'d': '2026-08-20', 'e': '2026-08-20'})
        self.assertEqual(result['fund_dates']['D_MAIN']['last_processed_date'], '2026-08-20')

    def test_stale_ledger_fails_even_when_web_is_current(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_root, web_root = self._fixture(root)
            target = state_root / 'D_MAIN.json'
            target.write_text(
                json.dumps({
                    'last_processed_date': '2026-08-19',
                    'conditional_plan_date': '2026-08-19',
                }),
                encoding='utf-8',
            )
            with self.assertRaisesRegex(RuntimeError, 'D_MAIN: last_processed_date=2026-08-19'):
                assert_v1_freshness('2026-08-20', state_root, web_root)

    def test_stale_public_snapshot_fails_even_when_ledgers_are_current(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_root, web_root = self._fixture(root)
            (web_root / 'e' / 'data.json').write_text(
                json.dumps({'updated_at': '2026-08-19'}),
                encoding='utf-8',
            )
            with self.assertRaisesRegex(RuntimeError, 'web/e/data.json: updated_at=2026-08-19'):
                assert_v1_freshness('2026-08-20', state_root, web_root)


if __name__ == '__main__':
    unittest.main()
