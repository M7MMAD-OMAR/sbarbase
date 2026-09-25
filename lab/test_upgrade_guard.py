"""The upgrade guard: the first step of every start, and the way back that never depends on the
new version's code. Crash windows are simulated by raising at injected points, then running
what the next start runs."""
import ast
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import dev
import upgrade
import upgrade_guard
from test_upgrade import Checkout, contents, locked, store

# The real probe, before any test replaces it.
GIT_RUNNING = upgrade_guard.git_running


def no_install(layout):
    pass


class Crash(BaseException):
    """A process that dies at this point: nothing after it runs, no handler catches it."""


class GuardTests(Checkout):
    def setUp(self):
        super().setUp()
        self.catalog = self.upstream / 'control.sqlite'
        self.keys = self.secrets / 'managed-keys.sqlite'
        store(self.catalog, 2, 3)
        store(self.keys, 0, 1)

    def guard(self):
        return upgrade_guard.guard(upgrade.layout(), install=no_install)

    def state(self):
        return upgrade.load_state()

    def never_ungated_on_the_failed_version(self):
        """What the next start runs is never the failed version without its gate."""
        state = self.state()
        if self.head() == self.second:
            self.assertIn(state['phase'], upgrade.PENDING)
        if state['phase'] == 'rollback_failed':
            self.assertEqual(self.head(), self.first)

    def test_nothing_pending_goes_on_at_once_and_changes_nothing(self):
        self.assertEqual(self.guard(), 0)
        self.assertIsNone(self.state())
        upgrade.UPGRADES.mkdir(parents=True)
        for text in ('not json', '[]', json.dumps({'phase': 'confirmed', 'from': 'a', 'to': 'b'}),
                     json.dumps({'phase': 'applied'})):
            upgrade.STATE_FILE.write_text(text)
            self.assertEqual(self.guard(), 0)
            self.assertEqual(upgrade.STATE_FILE.read_text(), text)
        self.assertEqual(self.head(), self.first)

    def test_each_start_of_a_pending_upgrade_opens_an_attempt(self):
        upgrade.start(self.second)
        self.assertEqual(self.guard(), 0)
        self.assertEqual(self.state()['guard'], {'phase': 'applied', 'attempts': 1, 'open': True})
        self.assertEqual(self.head(), self.second)

    def test_a_start_that_died_during_its_health_checks_goes_back_before_the_new_version_runs(self):
        """OOM or SIGKILL during confirmation: nothing in the new version's code ran the way back."""
        upgrade.start(self.second)
        self.guard()
        upgrade.before_start()
        store(self.catalog, 9, 8)  # the new version migrated the catalog, then died
        self.assertEqual(self.guard(), 0)
        state = self.state()
        self.assertEqual(self.head(), self.first)
        self.assertEqual((contents(self.catalog), contents(self.keys)), ((2, 3), (0, 1)))
        self.assertEqual((state['phase'], state['automatic'], state['restored']), ('rolling_back', True, state['snapshot']))
        self.assertIn('ended before its health checks passed', state['reason'])
        self.assertEqual(state['guard'], {'phase': 'rolling_back', 'attempts': 1, 'open': True})
        self.assertEqual(json.loads(upgrade.INTENT.read_text())['pins']['rest'], 'sha256:' + '4' * 64)
        # The previous version then confirms as usual.
        self.assertTrue(upgrade.before_start())
        self.assertFalse(upgrade.after_start(True))
        self.assertEqual(self.state()['phase'], 'rolled_back')

    def test_the_next_start_announces_what_the_guard_did_once(self):
        upgrade.start(self.second)
        self.guard()
        self.guard()  # the new version died: back to the previous one
        self.assertEqual(self.state()['notices'], [{'was': 'applied', 'phase': 'rolling_back'}])
        self.guard()  # the previous version died too
        self.assertEqual(self.state()['notices'], [{'was': 'applied', 'phase': 'rolling_back'},
                                                  {'was': 'rolling_back', 'phase': 'rollback_failed'}])
        seen = []
        with patch.object(dev.updates, 'announce_outcome', side_effect=lambda before, after, catalog=None:
                          seen.append((before['phase'], after['phase'], after['automatic'], before['started_at'] == after['started_at']))):
            self.assertEqual(dev.upgrade_notices(), 2)
            self.assertEqual(dev.upgrade_notices(), 0)
        self.assertEqual(seen, [('applied', 'rolling_back', True, True), ('rolling_back', 'rollback_failed', True, True)])
        self.assertNotIn('notices', self.state())
        self.assertEqual(self.state()['phase'], 'rollback_failed')

    def test_a_preflight_that_failed_before_the_supervisor_ran_goes_back_without_a_restore(self):
        """ExecStartPre install_server.py check, or bun install in the container, failed: the new
        version never opened the control state, so what the old version wrote since stays."""
        upgrade.start(self.second)
        store(self.catalog, 2, 6)
        self.guard()
        self.assertEqual(self.guard(), 0)
        state = self.state()
        self.assertEqual((self.head(), state['phase'], state['restore_pending']), (self.first, 'rolling_back', False))
        self.assertEqual(contents(self.catalog), (2, 6))

    def test_clean_stops_are_not_crashes_but_the_attempts_still_end(self):
        upgrade.start(self.second)
        for attempt in range(1, upgrade_guard.MAX_ATTEMPTS + 1):
            self.assertEqual(self.guard(), 0)
            self.assertEqual((self.head(), self.state()['guard']['attempts']), (self.second, attempt))
            self.assertTrue(upgrade.close_attempt())
        self.assertEqual(self.guard(), 0)
        self.assertEqual(self.head(), self.first)
        self.assertIn('in 3 starts', self.state()['reason'])

    def test_the_previous_version_failing_too_records_rollback_failed_on_the_previous_version(self):
        upgrade.start(self.second)
        self.guard()
        self.guard()  # goes back, and opens the previous version's first attempt
        self.assertEqual(self.guard(), 0)  # that attempt died too
        state = self.state()
        self.assertEqual((state['phase'], self.head()), ('rollback_failed', self.first))
        self.assertIn('did not finish a start either', state['failure'])
        self.assertEqual(self.guard(), 0)
        self.assertEqual(self.head(), self.first)

    def test_a_start_that_stopped_while_it_moved_the_checkout_is_undone(self):
        real = upgrade.checkout

        def dies_after_the_move(commit, intent):
            real(commit, intent)
            raise Crash()
        with patch.object(upgrade, 'checkout', dies_after_the_move):
            with self.assertRaises(Crash):
                upgrade.start(self.second)
        self.assertEqual((self.head(), self.state()['moved']), (self.second, False))
        with self.assertRaisesRegex(upgrade.UpgradeError, 'stopped while it moved'):
            upgrade.before_start()
        self.assertEqual(self.guard(), 0)
        self.assertEqual((self.head(), self.state()['phase']), (self.first, 'failed'))
        self.assertFalse(upgrade.INTENT.exists())

    def test_a_state_from_before_the_guard_is_not_mistaken_for_an_unfinished_move(self):
        """The upgrade that installs the guard was started by a version without it."""
        upgrade.start(self.second)
        state = self.state()
        for key in ('protocol', 'moved', 'way_back'):
            state.pop(key)
        upgrade.save_state(state)
        self.assertEqual(self.guard(), 0)
        self.assertEqual((self.head(), self.state()['phase']), (self.second, 'applied'))
        self.assertTrue(upgrade.before_start())
        # Its way back still knows which images the previous version runs.
        self.assertEqual(self.guard(), 0)
        self.assertEqual(self.head(), self.first)
        self.assertEqual(json.loads(upgrade.INTENT.read_text())['pins']['rest'], 'sha256:' + '4' * 64)

    def test_a_way_back_interrupted_at_each_step_is_completed_by_the_next_start(self):
        for point in ('before the move', 'after the move', 'in the restore'):
            with self.subTest(point):
                self.setUp()
                upgrade.start(self.second)
                upgrade.before_start()
                store(self.catalog, 9, 8)
                real, calls = upgrade.move_back, []

                def move_back(commit, intent, state):
                    calls.append(commit)
                    if point == 'before the move':
                        raise Crash()
                    real(commit, intent, state)
                    if point == 'after the move':
                        raise Crash()
                restore = patch.object(upgrade_guard, 'restore_snapshot', side_effect=Crash()) \
                    if point == 'in the restore' else contextlib.nullcontext()
                with patch.object(upgrade, 'move_back', move_back), restore:
                    with self.assertRaises(Crash):
                        upgrade.after_start(False, 'Runtime startup failed')
                self.assertEqual(calls, [self.first])
                self.assertEqual(self.state()['phase'], 'rolling_back')
                self.never_ungated_on_the_failed_version()
                with self.assertRaises(upgrade.UpgradeError):
                    upgrade.before_start()
                self.assertEqual(self.guard(), 0)
                state = self.state()
                self.assertEqual((self.head(), contents(self.catalog)), (self.first, (2, 3)))
                self.assertEqual((state['phase'], state['restored'], state['reason']),
                                 ('rolling_back', state['snapshot'], 'Runtime startup failed'))
                self.assertTrue(upgrade.before_start())

    def test_a_restore_that_fails_is_retried_by_the_next_start_not_skipped(self):
        upgrade.start(self.second)
        self.guard()
        upgrade.before_start()
        store(self.catalog, 9, 8)
        with patch.object(upgrade_guard, 'restore_snapshot', side_effect=OSError('No space left on device')):
            self.assertEqual(self.guard_status(), 1)
        self.assertEqual((self.head(), self.state()['phase'], contents(self.catalog)), (self.first, 'rolling_back', (9, 8)))
        self.assertEqual(self.guard(), 0)
        self.assertEqual(contents(self.catalog), (2, 3))

    def guard_status(self):
        try:
            return self.guard()
        except upgrade_guard.Refused:
            return 1

    def test_rollback_failed_is_never_recorded_with_the_failed_version_checked_out(self):
        failures = {
            'the pull of the previous images': lambda: patch.object(upgrade, 'pull', side_effect=upgrade.UpgradeError('pull failed')),
            'the upgrade lock': lambda: locked(upgrade.LOCK),
            'the checkout': lambda: patch.object(upgrade, 'move_back', side_effect=upgrade.UpgradeError('checkout failed')),
            'the dependencies': lambda: patch.object(upgrade, 'install_dependencies', side_effect=upgrade.UpgradeError('bun')),
            'the restore': lambda: patch.object(upgrade_guard, 'restore_snapshot', side_effect=OSError('disk')),
        }
        for name, failure in failures.items():
            with self.subTest(name):
                self.setUp()
                upgrade.start(self.second)
                self.guard()
                upgrade.before_start()
                with failure(), patch.object(upgrade, 'exclusive', self.no_wait(upgrade.exclusive)):
                    upgrade.after_start(False, 'boom')
                self.never_ungated_on_the_failed_version()
                self.assertNotEqual(self.state()['phase'], 'rollback_failed')
                # The next start's guard takes it from there, with nothing of the new version.
                self.assertEqual(self.guard(), 0)
                self.assertEqual(self.head(), self.first)
                self.never_ungated_on_the_failed_version()
                self.assertEqual(contents(self.catalog), (2, 3))

    @staticmethod
    def no_wait(real):
        def exclusive(path, refusal, wait=0):
            return real(path, refusal, 0)
        return exclusive

    def test_a_manual_rollback_interrupted_halfway_is_completed_before_the_next_start(self):
        store(self.catalog, 0, 3)  # a catalog the first version opens
        upgrade.start(self.second)
        upgrade.after_start(True)
        with patch.object(upgrade, 'install_dependencies', side_effect=Crash()):
            with self.assertRaises(Crash):
                upgrade.rollback()
        self.assertEqual((self.head(), self.state()['phase'], self.state()['moved_back']), (self.first, 'rolling_back', False))
        self.assertEqual(self.guard(), 0)
        self.assertTrue(self.state()['moved_back'])
        self.assertTrue(upgrade.before_start())

    def test_a_checkout_that_is_not_the_version_being_confirmed_is_never_confirmed(self):
        upgrade.start(self.second)
        self.guard()
        (self.repo.root / 'lab' / 'images.lock.json').write_text('{}')
        with self.assertRaisesRegex(upgrade.UpgradeError, 'not the version being confirmed'):
            upgrade.before_start()
        self.repo.git('checkout', '--', '.')
        self.repo.git('checkout', '-q', '--detach', self.first)
        with self.assertRaisesRegex(upgrade.UpgradeError, 'not the version being confirmed'):
            upgrade.before_start()
        self.assertTrue(upgrade.after_start(False, 'The pending upgrade cannot start'))
        self.assertEqual((self.head(), self.state()['phase']), (self.first, 'rolling_back'))
        # And while rolling back, a checkout moved away again is put back before any gate.
        self.repo.git('checkout', '-q', '--detach', self.second)
        with self.assertRaisesRegex(upgrade.UpgradeError, 'did not finish'):
            upgrade.before_start()
        self.assertTrue(upgrade.after_start(False))
        self.assertEqual((self.head(), self.state()['phase']), (self.first, 'rolling_back'))
        self.assertTrue(upgrade.before_start())

    def test_the_guard_refuses_while_sbarbase_runs_or_another_upgrade_holds_the_lock(self):
        upgrade.start(self.second)
        with locked(upgrade.SUPERVISOR_LOCK):
            with self.assertRaisesRegex(upgrade_guard.Refused, 'already running'):
                self.guard()
        self.assertNotIn('guard', self.state())

    def test_start_copies_the_guard_of_the_version_it_leaves(self):
        (self.repo.root / 'lab' / 'upgrade_guard.py').write_text('# the first version\n')
        self.repo.git('commit', '-q', '-am', 'first guard')
        first = self.head()
        self.repo.git('checkout', '-q', '--detach', self.second)
        target = self.repo.commit('newer guard', {'lab/upgrade_guard.py': '# the new version\n'})
        self.repo.git('checkout', '-q', '--detach', first)
        upgrade.start(target)
        self.assertEqual(upgrade.guard_copy().read_text(), '# the first version\n')
        self.assertEqual((self.repo.root / 'lab' / 'upgrade_guard.py').read_text(), '# the new version\n')


