"""Local daemon policy refuses endpoint, data and mount ambiguities."""
import unittest
from unittest.mock import patch
import docker_profile as profile


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.env = {'SBARBASE_DOCKER_PROFILE': 'local-v1', 'SBARBASE_CONTAINER': '1', 'DOCKER_HOST': 'unix:///var/run/docker.sock',
                    'SBARBASE_COMPOSE_PROJECT': 'fixture'}
        self.profile = profile.from_environment(self.env)
        self.info = {'ID': 'daemon-a', 'OSType': 'linux', 'DockerRootDir': '/var/lib/docker',
                     'OperatingSystem': 'Ubuntu', 'SecurityOptions': []}

    def test_paths_with_spaces_and_normalization(self):
        value = profile.from_environment(dict(self.env, SBARBASE_DOCKER_DATA_ROOT='/srv/docker data/../docker data/',
                                              SBARBASE_DOCKER_SOCKET='/run/custom socket.sock'))
        self.assertEqual(value.data_root, '/srv/docker data')
        self.assertEqual(value.socket, '/run/custom socket.sock')
        for value in ('relative', '/', '/srv/../', '/srv/\nroot'):
            with self.subTest(value=value), self.assertRaises(profile.ProfileError):
                profile.from_environment(dict(self.env, SBARBASE_DOCKER_DATA_ROOT=value))

    def test_context_and_remote_endpoint_refuse(self):
        for change in ({'DOCKER_CONTEXT': 'other'}, {'DOCKER_HOST': 'tcp://localhost:2375'},
                       {'DOCKER_TLS_VERIFY': '1'}, {'DOCKER_HOST': 'unix:///run/other.sock'}):
            with self.subTest(change=change), self.assertRaises(profile.ProfileError):
                profile.from_environment(dict(self.env, **change))

    def test_daemon_root_identity_and_supported_profile(self):
        self.assertEqual(profile.validate_daemon(self.profile, self.info), 'daemon-a')
        for change in ({'DockerRootDir': '/different'}, {'ID': ''}, {'OSType': 'windows'},
                       {'OperatingSystem': 'Docker Desktop'}, {'SecurityOptions': ['name=rootless']}):
            with self.subTest(change=change), self.assertRaises(profile.ProfileError):
                profile.validate_daemon(self.profile, dict(self.info, **change))

    def mounts(self):
        return {'Id': 'a' * 64, 'State': {'Running': True},
                'Config': {'Labels': {'com.docker.compose.project': 'fixture', 'com.docker.compose.service': 'sbarbase'}},
                'Mounts': [
            {'Type': 'bind', 'Source': '/var/lib/docker', 'Destination': '/var/lib/docker', 'RW': False},
            {'Type': 'bind', 'Source': '/var/run/docker.sock', 'Destination': '/var/run/docker.sock', 'RW': True}]}

    def test_mounts_must_be_exact_readonly_root_and_local_socket(self):
        profile.controller_mounts(self.profile, self.mounts(), 'a' * 64)
        for field, value in (('RW', True), ('Type', 'volume'), ('Source', '/other')):
            info = self.mounts(); info['Mounts'][0][field] = value
            with self.subTest(field=field), self.assertRaises(profile.ProfileError):
                profile.controller_mounts(self.profile, info, 'a' * 64)
        info = self.mounts(); info['Mounts'].pop()
        with self.assertRaises(profile.ProfileError): profile.controller_mounts(self.profile, info, 'a' * 64)

    def test_controller_identity_is_exact_and_hostname_independent(self):
        info = self.mounts()
        info['Config']['Hostname'] = 'custom-name'
        profile.controller_mounts(self.profile, info, 'a' * 64)
        for key, value in (('Id', 'b' * 64), ('State', {'Running': False}), ('Config', {'Labels': {}})):
            with self.subTest(key=key), self.assertRaises(profile.ProfileError):
                profile.controller_mounts(self.profile, dict(info, **{key: value}), 'a' * 64)

    def test_host_cgroup_identity_formats_and_private_namespace_refusal(self):
        identity = 'a' * 64
        for text in ('0::/system.slice/docker-' + identity + '.scope',
                     '4:cpu:/docker/' + identity + '\n5:memory:/docker/' + identity,
                     '0::/docker/' + identity + '/child'):
            self.assertEqual(profile.controller_id(text), identity)
        for text in ('0::/', 'invalid', '0::/docker/' + identity + '/docker/' + 'b' * 64, '0::/docker/' + identity + '\n4:cpu:/docker/' + 'b' * 64):
            with self.subTest(text=text), self.assertRaises(profile.ProfileError):
                profile.controller_id(text)

    def test_configured_native_endpoint_is_local_and_matches_socket(self):
        for env in ({'SBARBASE_DOCKER_PROFILE': 'bad'},
                    {'SBARBASE_DOCKER_PROFILE': 'local-v1', 'DOCKER_CONTEXT': 'remote'},
                    {'SBARBASE_DOCKER_SOCKET': '/run/custom.sock'},
                    {'SBARBASE_DOCKER_DATA_ROOT': '/srv/docker', 'DOCKER_HOST': 'ssh://remote'}):
            with self.subTest(env=env), self.assertRaises(profile.ProfileError):
                profile.from_environment(env)
        value = profile.from_environment({'SBARBASE_DOCKER_SOCKET': '/run/custom.sock',
                                          'DOCKER_HOST': 'unix:///run/custom.sock'})
        self.assertFalse(value.container)

    def test_validation_refuses_missing_root_before_device_consumption(self):
        with patch.object(profile, 'docker_json', side_effect=[self.info, [self.mounts()]]), \
                patch.object(profile, 'controller_id', return_value='a' * 64), \
                patch.object(profile, 'controller_mounts'), \
                patch.object(profile.Path, 'is_dir', return_value=False):
            with self.assertRaisesRegex(profile.ProfileError, 'docker_data_root_unavailable'):
                profile.validated_identity(self.profile)

    def test_configured_predicate_and_container_marker_contract(self):
        self.assertFalse(profile.configured({}))
        for env in ({'SBARBASE_DOCKER_PROFILE': 'local-v1'}, {'SBARBASE_CONTAINER': '1'},
                    {'SBARBASE_DOCKER_DATA_ROOT': '/srv/data'}, {'SBARBASE_DOCKER_SOCKET': '/run/socket'}):
            self.assertTrue(profile.configured(env))
        for env in ({'SBARBASE_CONTAINER': '1', 'DOCKER_HOST': profile.CONTROLLER_ENDPOINT},
                    {'SBARBASE_CONTAINER': 'true'}, {'SBARBASE_CONTAINER': '0'}):
            with self.subTest(env=env), self.assertRaises(profile.ProfileError):
                profile.from_environment(env)

    def test_malformed_daemon_and_inspection_objects_are_named_refusals(self):
        for info in ([], None, 'value', dict(self.info, SecurityOptions=None), dict(self.info, SecurityOptions='rootless'), dict(self.info, SecurityOptions=[None])):
            with self.subTest(info=info), self.assertRaises(profile.ProfileError):
                profile.validate_daemon(self.profile, info)
        for info in ([], None, 'value', {'Config': []}, {'Config': {'Labels': []}},
                     dict(self.mounts(), Mounts={}), dict(self.mounts(), Mounts=[None]),
                     dict(self.mounts(), State='running')):
            with self.subTest(info=info), self.assertRaises(profile.ProfileError):
                profile.controller_mounts(self.profile, info, 'a' * 64)


