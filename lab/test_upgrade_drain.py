"""Before an update moves the checkout, the supervisor drains itself: the worker finishes the job
in hand and claims no other, nothing new starts, and no operation record is left unsettled.
Children are stand-ins; no test starts a container."""
import contextlib
import io
import json
import os
import signal
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import dev
import updates
import upgrade
import upgrade_guard
from test_updates import CURRENT, Private, Stand, at, document
from test_upgrade import Checkout, contents, locked, store

ROOT = Path(__file__).resolve().parents[1]


def sleeper():
    process = subprocess.Popen(['/usr/bin/python3', '-c', 'import time; time.sleep(60)'], start_new_session=True)
    return process


def ended(process):
    """Ends a stand-in child and waits until child_status sees it, without reaping it."""
    os.kill(process.pid, signal.SIGTERM)
    until = time.monotonic() + 10
    while dev.child_status(process) is None and time.monotonic() < until:
        time.sleep(.02)


class DrainTests(Private):
    def setUp(self):
        super().setUp()
        self.upstream = self.folder.parent / 'upstream'
        self.upstream.mkdir()
        for item in (patch.object(upgrade, 'UPSTREAM', self.upstream), patch.object(upgrade, 'BACKUP_LOCK', self.upstream / 'backup.lock'),
                     patch.object(upgrade, 'LOCK', self.folder / 'upgrade.lock'), patch.object(dev, 'STATE', self.upstream)):
            item.start()
            self.addCleanup(item.stop)
        self.put('settings.json', {'check': False, 'automatic': False, 'window': {'start': '03:00', 'end': '05:00'}})
        self.put('available.json', document())
        self.supervisor = dev.Supervisor(threading.Event(), worker_fd=99, catalog=self.folder / 'absent.sqlite')
        self.supervisor.current = self.supervisor.running = CURRENT
        self.supervisor.updates_since = at(0)
        self.spawned, self.workers = [], []
        self.outcome = 0
        self.supervisor.spawn_logged = lambda command, log: (self.spawned.append(command), Stand(self.outcome))[1]
        self.supervisor.server = sleeper()
        self.supervisor.worker = sleeper()
        for process in (self.supervisor.server, self.supervisor.worker):
            self.addCleanup(dev.terminate_group, process, 0)
        patcher = patch.object(self.supervisor, 'start_worker', side_effect=lambda: self.workers.append(True))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.supervisor.descriptor = lambda: None

    def turn(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.supervisor.check()
            self.supervisor.schedule_updates(at(12))

    def test_the_update_waits_for_the_worker_to_finish_its_job_and_stop(self):
        updates.create_request('apply', '0.2.0', moment=at(12))
        self.turn()
        marker = self.upstream / 'worker-drain'
        self.assertTrue(marker.exists())
        self.assertEqual(updates.read_request()['state'], 'running')
        self.assertEqual(self.spawned, [])
        self.turn()
        self.assertEqual(self.spawned, [])
        ended(self.supervisor.worker)
        self.turn()
        # Drained: the worker is not restarted, and the upgrade runs.
        self.assertIsNone(self.supervisor.worker)
        self.assertEqual(self.workers, [])
        self.assertEqual(self.spawned[0][:4], ['/usr/bin/python3', 'lab/upgrade.py', 'start', '--release'])
        self.turn()
        self.assertTrue(self.supervisor.restart_for_upgrade)
        self.assertEqual(self.workers, [])

    def test_an_unsettled_record_blocks_the_update_until_the_bounded_wait_fails_it_cleanly(self):
        updates.create_request('apply', '0.2.0', moment=at(12))
        (self.upstream / 'worker-effect.json').write_text('{}')
        self.turn()
        ended(self.supervisor.worker)
        self.turn()
        self.turn()
        self.assertEqual(self.spawned, [])
        self.supervisor.drain['until'] = time.monotonic() - 1
        self.turn()
        last = self.get('last-request.json')
        self.assertEqual(last['state'], 'failed')
        self.assertIn('nothing was changed', last['detail'])
        self.assertIsNone(self.supervisor.drain)
        self.assertFalse((self.upstream / 'worker-drain').exists())
        self.assertEqual(self.workers, [True])
        self.assertEqual(self.spawned, [])

    def test_a_failed_upgrade_child_resumes_provisioning(self):
        self.outcome = 1
        updates.create_request('apply', '0.2.0', moment=at(12))
        self.turn()
        ended(self.supervisor.worker)
        self.turn()
        self.turn()
        self.assertEqual(self.get('last-request.json')['state'], 'failed')
        self.assertFalse((self.upstream / 'worker-drain').exists())
        self.assertEqual(self.workers, [True])
        self.assertFalse(self.supervisor.restart_for_upgrade)

    def run_failing_child(self, kind, leave=None, head='same', request=None):
        """A child that exits 1 after leaving the upgrade state `leave` and HEAD at `head` (None:
        HEAD cannot be read)."""
        self.outcome = 1
        request = request or updates.create_request(kind, '0.2.0' if kind == 'apply' else None, moment=at(12))
        with patch.object(updates, 'rollback_verdict', return_value=(True, None)):
            self.turn()
            ended(self.supervisor.worker)
            self.turn()
        self.assertEqual(len(self.spawned), 1)
        if leave is not None:
            upgrade.save_state(leave)
        with patch.object(dev, 'checkout_head', return_value=self.supervisor.head if head == 'same' else head):
            self.turn()
        return request

    def assert_restarts_to_settle(self):
        self.assertTrue(self.supervisor.restart_for_upgrade)
        self.assertTrue(self.supervisor.stop_event.is_set())
        self.assertEqual(self.workers, [], 'no worker may start from a moved checkout')
        last = self.get('last-request.json')
        self.assertEqual(last['state'], 'failed')
        self.assertIn('Sbarbase restarts so the checkout is settled', last['detail'])

    def test_a_failed_upgrade_that_left_its_move_back_to_the_guard_restarts_instead_of_resuming(self):
        self.run_failing_child('apply', {'phase': 'applied', 'from': 'a' * 40, 'to': 'b' * 40, 'moved': False,
                                         'started_at': '2026-09-25T12:00:00+00:00'})
        self.assert_restarts_to_settle()

    def test_a_failed_rollback_that_left_rolling_back_restarts_instead_of_resuming(self):
        self.run_failing_child('rollback', {'phase': 'rolling_back', 'from': 'a' * 40, 'to': 'b' * 40, 'moved_back': False,
                                            'started_at': '2026-09-25T12:00:00+00:00'})
        self.assert_restarts_to_settle()

    def test_a_rollback_child_that_finished_its_way_back_but_exited_nonzero_is_done(self):
        with patch.object(upgrade_guard, 'way_back_done', return_value=True):
            self.run_failing_child('rollback', {'phase': 'rolling_back', 'from': 'a' * 40, 'to': 'b' * 40, 'moved_back': True,
                                                'started_at': '2026-09-25T12:00:00+00:00'}, head='a' * 40)
        self.assertTrue(self.supervisor.restart_for_upgrade)
        self.assertEqual(self.workers, [])
        self.assertEqual(self.get('last-request.json')['state'], 'done')

    def test_a_failed_child_that_moved_the_checkout_restarts_instead_of_resuming(self):
        self.run_failing_child('apply', head='c' * 40)
        self.assert_restarts_to_settle()

    def test_a_checkout_that_cannot_be_read_after_a_failed_child_is_not_resumed_either(self):
        self.run_failing_child('apply', head=None)
        self.assert_restarts_to_settle()

    def test_an_automatic_try_that_moved_but_could_not_write_its_outcome_is_not_spent(self):
        for moved, spent in ((True, False), (False, True)):
            with self.subTest(moved=moved):
                updates.path('ledger.json').unlink(missing_ok=True)
                upgrade.STATE_FILE.unlink(missing_ok=True)
                self.supervisor.stop_event.clear()
                self.supervisor.restart_for_upgrade = False
                self.supervisor.worker = sleeper()
                self.addCleanup(dev.terminate_group, self.supervisor.worker, 0)
                self.spawned.clear()
                request = updates.create_request('apply', '0.2.0', 'automatic', moment=at(12))
                # The start passed its point of no return; its last outcome write then failed.
                self.put('outcome.json', {'request': request['id'], 'kind': 'start', 'passed': True, 'changed': False,
                                          'refusals': [], 'error': None})
                target = 'd' * 40 if moved else self.supervisor.head
                self.run_failing_child('apply', {'phase': 'applied', 'from': self.supervisor.head, 'to': 'd' * 40,
                                                 'moved': moved, 'started_at': '2026-09-25T12:00:00+00:00'},
                                       head=target, request=request)
                self.assertEqual(self.entry('0.2.0')['spent'], spent)
                self.assertTrue(self.supervisor.restart_for_upgrade)
                # The console hears that the checkout moved, not that the update failed.
                self.assertEqual(self.get('last-request.json')['state'], 'done' if moved else 'failed')

    def test_nothing_new_starts_while_draining_but_running_children_are_reaped(self):
        updates.create_request('apply', '0.2.0', moment=at(12))
        studio = sleeper()
        self.addCleanup(dev.terminate_group, studio, 0)
        self.supervisor.studios = {'e_1': studio}
        self.turn()
        self.assertTrue(self.supervisor.paused())
        with patch.object(self.supervisor, 'studio_requests', return_value=[('e_2', 'running', 'stopped', None)]), \
                patch.object(self.supervisor, 'sign_in_requests', return_value=['e_2']), \
                patch.object(self.supervisor, 'toggle_requests', return_value=['e_2']), \
                patch.object(self.supervisor, 'spawn') as spawn:
            self.supervisor.schedule_studios()
            self.supervisor.schedule_sign_in()
            self.supervisor.schedule_toggles()
            self.supervisor.schedule_backup(at(12))
            spawn.assert_not_called()
            ended(studio)
            self.supervisor.schedule_studios()
            self.assertEqual(self.supervisor.studios, {})
            spawn.assert_not_called()
        ended(self.supervisor.worker)
        self.turn()
        self.assertEqual(len(self.spawned), 1)

    def test_a_stale_marker_never_outlives_a_restart(self):
        (self.upstream / 'worker-drain').write_text('{}')
        supervisor = dev.Supervisor(threading.Event())
        with patch.object(supervisor, 'reset_studios'), patch.object(supervisor, 'settle_updates'), \
                patch.object(supervisor, 'publish_current'), patch.object(supervisor, 'spawn', return_value=None), \
                patch.object(supervisor, 'start_worker'), patch.object(supervisor, 'check', side_effect=supervisor.stop_event.set), \
                contextlib.ExitStack() as stack, patch.object(dev, 'terminate_group'):
            for name in ('schedule_backup', 'schedule_studios', 'schedule_sign_in', 'schedule_toggles', 'schedule_updates'):
                stack.enter_context(patch.object(supervisor, name))
            supervisor.run()
        self.assertFalse((self.upstream / 'worker-drain').exists())


class WorkerSourceTests(unittest.TestCase):
    def test_the_worker_checks_the_drain_marker_before_it_claims_a_job(self):
        source = (ROOT / 'lab' / 'worker.ts').read_text()
        self.assertIn("'.lab/upstream/worker-drain'", source)
        loop = source[source.index('while(!settleOnly&&!stopping)'):]
        self.assertLess(loop.index('existsSync(drainPath)'), loop.index('catalog.claimProvision()'))


class FirstStartTests(Checkout):
    def setUp(self):
        super().setUp()
        self.catalog = self.upstream / 'control.sqlite'
        store(self.catalog, 2, 3)

    def test_a_record_left_unsettled_by_the_previous_version_is_settled_by_it(self):
        for name in upgrade.UNSETTLED:
            with self.subTest(name):
                self.setUp()
                upgrade.start(self.second)
                (self.upstream / name).write_text('{}')
                with self.assertRaisesRegex(upgrade.UpgradeError, 'pending operation record'):
                    upgrade.before_start()
                state = upgrade.load_state()
                self.assertNotIn('attempted_at', state)
                self.assertFalse(upgrade.HOLD.exists())
                store(self.catalog, 2, 5)  # what the previous version wrote until the restart stays
                self.assertTrue(upgrade.after_start(False, 'pending record'))
                self.assertEqual((self.head(), contents(self.catalog)), (self.first, (2, 5)))
                self.assertFalse(upgrade.load_state()['restore_pending'])
                # The previous version starts, settles the record, and confirms its way back.
                self.assertTrue(upgrade.before_start())

    def test_a_later_start_of_an_attempted_version_is_not_refused(self):
        upgrade.start(self.second)
        upgrade.before_start()
        (self.upstream / 'worker-effect.json').write_text('{}')
        self.assertTrue(upgrade.before_start())

    def test_a_command_line_start_while_sbarbase_runs_says_to_restart_at_once(self):
        output = io.StringIO()
        with locked(upgrade.SUPERVISOR_LOCK), contextlib.redirect_stdout(output):
            upgrade.start(self.second)
        self.assertIn('restart it now, before it starts anything else', output.getvalue())

    def test_the_console_start_does_not_warn(self):
        output = io.StringIO()
        with locked(upgrade.SUPERVISOR_LOCK), contextlib.redirect_stdout(output):
            upgrade.start(self.second, trigger='console')
        self.assertNotIn('restart it now', output.getvalue())


if __name__ == '__main__':
    unittest.main()
