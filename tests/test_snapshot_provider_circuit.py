import unittest

from engine.snapshot_provider_circuit import _try_snapshot_provider_with_circuit


class _Market:
    pass


class SnapshotProviderCircuitTests(unittest.TestCase):
    def test_hard_timeout_opens_circuit_without_immediate_retry(self):
        market = _Market()
        calls = []

        def bounded(seconds, fn):
            calls.append(seconds)
            raise TimeoutError('hung provider')

        first = _try_snapshot_provider_with_circuit(
            market,
            'sina',
            lambda: [],
            call_with_timeout=bounded,
            sleep_fn=lambda _seconds: None,
        )
        second = _try_snapshot_provider_with_circuit(
            market,
            'sina',
            lambda: [{'code': 'x'}] * 500,
            call_with_timeout=bounded,
            sleep_fn=lambda _seconds: None,
        )

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(calls, [30])

    def test_fast_transient_failure_still_gets_one_retry(self):
        market = _Market()
        attempts = {'count': 0}
        sleeps = []

        def provider():
            attempts['count'] += 1
            if attempts['count'] == 1:
                raise RuntimeError('temporary error')
            return [{'code': 'x'}] * 500

        out = _try_snapshot_provider_with_circuit(
            market,
            'eastmoney',
            provider,
            call_with_timeout=lambda _seconds, fn: fn(),
            sleep_fn=sleeps.append,
        )

        self.assertEqual(len(out), 500)
        self.assertEqual(attempts['count'], 2)
        self.assertEqual(sleeps, [3])
        self.assertNotIn('eastmoney', market._v1_failed_snapshot_providers)

    def test_exhausted_fast_failures_open_circuit_for_later_snapshot_calls(self):
        market = _Market()
        attempts = {'count': 0}

        def provider():
            attempts['count'] += 1
            raise RuntimeError('still down')

        first = _try_snapshot_provider_with_circuit(
            market,
            'eastmoney',
            provider,
            call_with_timeout=lambda _seconds, fn: fn(),
            sleep_fn=lambda _seconds: None,
        )
        second = _try_snapshot_provider_with_circuit(
            market,
            'eastmoney',
            provider,
            call_with_timeout=lambda _seconds, fn: fn(),
            sleep_fn=lambda _seconds: None,
        )

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(attempts['count'], 2)

    def test_provider_specific_timeout_budgets_are_used(self):
        seen = []

        def bounded(seconds, fn):
            seen.append(seconds)
            return fn()

        for label in ('eastmoney', 'sina'):
            market = _Market()
            out = _try_snapshot_provider_with_circuit(
                market,
                label,
                lambda: [{'code': 'x'}] * 500,
                call_with_timeout=bounded,
                sleep_fn=lambda _seconds: None,
            )
            self.assertEqual(len(out), 500)

        self.assertEqual(seen, [35, 30])


if __name__ == '__main__':
    unittest.main()
