"""The supervisor's host watch: disk space and stopped service containers (lab/host_watch.py)."""
import json
import sqlite3
import subprocess
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

import host_watch
import notification_producers
import notify

ROOT = Path(__file__).resolve().parents[1]
GIB = 2**30
Usage = namedtuple('Usage', 'total used free')
E = 'e_' + 'a' * 24


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class DiskTests(unittest.TestCase):
    def test_levels(self):
        self.assertIsNone(host_watch.disk_level(50 * GIB, 200 * GIB))
        self.assertEqual(host_watch.disk_level(15 * GIB, 200 * GIB), 'warning')    # 7.5 %
        self.assertEqual(host_watch.disk_level(4 * GIB, 20 * GIB), 'warning')      # under 5 GiB
        self.assertEqual(host_watch.disk_level(9 * GIB, 200 * GIB), 'critical')    # 4.5 %
        self.assertEqual(host_watch.disk_level(GIB, 10 * GIB), 'critical')         # under 2 GiB
        self.assertIsNone(host_watch.disk_level(0, 0))


class ContainerTests(unittest.TestCase):
    def test_only_the_core_services_are_watched(self):
        listing = '\n'.join([
            'sbarbase-durable-db\trunning', 'sbarbase-durable-storage\texited',
            f'sbarbase-durable-{E}-auth\trestarting', f'sbarbase-durable-{E}-rest\trunning',
            f'sbarbase-durable-{E}-functions\texited', f'sbarbase-durable-{E}-auth-previous\texited',
            f'sbarbase-durable-{E}-studio\texited', 'sbarbase-durable-management-auth\tcreated'])
        self.assertEqual(host_watch.stopped(listing), sorted([
            'sbarbase-durable-storage', f'sbarbase-durable-{E}-auth', 'sbarbase-durable-management-auth']))


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.clock = Clock()
        self.listing = 'sbarbase-durable-db\trunning'
        self.free = 100 * GIB

        def run(*args):
            if args[0] == 'info':
                return '/var/lib/docker\n'
            return self.listing
        self.watch = host_watch.HostWatch(emit=lambda *args, **options: self.events.append(args), clock=self.clock,
                                          usage=lambda path: Usage(200 * GIB, 0, self.free), run=run)

    def test_a_container_stopped_for_five_minutes_is_reported_and_a_short_stop_is_not(self):
        self.watch.step()
        self.listing = 'sbarbase-durable-db\texited\nsbarbase-durable-management-auth\texited'
        for _ in range(5):
            self.clock.now += 60
            self.watch.step()
        self.assertEqual(self.events, [])
        self.listing = 'sbarbase-durable-db\trunning\nsbarbase-durable-management-auth\texited'
        self.clock.now += 60
        self.watch.step()
        self.assertEqual([(event[0], event[6]) for event in self.events],
                         [('runtime.container_stopped', {'container': 'management-auth', 'minutes': 5})])
        # Running again forgets it; a new stop starts a new count.
        self.events.clear()
        self.listing = 'sbarbase-durable-management-auth\trunning'
        self.clock.now += 60
        self.watch.step()
        self.listing = 'sbarbase-durable-management-auth\texited'
        self.clock.now += 60
        self.watch.step()
        self.assertEqual(self.events, [])

    def test_it_looks_once_a_minute_and_reports_both_filesystems(self):
        self.free = 9 * GIB
        self.watch.step()
        self.watch.step()
        self.clock.now += 30
        self.watch.step()
        self.assertEqual(sorted((event[0], event[1], event[6]['filesystem']) for event in self.events),
                         [('host.disk_low', 'critical', 'checkout'), ('host.disk_low', 'critical', 'docker')])

    def test_a_failing_daemon_never_stops_the_supervisor(self):
        def broken(*args):
            raise RuntimeError('docker failed')
        watch = host_watch.HostWatch(emit=lambda *args, **options: self.events.append(args), clock=self.clock,
                                     usage=lambda path: Usage(200 * GIB, 0, 100 * GIB), run=broken)
        watch.step()
        self.assertEqual(self.events, [])


class DeliveryTests(unittest.TestCase):
    """The events pass the notifier's closed lists and credential scan, into a real catalog."""

    def test_both_events_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / 'control.sqlite'
            subprocess.run(['bun', '-e', f"import {{Catalog}} from '{ROOT}/src/control/catalog';new Catalog({json.dumps(str(catalog))}).close();"],
                           cwd=ROOT, check=True, capture_output=True)
            clock = Clock()
            listing = {'value': f'sbarbase-durable-{E}-auth\texited\nsbarbase-durable-management-auth\texited'}
            watch = host_watch.HostWatch(catalog=catalog, clock=clock, usage=lambda path: Usage(200 * GIB, 0, GIB),
                                         run=lambda *args: '/var/lib/docker' if args[0] == 'info' else listing['value'])
            watch.step()
            clock.now += 301
            watch.step()
            with sqlite3.connect(catalog) as database:
                rows = sorted(database.execute('SELECT kind, severity, detail FROM notification_outbox').fetchall())
            kinds = [(kind, severity) for kind, severity, _ in rows]
            self.assertEqual(kinds.count(('host.disk_low', 'critical')), 2)
            self.assertEqual(kinds.count(('runtime.container_stopped', 'critical')), 2)
            details = [json.loads(detail) for kind, _, detail in rows if kind == 'runtime.container_stopped']
            self.assertEqual(sorted(item['container'] for item in details), sorted([f'{E}-auth', 'management-auth']))
            for kind in ('host.disk_low', 'runtime.container_stopped'):
                self.assertIn(kind, notify.SUMMARY)


if __name__ == '__main__':
    unittest.main()