class ForcedMoveTests(Checkout):
    """The way back moves the checkout whatever the tree holds, proves it did, and never refuses
    for good. Crash points are injected into the move itself."""

    def setUp(self):
        super().setUp()
        self.catalog = self.upstream / 'control.sqlite'
        self.keys = self.secrets / 'managed-keys.sqlite'
        store(self.catalog, 2, 3)
        store(self.keys, 0, 1)
        self.edited = self.repo.root / 'lab' / 'images.lock.json'
        # No test looks at the machine's real processes: other agents run git here too.
        probe = patch.object(upgrade_guard, 'git_running', return_value=False)
        probe.start()
        self.addCleanup(probe.stop)

    guard = GuardTests.guard
    state = GuardTests.state
    guard_status = GuardTests.guard_status

    def tidy(self):
        return self.repo.git('status', '--porcelain', '--untracked-files=no') == ''

    def asides(self):
        return {path.relative_to(folder).as_posix(): path.read_text()
                for folder in upgrade.UPGRADES.glob('aside-*') for path in folder.rglob('*') if path.is_file()}

    def rolling_back_with_an_edit(self):
        """The state go_back saved before the old, plain checkout refused a local edit."""
        upgrade.start(self.second)
        upgrade.after_start(True)
        state = self.state()
        upgrade_guard.begin_way_back(state, False, None)
        state['way_back'] = upgrade.way_back(state)
        upgrade.save_state(state)
        self.edited.write_text('{"ports": "local"}')

    def test_a_way_back_blocked_by_a_local_edit_sets_it_aside_and_finishes(self):
        self.rolling_back_with_an_edit()
        self.assertEqual(self.guard(), 0)
        state = self.state()
        self.assertEqual((self.head(), state['phase'], state['moved_back']), (self.first, 'rolling_back', True))
        self.assertTrue(self.tidy())
        self.assertEqual(self.asides(), {'lab/images.lock.json': '{"ports": "local"}'})
        self.assertEqual(stat.S_IMODE(next(upgrade.UPGRADES.glob('aside-*')).stat().st_mode), 0o700)
        self.assertTrue(upgrade.before_start())

    def test_an_operator_rollback_that_raced_an_edit_moves_it_aside(self):
        """The refusal comes first; a change made between it and the move is set aside, not fatal."""
        store(self.catalog, 0, 3)  # a catalog the first version opens
        upgrade.start(self.second)
        upgrade.after_start(True)
        real = upgrade.pull

        def edit_then_pull(commit):
            self.edited.write_text('{"late": true}')
            return real(commit)
        with patch.object(upgrade, 'pull', edit_then_pull):
            upgrade.rollback()
        self.assertEqual((self.head(), self.state()['moved_back']), (self.first, True))
        self.assertEqual(self.asides(), {'lab/images.lock.json': '{"late": true}'})

    def test_a_crash_at_each_point_of_the_forced_move_is_finished_by_the_next_start(self):
        real_git, real_aside = upgrade_guard.git, upgrade_guard.set_aside

        def aside_then_crash(*args):
            real_aside(*args)
            raise Crash()

        def checkout_then_crash(layout, *args, **options):
            result = real_git(layout, *args, **options)
            if args[:3] == ('checkout', '-q', '-f'):
                raise Crash()
            return result

        def install_crashes(layout):
            raise Crash()
        points = {
            'after the copy, before the checkout': (patch.object(upgrade_guard, 'set_aside', aside_then_crash), no_install),
            'after the checkout, before it is verified': (patch.object(upgrade_guard, 'git', checkout_then_crash), no_install),
            'after the move, before moved_back is saved': (contextlib.nullcontext(), install_crashes),
        }
        for name, (point, install) in points.items():
            with self.subTest(name):
                self.setUp()
                self.rolling_back_with_an_edit()
                with point, self.assertRaises(Crash):
                    upgrade_guard.guard(upgrade.layout(), install=install)
                self.assertEqual(self.state()['moved_back'], False)
                self.assertEqual(self.guard(), 0)
                self.assertEqual((self.head(), self.state()['moved_back']), (self.first, True))
                self.assertTrue(self.tidy())
                # The first copy is the operator's edit; a retry never overwrites it.
                self.assertEqual(self.asides(), {'lab/images.lock.json': '{"ports": "local"}'})

    def test_a_half_written_tree_whose_head_never_moved_is_put_back(self):
        """A checkout killed after it wrote some files, before the index and HEAD moved: the plain
        `git checkout <from>` was a no-op, and the half-written tree ran ungated as `failed`."""
        def half(commit, intent):
            self.edited.write_text(self.repo.git('show', f'{commit}:lab/images.lock.json'))
            raise Crash()
        with patch.object(upgrade, 'checkout', half), self.assertRaises(Crash):
            upgrade.start(self.second)
        self.assertEqual((self.head(), self.state()['moved']), (self.first, False))
        self.assertFalse(self.tidy())
        self.assertEqual(self.guard(), 0)
        self.assertEqual((self.head(), self.state()['phase']), (self.first, 'failed'))
        self.assertTrue(self.tidy())
        self.assertIn('postgrest:v1', self.edited.read_text())

    def test_a_stale_index_lock_is_cleared_only_when_no_git_process_holds_it(self):
        with patch.object(upgrade, 'checkout', side_effect=Crash()), self.assertRaises(Crash):
            upgrade.start(self.second)
        lock = self.repo.root / '.git' / 'index.lock'
        lock.write_text('')
        with patch.object(upgrade_guard, 'git_running', return_value=True):
            with self.assertRaisesRegex(upgrade_guard.Refused, 'the next start tries again'):
                self.guard()
        self.assertTrue(lock.exists())
        self.assertEqual(self.state()['move_failures'], 1)
        self.assertEqual(self.guard(), 0)
        self.assertFalse(lock.exists())
        self.assertEqual((self.head(), self.state()['phase']), (self.first, 'failed'))
        self.assertNotIn('move_failures', self.state())

    def test_the_git_process_probe_finds_a_git_working_in_the_checkout(self):
        # A git that waits on its input, working in this checkout. Only the positive answer is
        # checked: whether other processes on this machine count depends on who runs them.
        process = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=self.repo.root, stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL)
        try:
            self.assertTrue(GIT_RUNNING(self.repo.root))
        finally:
            process.stdin.close()
            process.wait()

    def test_a_failed_move_back_in_start_is_left_for_the_guard_not_recorded_failed(self):
        """apply() used to ignore a failed move back and record `failed`, so the moved checkout ran
        without its gate."""
        with patch.object(upgrade, 'install_dependencies', side_effect=upgrade.UpgradeError('No space left on device')):
            with self.assertRaisesRegex(upgrade.UpgradeError, 'Moving the checkout back failed too'):
                upgrade.start(self.second)
        state = self.state()
        self.assertEqual((state['phase'], state['moved']), ('applied', False))
        self.assertEqual(self.head(), self.first)  # moved, verified, only the dependencies failed
        self.assertEqual(self.guard(), 0)
        self.assertEqual((self.head(), self.state()['phase']), (self.first, 'failed'))

    def test_a_crash_during_the_move_back_of_start_is_finished_by_the_next_start(self):
        with patch.object(upgrade, 'install_dependencies', side_effect=upgrade.UpgradeError('bun install failed')), \
                patch.object(upgrade, 'move_back', side_effect=Crash()), self.assertRaises(Crash):
            upgrade.start(self.second)
        self.assertEqual((self.head(), self.state()['phase'], self.state()['moved']), (self.second, 'applied', False))
        self.assertEqual(self.guard(), 0)
        self.assertEqual((self.head(), self.state()['phase']), (self.first, 'failed'))
        self.assertTrue(self.tidy())

    @unittest.skipIf(os.geteuid() == 0, 'root writes through the permissions this test takes away')
    def test_a_move_back_that_cannot_write_the_tree_is_redone_once_it_can(self):
        """The reviewer's case: the dependencies fail and the same condition stops the move back."""
        folder = self.repo.root / 'lab'

        def failing():
            os.chmod(folder, 0o555)
            raise upgrade.UpgradeError('bun install failed for this version')
        self.addCleanup(os.chmod, folder, 0o755)
        with patch.object(upgrade, 'install_dependencies', side_effect=failing), \
                self.assertRaisesRegex(upgrade.UpgradeError, 'Moving the checkout back failed too'):
            upgrade.start(self.second)
        self.assertEqual((self.state()['phase'], self.state()['moved']), ('applied', False))
        os.chmod(folder, 0o755)
        self.assertEqual(self.guard(), 0)
        self.assertEqual((self.head(), self.state()['phase']), (self.first, 'failed'))
        self.assertTrue(self.tidy())

    def test_a_move_that_keeps_failing_stays_stopped_rather_than_start_the_failed_version(self):
        upgrade.start(self.second)
        self.guard()
        self.guard()  # died: the way back begins
        self.repo.git('checkout', '-q', '--detach', self.second)
        state = self.state()
        state.update({'moved_back': False, 'restored': None, 'guard': None})
        upgrade.save_state(state)
        with patch.object(upgrade_guard, 'force_checkout', side_effect=OSError(28, 'No space left on device')):
            for attempt in range(1, upgrade_guard.MAX_ATTEMPTS):
                with self.assertRaisesRegex(upgrade_guard.Refused, 'the next start tries again'):
                    self.guard()
                self.assertEqual(self.state()['move_failures'], attempt)
            with contextlib.redirect_stderr(io.StringIO()) as said:
                self.assertEqual(self.guard(), upgrade_guard.STUCK)
            self.assertEqual(said.getvalue().count('\n'), 1)
            self.assertIn('stays stopped', said.getvalue())
            self.assertEqual(self.guard(), upgrade_guard.STUCK)
        state = self.state()
        self.assertEqual((state['phase'], self.head()), ('rolling_back', self.second))
        self.assertIn('No space left on device', state['stuck']['reason'])
        with contextlib.redirect_stdout(io.StringIO()) as shown:
            upgrade.status()
        self.assertIn('stuck', shown.getvalue())
        self.never_ungated_on_the_failed_version()
        # Once the cause is gone, the next start finishes the way back.
        self.assertEqual(self.guard(), 0)
        state = self.state()
        self.assertEqual((self.head(), state['phase'], contents(self.catalog)), (self.first, 'rolling_back', (2, 3)))
        self.assertNotIn('stuck', state)

    never_ungated_on_the_failed_version = GuardTests.never_ungated_on_the_failed_version

    def test_a_way_back_that_reached_the_previous_version_ends_there_when_the_rest_keeps_failing(self):
        upgrade.start(self.second)
        self.guard()
        upgrade.before_start()
        store(self.catalog, 9, 8)
        def failing(layout):
            raise upgrade_guard.Refused('bun install failed for the previous version')
        # The attempt is still open: the way back begins, and the previous version's
        # dependencies fail on every start.
        for _ in range(upgrade_guard.MAX_ATTEMPTS - 1):
            with self.assertRaises(upgrade_guard.Refused):
                upgrade_guard.guard(upgrade.layout(), install=failing)
        self.assertEqual(upgrade_guard.guard(upgrade.layout(), install=failing), 0)
        state = self.state()
        self.assertEqual((state['phase'], self.head()), ('rollback_failed', self.first))
        self.assertIn('bun install failed', state['failure'])
        self.assertEqual((state['restored'], contents(self.catalog)), (state['snapshot'], (2, 3)))
        self.assertEqual(state['notices'][-1], {'was': 'rolling_back', 'phase': 'rollback_failed'})

    def test_an_operator_rollback_that_cannot_move_keeps_the_confirmed_version(self):
        self.rolling_back_with_an_edit()
        self.repo.git('checkout', '--', '.')
        upgrade.INTENT.parent.mkdir(parents=True, exist_ok=True)
        upgrade.INTENT.write_text(json.dumps(self.state()['way_back']))
        with patch.object(upgrade_guard, 'force_checkout', side_effect=upgrade_guard.Refused('checkout failed')):
            for _ in range(upgrade_guard.MAX_ATTEMPTS - 1):
                with self.assertRaises(upgrade_guard.Refused):
                    self.guard()
            self.assertEqual(self.guard(), 0)
        state = self.state()
        self.assertEqual((state['phase'], self.head()), ('confirmed', self.second))
        self.assertIn('stays on the confirmed version', state['rollback_failure'])
        self.assertFalse(upgrade.INTENT.exists(), "the previous version's pins must not reach the confirmed version")

    def test_a_way_back_from_an_older_record_is_never_taken_for_a_confirmed_one(self):
        self.rolling_back_with_an_edit()
        state = self.state()
        state.pop('back_from')
        upgrade.save_state(state)
        self.repo.git('checkout', '--', '.')
        with patch.object(upgrade_guard, 'force_checkout', side_effect=upgrade_guard.Refused('checkout failed')):
            for _ in range(upgrade_guard.MAX_ATTEMPTS - 1):
                with self.assertRaises(upgrade_guard.Refused):
                    self.guard()
            self.assertEqual(self.guard(), upgrade_guard.STUCK)

    def test_main_waits_before_it_exits_on_stuck_unless_a_terminal_started_it(self):
        with patch.object(upgrade_guard, 'guard', return_value=upgrade_guard.STUCK), \
                patch.object(upgrade_guard.time, 'sleep') as sleep:
            with patch.dict(os.environ, {'SBARBASE_GUARD_WAIT': ''}):
                self.assertEqual(upgrade_guard.main([str(self.repo.root)]), 1)
            sleep.assert_called_once_with(upgrade_guard.STUCK_WAIT)
            sleep.reset_mock()
            with patch.dict(os.environ, {'SBARBASE_GUARD_WAIT': '0'}):
                self.assertEqual(upgrade_guard.main([str(self.repo.root)]), 1)
            sleep.assert_not_called()
        self.assertLess(upgrade_guard.STUCK_WAIT, 600, 'inside the unit TimeoutStartSec')


