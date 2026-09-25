"""Health-gated upgrade confirmation: the probes, their deadline, and the supervisor's gate."""
import concurrent.futures
import contextlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import dev
import upgrade_health

RUNTIME = 'e_' + '1' * 24


def done(function):
    """Runs a round at once, as the tests' stand-in for the background thread."""
    future = concurrent.futures.Future()
    future.set_result(function())
    return future


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ProbeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        (self.state / 'server.json').write_text(json.dumps({'url': 'http://127.0.0.1:4000', 'pid': 42}))
        (self.state / 'management.json').write_text(json.dumps({'auth': 'http://10.0.0.2:9999'}))
        (self.state / 'endpoints.json').write_text(json.dumps({RUNTIME: {
            'auth': 'http://10.0.0.3:9999', 'rest': 'http://10.0.0.4:3000',
            'storage': {'url': 'http://10.0.0.5:5000', 'tenantHost': RUNTIME + '.storage.internal'}}}))
        self.secrets = {'environments': {RUNTIME: {'jwt': 'x' * 64}}}
        self.asked = []

    def get(self, statuses=None):
        def answer(url, headers):
            self.asked.append((url, headers))
            return (statuses or {}).get(url, 200)
        return answer

    def test_every_service_is_probed_directly_never_through_the_gateway(self):
        healthy, detail = upgrade_health.check(self.state, 42, self.secrets, self.get(), {RUNTIME})
        self.assertTrue(healthy, detail)
        self.assertEqual([url for url, _ in self.asked], [
            'http://127.0.0.1:4000/health', 'http://10.0.0.2:9999/health', 'http://10.0.0.3:9999/health',
            'http://10.0.0.4:3000/', 'http://10.0.0.5:5000/bucket'])
        storage = self.asked[-1][1]
        self.assertEqual(storage['x-forwarded-host'], RUNTIME + '.storage.internal')
        self.assertTrue(storage['authorization'].startswith('Bearer '))

    def test_one_failing_service_or_a_stale_console_record_fails_the_round_without_secrets(self):
        healthy, detail = upgrade_health.check(self.state, 42, self.secrets, self.get({'http://10.0.0.4:3000/': 503}), {RUNTIME})
        self.assertEqual((healthy, detail), (False, f'{RUNTIME} rest answered HTTP 503'))
        # A server.json left by an earlier run names another process.
        healthy, detail = upgrade_health.check(self.state, 43, self.secrets, self.get(), {RUNTIME})
        self.assertFalse(healthy)
        self.assertIn('does not name the console', detail)
        healthy, detail = upgrade_health.check(self.state, 42, {'environments': {}}, self.get(), {RUNTIME})
        self.assertEqual((healthy, detail), (False, 'health probes unavailable (KeyError)'))

        def refused(url, headers):
            raise ConnectionRefusedError('refused')
        healthy, detail = upgrade_health.check(self.state, 42, self.secrets, refused, {RUNTIME})
        self.assertEqual((healthy, detail), (False, 'console did not answer (ConnectionRefusedError)'))
        self.assertNotIn('x' * 64, detail)

    def test_only_runtimes_the_gateway_serves_are_probed(self):
        other, deleted, moved = ('e_' + digit * 24 for digit in '234')
        endpoints = json.loads((self.state / 'endpoints.json').read_text())
        for e in (other, deleted, moved):
            endpoints[e] = {'auth': f'http://{e}:9999', 'rest': f'http://{e}:3000'}
        (self.state / 'endpoints.json').write_text(json.dumps(endpoints))
        with contextlib.closing(sqlite3.connect(self.state / 'control.sqlite')) as database, database:
            database.executescript('CREATE TABLE provision_jobs(runtime TEXT, state TEXT);'
                                   'CREATE TABLE deleted_runtimes(runtime TEXT);'
                                   'CREATE TABLE runtime_routing(runtime TEXT, maintenance INTEGER, placement TEXT);')
            database.executemany('INSERT INTO provision_jobs VALUES (?,?)',
                                 [(RUNTIME, 'succeeded'), (other, 'queued'), (deleted, 'succeeded'), (moved, 'succeeded')])
            database.execute('INSERT INTO deleted_runtimes VALUES (?)', (deleted,))
            database.execute("INSERT INTO runtime_routing VALUES (?, 0, '{}')", (moved,))
        self.assertEqual(upgrade_health.routed(self.state), {RUNTIME})
        healthy, detail = upgrade_health.check(self.state, 42, self.secrets, self.get())
        self.assertTrue(healthy, detail)
        self.assertFalse(any(e in url for url, _ in self.asked for e in (other, deleted, moved)))
        (self.state / 'control.sqlite').unlink()
        self.assertEqual(upgrade_health.check(self.state, 42, self.secrets, self.get())[0], False)


