"""Backup admission refuses unproved tools before artifacts or live mutations."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backup

LOGICAL = 'sha256:' + 'a' * 64
NATIVE = 'sha256:' + 'b' * 64
PIN = {'tag': 'supabase/storage-api:v1.2.3', 'id': LOGICAL,
       'digests': ['supabase/storage-api@' + LOGICAL]}
REF = 'docker.io/supabase/storage-api@' + LOGICAL
DBPIN = {'tag': 'supabase/postgres:17.6.1.166', 'id': LOGICAL,
         'digests': ['supabase/postgres@' + LOGICAL]}
DBREF = 'docker.io/supabase/postgres@' + LOGICAL
E = 'e_' + 'a' * 24


def result(value, code=0, stderr=''):
    return subprocess.CompletedProcess([], code, json.dumps(value), stderr)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'lab').mkdir()
        for name, pin in (('storage-image.lock.json', PIN), ('distro-image.lock.json', DBPIN)):
            (self.root / 'lab' / name).write_text(json.dumps(pin))
        self.root_patch = patch.object(backup, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def image(self, reference=REF, native=NATIVE):
        return result([{'Id': native, 'RepoDigests': [reference]}])

    def container(self, native=NATIVE, owner=backup.DATABASE_OWNER, running=True):
        return result([{'Id': 'c' * 64, 'Name': '/' + backup.DB, 'Image': native, 'Config': {'Labels': {'io.sbarbase.owner': owner}},
                        'State': {'Running': running}}])

    def native(self, argv, **kwargs):
        if argv[:3] == ['docker', 'image', 'inspect']:
            return self.image(argv[3])
        if argv[:3] == ['docker', 'container', 'inspect']:
            return self.container()
        if argv[:3] == ['docker', 'volume', 'inspect']:
            return result([{'Name': backup.OBJECTS_VOLUME, 'Labels': {'io.sbarbase.owner': backup.DATABASE_OWNER}}])
        self.fail('Unexpected lifecycle or data command: ' + repr(argv))

    def test_logical_archive_pin_differs_from_native_resolution(self):
        with patch.object(backup, 'run', self.native):
            self.assertEqual(backup.storage_image(), LOGICAL)
            self.assertEqual(backup.resolve_storage_image(), (REF, NATIVE))
            self.assertEqual(backup.preflight(), {'db': 'c' * 64, 'storage': (REF, NATIVE), 'objects_volume': backup.OBJECTS_VOLUME})

    def test_helper_uses_qualified_pull_never_and_retains_resources_and_mount_modes(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv[1:3] == ['image', 'inspect']:
                return self.image()
            if argv[1:3] == ['volume', 'inspect']:
                return self.native(argv)
            return result('')
        with patch.object(backup, 'run', run):
            backup.helper('echo test')
            backup.helper('echo test', writable=True)
        for index in (2, 5):
            command = calls[index]
            self.assertIn('--pull=never', command)
            self.assertIn(REF, command)
            self.assertNotIn(LOGICAL, command)
            for option, value in (('--network', 'none'), ('--memory', '256m'), ('--cpus', '.5'),
                                  ('--label', 'io.sbarbase.owner=backup')):
                self.assertEqual(command[command.index(option) + 1], value)
        self.assertIn(backup.OBJECTS_VOLUME + ':/data:ro', calls[2])
        self.assertIn(backup.OBJECTS_VOLUME + ':/data', calls[5])

    def test_invalid_lock_refuses_before_any_inspect(self):
        for pin in ({}, {**PIN, 'id': 'sha256:short'}, {**PIN, 'digests': ['other/image@' + LOGICAL]},
                    {**PIN, 'tag': 'supabase/storage-api:latest'}):
            with self.subTest(pin=pin), patch.object(backup, 'run') as run:
                (self.root / 'lab' / 'storage-image.lock.json').write_text(json.dumps(pin))
                with self.assertRaises(backup.BackupError):
                    backup.preflight()
                run.assert_not_called()

    def test_helper_inspection_failures_never_run_or_pull(self):
        failures = [result([], 1, 'permission denied'), result([], 1, 'daemon unavailable'),
                    result([], 1, 'Error response from daemon: No such image: ' + REF),
                    result([{'Id': NATIVE, 'RepoDigests': ['other/image@' + LOGICAL]}]),
                    result([{'Id': 'sha256:short', 'RepoDigests': [REF]}]), result([]),
                    result([{}, {}]), subprocess.CompletedProcess([], 0, '{', ''),
                    result([{'Id': NATIVE, 'RepoDigests': [REF]}], stderr='warning')]
        for inspected in failures:
            with self.subTest(inspected=inspected), patch.object(backup, 'run', return_value=inspected) as run:
                with self.assertRaises(backup.BackupError):
                    backup.helper('exit 0', writable=True)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(run.call_args.args[0], ['docker', 'image', 'inspect', REF])

    def test_database_rejects_wrong_native_owner_state_and_malformed_inspect(self):
        failures = [self.container(LOGICAL), self.container(owner='foreign'), self.container(running=False),
                    result([{'Image': NATIVE}]), result([]), result([{}, {}]),
                    result([], 1, 'permission denied'), result([{'Config': None}])]
        for key, value in (('Name', '/foreign'), ('Id', 'short'), ('Id', None)):
            item = json.loads(self.container().stdout)[0]
            item[key] = value
            failures.append(result([item]))
        for inspected in failures:
            with self.subTest(inspected=inspected), patch.object(backup, 'run', side_effect=[self.image(DBREF), inspected]):
                with self.assertRaises(backup.BackupError):
                    backup.admit_database()

    def test_psql_uses_admitted_container_id_instead_of_mutable_name(self):
        with patch.object(backup, 'run', self.native):
            argv = backup.psql('postgres')
        self.assertEqual(argv[3], 'c' * 64)
        self.assertNotIn(backup.DB, argv)

    def test_foreign_missing_and_malformed_objects_volume_never_launch_helper(self):
        failures = [result([], 1, 'No such volume'), result([], 1, 'permission denied'),
                    result([{'Name': 'foreign', 'Labels': {'io.sbarbase.owner': backup.DATABASE_OWNER}}]),
                    result([{'Name': backup.OBJECTS_VOLUME, 'Labels': {'io.sbarbase.owner': 'foreign'}}]),
                    result([{'Name': backup.OBJECTS_VOLUME, 'Labels': None}]), result([]), result([{}, {}])]
        for inspected in failures:
            with self.subTest(inspected=inspected), patch.object(backup, 'run', side_effect=[self.image(), inspected]) as run:
                with self.assertRaises(backup.BackupError):
                    backup.helper('exit 0', writable=True)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[0], ['docker', 'volume', 'inspect', backup.OBJECTS_VOLUME])

    def test_foreign_volume_refuses_create_before_writes_after_native_image_proof(self):
        def run(argv, **kwargs):
            if argv[1:3] == ['volume', 'inspect']:
                return result([{'Name': backup.OBJECTS_VOLUME, 'Labels': {'io.sbarbase.owner': 'foreign'}}])
            return self.native(argv, **kwargs)
        with patch.object(backup, 'published', return_value={E: {}}), patch.object(backup, 'run', run), \
                patch.object(backup, 'private_dir') as directory:
            with self.assertRaises(backup.BackupError):
                backup.create(E)
            directory.assert_not_called()

    def test_psql_revalidates_database_before_sql_exec(self):
        with patch.object(backup, 'run', side_effect=[self.image(DBREF), self.container(owner='foreign')]), \
                patch.object(backup.subprocess, 'run') as execute:
            with self.assertRaises(backup.BackupError):
                backup.sql('DROP DATABASE dangerous;')
            execute.assert_not_called()

    def test_operations_refuse_before_directory_service_sql_or_helper_mutations(self):
        with patch.object(backup, 'published', return_value={E: {}}), \
                patch.object(backup, 'verify', return_value={'counts': {}}), \
                patch.object(backup, 'verify_storage', return_value={'tenants': [E]}), \
                patch.object(backup, 'preflight', side_effect=backup.BackupError('proof refused')), \
                patch.object(backup, 'private_dir') as directory, patch.object(backup, 'run') as run, \
                patch.object(backup, 'sql') as sql, patch.object(backup, 'helper') as helper:
            operations = [lambda: backup.create(E), backup.create_storage,
                          lambda: backup.restore(E, '20261003T000000Z'),
                          lambda: backup.restore_storage('20261003T000000Z'),
                          lambda: backup.discard_previous(E), backup.discard_previous_storage]
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaisesRegex(backup.BackupError, 'proof refused'):
                    operation()
            for mutation in (directory, run, sql, helper):
                mutation.assert_not_called()

    def test_cli_refuses_before_backup_directory_or_lock_creation(self):
        with patch.object(backup, 'preflight', side_effect=backup.BackupError('proof refused')), \
                patch.object(backup, 'private_dir') as directory, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(backup.main(['create', E, '--local-only']), 1)
            directory.assert_not_called()

    def test_valid_db_but_unproved_storage_never_writes_artifacts(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv == ['docker', 'image', 'inspect', REF]:
                return self.image('other/image@' + LOGICAL)
            return self.native(argv, **kwargs)
        with patch.object(backup, 'published', return_value={E: {}}), \
                patch.object(backup, 'run', run), patch.object(backup, 'private_dir') as directory:
            with self.assertRaises(backup.BackupError):
                backup.create(E)
            directory.assert_not_called()
            self.assertEqual(len(calls), 3)
            self.assertFalse(any('run' in argv or 'pull' in argv or 'exec' in argv for argv in calls))


if __name__ == '__main__':
    unittest.main()
