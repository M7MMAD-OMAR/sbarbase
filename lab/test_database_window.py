"""The held window for a PostgreSQL restart (lab/database_window.py): order, a release on every failure and
on a timeout, the switch, reload only, one window at a time. No containers, no Docker: the database is a
stub and the clock is a counter. The marker is a real file in a temporary folder."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database_window
import upgrade

ON = {database_window.SWITCH: '1'}
FULL = ['settings', 'checkpoint', 'hold', 'restart', 'ready', 'release']


class Boom(Exception):
    pass


class Stub:
    """A database that logs each call into `log`, and can fail or stall at one step."""

    def __init__(self, log, contexts=None, fail=None, never_ready=False, restart_takes=0, clock=None):
        self.log, self.fail, self.never_ready = log, fail, never_ready
        self.contexts = {'max_connections': 'postmaster'} if contexts is None else contexts
        self.restart_takes, self.clock, self.on_restart, self.timeout = restart_takes, clock, None, None

    def step(self, name):
        self.log.append(name)
        if self.fail == name:
            raise (database_window.WindowError if name == 'settings' else Boom)(name)

    def settings(self, names):
        self.step('settings')
        return {name: self.contexts[name] for name in names if name in self.contexts}

    def checkpoint(self):
        self.step('checkpoint')

    def restart(self, timeout):
        self.step('restart')
        self.timeout = timeout
        if self.clock:
            self.clock.now += self.restart_takes
        if self.on_restart:
            self.on_restart()

    def ready(self, timeout=10):
        self.step('ready')
        return not self.never_ready


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Case(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='sbarbase-window-'))
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.marker, self.lock_path = self.folder / 'database-window', self.folder / 'database-window.lock'
        self.upgrade_lock, self.backup_lock = self.folder / 'upgrade.lock', self.folder / 'backup.lock'
        self.log, self.clock, self.until, self.settled_after = [], Clock(), None, []
        hold_file = patch.object(upgrade, 'HOLD', self.folder / 'upgrade-hold')
        hold_file.start()
        self.addCleanup(hold_file.stop)

    def exclusive(self):
        return database_window.exclusive(self.lock_path, self.upgrade_lock, self.backup_lock)

    def hold(self, seconds):
        self.log.append('hold')
        database_window.open_hold(seconds, self.marker, wall=lambda: 1000.0)
        self.until = json.loads(self.marker.read_text())['until']

    def sleep(self, seconds):
        if seconds == database_window.HOLD_SETTLE_SECONDS:
            self.settled_after.append(list(self.log))
        self.clock.sleep(seconds)

    def release(self):
        self.log.append('release')
        database_window.release_hold(self.marker)

    def go(self, database, names=('max_connections',), environ=ON):
        reports = []
        result = database_window.run(list(names), environ=environ, database=database, hold=self.hold, release=self.release,
                                     lock=lambda: self.exclusive(),
                                     clock=self.clock, sleep=self.sleep, report=reports.append)
        self.reports = reports
        return result

    def assert_released(self):
        self.assertFalse(self.marker.exists(), 'the marker is still there')
        self.assertEqual(self.log.count('hold'), self.log.count('release'))
        self.assertFalse(upgrade.HOLD.exists())


class OrderTests(Case):
    def test_success_path_order(self):
        held = self.go(Stub(self.log, clock=self.clock))
        self.assertEqual(self.log, FULL)
        self.assertFalse(self.marker.exists())
        self.assertAlmostEqual(held, database_window.HOLD_SETTLE_SECONDS)
        self.assertIn('restarted', self.reports[0])

    def test_the_readers_cache_is_waited_out_after_the_hold_and_before_the_restart(self):
        self.go(Stub(self.log))
        self.assertEqual(self.settled_after, [['settings', 'checkpoint', 'hold']])

    def test_the_marker_ends_by_itself_at_the_window(self):
        self.go(Stub(self.log), environ={**ON, database_window.SECONDS: '20'})
        self.assertEqual(self.until, 1000 * 1000 + 20_000)

    def test_the_restart_is_bounded_by_what_is_left_of_the_window(self):
        database = Stub(self.log)
        self.go(database, environ={**ON, database_window.SECONDS: '20'})
        self.assertAlmostEqual(database.timeout, 20 - database_window.HOLD_SETTLE_SECONDS)

    def test_ready_is_asked_again_until_the_database_answers(self):
        database = Stub(self.log)
        answers = iter([False, False, True])
        database.ready = lambda timeout=10: self.log.append('ready') or next(answers)
        self.go(database)
        self.assertEqual(self.log.count('ready'), 3)
        self.assertEqual(self.log[-1], 'release')


class FailureTests(Case):
    def test_failure_before_the_hold_never_opens_one(self):
        for step in ('settings', 'checkpoint'):
            with self.subTest(step=step):
                self.log.clear()
                with self.assertRaises((Boom, database_window.WindowError)):
                    self.go(Stub(self.log, fail=step))
                self.assertNotIn('hold', self.log)
                self.assertNotIn('restart', self.log)
                self.assert_released()

    def test_failure_at_every_step_after_the_hold_releases_it(self):
        for step in ('restart', 'ready'):
            with self.subTest(step=step):
                self.log.clear()
                with self.assertRaises(Boom):
                    self.go(Stub(self.log, fail=step))
                self.assertEqual(self.log[-1], 'release')
                self.assert_released()

    def test_a_hold_that_fails_half_way_is_released(self):
        def broken(seconds):
            self.log.append('hold')
            self.marker.write_text('{')
            raise OSError('disk')
        with self.assertRaises(OSError):
            database_window.run(['max_connections'], environ=ON, database=Stub(self.log), hold=broken, release=self.release,
                                lock=lambda: self.exclusive(), clock=self.clock, sleep=self.clock.sleep)
        self.assertNotIn('restart', self.log)
        self.assert_released()

    def test_an_interrupt_releases_the_hold(self):
        database = Stub(self.log)
        database.on_restart = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.go(database)
        self.assert_released()

    def test_the_lock_is_free_again_after_a_failure(self):
        with self.assertRaises(Boom):
            self.go(Stub(self.log, fail='restart'))
        self.log.clear()
        self.go(Stub(self.log))
        self.assertEqual(self.log, FULL)


class TimeoutTests(Case):
    def test_a_database_that_never_answers_ends_the_window_and_releases(self):
        with self.assertRaises(database_window.WindowError) as caught:
            self.go(Stub(self.log, never_ready=True), environ={**ON, database_window.SECONDS: '10'})
        self.assertIn('window (10 s) ended', str(caught.exception))
        self.assertIn('may still be starting', str(caught.exception))
        self.assert_released()
        self.assertEqual(self.log[-1], 'release')
        # Bounded: it stopped asking at the end of the window, not long after.
        self.assertLessEqual(self.clock.now - 100.0, 10 + database_window.POLL_SECONDS)

    def test_a_restart_that_takes_the_whole_window_releases(self):
        database = Stub(self.log, clock=self.clock, restart_takes=50, never_ready=True)
        with self.assertRaises(database_window.WindowError):
            self.go(database, environ={**ON, database_window.SECONDS: '10'})
        self.assertEqual(self.log, FULL)
        self.assert_released()

    def test_a_restart_command_that_times_out_releases(self):
        database = Stub(self.log)

        def slow(timeout):
            self.log.append('restart')
            raise database_window.WindowError('The database container did not restart within the window')
        database.restart = slow
        with self.assertRaises(database_window.WindowError):
            self.go(database)
        self.assertEqual(self.log[-1], 'release')
        self.assert_released()

    def test_the_window_is_configurable_and_capped(self):
        seconds = database_window.window_seconds
        self.assertEqual(seconds({}), database_window.DEFAULT_SECONDS)
        self.assertEqual(seconds({database_window.SECONDS: '45'}), 45)
        self.assertEqual(seconds({database_window.SECONDS: '100000'}), database_window.HARD_SECONDS)
        for bad in ('0', '-5', 'abc', '', '1.5'):
            self.assertEqual(seconds({database_window.SECONDS: bad}), database_window.DEFAULT_SECONDS, bad)


class SwitchTests(Case):
    def test_flag_off_does_nothing_at_all(self):
        for environ in ({}, {database_window.SWITCH: ''}, {database_window.SWITCH: '0'}, {database_window.SWITCH: 'true'}):
            with self.subTest(environ=environ):
                with self.assertRaises(database_window.WindowError) as caught:
                    self.go(Stub(self.log), environ=environ)
                self.assertIn('not 1', str(caught.exception))
                self.assertEqual(self.log, [])
                self.assertFalse(self.marker.exists())
                self.assertFalse(self.lock_path.exists())

    def without_flag(self):
        return patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != database_window.SWITCH}, clear=True)

    def test_upgrade_database_has_no_window_option_without_the_flag(self):
        with self.without_flag(), patch('sys.stderr', io.StringIO()), patch('database_window.run') as run:
            with self.assertRaises(SystemExit) as caught:
                upgrade.main(['database', '--window', 'max_connections'])
        self.assertEqual(caught.exception.code, 2)
        run.assert_not_called()

    def test_upgrade_database_help_is_unchanged_without_the_flag(self):
        with self.without_flag(), patch('sys.stdout', io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                upgrade.main(['database', '--help'])
        self.assertNotIn('window', out.getvalue())

    def test_upgrade_database_window_runs_the_window_with_the_flag(self):
        with patch.dict(os.environ, ON), patch('database_window.run') as run:
            self.assertEqual(upgrade.main(['database', '--window', 'max_connections', 'shared_buffers']), 0)
        run.assert_called_once_with(['max_connections', 'shared_buffers'])

    def test_upgrade_database_window_reports_a_refusal_and_fails(self):
        with patch.dict(os.environ, ON), patch('database_window.run', side_effect=database_window.WindowError('no')), \
                patch('sys.stderr', io.StringIO()) as err, patch.object(upgrade, 'record_outcome'):
            self.assertEqual(upgrade.main(['database', '--window', 'max_connections']), 1)
        self.assertIn('no', err.getvalue())

    def test_an_upgrade_waiting_for_confirmation_refuses_a_window(self):
        upgrade.HOLD.write_text('{}')
        with self.assertRaises(database_window.WindowError) as caught:
            self.go(Stub(self.log))
        self.assertIn('upgrade', str(caught.exception))
        self.assertEqual(self.log, [])
        self.assertTrue(upgrade.HOLD.exists(), 'the upgrade hold is not ours to remove')


class ReloadTests(Case):
    def test_reload_only_settings_do_not_restart_or_hold(self):
        for context in ('sighup', 'superuser', 'user', 'backend', 'superuser-backend', 'internal'):
            with self.subTest(context=context):
                self.log.clear()
                with self.assertRaises(database_window.WindowError) as caught:
                    self.go(Stub(self.log, contexts={'work_mem': context}), names=['work_mem'])
                self.assertIn('pg_reload_conf', str(caught.exception))
                self.assertEqual(self.log, ['settings'])
                self.assertFalse(self.marker.exists())

    def test_one_postmaster_setting_among_reloadable_ones_restarts(self):
        self.go(Stub(self.log, contexts={'work_mem': 'user', 'max_connections': 'postmaster'}), names=['work_mem', 'max_connections'])
        self.assertIn('restart', self.log)

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(database_window.WindowError) as caught:
            self.go(Stub(self.log), names=['no_such'])
        self.assertIn('does not know', str(caught.exception))
        self.assertEqual(self.log, ['settings'])

    def test_malformed_names_are_refused_and_never_sent_to_the_database(self):
        for name in ("x'; DROP TABLE t; --", 'Max_Connections', '', 'a' * 81):
            with self.subTest(name=name):
                self.log.clear()
                with self.assertRaises(database_window.WindowError):
                    self.go(Stub(self.log), names=[name])
                self.assertEqual(self.log, [])

    def test_no_names_at_all_is_refused(self):
        with self.assertRaises(database_window.WindowError):
            self.go(Stub(self.log), names=[])
        self.assertEqual(self.log, [])


class ConcurrencyTests(Case):
    def test_a_second_window_while_one_is_open_is_refused_and_leaves_the_first_alone(self):
        outcome = {}
        first = Stub(self.log)

        def during_restart():
            second = []
            try:
                self.go(Stub(second))
            except database_window.WindowError as error:
                outcome['error'] = str(error)
            outcome['second'] = second
            outcome['marker'] = self.marker.exists()
        first.on_restart = during_restart
        self.go(first)
        self.assertIn('Another database window', outcome['error'])
        self.assertEqual(outcome['second'], [], 'the refused window touched the database')
        self.assertTrue(outcome['marker'], 'the refused window released the first one hold')
        self.assertEqual(self.log, FULL)
        self.assert_released()


class OtherOperationsTests(Case):
    def test_a_running_upgrade_or_rollback_refuses_the_window_and_nothing_is_touched(self):
        with upgrade.exclusive(self.upgrade_lock, 'busy'):
            database = Stub(self.log)
            with self.assertRaises(database_window.WindowError) as caught:
                self.go(database)
        self.assertIn('upgrade or rollback is running', str(caught.exception))
        self.assertEqual(self.log, [], 'the refused window touched the database or the hold')
        self.assertFalse(self.marker.exists())

    def test_a_backup_holding_its_lock_refuses_the_window(self):
        import fcntl
        with self.backup_lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(database_window.WindowError) as caught:
                self.go(Stub(self.log))
        self.assertIn('backup or restore', str(caught.exception))
        self.assertEqual(self.log, [])

    def test_the_upgrade_lock_is_taken_during_the_window_and_freed_after(self):
        seen = {}
        database = Stub(self.log)
        database.on_restart = lambda: seen.update(held=upgrade.held(self.upgrade_lock))
        self.go(database)
        self.assertTrue(seen['held'])
        self.assertFalse(upgrade.held(self.upgrade_lock))


class StatusTests(Case):
    def test_status_says_open_ended_and_unreadable(self):
        self.assertIn('No database window', database_window.status(self.marker))
        self.marker.write_text(json.dumps({'until': 1}))
        self.assertIn('ended', database_window.status(self.marker))
        self.marker.write_text('{')
        self.assertIn('unreadable', database_window.status(self.marker))


class RealDatabaseTests(unittest.TestCase):
    """The Docker class, with its one subprocess call stubbed: what it asks and how it fails."""

    def test_settings_parses_rows_and_quotes_validated_names(self):
        with patch.object(database_window.Docker, 'psql', return_value='max_connections\tpostmaster\nwork_mem\tuser') as psql:
            found = database_window.Docker().settings(['max_connections', 'work_mem'])
        self.assertEqual(found, {'max_connections': 'postmaster', 'work_mem': 'user'})
        self.assertIn("IN ('max_connections', 'work_mem')", psql.call_args[0][0])

    def test_a_checkpoint_that_fails_does_not_stop_the_window(self):
        with patch.object(database_window.Docker, 'psql', side_effect=database_window.WindowError('slow')):
            database_window.Docker().checkpoint()

    def test_ready_is_false_when_the_database_does_not_answer(self):
        with patch.object(database_window.Docker, 'psql', side_effect=database_window.WindowError('down')):
            self.assertFalse(database_window.Docker().ready())
        with patch.object(database_window.Docker, 'psql', return_value='f'):
            self.assertFalse(database_window.Docker().ready())


if __name__ == '__main__':
    unittest.main()
