"""Per-environment backup completeness, retention and explicit restore recovery."""
import contextlib
from types import SimpleNamespace
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import backup

E = 'e_' + 'a' * 24


class Fixture(unittest.TestCase):
    def setUp(self):
        # Data-flow tests isolate admission; test_backup_image_identity covers native proof.
        for name in ('preflight', 'admit_database'):
            admission = patch.object(backup, name, return_value={'db': 'c' * 64} if name == 'preflight' else 'c' * 64)
            admission.start()
            self.addCleanup(admission.stop)
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.backups = root / 'backups'
        self.state = root / 'state'
        self.state.mkdir()
        (self.state / 'endpoints.json').write_text(json.dumps({E: {'auth': 'http://auth.invalid'}}))
        for target, value in (('BACKUPS', self.backups), ('STATE', self.state)):
            patcher = patch.object(backup, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        import cluster_inventory
        self.cluster = {'version': 1, 'image': 'sha256:' + '9' * 64, 'roles': [], 'parameters': [], 'refusals': []}
        self.order = []
        taking = patch.object(cluster_inventory, 'take', lambda: self.order.append('inventory') or self.cluster)
        taking.start()
        self.addCleanup(taking.stop)

    def tearDown(self):
        self.directory.cleanup()

    def complete(self, stamp, counts=None, reason=None, e=E):
        path = backup.private_dir(self.backups / e / stamp)
        (path / 'database.dump').write_bytes(b'dump-' + stamp.encode())
        (path / 'objects.tar').write_bytes(b'tar-' + stamp.encode())
        manifest = {'version': 1, 'runtime': e, 'created_at': stamp,
                    'database': {'file': 'database.dump', 'bytes': (path / 'database.dump').stat().st_size,
                                 'sha256': backup.digest(path / 'database.dump')},
                    'objects': {'file': 'objects.tar', 'bytes': (path / 'objects.tar').stat().st_size,
                                'sha256': backup.digest(path / 'objects.tar'), 'files': 0},
                    'counts': counts or {'auth.users': 1}}
        if reason:
            manifest['reason'] = reason
        (path / 'manifest.json').write_text(json.dumps(manifest))
        return path


class RetentionTests(Fixture):
    def test_only_complete_backups_count_and_the_newest_are_kept(self):
        for stamp in ('20260901T030000Z', '20260902T030000Z', '20260903T030000Z'):
            self.complete(stamp)
        backup.private_dir(self.backups / E / '20260902T120000Z')  # interrupted, no manifest
        backup.private_dir(self.backups / E / '20260904T030000Z')  # interrupted, newer than every complete one
        doomed = backup.prune(E, 2)
        names = sorted(path.name for path in (self.backups / E).iterdir())
        self.assertEqual(names, ['20260902T030000Z', '20260903T030000Z', '20260904T030000Z'])
        self.assertEqual(len(doomed), 2)
        self.assertEqual([path.name for path in backup.complete_backups(E)], ['20260902T030000Z', '20260903T030000Z'])

    def test_keeping_nothing_is_refused(self):
        with self.assertRaises(backup.BackupError):
            backup.prune(E, 0)

    def test_backups_of_the_last_three_upgrades_are_kept_and_not_counted(self):
        upgrades = ['20260901T100000Z', '20260903T100000Z', '20260905T100000Z', '20260907T100000Z']
        for day in range(1, 10):
            self.complete(f'202609{day:02d}T030000Z')
        for stamp in upgrades:
            self.complete(stamp, reason='upgrade')
        self.assertEqual(backup.upgrade_runs(), set(upgrades[1:]))
        backup.prune(E, 2)
        names = [path.name for path in backup.complete_backups(E)]
        # The two newest daily backups, plus the last three upgrades whatever their age; the
        # oldest upgrade is an ordinary backup again and was pruned with the others.
        self.assertEqual(names, ['20260903T100000Z', '20260905T100000Z', '20260907T100000Z', '20260908T030000Z', '20260909T030000Z'])

    def test_only_upgrades_that_moved_the_checkout_keep_their_backups(self):
        """Failed tries (the backup ran, then the snapshot or the move failed) used to take the
        protected slots, and the backups of the upgrades that really moved were pruned."""
        moved = ['20260901T100000Z', '20260903T100000Z']
        failed = ['20260904T100000Z', '20260905T100000Z', '20260906T100000Z']
        for stamp in moved:
            self.complete(stamp, reason='upgrade')
            backup.mark_moved(stamp)
        for stamp in failed:
            self.complete(stamp, reason='upgrade')
        for day in range(1, 10):
            self.complete(f'202609{day:02d}T030000Z')
        self.assertEqual(backup.moved_runs(), set(moved))
        self.assertEqual(backup.upgrade_runs(), set(moved))
        # A run under way counts until it ends, moved or not.
        self.assertEqual(backup.upgrade_runs(current='20260909T100000Z'), {*moved, '20260909T100000Z'})
        backup.prune(E, 2)
        self.assertEqual([path.name for path in backup.complete_backups(E)],
                         [*moved, '20260908T030000Z', '20260909T030000Z'])
        self.assertEqual(oct(backup.moved_record().stat().st_mode & 0o777), '0o600')
        with self.assertRaises(backup.BackupError):
            backup.mark_moved('../elsewhere')

    def test_a_damaged_record_of_moved_upgrades_keeps_every_upgrade_run(self):
        for stamp in ('20260901T100000Z', '20260902T100000Z'):
            self.complete(stamp, reason='upgrade')
        backup.private_dir(self.backups)
        backup.moved_record().write_text('{')
        self.assertIsNone(backup.moved_runs())
        self.assertEqual(backup.upgrade_runs(), {'20260901T100000Z', '20260902T100000Z'})
        # Replacing it keeps them too.
        self.complete('20260903T100000Z', reason='upgrade')
        backup.mark_moved('20260903T100000Z')
        self.assertEqual(backup.upgrade_runs(), {'20260901T100000Z', '20260902T100000Z', '20260903T100000Z'})

    def test_the_first_mark_keeps_the_upgrade_runs_taken_before_the_record_existed(self):
        legacy = ['20260901T100000Z', '20260902T100000Z', '20260903T100000Z']
        for stamp in legacy:
            self.complete(stamp, reason='upgrade')
        self.complete('20260904T100000Z', reason='upgrade')
        backup.mark_moved('20260904T100000Z')
        self.assertEqual(backup.upgrade_runs(), {*legacy[1:], '20260904T100000Z'})
        # From then on a try that did not move is an ordinary run.
        self.complete('20260905T100000Z', reason='upgrade')
        self.assertEqual(backup.upgrade_runs(), {*legacy[1:], '20260904T100000Z'})

    def test_an_upgrade_run_under_way_counts_among_the_last_ones_read_once_for_the_run(self):
        upgrades = ['20260901T100000Z', '20260903T100000Z', '20260905T100000Z']
        for stamp in upgrades:
            self.complete(stamp, reason='upgrade')
        current = '20260907T100000Z'
        protected = backup.upgrade_runs(current=current)
        self.assertEqual(protected, {*upgrades[1:], current})
        for day in range(1, 10):
            self.complete(f'202609{day:02d}T030000Z')
        # The set read at the start of the run is the one every prune of that run uses.
        with patch.object(backup, 'upgrade_runs', side_effect=AssertionError('read again')):
            backup.prune(E, 1, protected)
        self.assertEqual([path.name for path in backup.complete_backups(E)],
                         ['20260903T100000Z', '20260905T100000Z', '20260909T030000Z'])

    def test_an_upgrade_is_one_run_across_environments(self):
        other = 'e_' + 'b' * 24
        # The last upgrade backed up only the other environment (this one failed or did not exist):
        # it still counts as one of the last three upgrades for every directory.
        for stamp in ('20260901T100000Z', '20260902T100000Z', '20260903T100000Z'):
            self.complete(stamp, reason='upgrade')
        self.complete('20260904T100000Z', reason='upgrade', e=other)
        self.complete('20260905T030000Z')
        self.assertEqual(backup.upgrade_runs(), {'20260902T100000Z', '20260903T100000Z', '20260904T100000Z'})
        backup.prune(E, 1)
        self.assertEqual([path.name for path in backup.complete_backups(E)], ['20260902T100000Z', '20260903T100000Z', '20260905T030000Z'])
        # A damaged manifest is never taken for an upgrade.
        (self.backups / E / '20260905T030000Z' / 'manifest.json').write_text('[')
        self.assertEqual(len(backup.upgrade_runs()), 3)

    def test_the_command_passes_its_reason_to_every_backup_of_the_run(self):
        import offsite
        parsed = []

        def create(e, keep, now, reason, protected):
            parsed.append(reason)
            raise backup.BackupError('stop here')
        # The off-site configuration is never read here: it lives with the secrets.
        with patch.object(backup, 'create', create), patch.object(offsite, 'load_config', return_value=None), \
                patch('sys.stderr'), patch('builtins.print'):
            self.assertEqual(backup.main(['create', E, '--reason', 'upgrade', '--local-only']), 1)
            self.assertEqual(backup.main(['create', E, '--local-only']), 1)
        self.assertEqual(parsed, ['upgrade', None])
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            backup.main(['create', E, '--reason', 'whim'])


class VerifyTests(Fixture):
    def test_a_complete_backup_verifies(self):
        path = self.complete('20260901T030000Z')
        self.assertEqual(backup.verify(E, path)['runtime'], E)

    def test_a_changed_file_an_interrupted_backup_and_another_environment_are_refused(self):
        path = self.complete('20260901T030000Z')
        (path / 'objects.tar').write_bytes(b'tampered')
        with self.assertRaisesRegex(backup.BackupError, 'digest'):
            backup.verify(E, path)
        partial = backup.private_dir(self.backups / E / '20260902T030000Z')
        with self.assertRaisesRegex(backup.BackupError, 'incomplete'):
            backup.verify(E, partial)
        with self.assertRaisesRegex(backup.BackupError, 'Not a backup'):
            backup.verify('e_' + 'b' * 24, self.complete('20260903T030000Z'))


class ResolveTests(Fixture):
    def test_a_console_environment_id_resolves_to_its_runtime(self):
        catalog = self.state / 'control.sqlite'
        with contextlib.closing(sqlite3.connect(catalog)) as database, database:
            database.execute('CREATE TABLE provision_jobs(environment TEXT, runtime TEXT, state TEXT)')
            database.execute("INSERT INTO provision_jobs VALUES ('11111111-2222-3333-4444-555555555555', ?, 'succeeded')", (E,))
        self.assertEqual(backup.resolve('11111111-2222-3333-4444-555555555555', catalog), E)
        self.assertEqual(backup.resolve(E), E)
        with self.assertRaises(backup.BackupError):
            backup.resolve('11111111-2222-3333-4444-666666666666', catalog)
        with self.assertRaises(backup.BackupError):
            backup.resolve('../etc')


class RestoreTests(Fixture):
    def recorder(self, fail_on=None):
        calls = []

        def run(argv, **kwargs):
            calls.append(('run', tuple(argv)))
            if fail_on and fail_on in argv:
                raise backup.BackupError('injected')
        def sql(query, database='postgres'):
            calls.append(('sql', query))
            if 'SELECT oid FROM pg_database' in query:
                return '12345'
            if 'datconnlimit' in query:
                return '100|{acl}'
            if 'coalesce(datacl' in query:
                return '{acl}'
            return ''
        def helper(script, *args, **kwargs):
            calls.append(('helper', script, args))
        return calls, run, sql, helper

    def test_a_failed_database_restore_preserves_original_until_explicit_recovery(self):
        from test_restore_cutover import NativeModel, environment_archive
        environment_archive(self)
        with NativeModel(self, fail='replay') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertEqual(model.dbs[E]['oid'], '100')
            self.assertFalse(any(event[0] in ('files', 'rename', 'start-services') for event in model.events))
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertEqual(model.dbs, {E: {'oid': '100', 'allow_connections': True}})
            self.assertTrue(model.running)

    def test_rows_that_do_not_match_the_backup_refuse_before_cutover(self):
        from test_restore_cutover import NativeModel, environment_archive
        environment_archive(self)
        with NativeModel(self, actual='4|1|1|1') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertEqual(model.dbs[E]['oid'], '100')
            self.assertEqual(model.trees[E], model.identity('1'))
            self.assertFalse(any(event[0] in ('files', 'rename') for event in model.events))
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertFalse(backup.completion_path(E).exists())

    def test_a_successful_restore_keeps_the_previous_state_and_records_it(self):
        from test_restore_cutover import NativeModel, environment_archive
        path = environment_archive(self)
        with NativeModel(self) as model:
            record = model.restore()
            self.assertEqual(model.dbs[E]['oid'], '20000')
            self.assertEqual(model.dbs[record['previous_database']], {'oid': '100', 'allow_connections': False})
            self.assertEqual(model.trees[record['previous_files']], model.identity('1'))
            self.assertFalse(any(event[0] == 'drop' for event in model.events))
            self.assertTrue(any(item.name.startswith('restore-') for item in path.iterdir()))

    def test_the_dump_reads_the_snapshot_its_counts_came_from(self):
        """A row written while the backup runs is in both the dump and its counts, or in neither."""
        import io
        written = []

        class Session:
            def __init__(self, argv, **kwargs):
                self.stdin = io.StringIO()
                self.stdin.close = lambda: written.append(self.stdin.getvalue())
                self.stdout = io.StringIO('00000003-0000002A-1\n7|7|1|2\n')
                self.done = False

            def poll(self):
                return 0 if self.done else None

            def wait(self, timeout=None):
                self.done = True
                return 0

        dumped = []
        def run(argv, **kwargs):
            if 'pg_dump' in argv:
                dumped.append(list(argv))
            return None
        with patch.object(backup.subprocess, 'Popen', Session), patch.object(backup, 'run', run), \
             patch.object(backup, 'helper', lambda *a, **k: None), patch.object(backup, 'ownership', return_value=None), \
             patch.object(backup.backup_consistency, 'inventory', return_value=[]), \
             patch.object(backup.backup_consistency, 'validate', return_value={'contract':'fixture'}), \
             patch.object(backup, 'storage_image', return_value='sha256:storage'), \
             patch.object(backup.tarfile, 'open', side_effect=lambda path: __import__('contextlib').nullcontext(
                 type('A', (), {'getmembers': lambda self: []})())):
            (self.backups / E).mkdir(parents=True, exist_ok=True)
            path, manifest = backup.create(E)
            self.assertNotIn('reason', manifest)
            import datetime
            later = datetime.datetime(2026, 9, 25, 3, tzinfo=datetime.UTC)
            marked = backup.create(E, reason='upgrade', now=later)[1]
            with self.assertRaisesRegex(backup.BackupError, 'reason'):
                backup.create(E, reason='whim', now=later.replace(hour=4))
        self.assertEqual(marked['reason'], 'upgrade')
        self.assertEqual(json.loads((self.backups / E / '20260925T030000Z' / 'manifest.json').read_text())['reason'], 'upgrade')
        self.assertEqual(backup.upgrade_runs(), {'20260925T030000Z'})
        self.assertEqual(manifest['counts'], {'auth.users': 7, 'auth.identities': 7, 'storage.buckets': 1, 'storage.objects': 2})
        self.assertIn('--snapshot=00000003-0000002A-1', dumped[0])
        self.assertTrue(written[0].startswith('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;'))
        self.assertTrue(written[0].rstrip().endswith('COMMIT;'), 'the snapshot is released after the dump')

    def test_a_snapshot_that_fails_stops_the_backup(self):
        import io

        class Broken:
            def __init__(self, argv, **kwargs):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO('')

            def poll(self):
                return 3

        with patch.object(backup.subprocess, 'Popen', Broken):
            with self.assertRaisesRegex(backup.BackupError, 'snapshot'):
                with backup.snapshot(E):
                    self.fail('a failed snapshot must not reach the dump')

    def test_an_unpublished_environment_is_never_touched(self):
        calls, run, sql, helper = self.recorder()
        with patch.object(backup, 'run', run), patch.object(backup, 'sql', sql):
            with self.assertRaises(backup.BackupError):
                backup.restore('e_' + 'c' * 24, '20260901T030000Z')
        self.assertEqual(calls, [])


class StorageMetadataTests(Fixture):
    """Storage's shared storage_metadata database is backed up with every run and restored like an
    environment's database, so a release that changes the Storage pin can be undone completely."""
    OTHER = 'e_' + 'b' * 24

    def storage(self, stamp, tenants=(E,), reason=None):
        path = backup.private_dir(self.backups / backup.STORAGE / stamp)
        (path / 'database.dump').write_bytes(b'storage-' + stamp.encode())
        manifest = {'version': 1, 'kind': 'storage_metadata', 'created_at': stamp,
                    'database': {'file': 'database.dump', 'name': 'storage_metadata',
                                 'bytes': (path / 'database.dump').stat().st_size,
                                 'sha256': backup.digest(path / 'database.dump')},
                    'counts': {'tenants': len(tenants)}, 'tenants': sorted(tenants)}
        if reason:
            manifest['reason'] = reason
        (path / 'manifest.json').write_text(json.dumps(manifest))
        return path

    def test_the_dump_is_taken_inside_the_snapshot_its_tenants_came_from(self):
        import datetime
        import io
        other = self.OTHER
        sessions, written, dumped = [], [], []

        class Session:
            def __init__(self, argv, **kwargs):
                sessions.append(list(argv))
                self.stdin = io.StringIO()
                self.stdin.close = lambda: written.append(self.stdin.getvalue())
                self.stdout = io.StringIO(f'00000003-0000002B-1\n2|{E},{other}\n')
                self.done = False

            def poll(self):
                return 0 if self.done else None

            def wait(self, timeout=None):
                self.done = True
                return 0

        def run(argv, **kwargs):
            dumped.append(list(argv))
        with patch.object(backup.subprocess, 'Popen', Session), patch.object(backup, 'run', run), \
                patch.object(backup, 'storage_image', return_value='sha256:storage'):
            now = datetime.datetime(2026, 9, 25, 3, tzinfo=datetime.UTC)
            path, manifest = backup.create_storage(now=now, reason='upgrade')
            with self.assertRaisesRegex(backup.BackupError, 'already exists'):
                backup.create_storage(now=now)
            with self.assertRaisesRegex(backup.BackupError, 'reason'):
                backup.create_storage(now=now.replace(hour=4), reason='whim')
        self.assertEqual(path, self.backups / 'storage' / '20260925T030000Z')
        # psql and pg_dump both reach storage_metadata, and the dump reads the exported snapshot.
        self.assertEqual(sessions[0][-1], 'storage_metadata')
        self.assertIn('FROM public.tenants', written[0])
        self.assertTrue(written[0].rstrip().endswith('COMMIT;'), 'the snapshot is released after the dump')
        self.assertEqual(dumped[0][-2:], ['-d', 'storage_metadata'])
        self.assertIn('--snapshot=00000003-0000002B-1', dumped[0])
        self.assertEqual(manifest['counts'], {'tenants': 2})
        self.assertEqual(manifest['tenants'], [E, other])
        self.assertEqual(manifest['reason'], 'upgrade')
        # Its own manifest; the run counts among the upgrades, like its environment backups.
        self.assertEqual(backup.verify_storage(path)['kind'], 'storage_metadata')
        self.assertEqual(backup.upgrade_runs(), {'20260925T030000Z'})

    def test_a_changed_an_incomplete_or_a_misplaced_backup_is_refused(self):
        path = self.storage('20260901T030000Z')
        self.assertEqual(backup.verify_storage(path)['tenants'], [E])
        (path / 'database.dump').write_bytes(b'tampered')
        with self.assertRaisesRegex(backup.BackupError, 'digest'):
            backup.verify_storage(path)
        with self.assertRaisesRegex(backup.BackupError, 'incomplete'):
            backup.verify_storage(backup.private_dir(self.backups / 'storage' / '20260902T030000Z'))
        with self.assertRaisesRegex(backup.BackupError, 'Not a Storage'):
            backup.verify_storage(self.complete('20260903T030000Z'))
        # An environment backup placed in the storage folder is not taken for one.
        misplaced = backup.private_dir(self.backups / 'storage' / '20260904T030000Z')
        environment = self.complete('20260904T030000Z')
        for name in ('database.dump', 'manifest.json'):
            (misplaced / name).write_bytes((environment / name).read_bytes())
        with self.assertRaisesRegex(backup.BackupError, 'another format'):
            backup.verify_storage(misplaced)

    def recorder(self, fail_on=None, restored=None):
        calls = []

        def run(argv, **kwargs):
            calls.append(('run', tuple(argv)))
            if fail_on and fail_on in argv:
                raise backup.BackupError('injected')

        def sql(query, database='postgres'):
            calls.append(('sql', query))
            if 'SELECT oid FROM pg_database' in query:
                return '12345'
            if 'datconnlimit' in query:
                return '6|{acl}'
            if 'coalesce(datacl' in query:
                return '{acl}'
            if 'FROM tenants' in query:
                return restored if restored is not None else f'1|{E}'
            return ''
        return calls, run, sql

    def restore(self, calls, run, sql, name='20260901T030000Z'):
        starts = []

        def start_storage():
            starts.append('start')
            calls.append(('start',))
            return '10.0.0.9'
        with patch.object(backup, 'run', run), patch.object(backup, 'sql', sql), \
                patch.object(backup, 'start_storage', start_storage), \
                patch.object(backup, 'wait_storage', lambda address: calls.append(('wait', address))):
            return backup.restore_storage(name), starts

    def test_a_successful_restore_stops_storage_keeps_the_previous_database_and_records_it(self):
        from test_restore_cutover import NativeModel
        path = self.storage('20260901T030000Z')
        with NativeModel(self, scope=backup.STORAGE) as model:
            record = model.restore()
            self.assertEqual(model.dbs[backup.STORAGE_DATABASE]['oid'], '20000')
            self.assertEqual(model.dbs[record['previous_database']], {'oid': '100', 'allow_connections': False})
            self.assertEqual(next(event for event in model.events if event[0] == 'stop')[1], 'stop-intent')
            self.assertTrue(model.running)
            self.assertFalse(any(event[0] in ('drop', 'files') for event in model.events))
            self.assertTrue(any(item.name.startswith('restore-') for item in path.iterdir()))

    def test_a_failed_restore_requires_recovery_without_replacing_shared_metadata(self):
        from test_restore_cutover import NativeModel
        self.storage('20260901T030000Z')
        with NativeModel(self, scope=backup.STORAGE, fail='replay') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertEqual(model.dbs[backup.STORAGE_DATABASE]['oid'], '100')
            self.assertTrue(model.running)
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertEqual(model.dbs, {backup.STORAGE_DATABASE: {'oid': '100', 'allow_connections': True}})

    def test_registrations_that_do_not_match_the_backup_refuse_before_cutover(self):
        from test_restore_cutover import NativeModel
        self.storage('20260901T030000Z')
        with NativeModel(self, scope=backup.STORAGE, actual=f'2|{E},{self.OTHER}') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertFalse(any(event[0] in ('rename', 'stop') for event in model.events))
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertFalse(backup.completion_path(backup.STORAGE).exists())

    def test_a_backup_that_misses_an_environment_published_now_is_refused_before_anything_stops(self):
        (self.state / 'endpoints.json').write_text(json.dumps({E: {'auth': 'a'}, self.OTHER: {'auth': 'b'}}))
        self.storage('20260901T030000Z', tenants=(E,))
        calls, run, sql = self.recorder()
        with self.assertRaisesRegex(backup.BackupError, self.OTHER):
            self.restore(calls, run, sql)
        self.assertEqual(calls, [])

    def test_storage_is_published_again_for_every_environment_after_its_restart(self):
        import durable_runtime
        (self.state / 'endpoints.json').write_text(json.dumps({
            E: {'auth': 'a', 'storage': {'url': 'http://10.0.0.4:5000', 'tenantHost': E + '.storage.internal'}},
            self.OTHER: {'auth': 'b'}}))
        started = []
        container = {'NetworkSettings': {'Networks': {durable_runtime.NETWORK: {'IPAddress': '10.0.0.9'}}}}
        with patch.object(backup, 'run', lambda argv, **kwargs: started.append(list(argv))), \
                patch.object(durable_runtime, 'inspect', return_value=container):
            self.assertEqual(backup.start_storage(), '10.0.0.9')
        self.assertEqual(started, [['docker', 'start', 'sbarbase-durable-storage']])
        endpoints = json.loads((self.state / 'endpoints.json').read_text())
        self.assertEqual(endpoints[E]['storage'], {'url': 'http://10.0.0.9:5000', 'tenantHost': E + '.storage.internal'})
        self.assertNotIn('storage', endpoints[self.OTHER])

    def test_every_run_backs_it_up_and_a_failure_fails_the_run(self):
        import backup_offsite
        import offsite
        seen = []

        def create(e, keep, now, reason, protected):
            self.order.append('dump')
            return self.complete(now.strftime('%Y%m%dT%H%M%SZ')), {'database': {'bytes': 1}, 'objects': {'files': 0},
                                                                   'counts': {'auth.users': 1}}

        def create_storage(keep, now, reason, protected):
            seen.append((keep, now.strftime('%Y%m%dT%H%M%SZ'), reason))
            if len(seen) == 2:
                raise backup.BackupError('dump failed')
            return self.storage(now.strftime('%Y%m%dT%H%M%SZ')), {'database': {'bytes': 1}, 'counts': {'tenants': 1}}
        def create_management(keep, now, reason, protected):
            return self.storage(now.strftime('%Y%m%dT%H%M%SZ')), {'database': {'bytes': 1}, 'counts': {'auth.users': 1}}
        with patch.object(backup, 'create', create), patch.object(backup, 'create_storage', create_storage), \
                patch.object(backup, 'create_management', create_management), \
                patch.object(backup_offsite, 'write_installation') as installation, patch.object(offsite, 'load_config', return_value=None), \
                patch('sys.stderr'), patch('builtins.print'):
            self.assertEqual(backup.main(['create', 'all', '--reason', 'upgrade', '--local-only']), 0)
            # The inventory is taken once, before any dump, and is the one the manifest records.
            self.assertEqual(self.order[:2], ['inventory', 'dump'])
            self.assertIs(installation.call_args.kwargs['cluster'], self.cluster)
            self.assertEqual(backup.main(['create', 'all', '--local-only']), 1)
            # One environment only: Storage's shared database belongs to the whole run.
            self.order.clear()
            self.assertEqual(backup.main(['create', E, '--local-only']), 0)
            self.assertNotIn('inventory', self.order)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][2], 'upgrade')
        self.assertIsNone(seen[1][2])

    def test_listing_and_discarding_what_a_restore_set_aside(self):
        import contextlib
        import io
        self.storage('20260901T030000Z', reason='upgrade')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(backup.main(['list']), 0)
        self.assertIn('storage_metadata  20260901T030000Z', out.getvalue())
        self.assertIn('before an upgrade', out.getvalue())
        dropped = []

        def sql(query, database='postgres'):
            dropped.append(query)
            return 'storage_metadata_pre_20260901t030000z\n' if query.startswith('SELECT') else ''
        with patch.object(backup, 'sql', sql), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(backup.main(['discard-previous', 'storage']), 0)
        self.assertEqual(dropped[-1], 'DROP DATABASE storage_metadata_pre_20260901t030000z WITH (FORCE);')


