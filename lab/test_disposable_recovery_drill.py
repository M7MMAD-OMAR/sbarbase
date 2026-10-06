"""Source checks for the public-clone drill; native execution is a separate gate."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import backup
import recovery_bundle

spec = importlib.util.spec_from_file_location('disposable_drill', Path(__file__).with_name('disposable-recovery-drill.py'))
drill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drill)


def package():
    parts = {}
    for scope, filenames in ((drill.ENVIRONMENT, ('database.dump', 'objects.tar', 'manifest.json')),
                              ('storage', ('database.dump', 'manifest.json'))):
        for name in filenames:
            raw = (scope + '/' + name).encode()
            parts[scope + '/' + name] = {'data': base64.b64encode(raw).decode(), 'sha256': hashlib.sha256(raw).hexdigest()}
    return {'schema': 1, 'environment': drill.ENVIRONMENT, 'reference': drill.pinned_identity(),
            'contract': drill.CONTRACT, 'features': drill.default_features(),
            'settings': {'public_url': 'https://disposable.invalid', 'backup_hour': 3},
            'secrets': {name: 'a' * 64 for name in ('auth', 'rest', 'storage', 'jwt')},
            'roles': {'records': [
                {'rolname': drill.ENVIRONMENT + '_' + kind, 'rolcanlogin': True, 'rolinherit': False,
                 'rolsuper': False, 'rolcreatedb': False, 'rolcreaterole': False, 'rolreplication': False,
                 'rolbypassrls': False, 'rolconnlimit': 7, 'rolvaliduntil': None,
                 'rolconfig': ['statement_timeout=8s'] if kind == 'rest' else None}
                for kind in ('auth', 'rest', 'storage')], 'memberships': []}, 'archives': parts}


class ContractTests(unittest.TestCase):
    def test_plan_does_not_contact_docker_or_retained_installation(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('External command forbidden')):
            result = drill.plan()
        self.assertEqual(result['status'], 'planned-runtime-unrun')
        self.assertEqual(result['reference']['tag'], 'self-hosted/v0.8.2')
        self.assertEqual(result['reference']['commit'], '564eab8ad7840b13324f68b1bfac074ef8d51c21')
        self.assertEqual(result['resources']['max_running_test_containers'], 1)
        self.assertEqual(result['resources']['database_memory_mib'], 512)
        self.assertEqual(result['resources']['database_cpus'], .5)
        self.assertEqual(set(result['required_unproven']), set(drill.UNPROVEN))

    def test_every_enabled_unproven_capability_refuses_before_allocation(self):
        for feature in drill.UNPROVEN:
            with self.subTest(feature=feature):
                features = drill.default_features()
                features[feature] = True
                with patch.object(drill.Disposable, 'volume', side_effect=AssertionError('Mutation forbidden')):
                    with self.assertRaisesRegex(drill.Refusal, feature):
                        drill.plan(features)

    def test_unknown_or_incomplete_inventory_cannot_silently_disable_features(self):
        for mutate in (lambda value: value.pop('vault'), lambda value: value.update({'future_feature': False}),
                       lambda value: value.update({'cron': 'false'}), lambda value: value.update({'database_fixture': False})):
            features = drill.default_features()
            mutate(features)
            with self.assertRaises(drill.Refusal):
                drill.plan(features)

    def test_authenticated_complete_package_and_independent_key(self):
        payload = package()
        envelope = recovery_bundle.seal(payload, b'a' * 32)
        self.assertEqual(drill.validate_package(recovery_bundle.open_bundle(envelope, b'a' * 32)), payload)
        self.assertNotIn(payload['secrets']['auth'], json.dumps(envelope))
        with self.assertRaises(Exception):
            recovery_bundle.open_bundle(envelope, b'b' * 32)

    def test_missing_archives_changed_bytes_or_changed_reference_refuse(self):
        for mutation in (lambda value: value['archives'].pop('storage/database.dump'),
                         lambda value: value['archives'][drill.ENVIRONMENT + '/objects.tar'].update({'data': 'eA=='}),
                         lambda value: value['reference'].update({'commit': '0' * 40}),
                         lambda value: value.update({'extra_unreviewed_state': {}}),
                         lambda value: value['roles']['records'][0].update({'rolsuper': True}),
                         lambda value: value['settings'].update({'production_webhook': 'https://example.invalid'})):
            payload = copy.deepcopy(package())
            mutation(payload)
            with self.assertRaises(drill.Refusal):
                drill.validate_package(payload)


class IsolationTests(unittest.TestCase):
    def test_missing_native_storage_toolchain_refuses_before_any_volume_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.Disposable(directory)
            with patch.object(backup, 'resolve_image'), patch.object(backup, 'resolve_storage_image'), \
                    patch.object(instance, 'native_toolchain', side_effect=backup.BackupError('native interpreter absent')), \
                    patch.object(instance, 'volume', side_effect=AssertionError('Mutation forbidden')) as volume:
                with self.assertRaisesRegex(backup.BackupError, 'native interpreter absent'):
                    instance.execute(drill.default_features())
            volume.assert_not_called()

    def test_private_settings_replace_all_retained_paths_and_restore_module(self):
        original = {name: getattr(backup, name) for name in ('ROOT', 'STATE', 'BACKUPS', 'DB', 'OBJECTS_VOLUME', 'DATABASE_OWNER')}
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.Disposable(directory)
            with instance.settings():
                self.assertEqual(backup.ROOT, Path(directory))
                self.assertEqual(backup.STATE, Path(directory) / 'state')
                self.assertEqual(backup.STATE.stat().st_mode & 0o777, 0o700)
                self.assertEqual((backup.STATE / 'endpoints.json').stat().st_mode & 0o777, 0o600)
                self.assertTrue(backup.DB.startswith('sbarbase-disposable-'))
                self.assertEqual(backup.DATABASE_OWNER, instance.owner)
                self.assertEqual(set(backup.published()), {drill.ENVIRONMENT, drill.NEIGHBOR})
            for name, value in original.items():
                self.assertEqual(getattr(backup, name), value)

    def test_collision_or_unclassified_native_absence_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.Disposable(directory)
            name = instance.db
            for result in (subprocess.CompletedProcess([], 0, '[{}]', ''),
                           subprocess.CompletedProcess([], 1, '[]', 'Cannot connect to daemon'),
                           subprocess.CompletedProcess([], 1, '[]', 'Error: No such container: unrelated')):
                with patch.object(instance, 'docker', return_value=result):
                    with self.assertRaises(drill.Refusal):
                        instance.absent('container', name)
            result = subprocess.CompletedProcess([], 1, '[]', 'Error: No such container: ' + name + '\n')
            with patch.object(instance, 'docker', return_value=result):
                instance.absent('container', name)

    def test_cleanup_checks_native_owner_before_removing(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.Disposable(directory)
            instance.resources = [('volume', instance.objects)]
            result = subprocess.CompletedProcess([], 0, json.dumps([{'Name': instance.objects, 'Labels': {'io.sbarbase.owner': 'foreign'}}]), '')
            with patch.object(instance, 'docker', return_value=result) as native:
                with self.assertRaises(drill.Refusal):
                    instance.cleanup()
            self.assertEqual(native.call_count, 1)
            self.assertEqual(instance.resources, [('volume', instance.objects)])

    def test_helper_stops_database_before_original_native_helper_and_restarts_after(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.Disposable(directory)
            events = []
            def docker(*args, **kwargs):
                events.append(args[0])
                if args[:2] == ('container', 'inspect'):
                    return subprocess.CompletedProcess([], 1, '[]', 'Error: No such container: ' + args[2])
                return subprocess.CompletedProcess([], 0, '', '')
            instance.native_helper = lambda *args, **kwargs: events.append('original-helper')
            with patch.object(instance, 'docker', side_effect=docker), patch.object(instance, 'ready', side_effect=lambda: events.append('ready')):
                instance.helper('fixture')
            self.assertEqual(events, ['stop', 'container', 'original-helper', 'container', 'start', 'ready'])


class NativeReadinessTests(unittest.TestCase):
    def probe(self, initial_health, role='t', mutate=None):
        with tempfile.TemporaryDirectory() as directory:
            instance = drill.Disposable(directory)
            native = 'sha256:' + 'a' * 64
            image = {'Id': native, 'RepoDigests': [instance.db_reference]}
            item = {'Name': '/' + instance.db, 'Image': native,
                    'Config': {'Labels': {'io.sbarbase.owner': instance.owner}},
                    'State': {'Running': True, 'OOMKilled': False, 'Paused': False,
                              'Restarting': False, 'Health': {'Status': initial_health}}}
            if mutate:
                mutate(item)
            executed = []
            def docker(*args, **kwargs):
                if args[:2] == ('image', 'inspect'):
                    return subprocess.CompletedProcess([], 0, json.dumps([image]), '')
                if args[:2] == ('container', 'inspect'):
                    return subprocess.CompletedProcess([], 0, json.dumps([item]), '')
                executed.append(args)
                return subprocess.CompletedProcess([], 0, role + '\n', '')
            with patch.object(instance, 'docker', side_effect=docker), \
                    patch.object(drill.time, 'monotonic', side_effect=[0, 1, 181]), \
                    patch.object(drill.time, 'sleep'):
                if initial_health == 'healthy' and role == 't' and mutate is None:
                    instance.ready()
                else:
                    with self.assertRaises(drill.Refusal):
                        instance.ready()
            return executed

    def test_initialization_sql_cannot_admit_native_starting_or_unhealthy(self):
        for state in ('starting', 'unhealthy'):
            with self.subTest(state=state):
                self.assertEqual(self.probe(state), [])

    def test_native_healthy_and_expected_privileged_role_admit_final_server(self):
        commands = self.probe('healthy')
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], 'exec')

    def test_native_healthy_without_expected_role_is_not_ready(self):
        self.assertEqual(len(self.probe('healthy', role='f')), 1)

    def test_foreign_stopped_or_oom_database_cannot_pass_readiness(self):
        for mutation in (lambda item: item['Config']['Labels'].update({'io.sbarbase.owner': 'foreign'}),
                         lambda item: item.update({'Image': 'sha256:' + 'b' * 64}),
                         lambda item: item['State'].update({'Running': False}),
                         lambda item: item['State'].update({'OOMKilled': True})):
            self.assertEqual(self.probe('healthy', mutate=mutation), [])


if __name__ == '__main__':
    unittest.main()
