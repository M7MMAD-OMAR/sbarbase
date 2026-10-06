"""Source checks for native HTTP fixture ownership, budgets and race scheduling."""
import contextlib
import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup

SPEC = importlib.util.spec_from_file_location('native_storage_drill_test', Path(__file__).with_name('disposable-storage-backup-drill.py'))
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)


class PlanTests(unittest.TestCase):
    def test_plan_is_source_only_and_declares_actual_aggregate_budgets(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('External command forbidden')):
            result = drill.plan()
        self.assertEqual(result['status'], 'planned-runtime-unrun')
        resources = result['resources']
        for service in ('auth_bootstrap', 'storage'):
            self.assertEqual(resources['database_memory_mib'] + resources[service + '_memory_mib'], 512)
            self.assertEqual(resources['database_cpus'] + resources[service + '_cpus'], .5)
        self.assertEqual(resources['published_ports'], [])
        self.assertIn('enduser_auth', result['required_unproven'])
        self.assertIn('fresh_host_full_application', result['required_unproven'])
        self.assertEqual(result['reference']['tag'], 'self-hosted/v0.8.2')

    def test_original_storage_etag_configuration_is_not_overridden(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.NativeStorage(directory)
            configuration = instance.storage_configuration()
        self.assertFalse(any('ETAG' in key for key in configuration))
        self.assertEqual(configuration['STORAGE_BACKEND'], 'file')
        self.assertEqual(configuration['PG_QUEUE_ENABLE'], 'false')
        self.assertEqual(configuration['S3_PROTOCOL_ENABLED'], 'false')


class IsolationTests(unittest.TestCase):
    def test_private_published_inventory_contains_only_native_fixture_tenant(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.NativeStorage(directory)
            with instance.settings():
                self.assertEqual(set(backup.published()), {drill.ENVIRONMENT})
                self.assertEqual(backup.ROOT, Path(directory))
                self.assertEqual(backup.DATABASE_OWNER, instance.owner)

    def test_running_storage_is_stopped_before_helper_and_restarted_after_database(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.NativeStorage(directory)
            events = []
            with patch.object(instance, 'running', return_value=True), \
                    patch.object(instance, 'docker', side_effect=lambda *args, **kwargs: events.append(args[0])), \
                    patch.object(drill.base.Disposable, 'helper', side_effect=lambda *args, **kwargs: events.append('original-helper-pauses-db')), \
                    patch.object(instance, 'start_storage', side_effect=lambda: events.append('start-storage')), \
                    patch.object(instance, 'storage_ready', side_effect=lambda: events.append('storage-ready')):
                instance.helper('fixture')
            self.assertEqual(events, ['stop', 'original-helper-pauses-db', 'start-storage', 'storage-ready'])

    def test_fenced_storage_stays_stopped_through_file_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.NativeStorage(directory)
            with patch.object(instance, 'running', return_value=False), \
                    patch.object(drill.base.Disposable, 'helper', return_value='native-helper'), \
                    patch.object(instance, 'start_storage', side_effect=AssertionError('Writer must remain stopped')):
                self.assertEqual(instance.helper('fixture'), 'native-helper')

    def test_unknown_network_inspection_does_not_authorize_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.NativeStorage(directory)
            with patch.object(instance, 'docker', return_value=subprocess.CompletedProcess([], 1, '[]', 'daemon unavailable')):
                with self.assertRaises(drill.base.Refusal):
                    instance.absent('network', instance.network)

    def test_http_upload_and_delete_use_original_api_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.NativeStorage(directory)
            calls = []
            def http(url, method='GET', body=None, headers=None):
                calls.append((url, method, body, headers))
                return 200, b'{}'
            with patch.object(instance, 'endpoint', return_value='http://127.0.0.1:5000'), patch.object(drill.runtime, 'http', side_effect=http):
                instance.upload(drill.CHANGED, upsert=True)
                instance.delete()
            upload, deletion = calls
            self.assertEqual(upload[1], 'POST')
            self.assertEqual(upload[2], drill.CHANGED)
            self.assertEqual(upload[3]['x-upsert'], 'true')
            self.assertEqual(upload[3]['x-forwarded-host'], drill.ENVIRONMENT + '.storage.internal')
            self.assertTrue(upload[3]['authorization'].startswith('Bearer '))
            self.assertEqual(deletion[1], 'DELETE')
            self.assertEqual(json.loads(deletion[2]), {'prefixes': [drill.OBJECT]})


class RaceTests(unittest.TestCase):
    def test_http_race_occurs_after_native_dump_and_preserves_snapshot_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.NativeStorage(directory)
            rows = [{'bucket_id': 'actual', 'name': 'native', 'version': 'observed', 'metadata': {'eTag': 'actual-native-shape'}}]
            archive = Path(directory) / '20310102T000000Z'
            manifest = {'counts': {'storage.objects': 1}}
            events = []
            def create(environment, now=None):
                backup.run(['docker', 'exec', instance.db, 'pg_dump', '-d', environment])
                events.append('archive-files')
                return archive, manifest
            with patch.object(instance, 'rows', return_value=rows), \
                    patch.object(instance, 'verify_download'), \
                    patch.object(instance, 'upload', side_effect=lambda *args, **kwargs: events.append('http-upload')), \
                    patch.object(instance, 'observe', return_value={'metadata_rows': []}), \
                    patch.object(backup, 'run', side_effect=lambda *args, **kwargs: events.append('native-dump')), \
                    patch.object(backup, 'create', side_effect=create), \
                    patch.object(drill.backup_consistency, 'validate') as validation:
                result = instance.race('overwrite', datetime.datetime(2031, 1, 2, tzinfo=datetime.UTC))
            self.assertEqual(events, ['native-dump', 'http-upload', 'archive-files', 'http-upload'])
            validation.assert_called_once_with(archive / 'objects.tar', drill.ENVIRONMENT, rows, 1)
            self.assertEqual(result['outcome'], 'coherent-checkpoint-admitted')

    def test_no_http_injection_cannot_count_as_an_adversarial_race(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.NativeStorage(directory)
            with patch.object(instance, 'rows', return_value=[]), patch.object(instance, 'verify_download'), \
                    patch.object(backup, 'create', side_effect=backup.BackupError('unrelated early failure')):
                with self.assertRaises(drill.base.Refusal):
                    instance.race('delete', datetime.datetime(2031, 1, 2, tzinfo=datetime.UTC))


if __name__ == '__main__':
    unittest.main()