class ManagementDatabaseTests(Fixture):
    """The management realm's Auth database is backed up with every run and restored through the
    same fenced cutover, so an Auth release that migrates the operator accounts has a way back."""

    def management(self, stamp, counts=None, reason=None):
        path = backup.private_dir(self.backups / backup.MANAGEMENT / stamp)
        (path / 'database.dump').write_bytes(b'management-' + stamp.encode())
        manifest = {'version': 1, 'kind': 'management', 'created_at': stamp,
                    'database': {'file': 'database.dump', 'name': 'management',
                                 'bytes': (path / 'database.dump').stat().st_size,
                                 'sha256': backup.digest(path / 'database.dump')},
                    'counts': counts or {'auth.users': 2, 'auth.identities': 2}}
        if reason:
            manifest['reason'] = reason
        (path / 'manifest.json').write_text(json.dumps(manifest))
        return path

    def test_the_dump_is_taken_inside_the_snapshot_its_counts_came_from(self):
        import datetime
        import io
        sessions, written, dumped = [], [], []

        class Session:
            def __init__(self, argv, **kwargs):
                sessions.append(list(argv))
                self.stdin = io.StringIO()
                self.stdin.close = lambda: written.append(self.stdin.getvalue())
                self.stdout = io.StringIO('00000003-0000002B-1\n3|4\n')
                self.done = False

            def poll(self):
                return 0 if self.done else None

            def wait(self, timeout=None):
                self.done = True
                return 0

        auth = {'tag': 'public.ecr.aws/supabase/gotrue:v2.196.0', 'id': 'sha256:' + '3' * 64}
        with patch.object(backup.subprocess, 'Popen', Session), patch.object(backup, 'run', lambda argv, **kw: dumped.append(list(argv))), \
                patch.object(backup, 'image_pin', return_value=auth):
            now = datetime.datetime(2026, 9, 25, 3, tzinfo=datetime.UTC)
            with patch.object(backup, 'ROOT', self.state):
                (self.state / 'lab').mkdir()
                (self.state / 'lab' / 'distro-image.lock.json').write_text(json.dumps({'id': 'sha256:' + '1' * 64}))
                with patch.object(backup, 'prune', wraps=backup.prune) as pruned:
                    path, manifest = backup.create_management(now=now, reason='upgrade')
                # Every run applies retention to the management backups too.
                pruned.assert_called_once_with(backup.MANAGEMENT, backup.DEFAULT_KEEP, None)
                with self.assertRaisesRegex(backup.BackupError, 'already exists'):
                    backup.create_management(now=now)
        self.assertEqual(path, self.backups / 'management' / '20260925T030000Z')
        self.assertEqual(sessions[0][-1], 'management')
        self.assertIn('FROM auth.users', written[0])
        self.assertTrue(written[0].rstrip().endswith('COMMIT;'))
        self.assertEqual(dumped[0][-2:], ['-d', 'management'])
        self.assertIn('--snapshot=00000003-0000002B-1', dumped[0])
        self.assertEqual(manifest['counts'], {'auth.users': 3, 'auth.identities': 4})
        self.assertEqual(manifest['images']['auth'], auth['id'])
        self.assertEqual(backup.verify_management(path)['kind'], 'management')
        self.assertEqual(backup.upgrade_runs(), {'20260925T030000Z'})

    def test_a_changed_an_incomplete_or_a_misplaced_backup_is_refused(self):
        path = self.management('20260901T030000Z')
        self.assertEqual(backup.verify_scope(backup.MANAGEMENT, path)['counts']['auth.users'], 2)
        (path / 'database.dump').write_bytes(b'tampered')
        with self.assertRaisesRegex(backup.BackupError, 'digest'):
            backup.verify_management(path)
        with self.assertRaisesRegex(backup.BackupError, 'incomplete'):
            backup.verify_management(backup.private_dir(self.backups / 'management' / '20260902T030000Z'))
        with self.assertRaisesRegex(backup.BackupError, 'Not a management'):
            backup.verify_management(self.complete('20260903T030000Z'))
        # A Storage metadata backup placed in the management folder is not taken for one.
        misplaced = backup.private_dir(self.backups / 'management' / '20260904T030000Z')
        storage = StorageMetadataTests.storage(self, '20260904T030000Z')
        for name in ('database.dump', 'manifest.json'):
            (misplaced / name).write_bytes((storage / name).read_bytes())
        with self.assertRaisesRegex(backup.BackupError, 'another format'):
            backup.verify_management(misplaced)

    def test_a_successful_restore_stops_the_management_auth_and_keeps_the_previous_database(self):
        from test_restore_cutover import NativeModel
        path = self.management('20260901T030000Z')
        with NativeModel(self, scope=backup.MANAGEMENT) as model:
            record = model.restore()
            self.assertEqual(model.service_checks, [], 'no environment service is involved')
            self.assertEqual(model.dbs['management']['oid'], '20000')
            self.assertEqual(model.dbs[record['previous_database']], {'oid': '100', 'allow_connections': False})
            self.assertEqual(record['previous_database'], 'management_pre_' + record['restored_at'])
            self.assertNotIn('previous_files', record)
            stop = next(event for event in model.events if event[0] == 'stop')
            self.assertEqual(stop[1], 'stop-intent')
            self.assertTrue(model.running and model.management_running)
            # The management Auth itself stops, beside Storage, and no environment's services do.
            self.assertEqual(model.stopped, [model.cid, backup.MANAGEMENT_CONTAINER])
            # The database-level settings of the dump follow the stage onto the restored database.
            held = '\n'.join(model.metadata)
            self.assertIn('GRANT CONNECT ON DATABASE', held)
            self.assertIn('ALTER ROLE management_auth IN DATABASE', held)
            self.assertIn('CONNECTION LIMIT = 5', held)
            names = [event[0] for event in model.events]
            # The restore waits for the management Auth it started, at the address it published.
            self.assertEqual(names[names.index('start-management') + 1], 'wait-management')
            self.assertEqual(model.waited, ['http://10.0.0.8:9999'])
            self.assertFalse(any(event[0] in ('drop', 'files', 'start-services') for event in model.events))
            self.assertTrue(any(item.name.startswith('restore-') for item in path.iterdir()))
            self.assertEqual(model.journal()['phase'], 'completed')
            self.assertEqual(backup.restore_status(), [])
            names = [event[0] for event in model.events]
            self.assertLess(names.index('stop'), names.index('rename'), 'the writers stop before any rename')

    def test_recovery_after_a_rename_stops_the_management_auth_before_renaming_back(self):
        from test_restore_cutover import NativeModel
        self.management('20260901T030000Z')
        with NativeModel(self, scope=backup.MANAGEMENT, fail='rename') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore management'):
                model.restore()
            model.fail, model.management_running, model.stopped, seen = None, True, [], len(model.events)
            self.assertEqual(model.recover()['status'], 'rolled-back')
            names = [event[0] for event in model.events[seen:]]
            self.assertIn(backup.MANAGEMENT_CONTAINER, model.stopped)
            self.assertLess(names.index('stop'), names.index('rename'))
            self.assertEqual(model.dbs['management']['oid'], '100')

    def test_operator_counts_that_differ_from_the_backup_refuse_before_cutover(self):
        from test_restore_cutover import NativeModel
        self.management('20260901T030000Z')
        with NativeModel(self, scope=backup.MANAGEMENT, actual='1|1') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore management'):
                model.restore()
            self.assertFalse(any(event[0] in ('rename', 'stop') for event in model.events))
            self.assertEqual(backup.restore_status()[0]['scope'], 'management')
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertEqual(model.dbs, {'management': {'oid': '100', 'allow_connections': True}})
            # Recovery stops the management Auth before touching the databases, and starts it again.
            self.assertIn(backup.MANAGEMENT_CONTAINER, model.stopped)
            self.assertTrue(model.management_running)
            self.assertFalse(backup.completion_path(backup.MANAGEMENT).exists())

    def test_a_management_auth_that_does_not_come_back_leaves_readiness_to_complete_restore(self):
        from test_restore_cutover import NativeModel
        self.management('20260901T030000Z')
        with NativeModel(self, scope=backup.MANAGEMENT, fail='start-management') as model:
            with self.assertRaisesRegex(backup.BackupError, 'complete-restore management 20260901T030000Z'):
                model.restore()
            stamp = model.journal()['stamp']
            self.assertEqual(backup.restore_status()[0]['scope'], 'management')
            # Data is restored and kept; readiness alone is retried, with nothing replayed.
            record = backup.complete_restore(backup.MANAGEMENT, '20260901T030000Z', stamp)
            self.assertEqual(record['previous_database'], 'management_pre_' + stamp)
            self.assertTrue(model.management_running)
            self.assertEqual(backup.restore_status(), [])

    def test_a_management_auth_that_never_answers_leaves_the_restore_incomplete(self):
        from test_restore_cutover import NativeModel
        self.management('20260901T030000Z')
        with NativeModel(self, scope=backup.MANAGEMENT, fail='wait-management') as model:
            with self.assertRaisesRegex(backup.BackupError, 'complete-restore management 20260901T030000Z'):
                model.restore()
            self.assertNotEqual(model.journal()['phase'], 'completed')
            # complete-restore waits again before it records completion.
            backup.complete_restore(backup.MANAGEMENT, '20260901T030000Z', model.journal()['stamp'])
            self.assertEqual(model.waited, ['http://10.0.0.8:9999'] * 2)
            self.assertEqual(backup.restore_status(), [])

    def test_a_new_management_auth_address_is_announced_until_sbarbase_restarts(self):
        import io
        import os
        server, marker = self.state / 'server.json', self.state / backup.MANAGEMENT_RESTART
        server.write_text('{}')
        os.utime(server, (200, 200))
        self.assertIsNone(backup.address_notice())
        # A restore moved the address while that console ran; every later command says so.
        backup.mark_management_moved()
        os.utime(marker, (300, 300))
        self.assertIn('restart Sbarbase', backup.address_notice())
        # A console that started at about the same moment may have read the old address.
        os.utime(server, (305, 305))
        self.assertIn('restart Sbarbase', backup.address_notice())
        os.utime(server, (200, 200))
        restored = {'previous_database': 'management_pre_x'}
        for argv, target in ((['restore-management', '20260901T030000Z'], 'restore_management'),
                             (['complete-restore', 'management', '20260901T030000Z', '20261003t060000z'], 'complete_restore'),
                             (['recover-restore', 'management', '20260901T030000Z', '20261003t060000z'], 'recover_restore')):
            with self.subTest(command=argv[0]), patch.object(backup, target, return_value=restored), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(backup.main(argv), 0)
                self.assertIn('restart Sbarbase', out.getvalue())
        with patch.object(backup, 'restore_status', return_value=[]), contextlib.redirect_stdout(io.StringIO()) as out:
            backup.main(['restore-status'])
        self.assertIn('restart Sbarbase', out.getvalue())
        self.assertTrue(marker.exists(), 'status only reads')
        # Sbarbase restarted clearly after the move: that console read the new address.
        os.utime(server, (400, 400))
        self.assertIsNone(backup.address_notice())
        # No console running: the next one reads the new address.
        server.unlink()
        self.assertIsNone(backup.address_notice())

    def test_the_marker_is_written_before_the_new_address_so_an_interruption_keeps_the_notice(self):
        import durable_runtime
        (self.state / 'management.json').write_text(json.dumps({'auth': 'http://10.0.0.2:9999'}))
        order = []
        item = {'NetworkSettings': {'Networks': {durable_runtime.NETWORK: {'IPAddress': '10.0.0.8'}}}}

        def published(path, value):
            order.append('address')
            raise OSError('killed before the address was published')
        with patch.object(backup, 'run', lambda argv, **kw: None), patch.object(durable_runtime, 'inspect', return_value=item), \
                patch.object(durable_runtime, 'atomic', published), \
                patch.object(backup, 'mark_management_moved', side_effect=lambda: order.append('marker')):
            with self.assertRaises(OSError):
                backup.start_management()
        self.assertEqual(order, ['marker', 'address'])

    def test_restore_management_fetches_an_offsite_set_that_is_not_here(self):
        import io
        import backup_offsite
        fetched = []
        with patch.object(backup_offsite, 'fetch', side_effect=fetched.append), \
                patch.object(backup, 'restore_management', return_value={'previous_database': 'management_pre_x'}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(backup.main(['restore-management', '20260901T030000Z', '--offsite']), 0)
            self.management('20260902T030000Z')
            self.assertEqual(backup.main(['restore-management', '20260902T030000Z', '--offsite']), 0)
        self.assertEqual(fetched, ['20260901T030000Z'])

    def test_a_manifest_with_other_or_negative_counts_is_another_format(self):
        for counts in ({'auth.users': 2}, {'auth.users': 2, 'auth.identities': 2, 'tenants': 1},
                       {'auth.users': -1, 'auth.identities': 0}, {'auth.users': '2', 'auth.identities': 2}):
            path = self.management('20260905T030000Z', counts=counts)
            with self.subTest(counts=counts), self.assertRaisesRegex(backup.BackupError, 'another format'):
                backup.verify_management(path)

    def test_every_full_run_backs_it_up_and_a_failure_fails_the_run(self):
        import backup_offsite
        import offsite
        seen = []

        def create(e, keep, now, reason, protected):
            return self.complete(now.strftime('%Y%m%dT%H%M%SZ')), {'database': {'bytes': 1}, 'objects': {'files': 0},
                                                                   'counts': {'auth.users': 1}}

        def create_storage(keep, now, reason, protected):
            return StorageMetadataTests.storage(self, now.strftime('%Y%m%dT%H%M%SZ')), {'database': {'bytes': 1}, 'counts': {'tenants': 1}}

        def create_management(keep, now, reason, protected):
            seen.append(reason)
            if len(seen) == 2:
                raise backup.BackupError('dump failed')
            return self.management(now.strftime('%Y%m%dT%H%M%SZ')), {'database': {'bytes': 1}, 'counts': {'auth.users': 2}}
        with patch.object(backup, 'create', create), patch.object(backup, 'create_storage', create_storage), \
                patch.object(backup, 'create_management', create_management), \
                patch.object(backup_offsite, 'write_installation'), patch.object(offsite, 'load_config', return_value=None), \
                patch('sys.stderr'), patch('builtins.print'):
            self.assertEqual(backup.main(['create', 'all', '--reason', 'upgrade', '--local-only']), 0)
            self.assertEqual(backup.main(['create', 'all', '--local-only']), 1)
            self.assertEqual(backup.main(['create', E, '--local-only']), 0)
        self.assertEqual(seen, ['upgrade', None])

    def test_pruning_keeps_upgrade_backups_and_the_one_a_restore_completed_from(self):
        stamps = ['20260901T030000Z', '20260902T030000Z', '20260903T030000Z', '20260904T030000Z']
        for stamp in stamps:
            self.management(stamp)
        backup.completion_path(backup.MANAGEMENT).parent.mkdir()
        backup.completion_path(backup.MANAGEMENT).write_text(json.dumps({'status': 'completed', 'scope': backup.MANAGEMENT, 'backup': stamps[1]}))
        with patch.object(backup, 'no_pending_completion', lambda: None):
            doomed = backup.prune(backup.MANAGEMENT, 1, {stamps[0]})
        self.assertEqual([path.name for path in doomed], [stamps[2]])
        self.assertEqual(sorted(path.name for path in (self.backups / backup.MANAGEMENT).iterdir()),
                         [stamps[0], stamps[1], stamps[3]])

    def test_listing_and_discarding_what_a_restore_set_aside(self):
        import io
        self.management('20260901T030000Z', reason='upgrade')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(backup.main(['list']), 0)
        self.assertIn('management  20260901T030000Z', out.getvalue())
        self.assertIn('operators 2', out.getvalue())
        dropped = []

        def sql(query, database='postgres'):
            dropped.append(query)
            return 'management_pre_20260901t030000z\n' if query.startswith('SELECT') else ''
        with patch.object(backup, 'sql', sql), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(backup.main(['discard-previous', 'management']), 0)
        self.assertEqual(dropped[-1], 'DROP DATABASE management_pre_20260901t030000z WITH (FORCE);')

    def test_the_management_auth_is_published_again_after_its_restart(self):
        import durable_runtime
        (self.state / 'management.json').write_text(json.dumps({'auth': 'http://10.0.0.2:9999'}))
        started = []
        item = {'NetworkSettings': {'Networks': {durable_runtime.NETWORK: {'IPAddress': '10.0.0.8'}}}}
        with patch.object(backup, 'run', lambda argv, **kw: started.append(argv)), \
                patch.object(durable_runtime, 'inspect', return_value=item), \
                patch.object(durable_runtime, 'atomic', lambda path, value: path.write_text(json.dumps(value))):
            self.assertEqual(backup.start_management(), 'http://10.0.0.8:9999')
        self.assertEqual(started, [['docker', 'start', backup.MANAGEMENT_CONTAINER]])
        self.assertEqual(json.loads((self.state / 'management.json').read_text()), {'auth': 'http://10.0.0.8:9999'})
        self.assertTrue((self.state / backup.MANAGEMENT_RESTART).exists(), 'a moved address is recorded for the notice')
        # Back on the same address, the file is left alone, so no restart is announced.
        written = []
        with patch.object(backup, 'run', lambda argv, **kw: None), patch.object(durable_runtime, 'inspect', return_value=item), \
                patch.object(durable_runtime, 'atomic', lambda path, value: written.append(value)):
            backup.start_management()
        self.assertEqual(written, [])

    def test_the_health_wait_returns_only_when_the_management_auth_answers(self):
        import urllib.request
        answers = [OSError('refused'), type('Response', (), {'status': 503, '__enter__': lambda self: self,
                                                             '__exit__': lambda self, *a: None})(),
                   type('Response', (), {'status': 200, '__enter__': lambda self: self,
                                         '__exit__': lambda self, *a: None})()]
        asked = []

        def urlopen(url, timeout):
            asked.append(url)
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with patch.object(urllib.request, 'urlopen', urlopen), patch.object(backup.time, 'sleep'):
            self.assertTrue(backup.wait_management('http://10.0.0.8:9999'))
        self.assertEqual(asked, ['http://10.0.0.8:9999/health'] * 3)
        with patch.object(urllib.request, 'urlopen', side_effect=OSError('refused')), \
                patch.object(backup.time, 'sleep'), patch.object(backup.time, 'time', side_effect=[0, 1, 2, 999]):
            with self.assertRaisesRegex(backup.BackupError, 'did not come back'):
                backup.wait_management('http://10.0.0.8:9999', timeout=10)

    def test_both_operator_counts_are_checked(self):
        self.assertEqual(backup.parse_management_counts('2|3'), {'auth.users': 2, 'auth.identities': 3})
        self.assertEqual(backup.restore_counts(backup.MANAGEMENT, lambda query: '2|3' if 'auth.identities' in query else 'x'),
                         {'auth.users': 2, 'auth.identities': 3})
        from test_restore_cutover import NativeModel
        self.management('20260901T030000Z')
        with NativeModel(self, scope=backup.MANAGEMENT, actual='2|1') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore management'):
                model.restore()
            self.assertFalse(any(event[0] == 'rename' for event in model.events))


if __name__ == '__main__':
    unittest.main()


SUPERVISOR = backup.supervisor_running


class StartDatabaseTests(Fixture):
    """P4: the database container alone, for a restore when the rest of Sbarbase cannot start."""

    NATIVE = 'sha256:' + '9' * 64
    CID = 'f' * 64

    def setUp(self):
        super().setUp()
        import hba_generation
        import hba_migration
        self.running = False
        self.calls = []
        self.ready_after = 2
        self.item = {'Id': self.CID, 'Name': '/' + backup.DB, 'Image': self.NATIVE,
                     'Config': {'Labels': {'io.sbarbase.owner': backup.DATABASE_OWNER}}}
        self.pin = {'container_id': self.CID, 'name': backup.DB, 'owner': backup.DATABASE_OWNER, 'image': self.NATIVE}
        for target, name, value in ((backup, 'supervisor_running', lambda: False),
                                    (backup, 'image_pin', lambda name: {}),
                                    (backup, 'resolve_image', lambda pin: ('ref', self.NATIVE)),
                                    (backup, 'run', self.docker), (backup.time, 'sleep', lambda _: None),
                                    (hba_migration, 'present', lambda state: False),
                                    (hba_generation, 'load', lambda state: {'target': self.pin})):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def docker(self, argv, check=True, **kwargs):
        self.calls.append(argv[:3])
        if argv[:3] == ['docker', 'container', 'inspect']:
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps([{**self.item, 'State': {'Running': self.running}}]))
        if argv[:2] == ['docker', 'start']:
            self.running = True
            return SimpleNamespace(returncode=0, stderr='', stdout='')
        if argv[:2] == ['docker', 'exec']:
            # Readiness is the database accepting connections, nothing weaker.
            if argv[2:] != [backup.DB, 'pg_isready', '-h', '127.0.0.1']:
                raise AssertionError(argv)
            self.ready_after -= 1
            return SimpleNamespace(returncode=0 if self.ready_after <= 0 else 2, stderr='', stdout='')
        raise AssertionError(argv)

    def test_the_pinned_container_on_the_locked_image_is_started_and_awaited(self):
        self.assertEqual(backup.start_database(), self.CID)
        self.assertIn(['docker', 'start', backup.DB], self.calls)
        # Nothing but the database is started.
        self.assertEqual([call for call in self.calls if call[:2] == ['docker', 'start']], [['docker', 'start', backup.DB]])
        self.assertEqual(sum(1 for call in self.calls if call[:2] == ['docker', 'exec']), 2)
        # Already running: nothing is started again.
        self.calls.clear()
        self.ready_after = 0
        backup.start_database()
        self.assertNotIn(['docker', 'start', backup.DB], self.calls)

    def test_anything_but_that_container_with_sbarbase_stopped_is_refused(self):
        import hba_migration
        cases = [
            ('supervisor', 'Sbarbase is running'), ('migration', 'generation migration is waiting'),
            ('image', "lock and owner name"), ('owner', "lock and owner name"), ('pin', 'generation pin names'),
        ]
        for case, reason in cases:
            with self.subTest(case=case):
                self.calls.clear()
                patches = {'supervisor': patch.object(backup, 'supervisor_running', lambda: True),
                           'migration': patch.object(hba_migration, 'present', lambda state: True),
                           'image': patch.dict(self.item, {'Image': 'sha256:' + '8' * 64}),
                           'owner': patch.dict(self.item, {'Config': {'Labels': {'io.sbarbase.owner': 'other'}}}),
                           'pin': patch.dict(self.pin, {'container_id': 'e' * 64})}
                with patches[case], self.assertRaisesRegex(backup.BackupError, reason):
                    backup.start_database()
                self.assertNotIn(['docker', 'start', backup.DB], self.calls)

    def test_the_pin_must_match_in_every_field_and_be_readable(self):
        import hba_generation
        for field, value in (('image', 'sha256:' + '7' * 64), ('name', 'other-db'), ('owner', 'other')):
            with self.subTest(field=field), patch.dict(self.pin, {field: value}), \
                    self.assertRaisesRegex(backup.BackupError, 'generation pin names'):
                backup.start_database()
        for error in (ValueError('torn'), FileNotFoundError('gone'), PermissionError('not the owner')):
            with self.subTest(error=error), patch.object(hba_generation, 'load', side_effect=error), \
                    self.assertRaisesRegex(backup.BackupError, 'pin cannot be read'):
                backup.start_database()
        with patch.dict(self.item, {'Name': '/other'}), self.assertRaisesRegex(backup.BackupError, 'lock and owner name'):
            backup.start_database()
        self.assertNotIn(['docker', 'start', backup.DB], self.calls)

    def test_another_account_is_refused_rather_than_failing(self):
        with patch.object(backup, 'supervisor_running', side_effect=PermissionError('supervisor.lock')), \
                self.assertRaisesRegex(backup.BackupError, 'run it as the service account'):
            backup.start_database()
        self.assertNotIn(['docker', 'start', backup.DB], self.calls)

    def test_the_database_lock_is_the_one_read(self):
        asked = []
        with patch.object(backup, 'image_pin', lambda name: asked.append(name) or {}):
            backup.start_database()
        self.assertEqual(asked, ['distro-image.lock.json'])

    def test_a_held_supervisor_lock_is_seen(self):
        import fcntl
        self.assertFalse(SUPERVISOR(self.state))
        with (self.state / 'supervisor.lock').open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertTrue(SUPERVISOR(self.state))
        self.assertFalse(SUPERVISOR(self.state))

    def test_a_database_that_never_becomes_ready_is_reported(self):
        self.ready_after = 10 ** 6
        with patch.object(backup.time, 'time', side_effect=[0, 0, 200]), self.assertRaisesRegex(backup.BackupError, 'did not become ready'):
            backup.start_database(wait=120)

    def test_the_command_starts_it_without_a_preflight(self):
        import io
        with patch.object(backup, 'start_database', return_value=self.CID) as started, \
                patch.object(backup, 'preflight', side_effect=AssertionError('no preflight')), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(backup.main(['start-database']), 0)
        started.assert_called_once_with()
        self.assertIn('nothing else was started', out.getvalue())


