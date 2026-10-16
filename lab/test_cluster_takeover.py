"""P3 part 4: a rebuilt cluster taken into use, through doubles only."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cluster_takeover as takeover

VOLUME = 'sbarbase-durable-pgdata-0123456789ab'
IMAGE = 'sha256:' + '1' * 64
OTHER = 'sha256:' + '2' * 64
FROM, TO = 'a' * 40, 'b' * 40
ARCHIVED = 'database-move-' + 'b' * 12 + '-20261016T010000Z-superseded.json'
STUCK = {'migration': 'm-stuck', 'volume': 'sbarbase-durable-pgdata', 'old': {'image': OTHER}, 'image': '3'}


class TakeoverTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.record_path = self.root / 'cluster-rebuild.json'
        self.move_path = self.root / 'database-move.json'
        self.write_rebuild({'version': 1, 'run': '20261016T030000Z', 'image': IMAGE, 'volume': VOLUME,
                            'phase': 'completed', 'container_removed': True})
        self.lock = IMAGE
        self.volume = 'sbarbase-durable-pgdata'
        self.intent = None
        self.pending = None
        self.locked = []
        self.runs = []
        self.moved = []
        self.finishes = True
        self.from_image = IMAGE
        self.from_script = 'import os\nSUPERSEDE_PROTOCOL=1\n'
        self.pinned_image = OTHER
        self.marked = []
        self.refusal = None
        self.proved = []
        self.volume_refusal = None

        @contextlib.contextmanager
        def held(*args):
            self.locked.append(True)
            try:
                yield
            finally:
                self.locked.pop()
        patches = [
            patch.object(takeover.cluster_rebuild, 'RECORD', self.record_path),
            patch.object(takeover.upgrade, 'exclusive', held),
            patch.object(takeover.database_move, 'STATE_FILE', self.move_path),
            patch.object(takeover.database_move, 'lock_image', lambda: self.lock),
            patch.object(takeover.runtime, 'STATE', self.root),
            patch.object(takeover.hba_migration, 'discard_unpublished', lambda state: False),
            patch.object(takeover.hba_migration, 'present', lambda state: self.intent is not None),
            patch.object(takeover.hba_migration, 'load', lambda state: self.intent),
            patch.object(takeover.hba_migration, 'pending_superseded', lambda state: self.pending),
            patch.object(takeover.database_volume, 'current', lambda state: self.volume),
            patch.object(takeover.release_channel, 'lock_at', lambda commit, name: {'commit': commit, 'name': name}),
            patch.object(takeover.backup, 'resolve_image', lambda pin: ('ref', self.from_image if pin['commit'] == FROM else OTHER)),
            patch.object(takeover.upgrade, 'git', lambda *args, check=True: self.from_script if args[:1] == ('show',) and args[1] == FROM + ':lab/hba_migration.py' else ''),
            patch.object(takeover.upgrade, 'move_back', self.move_back),
            patch.object(takeover.upgrade, 'now', lambda: '2026-10-16T12:00:00Z'),
            patch.object(takeover.hba_migration, 'pinned', lambda state: {'target': {'image': self.pinned_image, 'owner': 'durable-upstream'}}),
            patch.object(takeover.hba_migration, 'require_cluster_volume', self.prove),
            patch.object(takeover.backup, 'mark_moved', lambda stamp: self.marked.append(stamp)),
            patch.object(takeover.hba_migration, 'stuck_refusal', lambda docker, state, intent: self.refusal),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def write_rebuild(self, record):
        self.record_path.write_text(json.dumps(record))

    def write_move(self, phase='migrating'):
        self.move = {'version': 1, 'phase': phase, 'from': FROM, 'to': TO, 'backup': '20261016T030000Z', 'marked': True,
                     'started_at': '2026-10-16T01:00:00Z',
                     'images': {'from': '17.6', 'to': '17.11'}}
        self.move_path.write_text(json.dumps(self.move))

    def prove(self, docker, volume, owner, image, state):
        self.proved.append((volume, owner, image))
        if self.volume_refusal:
            raise RuntimeError(self.volume_refusal)

    def move_back(self, commit, intent, state):
        self.moved.append((commit, intent, bool(self.locked)))
        self.lock = self.from_image

    def fake_run(self, command, cwd=None, pass_fds=None):
        # The worker lock goes to the migration as an inherited descriptor, named last.
        self.assertEqual(command[-2], '--worker-fd')
        if pass_fds is not None:
            self.assertEqual(pass_fds, (int(command[-1]),))
        command = command[:-2]
        self.runs.append((command, bool(self.locked)))
        if self.finishes:
            self.intent = None
            self.pending = None
            self.volume = VOLUME
            self.pinned_image = IMAGE
        return SimpleNamespace(returncode=0)

    def take(self):
        return takeover.takeover(run=self.fake_run)

    def test_without_a_record_the_migration_runs_onto_the_rebuilt_volume_and_image(self):
        record, move, migrated = self.take()
        self.assertTrue(migrated)
        self.assertEqual((record['volume'], move), (VOLUME, None))
        command, locked = self.runs[0]
        self.assertTrue(locked)
        self.assertEqual(command[-3:], ['--image-from-lock', '--to-volume', VOLUME])
        self.assertIn('lab/migrate-generation.py', command)
        self.assertEqual(command[command.index('--volume') + 1], 'sbarbase-durable-pgdata')
        self.assertNotIn('--supersede-stuck', command)

    def test_the_takeover_s_locks_are_held_while_the_migration_runs(self):
        held = []

        @contextlib.contextmanager
        def counting(path, refusal):
            held.append(path)
            try:
                yield
            finally:
                held.remove(path)
        seen = []
        with patch.object(takeover.upgrade, 'exclusive', counting):
            takeover.takeover(run=lambda command, cwd=None, pass_fds=(): seen.append(sorted(map(str, held))) or self.fake_run(command))
        self.assertEqual(seen, [sorted(map(str, (takeover.upgrade.LOCK, takeover.upgrade.SUPERVISOR_LOCK,
                                                 takeover.upgrade.BACKUP_LOCK)))])
        self.assertEqual(held, [])


    def test_an_empty_record_a_kill_left_is_cleared_before_the_record_is_read(self):
        calls = []

        def discard(state):
            calls.append('discard')
            self.intent = None  # the empty directory, gone
            return True
        self.intent = {'not': 'an intent'}
        with patch.object(takeover.hba_migration, 'discard_unpublished', discard):
            self.take()
        self.assertEqual(calls, ['discard'])
        self.assertNotIn('--supersede-stuck', self.runs[0][0])
        self.assertNotIn('--reconcile', self.runs[0][0])

    def test_a_stuck_move_s_record_is_superseded(self):
        self.intent = STUCK
        self.take()
        self.assertEqual(self.runs[0][0][-4:], ['--image-from-lock', '--to-volume', VOLUME, '--supersede-stuck'])

    def test_a_superseded_migration_waiting_for_its_intent_is_superseded_again(self):
        self.pending = {'superseded_by': 'm-new'}
        self.take()
        self.assertEqual(self.runs[0][0][-1], '--supersede-stuck')

    def test_an_interrupted_migration_onto_the_rebuilt_volume_is_finished(self):
        for intent in ({'migration': 'm', 'volume': 'sbarbase-durable-pgdata', 'new_volume': VOLUME, 'old': {'image': IMAGE}},
                       {'migration': 'm', 'volume': 'sbarbase-durable-pgdata', 'new_volume': VOLUME, 'supersedes': 'm-stuck',
                        'old': {'image': OTHER}, 'image': IMAGE}):
            with self.subTest(intent=intent):
                self.intent, self.volume, self.runs = intent, 'sbarbase-durable-pgdata', []
                self.take()
                self.assertEqual(self.runs[0][0][-1], '--reconcile')

    def test_a_migration_onto_the_rebuilt_volume_but_another_image_refuses_unchanged(self):
        self.write_move()
        self.lock = OTHER
        self.intent = {'migration': 'm', 'volume': 'sbarbase-durable-pgdata', 'new_volume': VOLUME, 'old': {'image': OTHER}}
        with self.assertRaisesRegex(takeover.TakeoverError, 'another image'):
            self.take()
        self.assertEqual((self.runs, self.moved), ([], []), 'the checkout stays where it was')
        self.assertTrue(self.move_path.exists())

    def test_a_migration_that_ends_on_another_image_is_not_reported_as_finished(self):
        def other_image(command, cwd=None, pass_fds=()):
            self.volume = VOLUME
            return SimpleNamespace(returncode=0)
        self.write_move()
        with self.assertRaisesRegex(takeover.TakeoverError, 'did not finish'):
            takeover.takeover(run=other_image)
        self.assertTrue(self.move_path.exists())

    def test_a_refused_record_leaves_the_checkout_where_it_was(self):
        self.write_move()
        self.lock = OTHER
        self.intent = {'migration': 'm', 'volume': 'sbarbase-durable-pgdata',
                       'new_volume': 'sbarbase-durable-pgdata-ba9876543210', 'supersedes': 'm-stuck', 'old': {'image': OTHER}}
        with self.assertRaises(takeover.TakeoverError):
            self.take()
        self.assertEqual(self.moved, [])

    def test_an_unmarked_move_s_backups_are_kept_from_pruning_before_it_is_archived(self):
        self.write_move()
        self.move['marked'] = False
        self.move_path.write_text(json.dumps(self.move))
        self.take()
        self.assertEqual(self.marked, ['20261016T030000Z'])
        archived = json.loads((self.root / ARCHIVED).read_text())
        self.assertTrue(archived['marked'])
        self.marked.clear()
        self.write_move()
        self.take()
        self.assertEqual(self.marked, [], 'marked backups are not marked again')

    def test_backups_that_cannot_be_kept_leave_the_move_recorded(self):
        self.write_move()
        self.move['marked'] = False
        self.move_path.write_text(json.dumps(self.move))
        with patch.object(takeover.backup, 'mark_moved', side_effect=takeover.backup.BackupError('no run')), \
                self.assertRaisesRegex(takeover.TakeoverError, 'could not be kept from pruning'):
            self.take()
        self.assertTrue(self.move_path.exists())
        self.assertFalse((self.root / ARCHIVED).exists())

    def test_a_record_that_cannot_be_superseded_refuses_before_the_checkout_moves(self):
        self.write_move()
        self.lock = OTHER
        self.intent = STUCK
        self.refusal = 'The retired container still exists; reconcile the migration instead'
        with self.assertRaisesRegex(takeover.TakeoverError, 'still exists'):
            self.take()
        self.assertEqual((self.moved, self.runs), ([], []))
        self.assertTrue(self.move_path.exists())

    def test_a_migration_that_leaves_the_runtime_on_the_old_volume_is_not_finished(self):
        def old_volume(command, cwd=None, pass_fds=()):
            self.intent = None
            self.pinned_image = IMAGE
            return SimpleNamespace(returncode=0)
        self.write_move()
        with self.assertRaisesRegex(takeover.TakeoverError, 'did not finish'):
            takeover.takeover(run=old_volume)
        self.assertTrue(self.move_path.exists())

    def test_two_superseded_moves_to_one_version_keep_their_own_archives(self):
        for started in ('2026-10-16T01:00:00Z', '2026-10-17T01:00:00Z'):
            self.write_move()
            self.move['started_at'] = started
            self.move_path.write_text(json.dumps(self.move))
            self.volume = 'sbarbase-durable-pgdata'
            self.take()
        self.assertEqual(sorted(path.name for path in self.root.glob('database-move-*-superseded.json')),
                         [ARCHIVED, ARCHIVED.replace('20261016', '20261017')])

    def test_a_superseding_migration_onto_another_volume_refuses(self):
        self.intent = {'migration': 'm', 'volume': 'sbarbase-durable-pgdata',
                       'new_volume': 'sbarbase-durable-pgdata-ba9876543210', 'supersedes': 'm-stuck'}
        with self.assertRaisesRegex(takeover.TakeoverError, 'needs review'):
            self.take()
        self.assertEqual(self.runs, [])

    def test_a_cluster_already_in_use_runs_nothing(self):
        self.volume = VOLUME
        self.pinned_image = IMAGE
        self.take()
        self.assertEqual(self.runs, [])

    def test_a_cluster_on_the_rebuilt_volume_but_another_image_is_not_taken_as_done(self):
        self.write_move()
        self.volume = VOLUME
        with self.assertRaisesRegex(takeover.TakeoverError, 'pin names another image'):
            self.take()
        self.assertEqual(self.runs, [])
        self.assertTrue(self.move_path.exists())

    def test_a_migration_that_did_not_finish_refuses(self):
        self.finishes = False
        with self.assertRaisesRegex(takeover.TakeoverError, 'did not finish'):
            self.take()
        self.finishes = True
        failing = lambda command, cwd=None, pass_fds=(): self.fake_run(command) and SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(takeover.TakeoverError, 'did not finish'):
            takeover.takeover(run=failing)

    def test_a_migration_that_leaves_its_record_refuses(self):
        def leaves(command, cwd=None, pass_fds=()):
            self.volume = VOLUME
            self.pinned_image = IMAGE
            self.intent = STUCK
            return SimpleNamespace(returncode=0)
        with self.assertRaisesRegex(takeover.TakeoverError, 'did not finish'):
            takeover.takeover(run=leaves)

    def test_a_missing_unfinished_or_malformed_rebuild_refuses_before_anything(self):
        self.record_path.unlink()
        with self.assertRaisesRegex(takeover.TakeoverError, 'No cluster rebuild'):
            self.take()
        for record, reason in (({'phase': 'failed', 'volume': VOLUME, 'image': IMAGE}, 'did not complete'),
                               ({'phase': 'completed', 'volume': 'sbarbase-durable-pgdata', 'image': IMAGE}, 'malformed'),
                               ({'phase': 'completed', 'volume': VOLUME, 'image': 'latest'}, 'malformed'),
                               ([], 'did not complete')):
            with self.subTest(reason=reason):
                self.write_rebuild(record)
                with self.assertRaisesRegex(takeover.TakeoverError, reason):
                    self.take()
        self.record_path.write_text('{')
        with self.assertRaisesRegex(takeover.TakeoverError, 'cannot be read'):
            self.take()
        self.assertEqual(self.runs, [])

    def test_a_prepared_move_refuses(self):
        self.write_move('prepared')
        with self.assertRaisesRegex(takeover.TakeoverError, 'abandon it first'):
            self.take()
        self.assertEqual(self.runs, [])
        self.assertTrue(self.move_path.exists())

    def test_a_begun_move_is_archived_as_superseded_after_the_migration(self):
        for phase in ('moving', 'migrating'):
            with self.subTest(phase=phase):
                self.volume, self.runs = 'sbarbase-durable-pgdata', []
                self.write_move(phase)
                self.intent = STUCK
                self.take()
                self.assertFalse(self.move_path.exists())
                archived = json.loads((self.root / ARCHIVED).read_text())
                self.assertEqual(archived['phase'], 'superseded')
                self.assertEqual(archived['superseded_by'], {'run': '20261016T030000Z', 'volume': VOLUME, 'image': IMAGE})
                self.assertEqual((archived['backup'], archived['marked']), ('20261016T030000Z', True))

    def test_the_move_record_stays_while_the_migration_has_not_finished(self):
        self.write_move()
        self.intent = STUCK
        self.finishes = False
        with self.assertRaises(takeover.TakeoverError):
            self.take()
        self.assertTrue(self.move_path.exists())

    def test_a_run_stopped_before_the_move_was_archived_archives_it_on_the_rerun(self):
        self.write_move()
        with patch.object(takeover.lab, 'atomic', side_effect=OSError('killed')), self.assertRaises(OSError):
            self.take()
        self.assertTrue(self.move_path.exists())
        self.take()
        self.assertEqual(len(self.runs), 1, 'the migration is not run twice')
        self.assertFalse(self.move_path.exists())

    def test_the_checkout_moves_back_only_to_a_version_whose_lock_names_the_rebuilt_image(self):
        self.write_move()
        self.lock = OTHER
        self.take()
        self.assertEqual(self.moved, [(FROM, None, True)])
        self.assertEqual(self.runs[0][0][-3:], ['--image-from-lock', '--to-volume', VOLUME])

    def test_the_move_is_marked_as_taken_over_before_the_checkout_moves(self):
        self.write_move()
        self.lock = OTHER
        seen = []
        original = self.move_back
        with patch.object(takeover.upgrade, 'move_back',
                          lambda commit, intent, state: seen.append(json.loads(self.move_path.read_text()).get('taken_over'))
                          or original(commit, intent, state)):
            self.take()
        self.assertEqual(seen, [{'run': '20261016T030000Z', 'volume': VOLUME, 'checkout': 'moving'}])
        archived = json.loads((self.root / ARCHIVED).read_text())
        self.assertEqual(archived['taken_over'], {'run': '20261016T030000Z', 'volume': VOLUME, 'checkout': 'moved'})

    def test_a_refused_record_leaves_the_move_unmarked(self):
        self.write_move()
        self.intent = STUCK
        self.refusal = 'The retired container still exists; reconcile the migration instead'
        with self.assertRaises(takeover.TakeoverError):
            self.take()
        self.assertNotIn('taken_over', json.loads(self.move_path.read_text()))

    def test_another_lock_without_a_way_back_refuses_before_anything(self):
        self.lock = OTHER
        with self.assertRaisesRegex(takeover.TakeoverError, 'another image'):
            self.take()
        self.write_move()
        self.from_image = 'sha256:' + '3' * 64
        with self.assertRaisesRegex(takeover.TakeoverError, 'another image'):
            self.take()
        self.from_image = IMAGE
        for source in ('parser.add_argument("--supersede-stuck")', '# SUPERSEDE_PROTOCOL=1', 'SUPERSEDE_PROTOCOL=2', None):
            with self.subTest(source=source):
                self.from_script = source
                with self.assertRaisesRegex(takeover.TakeoverError, 'supersede protocol'):
                    self.take()
        self.assertEqual((self.moved, self.runs), ([], []))
        self.assertTrue(self.move_path.exists())
        self.assertNotIn('taken_over', json.loads(self.move_path.read_text()), 'a refusal leaves the move unmarked')

    def test_a_rebuilt_volume_that_cannot_take_the_database_refuses_before_anything(self):
        self.write_move()
        self.lock = OTHER
        self.volume_refusal = 'holds no PostgreSQL data directory'
        with self.assertRaisesRegex(takeover.TakeoverError, 'cannot take the database'):
            self.take()
        self.assertEqual(self.proved, [(VOLUME, 'durable-upstream', IMAGE)])
        self.assertEqual((self.moved, self.runs), ([], []))
        self.assertNotIn('taken_over', json.loads(self.move_path.read_text()))

    def test_a_kill_inside_the_move_back_moves_back_again_on_the_rerun(self):
        self.write_move()
        self.lock = OTHER

        def killed(commit, intent, state):
            self.lock = IMAGE  # the lock file arrived, the dependencies did not
            raise KeyboardInterrupt
        with patch.object(takeover.upgrade, 'move_back', killed), self.assertRaises(KeyboardInterrupt):
            self.take()
        self.assertEqual(json.loads(self.move_path.read_text())['taken_over']['checkout'], 'moving')
        self.take()
        self.assertEqual([commit for commit, _, _ in self.moved], [FROM])
        archived = json.loads((self.root / ARCHIVED).read_text())
        self.assertEqual(archived['taken_over']['checkout'], 'moved')

    def test_release_unmarks_a_move_whose_takeover_cannot_go_on(self):
        self.write_move()
        self.move['taken_over'] = {'run': 'r', 'volume': VOLUME, 'checkout': 'moved'}
        self.move_path.write_text(json.dumps(self.move))
        for intent, pending in (({'migration': 'm', 'volume': 'sbarbase-durable-pgdata', 'new_volume': VOLUME}, None),
                                (None, {'superseded_by': 'n'})):
            with self.subTest(intent=intent, pending=pending):
                self.intent, self.pending = intent, pending
                with self.assertRaisesRegex(takeover.TakeoverError, 'finish the takeover'):
                    takeover.release()
                self.assertIn('taken_over', json.loads(self.move_path.read_text()))
        self.intent, self.pending = None, None
        self.volume = VOLUME  # the migration finished: only the takeover may end it now
        with self.assertRaisesRegex(takeover.TakeoverError, 'already names the rebuilt volume'):
            takeover.release()
        self.volume = 'sbarbase-durable-pgdata'
        self.intent = STUCK
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(takeover.main(['--release']), 0)
        released = json.loads(self.move_path.read_text())
        self.assertNotIn('taken_over', released)
        self.assertEqual(released['phase'], 'migrating')
        self.assertIn('no longer marked', output.getvalue())
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(takeover.main(['--release']), 1)
        self.assertIn('No database move is marked', error.getvalue())

    def test_a_version_whose_lock_cannot_be_proved_is_no_way_back(self):
        self.write_move()
        self.lock = OTHER
        with patch.object(takeover.backup, 'resolve_image', side_effect=takeover.backup.BackupError('missing')), \
                self.assertRaisesRegex(takeover.TakeoverError, 'another image'):
            self.take()
        with patch.object(takeover.release_channel, 'lock_at', lambda commit, name: None), \
                self.assertRaisesRegex(takeover.TakeoverError, 'another image'):
            self.take()
        self.assertEqual(self.moved, [])

    def test_a_failed_move_back_or_a_lock_that_still_differs_refuses(self):
        self.write_move()
        self.lock = OTHER
        with patch.object(takeover.upgrade, 'move_back', side_effect=takeover.upgrade.UpgradeError('local changes')), \
                self.assertRaisesRegex(takeover.TakeoverError, 'could not be moved back'):
            self.take()
        with patch.object(takeover.upgrade, 'move_back', lambda commit, intent, state: None), \
                self.assertRaisesRegex(takeover.TakeoverError, 'still names another image'):
            self.take()
        self.assertEqual(self.runs, [])

    def test_the_lock_read_is_the_database_lock_at_the_from_version(self):
        seen = []
        self.write_move()
        self.lock = OTHER
        with patch.object(takeover.release_channel, 'lock_at', lambda commit, name: seen.append((commit, name)) or {'commit': commit}):
            self.take()
        self.assertEqual(seen, [(FROM, takeover.release_channel.DATABASE_LOCK)])

    def test_main_reports_refusals_and_the_outcome(self):
        error = io.StringIO()
        with patch.object(takeover, 'takeover', side_effect=takeover.cluster_rebuild.RebuildError('Sbarbase is running; stop it first')), \
                contextlib.redirect_stderr(error):
            self.assertEqual(takeover.main([]), 1)
        self.assertIn('refused: Sbarbase is running', error.getvalue())
        output = io.StringIO()
        with patch.object(takeover, 'takeover', lambda: ({'image': IMAGE, 'volume': VOLUME}, {'to': TO}, True)), \
                contextlib.redirect_stdout(output):
            self.assertEqual(takeover.main([]), 0)
        self.assertIn(VOLUME, output.getvalue())
        self.assertIn('archived as superseded', output.getvalue())
        self.assertIn('were not restored', output.getvalue())
        output = io.StringIO()
        with patch.object(takeover, 'takeover', lambda: ({'image': IMAGE, 'volume': VOLUME}, None, False)), \
                contextlib.redirect_stdout(output):
            self.assertEqual(takeover.main([]), 0)
        self.assertEqual(output.getvalue(), f'The database already runs on {VOLUME}; nothing was changed.\n')


class LockTests(unittest.TestCase):
    """The real locks: the migration the takeover runs takes the worker, operation and effect locks
    itself (hba_ownership), so the takeover holds none of them, and refuses while the worker runs."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for item in (patch.object(takeover.upgrade, 'LOCK', self.root / 'upgrade.lock'),
                     patch.object(takeover.upgrade, 'SUPERVISOR_LOCK', self.root / 'supervisor.lock'),
                     patch.object(takeover.upgrade, 'BACKUP_LOCK', self.root / 'backup.lock'),
                     patch.object(takeover.runtime, 'STATE', self.root)):
            item.start()
            self.addCleanup(item.stop)

    def test_the_migration_shares_the_held_worker_lock_and_takes_its_other_locks(self):
        import subprocess
        import sys
        # The real ownership entry the generation migration uses (hba_migration.execute).
        script = ('import sys\n'
                  'sys.path.insert(0, sys.argv[1])\n'
                  'import hba_startup\n'
                  'with hba_startup.acquire(sys.argv[2], worker_fd=int(sys.argv[3]), migration=True):\n'
                  '    pass\n')
        lab = str(Path(takeover.__file__).parent)
        (self.root / takeover.hba_migration.hba_generation.MIGRATION).mkdir(mode=0o700)  # the record it runs under
        with takeover.held_locks() as worker:
            shared = subprocess.run([sys.executable, '-c', script, lab, str(self.root), str(worker)],
                                    pass_fds=(worker,), capture_output=True, text=True)
            opened = subprocess.run([sys.executable, '-c', script.replace('worker_fd=int(sys.argv[3])', 'worker_fd=None'),
                                     lab, str(self.root), '0'], capture_output=True, text=True)
        self.assertEqual(shared.returncode, 0, shared.stderr)
        self.assertNotEqual(opened.returncode, 0, 'a worker lock opened afresh is refused while the takeover holds it')

    def test_a_running_worker_or_a_lock_held_elsewhere_refuses(self):
        import fcntl
        for name, reason in (('worker.lock', 'worker is running'), ('upgrade.lock', 'database move is running'),
                             ('supervisor.lock', 'Sbarbase is running'), ('backup.lock', 'backup or restore')):
            with self.subTest(name=name), (self.root / name).open('a') as other:
                fcntl.flock(other, fcntl.LOCK_EX)
                with self.assertRaisesRegex((takeover.TakeoverError, takeover.upgrade.UpgradeError), reason):
                    with takeover.held_locks():
                        pass
        with takeover.held_locks():
            pass


if __name__ == '__main__':
    unittest.main()