class RuntimeProfileTests(unittest.TestCase):
    def write_runtime(self, text):
        import tempfile
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = profile.Path(directory.name)
        (root / 'lab').mkdir()
        (root / 'lab' / 'resource_policy.py').write_text(text)
        return root

    def runtime(self, root=profile.DEFAULT_ROOT):
        return profile.DockerProfile(root, profile.DEFAULT_SOCKET, True, 'fixture')

    def test_default_root_allows_valid_historical_runtime(self):
        profile.runtime_profile(self.runtime(), self.write_runtime("VOLUME_ROOT = '/var/lib/docker'\n"))

    def test_custom_root_refuses_older_or_malformed_runtime(self):
        for text in ("VOLUME_ROOT = '/var/lib/docker'\n", 'def malformed(:\n',
                     "DOCKER_PROFILE_VERSION = 'other'\n", "DOCKER_PROFILE_VERSION = compute()\n",
                     "def helper():\n    DOCKER_PROFILE_VERSION = 'local-v1'\n"):
            with self.subTest(text=text), self.assertRaisesRegex(profile.ProfileError, 'unsupported_runtime_profile'):
                profile.runtime_profile(self.runtime('/srv/custom root'), self.write_runtime(text))

    def test_marker_is_literal_and_checkout_side_effects_are_never_run(self):
        root = self.write_runtime("raise RuntimeError('must never import')\nDOCKER_PROFILE_VERSION = 'local-v1'\n")
        profile.runtime_profile(self.runtime('/srv/custom root'), root)

    def test_reassigned_marker_refuses(self):
        root = self.write_runtime("DOCKER_PROFILE_VERSION = 'local-v1'\nDOCKER_PROFILE_VERSION = 'other'\n")
        with self.assertRaisesRegex(profile.ProfileError, 'unsupported_runtime_profile'):
            profile.runtime_profile(self.runtime('/srv/custom root'), root)

    def test_default_root_still_refuses_malformed_runtime(self):
        with self.assertRaisesRegex(profile.ProfileError, 'unsupported_runtime_profile'):
            profile.runtime_profile(self.runtime(), self.write_runtime('def malformed(:\n'))

    def test_cli_modes_keep_daemon_validation_separate_from_ast_read(self):
        root = self.write_runtime("DOCKER_PROFILE_VERSION = 'local-v1'\n")
        selected = self.runtime('/srv/custom root')
        with patch.object(profile, 'from_environment', return_value=selected), \
                patch.object(profile, 'validated_identity') as daemon:
            self.assertEqual(profile.main(['runtime', str(root)]), 0)
            daemon.assert_not_called()
            self.assertEqual(profile.main(['check']), 0)
            daemon.assert_called_once_with(selected)
