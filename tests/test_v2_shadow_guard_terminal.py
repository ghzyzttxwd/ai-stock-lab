import json
import tempfile
import unittest
from pathlib import Path

from engine_v2.shadow_guard import processed_session
from engine_v2.shadow_ledger import FUND_NAMES, ledger_content_hash, new_ledger, save_ledger, sha256_json


class V2ShadowGuardTerminalTests(unittest.TestCase):
    def _write_event(self, path: Path, body: dict) -> dict:
        event = dict(body)
        event['event_hash'] = sha256_json(event)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(event, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return event

    def test_append_only_restatement_is_accepted_as_completed_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'shadow_state' / 'v2'
            daily = self._write_event(root / 'audit' / '2026-08-20.json', {
                'schema_version': 'v2-shadow-audit-1.0',
                'event_kind': 'conditional_entries_and_decision',
                'trade_date': '2026-08-20',
                'previous_event_hashes': {fid: None for fid in FUND_NAMES},
                'funds': {},
            })

            states = {}
            for fid in FUND_NAMES:
                state = new_ledger(fid, '2026-08-20')
                state['last_processed_date'] = '2026-08-20'
                states[fid] = state

            correction_body = {
                'schema_version': 'v2-shadow-audit-1.0',
                'event_kind': 'conditional_execution_restatement',
                'trade_date': '2026-08-20',
                'previous_event_hashes': {fid: daily['event_hash'] for fid in FUND_NAMES},
                'funds': {
                    fid: {'closing_ledger_content_sha256': ledger_content_hash(state)}
                    for fid, state in states.items()
                },
                'safety': {'ledger_restatement': True},
            }
            correction = self._write_event(
                root / 'audit' / '2026-08-20~conditional-execution-restatement.json',
                correction_body,
            )
            for fid, state in states.items():
                state['audit_head'] = correction['event_hash']
                save_ledger(root / 'ledgers' / f'{fid}.json', state)

            result = processed_session(root, '2026-08-20')
            self.assertIsNotNone(result)
            self.assertEqual(result['event_hash'], correction['event_hash'])
            self.assertEqual(result['base_event_hash'], daily['event_hash'])
            self.assertEqual(result['correction_event_hash'], correction['event_hash'])
            self.assertEqual(result['terminal_event_kind'], 'conditional_execution_restatement')

    def test_execution_catchup_can_never_be_a_completed_daily_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'shadow_state' / 'v2'
            daily = self._write_event(root / 'audit' / '2026-08-20.json', {
                'schema_version': 'v2-shadow-audit-1.0',
                'event_kind': 'conditional_entries_and_decision',
                'trade_date': '2026-08-20',
                'previous_event_hashes': {fid: None for fid in FUND_NAMES},
                'funds': {},
            })
            states = {}
            for fid in FUND_NAMES:
                state = new_ledger(fid, '2026-08-20')
                state['last_processed_date'] = '2026-08-20'
                states[fid] = state
            catchup_body = {
                'schema_version': 'v2-shadow-audit-1.0',
                'event_kind': 'execution_catchup',
                'trade_date': '2026-08-20',
                'previous_event_hashes': {fid: daily['event_hash'] for fid in FUND_NAMES},
                'funds': {
                    fid: {'closing_ledger_content_sha256': ledger_content_hash(state)}
                    for fid, state in states.items()
                },
            }
            catchup = self._write_event(root / 'audit' / '2026-08-20-execution.json', catchup_body)
            for fid, state in states.items():
                state['audit_head'] = catchup['event_hash']
                save_ledger(root / 'ledgers' / f'{fid}.json', state)

            self.assertIsNone(processed_session(root, '2026-08-20'))


if __name__ == '__main__':
    unittest.main()
