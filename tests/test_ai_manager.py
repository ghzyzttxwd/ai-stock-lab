import os
import signal
import time
import unittest
from unittest.mock import patch

import requests

from engine import ai_manager


class AIManagerDeadlineTests(unittest.TestCase):
    def _env(self):
        return {
            'AI_API_KEY': 'test-key',
            'AI_MODEL': 'test-model',
            'AI_BASE_URL': 'https://example.invalid/v1',
        }

    def test_budget_env_is_bounded_and_malformed_value_uses_default(self):
        with patch.dict(os.environ, {'AI_DECISION_BUDGET_SECONDS': '5'}, clear=False):
            self.assertEqual(ai_manager._decision_budget_seconds(), 30.0)
        with patch.dict(os.environ, {'AI_DECISION_BUDGET_SECONDS': '999'}, clear=False):
            self.assertEqual(ai_manager._decision_budget_seconds(), 300.0)
        with patch.dict(os.environ, {'AI_DECISION_BUDGET_SECONDS': 'not-a-number'}, clear=False):
            self.assertEqual(
                ai_manager._decision_budget_seconds(),
                ai_manager.DEFAULT_AI_DECISION_BUDGET_SECONDS,
            )

    def test_successful_decision_receives_remaining_total_budget(self):
        env = {**self._env(), 'AI_DECISION_BUDGET_SECONDS': '45'}
        with patch.dict(os.environ, env, clear=False), patch(
            'engine.ai_manager._stream_chat',
            return_value='{"targets":[],"diary":"ok"}',
        ) as stream:
            result = ai_manager.decide_with_api([], {'cash': 1, 'positions': {}}, 50.0)
        self.assertEqual(result['targets'], [])
        self.assertEqual(stream.call_count, 1)
        max_seconds = stream.call_args.kwargs['max_seconds']
        self.assertGreater(max_seconds, 40.0)
        self.assertLessEqual(max_seconds, 45.0)

    def test_wall_clock_expiry_falls_back_without_second_attempt(self):
        env = {**self._env(), 'AI_DECISION_BUDGET_SECONDS': '60'}
        with patch.dict(os.environ, env, clear=False), patch(
            'engine.ai_manager._stream_chat',
            side_effect=ai_manager.AIDecisionTimeout('deadline'),
        ) as stream:
            result = ai_manager.decide_with_api([], {'cash': 1, 'positions': {}}, 50.0)
        self.assertIsNone(result)
        self.assertEqual(stream.call_count, 1)

    def test_quick_transient_failure_still_retries_once(self):
        env = {**self._env(), 'AI_DECISION_BUDGET_SECONDS': '60'}
        with patch.dict(os.environ, env, clear=False), patch(
            'engine.ai_manager._stream_chat',
            side_effect=[
                requests.ConnectionError('temporary relay failure'),
                '{"targets":[],"diary":"retry-ok"}',
            ],
        ) as stream, patch('engine.ai_manager.time.sleep') as sleep:
            result = ai_manager.decide_with_api([], {'cash': 1, 'positions': {}}, 50.0)
        self.assertEqual(result['diary'], 'retry-ok')
        self.assertEqual(stream.call_count, 2)
        sleep.assert_called_once()

    @unittest.skipUnless(
        hasattr(signal, 'SIGALRM') and hasattr(signal, 'setitimer'),
        'hard wall-clock alarm requires POSIX interval timers',
    )
    def test_hard_wall_clock_interrupts_blocking_work(self):
        started = time.monotonic()
        with self.assertRaises(ai_manager.AIDecisionTimeout):
            with ai_manager._wall_clock_limit(0.05):
                time.sleep(0.5)
        self.assertLess(time.monotonic() - started, 0.3)


if __name__ == '__main__':
    unittest.main()
