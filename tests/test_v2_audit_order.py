import json
import tempfile
import unittest
from pathlib import Path

from engine_v2.shadow_ledger import sha256_json
from engine_v2.shadow_reporting import _ordered_audit_events


class V2AuditOrderingTests(unittest.TestCase):
    def _event(self, parent, kind):
        body = {
            'schema_version': 'v2-shadow-audit-1.0',
            'event_kind': kind,
            'trade_date': '2026-08-20',
            'previous_event_hashes': {fund_id: parent for fund_id in ('A', 'B', 'C', 'D', 'L')},
            'funds': {},
        }
        body['event_hash'] = sha256_json(body)
        return body

    def test_parent_links_override_filename_sort_order(self):
        with tempfile.TemporaryDirectory() as directory:
            state_root = Path(directory)
            audit = state_root / 'audit'
            audit.mkdir(parents=True)

            root = self._event(None, 'root')
            correction = self._event(root['event_hash'], 'late_checkpoint_correction')
            checkpoint = self._event(correction['event_hash'], 'conditional_exit_scan')

            # Lexicographic filename order is root -> checkpoint -> correction,
            # while the real hash chain is root -> correction -> checkpoint.
            files = {
                '2026-08-20-execution-0940.json': root,
                '2026-08-20~late-checkpoint-correction.json': correction,
                '2026-08-20-execution-1330.json': checkpoint,
            }
            for name, event in files.items():
                (audit / name).write_text(json.dumps(event, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

            ordered = _ordered_audit_events(state_root)
            self.assertEqual(
                [event['event_kind'] for _, event in ordered],
                ['root', 'late_checkpoint_correction', 'conditional_exit_scan'],
            )


if __name__ == '__main__':
    unittest.main()