class StandaloneTests(unittest.TestCase):
    def test_the_guard_uses_the_standard_library_only(self):
        tree = ast.parse(Path(upgrade_guard.__file__).read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module.split('.')[0])
        self.assertTrue(names)
        self.assertEqual(sorted(name for name in names if name not in sys.stdlib_module_names), [])

    def test_the_copy_runs_on_its_own_and_goes_back(self):
        """The copy under .lab/upgrades, run as the unit runs it: another directory depth, no lab/
        on the import path, and a fake bun that records the install."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        root = base / 'checkout'
        root.mkdir()
        run = lambda *args: subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
        run('init', '-q')
        run('config', 'user.email', 'test@example.com')
        run('config', 'user.name', 'test')
        (root / 'lab').mkdir()
        (root / 'lab' / 'images.lock.json').write_text(json.dumps({'rest': {'id': 'sha256:old'}}))
        run('add', '-A')
        run('commit', '-q', '-m', 'first')
        first = run('rev-parse', 'HEAD')
        (root / 'lab' / 'images.lock.json').write_text(json.dumps({'rest': {'id': 'sha256:new'}}))
        run('commit', '-q', '-am', 'second')
        second = run('rev-parse', 'HEAD')
        upgrades = root / '.lab' / 'upgrades'
        upgrades.mkdir(parents=True)
        shutil.copy(upgrade_guard.__file__, upgrades / 'guard.py')
        (upgrades / 'state.json').write_text(json.dumps({
            'phase': 'applied', 'from': first, 'to': second, 'protocol': 2, 'moved': True,
            'way_back': {'pins': {'rest': 'sha256:old'}, 'from': second, 'to': first},
            'guard': {'phase': 'applied', 'attempts': 1, 'open': True}}))
        tools = base / 'bin'
        tools.mkdir()
        (tools / 'bun').write_text(f'#!/bin/sh\necho "$@" > {base}/bun-called\n')
        (tools / 'bun').chmod(0o755)
        environment = dict(os.environ, PATH=f"{tools}:{os.environ.get('PATH', '/usr/bin:/bin')}")
        environment.pop('PYTHONPATH', None)
        result = subprocess.run(['/usr/bin/python3', '-I', '.lab/upgrades/guard.py'], cwd=root, env=environment,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(run('rev-parse', 'HEAD'), first)
        self.assertEqual((base / 'bun-called').read_text().strip(), 'install --frozen-lockfile')
        state = json.loads((upgrades / 'state.json').read_text())
        self.assertEqual((state['phase'], state['moved_back']), ('rolling_back', True))
        self.assertEqual(json.loads((root / '.lab' / 'upstream' / 'upgrade-intent.json').read_text())['pins'], {'rest': 'sha256:old'})
        self.assertIn('moving back', result.stderr)


class DevGuardTests(unittest.TestCase):
    def test_a_terminal_start_runs_the_guard_first_and_exits_when_it_moved_the_checkout(self):
        heads = iter(['a', 'b'])
        with patch.object(dev.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
                patch.object(dev, 'checkout_head', side_effect=lambda: next(heads)):
            with self.assertRaises(SystemExit) as stopped:
                dev.run_guard({})
        self.assertEqual(stopped.exception.code, dev.RESTART_FOR_UPGRADE)
        self.assertEqual(run.call_args.args[0][0], '/usr/bin/python3')
        self.assertTrue(run.call_args.args[0][1].endswith('guard.py'))
        # A terminal gets the guard's line at once, without the wait before a service restart.
        self.assertEqual(run.call_args.kwargs['env']['SBARBASE_GUARD_WAIT'], '0')
        with patch.object(dev.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(SystemExit, 'guard stopped'):
                dev.run_guard({})
        with patch.object(dev.subprocess, 'run') as run:
            dev.run_guard({'SBARBASE_GUARDED': '1'})
        run.assert_not_called()


class MainTests(unittest.TestCase):
    """dev.main takes the way back on any failure of a gated start and closes the attempt on a stop."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.calls = []
        for item in [patch.object(dev, 'STATE', self.state), patch.object(dev.os, 'chdir'),
                     patch.object(dev, 'run_guard'), patch.object(dev.signal, 'signal'),
                     patch.object(dev, 'upgrade_prepare', return_value=True),
                     patch.object(dev, 'run_stage', return_value=0),
                     patch.object(dev.console_build_check, 'is_fresh', return_value=(True, 'fresh')),
                     patch.object(dev, 'notify_installation'), patch.object(dev, 'upgrade_notices'),
                     patch.object(dev, 'upgrade_confirmation'),
                     patch.object(dev, 'upgrade_outcome', side_effect=lambda started, catalog=None, reason=None:
                                  self.calls.append(('outcome', started, reason)) or False),
                     patch.object(dev, 'upgrade_close_attempt', side_effect=lambda: self.calls.append(('close',))),
                     patch.object(dev.sys, 'argv', ['dev.py'])]:
            item.start()
            self.addCleanup(item.stop)

    def run_with(self, error):
        def run(supervisor):
            if error is not None:
                raise error
        with patch.object(dev.Supervisor, 'run', run), contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            dev.main()

    def test_any_exception_in_the_gated_start_takes_the_way_back(self):
        for error in (TypeError('new code'), ImportError('missing'), subprocess.TimeoutExpired(['studio'], 300),
                      RuntimeError('Runtime startup failed')):
            with self.subTest(error.__class__.__name__):
                self.calls.clear()
                with self.assertRaises(SystemExit) as stopped:
                    self.run_with(error)
                self.assertEqual(stopped.exception.code, 1)
                self.assertEqual(self.calls[0][:2], ('outcome', False))
                self.assertTrue(self.calls[0][2])
                self.assertNotIn(('close',), self.calls)

    def test_a_clean_stop_closes_the_attempt_instead(self):
        for error in (None, KeyboardInterrupt(), InterruptedError()):
            with self.subTest(repr(error)):
                self.calls.clear()
                self.run_with(error)
                self.assertEqual(self.calls, [('close',)])


if __name__ == '__main__':
    unittest.main()
