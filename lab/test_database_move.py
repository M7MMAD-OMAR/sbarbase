"""The attended move to a version with another PostgreSQL image: refusals, order and resumption."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database_move as move
import release_channel
import upgrade

CURRENT, TARGET = 'a' * 40, 'b' * 40
DB_DIFF = (release_channel.DATABASE_LOCK, 'db', {'tag': 'postgres:17.6.1.166', 'id': 'sha256:' + '1' * 64},
           {'tag': 'postgres:17.11.0.004', 'id': 'sha256:' + '2' * 64})
REST_DIFF = ('images.lock.json', 'rest', {'id': 'sha256:' + '3' * 64}, {'id': 'sha256:' + '4' * 64})
E = 'e_' + 'a' * 24


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state_file = self.root / 'database-move.json'
        self.events = []
        self.head = CURRENT
        self.diffs = [DB_DIFF]
        self.paused = [{'runtime': E, 'phase': 'held'}]
        self.supervisor = False
        self.database_running = False
        self.migration_present = False
        self.migration_returncode = 0
        self.move_fails = False
        self.changes = []
        self.transient_calls = []
        self.marked = []
        self.pulls = []
        self.mark_fails = False
        self.empty_record = False
        self.volume = 'sbarbase-durable-pgdata'
        self.pinned_image = 'sha256:' + '1' * 64
        self.lock_image = 'sha256:' + '2' * 64
        self.target_script = 'parser.add_argument("--image-from-lock")'
        patches = [
            patch.object(move, 'STATE_FILE', self.state_file), patch.object(upgrade, 'DATABASE_MOVE', self.state_file),
            patch.object(upgrade, 'LOCK', self.root / 'upgrade.lock'),
            patch.object(upgrade, 'git', self.git), patch.object(upgrade, 'resolve', lambda ref: TARGET),
            patch.object(upgrade, 'local_changes', lambda: list(self.changes)),
            patch.object(upgrade, 'transient_refusals', self.transient),
            patch.object(upgrade, 'pull', lambda commit: self.pulls.append(commit)),
            patch.object(upgrade, 'back_up', lambda: self.events.append(('backup',)) or '20261016T030000Z'),
            patch.object(upgrade, 'snapshot', lambda commit: self.events.append(('snapshot', commit)) or 'snap-1'),
            patch.object(upgrade, 'move_back', self.move_back),
            patch.object(upgrade, 'held', lambda path: self.supervisor if path == upgrade.SUPERVISOR_LOCK else False),
            patch.object(release_channel, 'pin_diffs', lambda current, target: iter(self.diffs)),
            patch.object(release_channel, 'lock_at', lambda commit, name: {'tag': 'postgres:17.6.1.166' if commit == CURRENT else 'postgres:17.11.0.004'}),
            patch.object(move.write_pause, 'status', lambda: self.paused),
            patch.object(move, 'published', lambda: {E: {}}),
            patch.object(upgrade, 'SUPERVISOR_LOCK', self.root / 'supervisor.lock'),
            patch.object(move.runtime, 'inspect', lambda kind, name: {'State': {'Running': self.database_running}}),
            patch.object(move.hba_migration, 'present', lambda state: self.migration_present),
            patch.object(move.hba_migration, 'pinned', lambda state: {'target': {'image': self.pinned_image}}),
            patch.object(move, 'lock_image', self.count_lock_image),
            patch.object(move.backup, 'mark_moved', self.mark_moved),
            patch.object(move.hba_migration, 'discard_unpublished', self.discard),
            patch.object(move.database_volume, 'current', lambda state: self.volume),
            patch.object(move, 'taken_over', lambda record=None: getattr(self, 'taken', False)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def transient(self, lock=True, move=True):
        self.transient_calls.append(lock)
        self.move_asked = getattr(self, 'move_asked', []) + [move]
        return [item for item in getattr(self, 'transient_refused', []) if move or 'database move' not in item]

    def count_lock_image(self):
        self.lock_reads = getattr(self, 'lock_reads', 0) + 1
        return self.lock_image

    def discard(self, state):
        # An empty record left before its intent: removed, and then there is no migration waiting.
        if self.empty_record:
            self.empty_record, self.migration_present = False, False
            return True
        return False

    def mark_moved(self, run):
        if self.mark_fails:
            raise OSError('read-only')
        self.marked.append(run)

    def git(self, *args, check=True):
        if args[:2] == ('rev-parse', 'HEAD'):
            return self.head
        if args[0] == 'show' and args[1].endswith(':lab/migrate-generation.py'):
            return self.target_script
        raise AssertionError(args)

    def move_back(self, commit, intent, state):
        self.events.append(('move', commit, intent))
        if self.move_fails:
            raise upgrade.UpgradeError('checkout interrupted')
        self.head = commit

    def migrate(self, command, cwd=None):
        self.events.append(('migrate', command))
        if self.migration_returncode == 0 and not getattr(self, 'migration_lies', False):
            # A finished change: the record is gone and the pin names the lock's image.
            self.migration_present, self.pinned_image = False, self.lock_image
        return subprocess.CompletedProcess(command, self.migration_returncode)

    def quiet(self, function, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return function(*args, **kwargs)

    def record(self):
        return json.loads(self.state_file.read_text())


class PrepareTests(Fixture):
    def test_only_a_paused_move_that_changes_the_database_image_alone_is_prepared(self):
        cases = [
            ('diffs', [], 'keeps the PostgreSQL image'),
            ('diffs', [DB_DIFF, REST_DIFF], 'also changes other images'),
            ('paused', [], 'Pause writes first'),
            ('paused', [{'runtime': E, 'phase': 'pausing'}], 'Pause writes first'),
            ('target_script', 'no such option here', 'has no --image-from-lock'),
            ('target_script', None, 'has no --image-from-lock'),
        ]
        for attribute, value, reason in cases:
            with self.subTest(reason=reason):
                self.setUp()
                setattr(self, attribute, value)
                with self.assertRaises(move.MoveError) as raised:
                    self.quiet(move.prepare, 'v2')
                self.assertTrue(any(reason in item for item in raised.exception.refusals))
                self.assertEqual(self.events, [], 'a refused move pulls, backs up and records nothing')
                self.assertFalse(self.state_file.exists())

    def test_local_changes_or_the_same_version_refuse_and_the_upgrade_lock_is_not_asked_for_twice(self):
        self.changes = ['compose.yaml']
        with self.assertRaises(move.MoveError) as raised:
            self.quiet(move.prepare, 'v2')
        self.assertTrue(any('local changes' in item for item in raised.exception.refusals))
        self.changes = []
        with patch.object(upgrade, 'resolve', lambda ref: CURRENT), self.assertRaises(move.MoveError) as raised:
            self.quiet(move.prepare, 'v2')
        self.assertTrue(any('Already at this version' in item for item in raised.exception.refusals))
        # prepare holds the upgrade lock itself, so the transient check must not ask for it, and it
        # names the recorded move itself.
        self.assertEqual(set(self.transient_calls), {False})
        self.assertEqual(set(self.move_asked), {False})
        self.assertEqual(self.events, [])

    def test_an_upgrade_s_other_refusals_still_refuse_the_move(self):
        self.transient_refused = ['An upgrade is pending', 'A backup is running',
                                  'A database move is recorded; continue or abandon it first']
        with self.assertRaises(move.MoveError) as raised:
            self.quiet(move.prepare, 'v2')
        refusals = raised.exception.refusals
        self.assertIn('An upgrade is pending', refusals)
        self.assertIn('A backup is running', refusals)
        self.assertNotIn('A database move is recorded; continue or abandon it first', refusals,
                         'the recorded move is named once, by the move itself')
        self.assertEqual(self.events, [])

    def test_a_prepared_move_pulled_backed_up_and_snapshotted_before_it_is_recorded(self):
        self.quiet(move.prepare, 'v2')
        self.assertEqual(self.pulls, [TARGET])
        self.assertEqual(self.events, [('backup',), ('snapshot', CURRENT)])
        record = self.record()
        self.assertEqual((record['phase'], record['from'], record['to'], record['backup'], record['snapshot']),
                         ('prepared', CURRENT, TARGET, '20261016T030000Z', 'snap-1'))
        self.assertEqual(record['images'], {'from': 'postgres:17.6.1.166', 'to': 'postgres:17.11.0.004'})
        self.assertEqual(self.head, CURRENT, 'nothing moved')
        with self.assertRaises(move.MoveError) as raised:
            self.quiet(move.prepare, 'v2')
        self.assertTrue(any('already recorded' in item for item in raised.exception.refusals))

    def test_nothing_is_recorded_when_the_pull_backup_or_snapshot_fails(self):
        for step in ('pull', 'back_up', 'snapshot'):
            with self.subTest(step=step):
                self.setUp()
                with patch.object(upgrade, step, side_effect=upgrade.UpgradeError(step + ' failed')), \
                        self.assertRaises(upgrade.UpgradeError):
                    self.quiet(move.prepare, 'v2')
                self.assertFalse(self.state_file.exists(), 'the move is recorded only after all three')

    def test_only_a_prepared_move_can_be_abandoned(self):
        with self.assertRaisesRegex(move.MoveError, 'No database move'):
            move.abandon()
        self.quiet(move.prepare, 'v2')
        self.quiet(move.abandon)
        self.assertFalse(self.state_file.exists())
        self.quiet(move.prepare, 'v2')
        move.save({**self.record(), 'phase': 'moving'})
        with self.assertRaisesRegex(move.MoveError, 'has started'):
            move.abandon()


class ContinueTests(Fixture):
    def prepared(self):
        self.quiet(move.prepare, 'v2')
        self.events.clear()

    def resume(self):
        return self.quiet(move.resume, run=self.migrate)

    def test_it_refuses_while_sbarbase_or_the_old_database_runs(self):
        import fcntl
        self.prepared()
        with (self.root / 'supervisor.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(move.MoveError, 'Sbarbase is running'):
                self.resume()
        self.database_running = True
        with self.assertRaisesRegex(move.MoveError, 'still runs'):
            self.resume()
        self.database_running = False
        self.assertEqual(self.record()['phase'], 'prepared')
        self.assertEqual(self.events, [])

    def test_the_migration_command_is_exactly_the_attended_retained_run(self):
        command = move.migration_command(False)
        self.assertEqual(command, [
            '/usr/bin/python3', 'lab/migrate-generation.py', '--state', str(move.runtime.STATE),
            '--volume', self.volume, '--network', move.runtime.NETWORK, '--tier', 'system.db',
            '--inventory-runtime', str(move.runtime.PRIVATE / 'runtime.json'),
            '--retired', 'stopped', '--assert-retired-will-not-return',
            '--attended-retained', '--confirm-retained', move.runtime.DB, '--image-from-lock'])
        self.assertEqual(move.migration_command(True), command[:-1] + ['--reconcile'])
        # After a rebuilt cluster was taken into use, the move runs on the volume the runtime mounts.
        self.volume = 'sbarbase-durable-pgdata-0123456789ab'
        self.assertEqual(move.migration_command(False)[move.migration_command(False).index('--volume') + 1], self.volume)

    def test_the_old_database_running_refuses_in_every_phase_before_the_change_begins(self):
        self.prepared()
        self.database_running = True
        for phase in ('moving', 'migrating'):
            with self.subTest(phase=phase):
                move.save({**self.record(), 'phase': phase})
                self.head = TARGET if phase == 'migrating' else CURRENT
                with self.assertRaisesRegex(move.MoveError, 'old database container still runs'):
                    self.resume()
                self.assertEqual(self.events, [])

    def test_the_backups_are_kept_as_an_upgrade_s_once_the_checkout_moved(self):
        self.prepared()
        self.move_fails = True
        with self.assertRaises(upgrade.UpgradeError):
            self.resume()
        self.assertEqual(self.marked, [], 'not before the checkout moved')
        self.move_fails = False
        self.resume()
        self.assertEqual(self.marked, ['20261016T030000Z'])

    def test_a_failed_mark_stops_before_the_database_and_a_rerun_marks_them(self):
        self.prepared()
        self.mark_fails = True
        with self.assertRaisesRegex(move.MoveError, 'could not be kept from pruning'):
            self.resume()
        self.assertEqual([event[0] for event in self.events], ['move'], 'the database is not changed')
        self.assertEqual(self.record()['phase'], 'migrating')
        self.mark_fails = False
        self.resume()
        self.assertEqual(self.marked, ['20261016T030000Z'])
        self.assertEqual([event[0] for event in self.events], ['move', 'migrate'])

    def test_a_move_killed_before_the_mark_marks_them_on_the_rerun(self):
        self.prepared()
        move.save({**self.record(), 'phase': 'migrating'})
        self.head = TARGET
        self.resume()
        self.assertEqual(self.marked, ['20261016T030000Z'])

    def test_a_marked_record_is_not_marked_twice(self):
        self.prepared()
        move.save({**self.record(), 'phase': 'migrating', 'marked': True})
        self.head = TARGET
        self.resume()
        self.assertEqual(self.marked, [])

    def test_a_running_database_refuses_before_the_checkout_moved_even_when_the_pin_matches_the_lock(self):
        # Before the checkout moves, the lock read is still the old one, so pin and lock agree.
        self.prepared()
        move.save({**self.record(), 'phase': 'moving'})
        self.database_running, self.pinned_image = True, self.lock_image
        with self.assertRaisesRegex(move.MoveError, 'old database container still runs'):
            self.resume()
        self.assertEqual(self.events, [])

    def test_an_empty_record_left_before_its_intent_is_started_fresh_not_reconciled(self):
        self.prepared()
        move.save({**self.record(), 'phase': 'migrating'})
        self.head = TARGET
        self.migration_present = self.empty_record = True
        self.resume()
        command = self.events[-1][1]
        self.assertIn('--image-from-lock', command)
        self.assertNotIn('--reconcile', command)
        self.assertFalse(self.state_file.exists())

    def test_a_change_that_exits_cleanly_but_left_its_record_is_not_taken_as_done(self):
        self.prepared()
        self.migration_lies = True
        self.migration_present = False
        with self.assertRaisesRegex(move.MoveError, 'did not finish'):
            self.resume()
        self.assertEqual(self.record()['phase'], 'migrating', 'the move stays recorded')
        self.migration_present = True
        with self.assertRaisesRegex(move.MoveError, 'did not finish'):
            self.resume()
        self.assertTrue(self.state_file.exists())

    def test_an_operation_record_that_appeared_after_prepare_refuses_before_anything_moves(self):
        self.prepared()
        self.transient_refused = ['A pending operation record (worker-effect.json) must be settled or reconciled first',
                                  'A database move is recorded; continue or abandon it first (lab/upgrade.py database)']
        self.move_asked = []
        with self.assertRaisesRegex(move.MoveError, 'worker-effect.json') as raised:
            self.resume()
        self.assertNotIn('database move is recorded', str(raised.exception))
        # The move's own record is left out by asking for it, not by filtering the text.
        self.assertEqual(self.move_asked, [False])
        self.assertEqual(self.record()['phase'], 'prepared', 'still abandonable')
        self.assertEqual(self.events, [])
        self.quiet(move.abandon)
        self.assertFalse(self.state_file.exists())

    def test_the_pause_baseline_is_taken_before_the_backups(self):
        self.paused = [{'runtime': E, 'phase': 'held', 'held_at': '2026-10-16T01:00:00Z'}]

        def back_up():
            # Resumed and paused again while the backup runs.
            self.paused = [{'runtime': E, 'phase': 'held', 'held_at': '2026-10-16T01:05:00Z'}]
            return '20261016T030000Z'
        with patch.object(upgrade, 'back_up', back_up):
            self.quiet(move.prepare, 'v2')
        self.assertEqual(self.record()['pauses'], {E: '2026-10-16T01:00:00Z'})
        self.events.clear()
        with self.assertRaisesRegex(move.MoveError, 'since the backups were taken'):
            self.resume()

    def test_an_unreadable_pause_record_is_a_refusal_not_a_crash(self):
        with patch.object(move.write_pause, 'status', side_effect=move.write_pause.PauseError('cannot be read')):
            with self.assertRaises(move.MoveError) as raised:
                self.quiet(move.prepare, 'v2')
            self.assertTrue(any('cannot be read' in item for item in raised.exception.refusals))
        self.prepared()
        with patch.object(move.write_pause, 'status', side_effect=move.write_pause.PauseError('cannot be read')), \
                self.assertRaisesRegex(move.MoveError, 'cannot be read'):
            self.resume()
        self.assertEqual(self.record()['phase'], 'prepared')

    def test_a_resumed_pause_or_a_new_environment_since_the_backups_refuses_before_anything_moves(self):
        self.paused = [{'runtime': E, 'phase': 'held', 'held_at': '2026-10-16T01:00:00Z'}]
        self.prepared()
        self.assertEqual(self.record()['pauses'], {E: '2026-10-16T01:00:00Z'})
        other = 'e_' + 'b' * 24
        for change in ('resumed', 'paused again', 'published'):
            with self.subTest(change=change):
                if change == 'resumed':
                    self.paused = []
                elif change == 'paused again':
                    self.paused = [{'runtime': E, 'phase': 'held', 'held_at': '2026-10-16T02:00:00Z'}]
                else:
                    self.paused = [{'runtime': E, 'phase': 'held', 'held_at': '2026-10-16T01:00:00Z'},
                                   {'runtime': other, 'phase': 'held', 'held_at': '2026-10-16T01:30:00Z'}]
                    patcher = patch.object(move, 'published', lambda: {E: {}, other: {}})
                    patcher.start()
                    self.addCleanup(patcher.stop)
                with self.assertRaisesRegex(move.MoveError, 'since the backups were taken'):
                    self.resume()
                self.assertEqual(self.record()['phase'], 'prepared')
                self.assertEqual(self.events, [])

    def test_the_target_images_are_admitted_again_before_the_checkout_moves(self):
        self.prepared()
        self.pulls.clear()
        order = []
        with patch.object(upgrade, 'pull', lambda commit: order.append(('pull', commit))), \
                patch.object(upgrade, 'move_back', lambda commit, intent, state: order.append(('move', commit)) or setattr(self, 'head', commit)):
            self.resume()
        self.assertEqual(order[:2], [('pull', TARGET), ('move', TARGET)])

    def test_a_missing_lock_image_is_a_move_refusal_with_the_pull_to_run(self):
        import image_identity
        patch.stopall()
        with patch.object(move.release_channel, 'DATABASE_LOCK', 'distro-image.lock.json'), \
                patch.object(move.lab, 'docker', return_value=subprocess.CompletedProcess([], 1, '', 'No such image')):
            with self.assertRaisesRegex(move.MoveError, 'docker pull'):
                move.lock_image()

    def test_the_old_database_running_refuses_a_prepared_move_even_with_a_migration_waiting(self):
        self.prepared()
        self.database_running, self.migration_present = True, True
        with self.assertRaisesRegex(move.MoveError, 'old database container still runs'):
            self.resume()
        self.assertEqual(self.record()['phase'], 'prepared')
        self.assertEqual(self.events, [])

    def test_sbarbase_cannot_start_while_the_move_runs(self):
        import fcntl
        self.prepared()
        seen = []

        def migrate(command, cwd=None):
            with (self.root / 'supervisor.lock').open('a') as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    seen.append(False)
                except BlockingIOError:
                    seen.append(True)
            self.pinned_image = self.lock_image
            return subprocess.CompletedProcess(command, 0)
        self.quiet(move.resume, run=migrate)
        self.assertEqual(seen, [True])

    def test_a_rerun_finishes_while_the_replacement_database_runs(self):
        self.prepared()
        self.migration_returncode = 1
        with self.assertRaises(move.MoveError):
            self.resume()
        # The generation change created and started the replacement under the same name.
        self.database_running, self.migration_present, self.migration_returncode = True, True, 0
        self.resume()
        self.assertFalse(self.state_file.exists())
        self.assertIn('--reconcile', self.events[-1][1])

    def test_the_checkout_moves_then_the_database_moves_onto_the_new_lock_image(self):
        self.prepared()
        self.lock_reads = 0
        self.resume()
        self.assertEqual(self.lock_reads, 1, 'the lock image is proved once per run')
        self.assertEqual(self.events[0], ('move', TARGET, None))
        command = self.events[1][1]
        self.assertEqual(command[:2], ['/usr/bin/python3', 'lab/migrate-generation.py'])
        self.assertIn('--image-from-lock', command)
        self.assertNotIn('--reconcile', command)
        self.assertEqual(command[command.index('--confirm-retained') + 1], move.runtime.DB)
        self.assertIn('--assert-retired-will-not-return', command)
        self.assertFalse(self.state_file.exists())
        archived = json.loads((self.root / f'database-move-{TARGET[:12]}.json').read_text())
        self.assertEqual(archived['phase'], 'completed')

    def test_an_interrupted_checkout_is_moved_again(self):
        self.prepared()
        self.move_fails = True
        with self.assertRaisesRegex(upgrade.UpgradeError, 'checkout interrupted'):
            self.resume()
        self.assertEqual(self.record()['phase'], 'moving')
        self.assertEqual([event[0] for event in self.events], ['move'])
        self.move_fails = False
        self.resume()
        self.assertEqual([event[0] for event in self.events], ['move', 'move', 'migrate'])

    def test_an_unfinished_generation_change_is_reconciled_onto_its_recorded_image(self):
        self.prepared()
        self.migration_returncode = 1
        with self.assertRaisesRegex(move.MoveError, 'did not finish'):
            self.resume()
        self.assertEqual(self.record()['phase'], 'migrating')
        # The migration left its record: the rerun reconciles it, never asks for the lock image again.
        self.migration_present, self.migration_returncode = True, 0
        self.events.clear()
        self.resume()
        self.assertEqual([event[0] for event in self.events], ['migrate'], 'the checkout is not moved twice')
        self.assertIn('--reconcile', self.events[0][1])
        self.assertNotIn('--image-from-lock', self.events[0][1])

    def test_a_change_that_finished_before_the_record_did_is_not_run_again(self):
        self.prepared()
        move.save({**self.record(), 'phase': 'migrating'})
        self.head = TARGET
        self.pinned_image = self.lock_image
        self.resume()
        self.assertEqual(self.events, [], 'no second generation change')
        self.assertFalse(self.state_file.exists())

    def test_a_waiting_migration_is_reconciled_even_when_the_pin_already_names_the_lock_image(self):
        self.prepared()
        move.save({**self.record(), 'phase': 'migrating'})
        self.head = TARGET
        self.pinned_image, self.migration_present = self.lock_image, True
        self.resume()
        self.assertEqual(len(self.events), 1)
        self.assertIn('--reconcile', self.events[0][1])

    def test_local_changes_after_the_move_stop_before_the_database(self):
        self.prepared()
        self.changes = ['lab/x.py']
        with self.assertRaisesRegex(move.MoveError, 'not a clean'):
            self.resume()
        self.assertEqual([event[0] for event in self.events], ['move'])

    def test_the_whole_move_holds_the_upgrade_lock(self):
        import fcntl
        self.prepared()
        held = []

        def migrate(command, cwd=None):
            with (self.root / 'upgrade.lock').open('a') as other:
                try:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held.append(False)
                except BlockingIOError:
                    held.append(True)
            self.pinned_image = self.lock_image
            return subprocess.CompletedProcess(command, 0)
        self.quiet(move.resume, run=migrate)
        self.assertEqual(held, [True])

    def test_a_checkout_that_is_not_the_target_stops_before_the_database(self):
        self.prepared()
        move.save({**self.record(), 'phase': 'migrating'})
        with self.assertRaisesRegex(move.MoveError, 'not a clean'):
            self.resume()
        self.assertEqual(self.events, [])


class StartTests(Fixture):
    def test_sbarbase_refuses_to_start_once_a_move_has_begun(self):
        self.assertIsNone(move.start_refusal())
        self.quiet(move.prepare, 'v2')
        self.assertIsNone(move.start_refusal(), 'a prepared move leaves the running version alone')
        for phase in ('moving', 'migrating'):
            move.save({**self.record(), 'phase': phase})
            self.assertIn('--continue', move.start_refusal())
            with patch.object(upgrade, 'load_state', return_value=None), self.assertRaisesRegex(upgrade.UpgradeError, 'database move'):
                upgrade.before_start()

    def test_an_upgrade_refuses_while_a_move_is_recorded(self):
        patch.stopall()
        with tempfile.TemporaryDirectory() as directory:
            recorded = Path(directory) / 'database-move.json'
            recorded.write_text('{}')
            with patch.object(upgrade, 'DATABASE_MOVE', recorded), patch.object(upgrade, 'load_state', return_value=None), \
                    patch.object(upgrade, 'held', return_value=False), patch.object(upgrade, 'present', lambda path: path == recorded):
                self.assertTrue(any('database move' in item for item in upgrade.transient_refusals()))

    def test_the_move_can_ask_for_the_other_refusals_without_its_own(self):
        patch.stopall()
        with tempfile.TemporaryDirectory() as directory:
            recorded = Path(directory) / 'database-move.json'
            recorded.write_text('{}')
            with patch.object(upgrade, 'DATABASE_MOVE', recorded), patch.object(upgrade, 'load_state', return_value=None), \
                    patch.object(upgrade, 'held', return_value=False), patch.object(upgrade, 'present', lambda path: path == recorded):
                self.assertTrue(any('database move' in item for item in upgrade.transient_refusals()))
                self.assertEqual(upgrade.transient_refusals(move=False), [])

    def test_a_rollback_refuses_while_a_move_is_recorded(self):
        self.quiet(move.prepare, 'v2')
        refusal = upgrade.rollback_refusal({'phase': 'confirmed', 'from': CURRENT, 'to': TARGET}, automatic=True)
        self.assertIn('database move is recorded', refusal)

    def test_an_unreadable_record_keeps_sbarbase_from_starting(self):
        self.state_file.write_text('not json')
        self.assertIn('reviewed', move.start_refusal())
        with patch.object(upgrade, 'load_state', return_value=None), self.assertRaises(upgrade.UpgradeError) as raised:
            upgrade.before_start()
        self.assertIs(type(raised.exception), upgrade.UpgradeError, 'the supervisor fails the start only on this exact class')

    def test_unreadable_published_environments_refuse_the_move(self):
        patch.stopall()
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(move.runtime, 'STATE', state):
                self.assertEqual(move.published(), {})
                (state / 'endpoints.json').write_text('{torn')
                with self.assertRaisesRegex(move.MoveError, 'cannot be read'):
                    move.published()
                (state / 'endpoints.json').write_text('[]')
                with self.assertRaisesRegex(move.MoveError, 'malformed'):
                    move.published()

    def test_a_malformed_record_needs_review(self):
        self.state_file.write_text('{"phase": "moving"}')
        with self.assertRaisesRegex(move.MoveError, 'malformed'):
            move.load()
        self.state_file.write_text('not json')
        with self.assertRaisesRegex(move.MoveError, 'cannot be read'):
            move.load()


class LockImageTests(unittest.TestCase):
    def test_the_lock_image_is_the_daemon_identity_not_the_lock_digest(self):
        index, config = 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lab').mkdir()
            (root / 'lab' / release_channel.DATABASE_LOCK).write_text(json.dumps(
                {'tag': 'public.ecr.aws/supabase/postgres:17.11.0.004', 'id': index,
                 'digests': ['public.ecr.aws/supabase/postgres@' + index]}))
            answer = json.dumps([{'Id': config, 'RepoDigests': ['public.ecr.aws/supabase/postgres@' + index]}])
            with patch.object(move.lab, 'ROOT', root), \
                    patch.object(move.lab, 'docker', lambda *a, check=True: subprocess.CompletedProcess(a, 0, answer, '')):
                self.assertEqual(move.lock_image(), config)


class CommandTests(unittest.TestCase):
    def test_the_upgrade_command_routes_each_step(self):
        calls = []
        for argv, expected in ((['database', '--to', 'v2'], ('prepare', 'v2')), (['database', '--continue'], ('resume',)),
                               (['database', '--abandon'], ('abandon',)), (['database'], ('status',))):
            with self.subTest(argv=argv), patch.object(move, 'prepare', lambda ref: calls.append(('prepare', ref))), \
                    patch.object(move, 'resume', lambda: calls.append(('resume',))), \
                    patch.object(move, 'abandon', lambda: calls.append(('abandon',))), \
                    patch.object(move, 'status', lambda: calls.append(('status',))):
                self.assertEqual(upgrade.main(argv), 0)
                self.assertEqual(calls[-1], expected)
        with patch.object(move, 'resume', side_effect=move.MoveError('Sbarbase is running')), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(upgrade.main(['database', '--continue']), 1)
        self.assertIn('Sbarbase is running', error.getvalue())



class TakenOverTests(unittest.TestCase):
    """A move superseded by a rebuilt cluster is finished by lab/cluster_takeover.py, never resumed."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.intent = None
        self.pending = None
        self.volume = 'sbarbase-durable-pgdata'
        import cluster_rebuild
        for item in (patch.object(move.runtime, 'STATE', self.root),
                     patch.object(cluster_rebuild, 'RECORD', self.root / 'cluster-rebuild.json'),
                     patch.object(move.hba_migration, 'present', lambda state: self.intent is not None),
                     patch.object(move.hba_migration, 'load', lambda state: self.intent),
                     patch.object(move.hba_migration, 'pending_superseded', lambda state: self.pending),
                     patch.object(move.database_volume, 'current', lambda state: self.volume),
                     patch.object(move, 'STATE_FILE', self.root / 'database-move.json')):
            item.start()
            self.addCleanup(item.stop)

    def test_each_sign_of_a_takeover_is_seen(self):
        self.assertFalse(move.taken_over())
        self.assertFalse(move.taken_over({'phase': 'migrating'}))
        self.assertTrue(move.taken_over({'phase': 'migrating', 'taken_over': {'volume': 'v'}}))
        self.intent = {'supersedes': 'm'}
        self.assertTrue(move.taken_over())
        self.intent = {'migration': 'm', 'volume': 'sbarbase-durable-pgdata'}
        self.assertFalse(move.taken_over())
        self.intent, self.pending = None, {'superseded_by': 'n'}
        self.assertTrue(move.taken_over())

    def test_a_migration_onto_a_rebuilt_volume_or_an_unreadable_record_counts_as_under_way(self):
        self.intent = {'migration': 'm', 'volume': 'sbarbase-durable-pgdata', 'old': {}, 'new_volume': 'sbarbase-durable-pgdata-0123456789ab'}
        self.assertTrue(move.taken_over())
        with patch.object(move.hba_migration, 'load', side_effect=ValueError('torn')):
            self.assertTrue(move.taken_over())
        self.intent = None
        with patch.object(move.hba_migration, 'pending_superseded', side_effect=RuntimeError('malformed')):
            self.assertTrue(move.taken_over())

    def test_a_finished_takeover_does_not_hold_back_a_later_move(self):
        # The rebuild record stays completed and its volume is the runtime's for good.
        self.volume = 'sbarbase-durable-pgdata-0123456789ab'
        (self.root / 'cluster-rebuild.json').write_text(json.dumps({'phase': 'completed', 'volume': self.volume}))
        self.assertFalse(move.taken_over({'phase': 'migrating', 'from': 'a' * 40, 'to': 'b' * 40}))
        # A later move's own migration, published on the rebuilt volume the runtime now mounts.
        self.intent = {'migration': 'm2', 'volume': self.volume, 'old': {}, 'image': 'sha256:' + '9' * 64}
        self.assertFalse(move.taken_over({'phase': 'migrating', 'from': 'a' * 40, 'to': 'b' * 40}))

    def test_a_taken_over_move_points_to_the_takeover_at_start_and_on_continue(self):
        (self.root / 'database-move.json').write_text(json.dumps({'version': 1, 'phase': 'migrating', 'from': 'a' * 40,
                                                                  'to': 'b' * 40}))
        self.assertIn('database --continue', move.start_refusal())
        self.intent = {'supersedes': 'm'}
        self.assertIn('cluster_takeover.py', move.start_refusal())
        self.intent = None
        (self.root / 'database-move.json').write_text(json.dumps({'version': 1, 'phase': 'migrating', 'from': 'a' * 40,
                                                                  'to': 'b' * 40, 'taken_over': {'volume': 'v'}}))
        self.assertIn('cluster_takeover.py', move.start_refusal())
        with patch.object(move.upgrade, 'LOCK', self.root / 'upgrade.lock'), \
                patch.object(move.upgrade, 'SUPERVISOR_LOCK', self.root / 'supervisor.lock'), \
                self.assertRaisesRegex(move.MoveError, 'cluster_takeover.py'):
            move.resume(run=lambda *args, **kwargs: self.fail('nothing runs'))


if __name__ == '__main__':
    unittest.main()