class ConfirmationTests(unittest.TestCase):
    def test_confirms_once_a_round_passes(self):
        clock, rounds, confirmed = Clock(), iter([(False, 'rest answered HTTP 503'), (True, 'ok')]), []
        gate = upgrade_health.Confirmation(lambda: next(rounds), lambda: confirmed.append(True), deadline=120,
                                           interval=2, clock=clock, submit=done)
        self.assertFalse(gate.poll())
        self.assertFalse(gate.poll())
        self.assertEqual(gate.detail, 'rest answered HTTP 503')
        clock.now = 2
        self.assertFalse(gate.poll())
        self.assertTrue(gate.poll())
        self.assertEqual(confirmed, [True])

    def test_past_the_deadline_it_raises_and_never_confirms(self):
        clock, confirmed = Clock(), []
        gate = upgrade_health.Confirmation(lambda: (False, 'storage answered HTTP 500'), lambda: confirmed.append(True),
                                           deadline=120, interval=2, clock=clock, submit=done)
        while clock.now < 120:
            self.assertFalse(gate.poll())
            clock.now += 1
        with self.assertRaisesRegex(RuntimeError, 'within 120 s: storage answered HTTP 500'):
            gate.poll()
        self.assertEqual(confirmed, [])

    def test_the_deadline_starts_when_the_console_exists_not_when_the_gate_is_made(self):
        clock, confirmed = Clock(), []
        gate = upgrade_health.Confirmation(lambda: (True, 'ok'), lambda: confirmed.append(True),
                                           deadline=120, clock=clock, submit=done)
        clock.now = 300  # a slow Studio reset before the server spawned
        self.assertFalse(gate.poll())
        self.assertTrue(gate.poll())
        self.assertEqual(confirmed, [True])

    def test_a_probe_that_hangs_does_not_block_the_deadline(self):
        clock, release = Clock(), threading.Event()
        gate = upgrade_health.Confirmation(lambda: release.wait(5) and (True, 'late'), lambda: None,
                                           deadline=10, clock=clock)
        self.assertFalse(gate.poll())
        clock.now = 10
        with self.assertRaises(RuntimeError):
            gate.poll()
        release.set()


class Gate:
    """A confirmation that passes on a given turn, or raises on it."""

    def __init__(self, turn, fail=False):
        self.turn, self.fail, self.polls = turn, fail, 0

    def poll(self):
        self.polls += 1
        if self.polls < self.turn:
            return False
        if self.fail:
            raise RuntimeError('The new version did not become healthy within 120 s')
        return True


class SupervisorGateTests(unittest.TestCase):
    def supervisor(self, gate, stop_after):
        stop = threading.Event()
        supervisor = dev.Supervisor(stop)
        supervisor.confirm = gate
        events = []
        supervisor.turns = 0

        def turn():
            supervisor.turns += 1
            events.append(('check', supervisor.worker is not None))
            if supervisor.turns >= stop_after:
                stop.set()
        patches = [patch.object(supervisor, 'reset_studios'), patch.object(supervisor, 'spawn', return_value=None),
                   patch.object(supervisor, 'settle_updates'), patch.object(supervisor, 'publish_current'),
                   patch.object(supervisor, 'descriptor', side_effect=lambda: events.append(('descriptor',))),
                   patch.object(supervisor, 'start_worker', side_effect=lambda: (events.append(('worker',)), setattr(supervisor, 'worker', object()))),
                   patch.object(supervisor, 'check', side_effect=turn)]
        for name in ('schedule_backup', 'schedule_studios', 'schedule_sign_in', 'schedule_toggles', 'schedule_updates'):
            patches.append(patch.object(supervisor, name, side_effect=lambda name=name: events.append((name,))))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        stop.wait = lambda timeout: stop.is_set()
        return supervisor, events

    def test_nothing_but_the_console_runs_until_the_upgrade_is_confirmed(self):
        supervisor, events = self.supervisor(Gate(3), stop_after=4)
        supervisor.worker = None
        with patch.object(dev, 'terminate_group'):
            supervisor.run()
        self.assertEqual(events[:4], [('descriptor',), ('check', False), ('check', False), ('check', False)])
        self.assertEqual(events[4], ('worker',))
        self.assertIn(('schedule_backup',), events)
        self.assertIsNone(supervisor.confirm)

    def test_a_deadline_passed_stops_the_supervisor_with_the_error_the_way_back_runs_on(self):
        supervisor, events = self.supervisor(Gate(2, fail=True), stop_after=10)
        with patch.object(dev, 'terminate_group'):
            with self.assertRaisesRegex(RuntimeError, 'did not become healthy'):
                supervisor.run()
        self.assertNotIn(('worker',), events)
        self.assertFalse(any(event[0].startswith('schedule_') for event in events))

    def test_an_ungated_start_runs_the_worker_at_once(self):
        supervisor, events = self.supervisor(None, stop_after=1)
        with patch.object(dev, 'terminate_group'):
            supervisor.run()
        self.assertEqual(events[:2], [('worker',), ('check', True)])


class MainGateTests(unittest.TestCase):
    """dev.main hands a pending upgrade to the supervisor instead of confirming it at once."""

    def test_a_snapshot_that_cannot_be_taken_is_a_failed_start(self):
        import upgrade
        with patch.object(upgrade, 'before_start', side_effect=upgrade.UpgradeError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'cannot start: disk full'):
                dev.upgrade_prepare()
        with patch.object(upgrade, 'before_start', side_effect=OSError('odd')):
            self.assertFalse(dev.upgrade_prepare())
        with patch.object(upgrade, 'before_start', return_value=True):
            self.assertTrue(dev.upgrade_prepare())


if __name__ == '__main__':
    unittest.main()
