"""The memory sampler for the Services page (lab/service_stats.py)."""
import json
import os
import stat
import tempfile
import unittest
import unittest.mock
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
        self.wall = 1234.5
        self.cache = lambda: {'totalBytes': 466_000_000, 'reclaimableBytes': 400_000_000}

    def stats(self, run):
        return service_stats.ServiceStats(run=run, clock=self.clock, wall=lambda: self.wall, state=self.directory.name, threaded=False, size=lambda: self.cache())

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
        # Names and numbers only: every value below the container names is a number, so no variable, key, address or log can be in the file.
        def leaves(value):
            return [leaf for item in value.values() for leaf in (leaves(item) if isinstance(item, dict) else [item])]
        self.assertTrue(all(isinstance(leaf, (int, float)) for leaf in leaves({key: document[key] for key in ('containers', 'costs', 'thresholds', 'buildCache')})))
        self.assertEqual(set(document), {'version', 'capturedAt', 'containers', 'costs', 'thresholds', 'buildCache', 'filesystems'})
        self.assertEqual(self.calls[0][0], 'stats')
        self.assertIn('--no-stream', self.calls[0])

    def test_the_thresholds_are_the_ones_the_supervisor_enforces_and_the_build_cache_size_is_read_every_ten_minutes(self):
        import host_watch
        import resource_admission
        reads = []
        self.cache = lambda: reads.append(1) or {'totalBytes': 466_000_000, 'reclaimableBytes': 400_000_000}
        watcher = self.stats(lambda *args: LISTING)
        watcher.sample()
        document = json.loads((Path(self.directory.name) / 'service-stats.json').read_text())
        self.assertEqual(document['thresholds']['memoryReserveBytes'], resource_admission.MEMORY_RESERVE)
        self.assertEqual(document['thresholds']['newEnvironmentMemoryBytes'], resource_admission.NEW_ENVIRONMENT_MEMORY)
        self.assertEqual((document['thresholds']['diskWarningBytes'], document['thresholds']['diskWarningShare']), host_watch.DISK_WARNING)
        self.assertEqual((document['thresholds']['diskCriticalBytes'], document['thresholds']['diskCriticalShare']), host_watch.DISK_CRITICAL)
        self.assertEqual(document['buildCache'], {'totalBytes': 466_000_000, 'reclaimableBytes': 400_000_000, 'at': 1234500})
        self.wall += 599
        watcher.sample()
        self.assertEqual(len(reads), 1)
        self.wall += 2
        watcher.sample()
        self.assertEqual(len(reads), 2)

    def test_an_unreadable_build_cache_keeps_the_last_known_size_and_never_writes_a_zero(self):
        watcher = self.stats(lambda *args: LISTING)
        watcher.sample()
        self.cache = lambda: None
        self.wall += 700
        watcher.sample()
        document = json.loads((Path(self.directory.name) / 'service-stats.json').read_text())
        self.assertEqual(document['buildCache']['totalBytes'], 466_000_000)
        fresh = self.stats(lambda *args: LISTING)
        fresh.sample()
        self.assertEqual(json.loads((Path(self.directory.name) / 'service-stats.json').read_text()).get('buildCache', {}).get('totalBytes'), None)

    def test_the_filesystems_sbarbase_writes_to_are_reported_once_each(self):
        from collections import namedtuple
        usage = namedtuple('Usage', 'total used free')
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        # Docker's data root on another filesystem is listed; on the same one it is not listed twice.
        sizes = {str(service_stats.ROOT): usage(100, 20, 80), other.name: usage(200, 150, 50)}
        with unittest.mock.patch.object(service_stats.os, 'stat') as stat_call:
            stat_call.side_effect = lambda path: type('S', (), {'st_dev': 7 if str(path) == str(service_stats.ROOT) else 9})()
            found = service_stats.filesystems(run=lambda *args: other.name + '\n', usage=lambda path: sizes[str(path)])
            same = service_stats.filesystems(run=lambda *args: str(service_stats.ROOT) + '\n', usage=lambda path: sizes[str(path)])
        self.assertEqual(found, {'checkout': {'availableBytes': 80, 'totalBytes': 100}, 'docker': {'availableBytes': 50, 'totalBytes': 200}})
        self.assertEqual(same, {'checkout': {'availableBytes': 80, 'totalBytes': 100}})

    def test_an_unreadable_filesystem_or_a_docker_that_does_not_answer_is_left_out_not_zero(self):
        def broken(*args):
            raise RuntimeError('docker failed')

        def refuse(path):
            raise OSError('no such disk')
        self.assertEqual(service_stats.filesystems(run=broken, usage=refuse), {})

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
        watcher = self.stats(lambda *args: (self.calls.append(args) if args[0] == 'stats' else None) or LISTING)
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
