"""The memory sampler for the Services page (lab/service_stats.py)."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import resource_policy
import service_stats

E = 'e_' + 'a' * 24
LISTING = '\n'.join([
    'sbarbase-durable-db\t148.3MiB / 1GiB',
    'sbarbase-durable-storage\t90MiB / 512MiB',
    f'sbarbase-durable-{E}-realtime\t183.2MiB / 320MiB',
    f'sbarbase-durable-{E}-functions\t31.5MiB / 384MiB',
    f'sbarbase-durable-{E}-auth\t12MiB / 256MiB',
    'sbarbase-durable-e_zz-realtime\t1MiB / 2MiB',
    'someone-elses-container\t1GiB / 2GiB',
    f'sbarbase-durable-{E}-rest\tbroken / 256MiB',
    f'sbarbase-durable-{E}-studio\t1.5GB / 2GiB',
])


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class ParseTests(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(service_stats.size('1KiB'), 1024)
        self.assertEqual(service_stats.size('1.5MiB'), int(1.5 * 2**20))
        self.assertEqual(service_stats.size('2kB'), 2000)
        self.assertIsNone(service_stats.size('12'))
        self.assertIsNone(service_stats.size('lots'))

    def test_only_owned_containers_with_two_sizes(self):
        parsed = service_stats.parse(LISTING)
        self.assertEqual(sorted(parsed), sorted(['db', 'storage', f'{E}-realtime', f'{E}-functions', f'{E}-auth', f'{E}-studio']))
        self.assertEqual(parsed[f'{E}-realtime'], {'memoryBytes': int(183.2 * 2**20), 'limitBytes': 320 * 2**20})
        self.assertEqual(parsed[f'{E}-studio']['memoryBytes'], 1_500_000_000)

    def test_costs_come_from_the_policy(self):
        self.assertEqual(service_stats.costs()['realtime'], 320 * 2**20)
        self.assertEqual(service_stats.costs()['functions'], 384 * 2**20)
        self.assertEqual(service_stats.costs()['realtime'], resource_policy.memory_mib(resource_policy.TIERS['production.realtime'].memory) * 2**20)


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        self.clock = Clock()

    def stats(self, run):
        return service_stats.ServiceStats(run=run, clock=self.clock, wall=lambda: 1234.5, state=self.directory.name, threaded=False)

    def test_writes_a_private_file_with_no_secrets_and_the_time(self):
        def run(*args):
            self.calls.append(args)
            return LISTING
        watcher = self.stats(run)
        self.assertTrue(watcher.sample())
        path = Path(self.directory.name) / 'service-stats.json'
        document = json.loads(path.read_text())
        self.assertEqual(document['capturedAt'], 1234500)
        self.assertIn(f'{E}-realtime', document['containers'])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertNotIn('environment', json.dumps(document).lower())
        self.assertEqual(self.calls[0][0], 'stats')
        self.assertIn('--no-stream', self.calls[0])

    def test_a_failed_sample_keeps_the_previous_file(self):
        watcher = self.stats(lambda *args: LISTING)
        watcher.sample()
        path = Path(self.directory.name) / 'service-stats.json'
        before = path.read_text()

        def broken(*args):
            raise RuntimeError('docker failed')
        self.assertFalse(self.stats(broken).sample())
        self.assertEqual(path.read_text(), before)

    def test_once_a_minute(self):
        watcher = self.stats(lambda *args: self.calls.append(args) or LISTING)
        watcher.step()
        watcher.step()
        self.clock.now += 30
        watcher.step()
        self.assertEqual(len(self.calls), 1)
        self.clock.now += 31
        watcher.step()
        self.assertEqual(len(self.calls), 2)


if __name__ == '__main__':
    unittest.main()
