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

    def complete(self, stamp, counts=None):
        path = backup.private_dir(self.backups / E / stamp)
        (path / 'database.dump').write_bytes(b'dump-' + stamp.encode())
        (path / 'objects.tar').write_bytes(b'tar-' + stamp.encode())
        manifest = {'version': 1, 'runtime': E, 'created_at': stamp,
                    'database': {'file': 'database.dump', 'bytes': (path / 'database.dump').stat().st_size,
                                 'sha256': backup.digest(path / 'database.dump')},
                    'objects': {'file': 'objects.tar', 'bytes': (path / 'objects.tar').stat().st_size,
                                'sha256': backup.digest(path / 'objects.tar'), 'files': 0},
                    'counts': counts or {'auth.users': 1}}
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
                backup.Snapshot(E)

    def test_an_unpublished_environment_is_never_touched(self):
        calls, run, sql, helper = self.recorder()
        with patch.object(backup, 'run', run), patch.object(backup, 'sql', sql):
            with self.assertRaises(backup.BackupError):
                backup.restore('e_' + 'c' * 24, '20260901T030000Z')
        self.assertEqual(calls, [])


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