class ServiceImageTests(Fixture):
    """A restore never starts an environment service on another release's image."""

    def setUp(self):
        super().setUp()
        self.images = {f'{backup.PREFIX}-{E}-auth': 'sha256:' + 'a' * 64, f'{backup.PREFIX}-{E}-rest': 'sha256:' + 'b' * 64,
                       f'{backup.PREFIX}-{E}-realtime': 'sha256:' + 'c' * 64}
        pinned = {('images.lock.json', 'auth'): 'sha256:' + 'a' * 64, ('images.lock.json', 'rest'): 'sha256:' + 'b' * 64,
                  ('realtime-image.lock.json', None): 'sha256:' + 'c' * 64}
        self.realtime = False
        self.old_rest = set()
        self.started = []
        for name, value in (('image_pin', lambda filename, key=None: (filename, key)),
                            ('resolve_image', lambda pin: ('ref', pinned[pin])),
                            ('published', lambda: {E: {'realtime': {'url': 'x'}} if self.realtime else {}}),
                            ('run', self.docker)):
            patcher = patch.object(backup, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def docker(self, argv, check=True, **kwargs):
        if argv[:3] == ['docker', 'container', 'inspect']:
            image = self.images.get(argv[3])
            if image is None:
                return SimpleNamespace(returncode=1, stdout='', stderr='')
            env = [] if argv[3] in self.old_rest else [f'{k}={v}' for k, v in backup.data_api.CONTAINER_ENV.items()]
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(
                [{'Image': image, 'Config': {'Labels': {'io.sbarbase.owner': backup.DATABASE_OWNER}, 'Env': env}}]))
        if argv[:2] == ['docker', 'start']:
            self.started.append(argv[2:])
            raise RuntimeError('stop here')
        raise AssertionError(argv)

    def test_realtime_is_checked_only_when_the_environment_runs_it(self):
        other = {f'{backup.PREFIX}-{E}-realtime': 'sha256:' + 'f' * 64}
        with patch.dict(self.images, other):
            backup.require_service_images(E)
            self.realtime = True
            with self.assertRaisesRegex(backup.BackupError, 'realtime image this version pins'):
                backup.require_service_images(E)
        self.assertEqual(backup.service_names(E)[-1], f'{backup.PREFIX}-{E}-realtime')
        self.realtime = False
        self.assertEqual(backup.service_names(E), [f'{backup.PREFIX}-{E}-auth', f'{backup.PREFIX}-{E}-rest'])

    def test_a_rest_container_from_before_the_data_api_settings_is_refused_before_anything_changes(self):
        self.old_rest.add(f'{backup.PREFIX}-{E}-rest')
        with self.assertRaisesRegex(backup.BackupError, 'ignore them'):
            backup.require_service_images(E)

    def test_services_on_the_pinned_images_pass_and_any_other_refuses(self):
        backup.require_service_images(E)
        for service in ('auth', 'rest'):
            name = f'{backup.PREFIX}-{E}-{service}'
            with self.subTest(service=service), patch.dict(self.images, {name: 'sha256:' + 'f' * 64}), \
                    self.assertRaisesRegex(backup.BackupError, f'{service} image this version pins'):
                backup.require_service_images(E)
        with patch.dict(self.images, {f'{backup.PREFIX}-{E}-auth': None}), self.assertRaises(backup.BackupError):
            backup.require_service_images(E)

    def test_a_container_of_another_owner_is_refused(self):
        original = self.docker

        def foreign(argv, check=True, **kwargs):
            result = original(argv, check, **kwargs)
            if argv[:3] == ['docker', 'container', 'inspect'] and argv[3].endswith('-rest'):
                item = json.loads(result.stdout)[0]
                item['Config']['Labels']['io.sbarbase.owner'] = 'someone-else'
                return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps([item]))
            return result
        with patch.object(backup, 'run', foreign), self.assertRaisesRegex(backup.BackupError, 'rest image'):
            backup.require_service_images(E)

    def test_realtime_is_checked_when_the_environment_runs_it(self):
        self.realtime = True
        backup.require_service_images(E)
        with patch.dict(self.images, {f'{backup.PREFIX}-{E}-realtime': 'sha256:' + 'f' * 64}), \
                self.assertRaisesRegex(backup.BackupError, 'realtime image'):
            backup.require_service_images(E)

    def test_services_are_never_started_on_another_image(self):
        with patch.dict(self.images, {f'{backup.PREFIX}-{E}-auth': 'sha256:' + 'f' * 64}), self.assertRaises(backup.BackupError):
            backup.start_services(E)
        self.assertEqual(self.started, [])


