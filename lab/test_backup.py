"""Per-environment backup: completeness, retention, verification and a restore that always rolls back."""
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
        with sqlite3.connect(catalog) as database:
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
            if 'datconnlimit' in query:
                return '100|{acl}'
            if 'coalesce(datacl' in query:
                return '{acl}'
            return ''
        def helper(script, *args, **kwargs):
            calls.append(('helper', script, args))
        return calls, run, sql, helper

    def test_a_failed_database_restore_puts_the_original_back_and_restarts_services(self):
        self.complete('20260901T030000Z')
        calls, run, sql, helper = self.recorder(fail_on='pg_restore')
        with patch.object(backup, 'run', run), patch.object(backup, 'sql', sql), patch.object(backup, 'helper', helper), \
             patch.object(backup, 'start_services', lambda e: calls.append(('run', ('docker', 'start', *backup.service_names(e))))):
            with self.assertRaisesRegex(backup.BackupError, 'injected'):
                backup.restore(E, '20260901T030000Z')
        statements = ' '.join(call[1] for call in calls if call[0] == 'sql')
        self.assertIn(f'ALTER DATABASE {E} RENAME TO {E}_pre_', statements)
        self.assertIn(f'DROP DATABASE IF EXISTS {E} WITH (FORCE)', statements)
        self.assertRegex(statements, rf'ALTER DATABASE {E}_pre_\w+ RENAME TO {E};')
        self.assertEqual(calls[-1][0:2], ('run', ('docker', 'start', *backup.service_names(E))))
        self.assertFalse(any(call[0] == 'helper' for call in calls), 'files are untouched when the database fails')

    def test_rows_that_do_not_match_the_backup_roll_back_files_and_database(self):
        self.complete('20260901T030000Z', counts={'auth.users': 5})
        calls, run, sql, helper = self.recorder()
        with patch.object(backup, 'run', run), patch.object(backup, 'sql', sql), patch.object(backup, 'helper', helper), \
             patch.object(backup, 'counts', return_value={'auth.users': 4}), \
             patch.object(backup, 'start_services', lambda e: calls.append(('run', ('docker', 'start', *backup.service_names(e))))):
            with self.assertRaisesRegex(backup.BackupError, 'rows'):
                backup.restore(E, '20260901T030000Z')
        statements = ' '.join(call[1] for call in calls if call[0] == 'sql')
        self.assertIn(f'DROP DATABASE IF EXISTS {E} WITH (FORCE)', statements)
        self.assertEqual(calls[-1][1][:2], ('docker', 'start'))

    def test_a_successful_restore_keeps_the_previous_state_and_records_it(self):
        path = self.complete('20260901T030000Z', counts={'auth.users': 1})
        calls, run, sql, helper = self.recorder()
        with patch.object(backup, 'run', run), patch.object(backup, 'sql', sql), patch.object(backup, 'helper', helper), \
             patch.object(backup, 'counts', return_value={'auth.users': 1}), patch.object(backup, 'wait_healthy'), \
             patch.object(backup, 'start_services'):
            record = backup.restore(E, '20260901T030000Z')
        self.assertTrue(record['previous_database'].startswith(E + '_pre_'))
        self.assertFalse(any('DROP DATABASE' in call[1] for call in calls if call[0] == 'sql'))
        self.assertTrue(any(name.name.startswith('restore-') for name in path.iterdir()))

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
        self.assertIn('FROM tenants', written[0])
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
        path = self.storage('20260901T030000Z')
        calls, run, sql = self.recorder()
        record, starts = self.restore(calls, run, sql)
        self.assertEqual(calls[0], ('run', ('docker', 'stop', 'sbarbase-durable-storage')))
        statements = ' '.join(call[1] for call in calls if call[0] == 'sql')
        self.assertIn('ALTER DATABASE storage_metadata RENAME TO storage_metadata_pre_', statements)
        self.assertIn('ALTER DATABASE storage_metadata CONNECTION LIMIT 6', statements)
        self.assertNotIn('DROP DATABASE', statements)
        restores = [call[1] for call in calls if call[0] == 'run' and 'pg_restore' in call[1]]
        self.assertEqual(restores[0][-2:], ('-d', 'postgres'))
        self.assertIn('--create', restores[0])
        self.assertEqual(starts, ['start'])
        self.assertEqual(calls[-1], ('wait', '10.0.0.9'))
        self.assertTrue(record['previous_database'].startswith('storage_metadata_pre_'))
        self.assertTrue(any(item.name.startswith('restore-') for item in path.iterdir()))

    def test_a_failed_restore_puts_the_original_back_and_starts_storage_again(self):
        self.storage('20260901T030000Z')
        calls, run, sql = self.recorder(fail_on='pg_restore')
        with self.assertRaisesRegex(backup.BackupError, 'injected'):
            self.restore(calls, run, sql)
        statements = ' '.join(call[1] for call in calls if call[0] == 'sql')
        self.assertIn('DROP DATABASE IF EXISTS storage_metadata WITH (FORCE)', statements)
        self.assertRegex(statements, r'ALTER DATABASE storage_metadata_pre_\w+ RENAME TO storage_metadata;')
        self.assertEqual(calls[-1], ('start',))

    def test_registrations_that_do_not_match_the_backup_roll_back(self):
        self.storage('20260901T030000Z')
        calls, run, sql = self.recorder(restored=f'2|{E},{self.OTHER}')
        with self.assertRaisesRegex(backup.BackupError, 'registrations'):
            self.restore(calls, run, sql)
        statements = ' '.join(call[1] for call in calls if call[0] == 'sql')
        self.assertIn('DROP DATABASE IF EXISTS storage_metadata WITH (FORCE)', statements)
        self.assertEqual(calls[-1], ('start',))

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
            return self.complete(now.strftime('%Y%m%dT%H%M%SZ')), {'database': {'bytes': 1}, 'objects': {'files': 0},
                                                                   'counts': {'auth.users': 1}}

        def create_storage(keep, now, reason, protected):
            seen.append((keep, now.strftime('%Y%m%dT%H%M%SZ'), reason))
            if len(seen) == 2:
                raise backup.BackupError('dump failed')
            return self.storage(now.strftime('%Y%m%dT%H%M%SZ')), {'database': {'bytes': 1}, 'counts': {'tenants': 1}}
        with patch.object(backup, 'create', create), patch.object(backup, 'create_storage', create_storage), \
                patch.object(backup_offsite, 'write_installation'), patch.object(offsite, 'load_config', return_value=None), \
                patch('sys.stderr'), patch('builtins.print'):
            self.assertEqual(backup.main(['create', 'all', '--reason', 'upgrade', '--local-only']), 0)
            self.assertEqual(backup.main(['create', 'all', '--local-only']), 1)
            # One environment only: Storage's shared database belongs to the whole run.
            self.assertEqual(backup.main(['create', E, '--local-only']), 0)
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


if __name__ == '__main__':
    unittest.main()


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
