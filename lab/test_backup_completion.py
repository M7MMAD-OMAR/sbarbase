"""Readiness retry cannot replay data replacement after cutover."""
import datetime
import json
import unittest
from unittest.mock import patch
import backup
from test_backup import Fixture, E

NAME = '20260901T030000Z'
STAMP = '20261003t070000z'


class CompletionTests(Fixture):
    def checkpoint(self, scope=E):
        path = self.complete(NAME, e=scope)
        if scope == backup.STORAGE:
            manifest = json.loads((path / 'manifest.json').read_text())
            manifest.update(kind=backup.STORAGE_DATABASE, tenants=[E])
            (path / 'manifest.json').write_text(json.dumps(manifest))
        database = backup.STORAGE_DATABASE if scope == backup.STORAGE else scope
        record = dict(restored_at=STAMP, backup=NAME, previous_database=database + '_pre_' + STAMP, counts={'auth.users': 1})
        if scope != backup.STORAGE:
            record['previous_files'] = '.pre-restore-' + scope + '-' + STAMP
        (path / ('restore-' + STAMP + '.json')).unlink(missing_ok=True)
        with patch.object(backup, 'database_oid', return_value='12345'):
            backup.checkpoint_restore(scope, NAME, STAMP, {'db': 'c' * 64}, record)
        return path, record

    def test_startup_and_health_errors_are_sanitized_pending_and_retry_is_data_free(self):
        for scope in (E, backup.STORAGE):
            for failure in ('startup', 'health'):
                with self.subTest(scope=scope, failure=failure):
                    path, record = self.checkpoint(scope)
                    start_name = 'start_storage' if scope == backup.STORAGE else 'start_services'
                    health_name = 'wait_storage' if scope == backup.STORAGE else 'wait_healthy'
                    with patch.object(backup, 'database_oid', return_value='12345'), \
                            patch.object(backup, start_name, side_effect=RuntimeError('PRIVATE SENSITIVE DETAIL') if failure == 'startup' else None), \
                            patch.object(backup, health_name, side_effect=RuntimeError('PRIVATE SENSITIVE DETAIL') if failure == 'health' else None):
                        with self.assertRaises(backup.BackupError) as caught:
                            backup.complete_restore(scope, NAME, STAMP)
                    self.assertNotIn('PRIVATE', str(caught.exception))
                    self.assertIn('complete-restore', str(caught.exception))
                    self.assertEqual(json.loads(backup.completion_path(scope).read_text())['status'], 'readiness-pending')
                    self.assertFalse((path / ('restore-' + STAMP + '.json')).exists())
                    with patch.object(backup, 'database_oid', return_value='12345'), \
                            patch.object(backup, start_name) as start, patch.object(backup, health_name), \
                            patch.object(backup, 'run', side_effect=AssertionError('destructive run')), \
                            patch.object(backup, 'helper', side_effect=AssertionError('file replay')), \
                            patch.object(backup, 'counts', side_effect=AssertionError('old snapshot counts')):
                        self.assertEqual(backup.complete_restore(scope, NAME, STAMP), record)
                        self.assertEqual(start.call_count, 1)
                        self.assertEqual(backup.complete_restore(scope, NAME, STAMP), record)
                        self.assertEqual(start.call_count, 1)

    def test_oid_container_archive_and_record_drift_refuse_before_readiness(self):
        mutations = [lambda c: c.update(database_oid='999'), lambda c: c.update(container_id='d' * 64),
                     lambda c: c.update(manifest_sha256='0' * 64), lambda c: c.update(backup='20260902T030000Z'),
                     lambda c: c['record'].update(previous_database='another_database'),
                     lambda c: c.pop('objects_sha256')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.checkpoint()
                path = backup.completion_path(E)
                value = json.loads(path.read_text())
                mutate(value)
                backup.atomic_private(path, value)
                with patch.object(backup, 'database_oid', return_value='12345'), patch.object(backup, 'start_services') as start:
                    with self.assertRaises(backup.BackupError):
                        backup.complete_restore(E, NAME, STAMP)
                    start.assert_not_called()

    def test_pending_blocks_direct_replacement_discard_and_all_retention(self):
        self.checkpoint()
        for function, args in [(backup.restore, (E, NAME)), (backup.restore_storage, (NAME,)),
                               (backup.discard_previous, (E,)), (backup.discard_previous_storage, ()),
                               (backup.prune, (E, 1)), (backup.prune, ('installation', 1)),
                               (backup.create, (E,)), (backup.create_storage, ())]:
            with self.subTest(function=function.__name__), patch.object(backup, 'run') as run:
                with self.assertRaisesRegex(backup.BackupError, 'pending'):
                    function(*args)
                run.assert_not_called()

    def test_checkpoint_permissions_and_identity_are_required(self):
        self.checkpoint()
        path = backup.completion_path(E)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        path.chmod(0o644)
        with self.assertRaisesRegex(backup.BackupError, 'private'):
            backup.complete_restore(E, NAME, STAMP)
        path.chmod(0o600)
        with self.assertRaises(backup.BackupError):
            backup.complete_restore(E, '../escape', STAMP)

    def test_success_record_persistence_failure_keeps_pending_and_retryable(self):
        self.checkpoint()
        atomic = backup.atomic_private
        def fail_record(path, value):
            if path.name.startswith('restore-'):
                raise OSError('private path')
            atomic(path, value)
        with patch.object(backup, 'database_oid', return_value='12345'), patch.object(backup, 'start_services'), \
                patch.object(backup, 'wait_healthy'), patch.object(backup, 'atomic_private', fail_record):
            with self.assertRaisesRegex(backup.BackupError, 'readiness remains pending'):
                backup.complete_restore(E, NAME, STAMP)
        self.assertEqual(json.loads(backup.completion_path(E).read_text())['status'], 'readiness-pending')

    def test_forged_completed_checkpoint_cannot_unlock_predecessor_deletion(self):
        backup.atomic_private(backup.completion_path(E), {'status': 'completed'})
        with patch.object(backup, 'sql') as sql:
            with self.assertRaises(backup.BackupError):
                backup.discard_previous_storage()
            sql.assert_not_called()

    def test_boolean_version_and_symlink_parent_refuse_before_service_start(self):
        self.checkpoint()
        path = backup.completion_path(E)
        value = json.loads(path.read_text())
        value['version'] = True
        backup.atomic_private(path, value)
        with patch.object(backup, 'start_services') as start:
            with self.assertRaises(backup.BackupError):
                backup.complete_restore(E, NAME, STAMP)
            start.assert_not_called()
        value['version'] = 1
        backup.atomic_private(path, value)
        actual = path.parent.with_name('moved-completions')
        path.parent.rename(actual)
        path.parent.symlink_to(actual, target_is_directory=True)
        with patch.object(backup, 'start_services') as start:
            with self.assertRaises(backup.BackupError):
                backup.complete_restore(E, NAME, STAMP)
            start.assert_not_called()

    def test_completed_history_does_not_freeze_later_container_replacement_and_retention_keeps_proof(self):
        path, record = self.checkpoint()
        with patch.object(backup, 'database_oid', return_value='12345'), patch.object(backup, 'start_services'), patch.object(backup, 'wait_healthy'):
            backup.complete_restore(E, NAME, STAMP)
        self.complete('20260902T030000Z')
        self.complete('20260903T030000Z')
        with patch.object(backup, 'preflight', side_effect=AssertionError('historical proof must not admit old live CID')), \
                patch.object(backup, 'database_oid', side_effect=AssertionError('historical proof must not query old live OID')):
            backup.no_pending_completion()
            backup.prune(E, 1)
        self.assertTrue(path.exists())
        self.assertEqual([p.name for p in backup.complete_backups(E)], [NAME, '20260903T030000Z'])