class ScheduleTests(unittest.TestCase):
    def test_the_daily_backup_runs_once_per_day_after_its_hour(self):
        import datetime
        import dev
        day = datetime.datetime(2026, 9, 24, 2, 59, tzinfo=datetime.UTC)
        self.assertFalse(dev.backup_due(day, None, 3))
        self.assertTrue(dev.backup_due(day.replace(hour=3), None, 3))
        self.assertTrue(dev.backup_due(day.replace(hour=22), '2026-09-23', 3))
        self.assertFalse(dev.backup_due(day.replace(hour=22), '2026-09-24', 3))
        self.assertFalse(dev.backup_due(day.replace(hour=22), None, None))

    def test_the_schedule_is_configured_by_environment(self):
        import dev
        self.assertEqual(dev.backup_hour({}), 3)
        self.assertIsNone(dev.backup_hour({'SBARBASE_BACKUP_HOUR': 'off'}))
        self.assertEqual(dev.backup_hour({'SBARBASE_BACKUP_HOUR': '23'}), 23)
        for bad in ('24', '-1', 'noon'):
            with self.assertRaises(ValueError):
                dev.backup_hour({'SBARBASE_BACKUP_HOUR': bad})
        self.assertEqual(dev.backup_keep({}), 7)
        with self.assertRaises(ValueError):
            dev.backup_keep({'SBARBASE_BACKUP_KEEP': '0'})
