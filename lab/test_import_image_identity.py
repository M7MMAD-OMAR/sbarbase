"""Import tool proof must authorize only the immutable image and exact owned CID."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import image_identity
import import_project as module

PIN = {'tag': 'supabase/postgres:17.6.1.166', 'id': 'sha256:' + 'a' * 64,
       'digests': ['supabase/postgres@sha256:' + 'a' * 64]}
REF = 'docker.io/supabase/postgres@sha256:' + 'a' * 64
NATIVE = 'sha256:' + 'b' * 64
CID = 'c' * 64
OLD = 'd' * 64
URL = 'postgresql://reader:synthetic@localhost:5432/postgres'


def result(output='', error='', code=0):
    return subprocess.CompletedProcess([], code, output, error)


def container(cid=CID, owner=None, image=NATIVE):
    return {'Id': cid, 'Name': '/' + module.TOOL, 'Image': image,
            'Config': {'Labels': {'io.sbarbase.owner': owner or module.runtime.OWNER}}}


class ImportIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / 'lab').mkdir()
        self.lock = root / 'lab' / 'distro-image.lock.json'
        self.lock.write_text(json.dumps(PIN))
        self.calls = []
        self.image = result(json.dumps([{'Id': NATIVE, 'RepoDigests': [REF]}]))
        self.retained = None
        self.created = container()
        self.create_output = CID + '\n'
        self.inspect_error = None
        self.missing_created = False
        self.network = patch.object(module.runtime, 'inspect', return_value={'Labels': {}}).start()
        self.addCleanup(patch.stopall)
        patch.object(module.lab, 'ROOT', root).start()
        patch.object(module.lab, 'docker', side_effect=self.docker).start()
        self.mkstemp = patch.object(module.tempfile, 'mkstemp', wraps=tempfile.mkstemp).start()

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('image', 'inspect'):
            self.assertEqual(args[2], REF)
            return self.image
        if args[0] == 'inspect':
            if self.inspect_error is not None:
                return self.inspect_error
            item = self.retained if args[1] == module.TOOL else self.created
            if item is None or (args[1] != module.TOOL and self.missing_created):
                return result('[]\n', f'Error: No such object: {args[1]}\n', 1)
            return result(json.dumps([item]))
        if args[0] == 'run':
            return result(self.create_output)
        if args[0] == 'rm':
            return result()
        raise AssertionError('Unexpected Docker effect: ' + repr(args))

    def source(self, url=URL):
        source = module.Source(url)
        self.addCleanup(source.close)
        return source

    def no_effects(self):
        self.assertFalse(any(call[0] in ('run', 'rm', 'pull', 'network') for call in self.calls))
        self.mkstemp.assert_not_called()
        self.network.assert_not_called()

    def test_malformed_lock_refuses_before_docker(self):
        self.lock.write_text(json.dumps({'id': PIN['id']}))
        with self.assertRaises(image_identity.IdentityError):
            module.Source(URL)
        self.assertEqual(self.calls, [])
        self.no_effects()

    def test_inconsistent_repository_lock_refuses_before_docker(self):
        self.lock.write_text(json.dumps({**PIN, 'digests': ['other/postgres@' + PIN['id']]}))
        with self.assertRaises(image_identity.IdentityError):
            module.Source(URL)
        self.assertEqual(self.calls, [])
        self.no_effects()

    def test_native_missing_never_pulls_or_changes_resources(self):
        self.image = result('[]\n', f'Error response from daemon: No such image: {REF}\n', 1)
        with self.assertRaises(image_identity.IdentityError):
            module.Source(URL)
        self.no_effects()

    def test_permission_error_never_pulls_or_changes_resources(self):
        self.image = result('[]\n', 'permission denied', 1)
        with self.assertRaisesRegex(image_identity.IdentityError, 'permission denied'):
            module.Source(URL)
        self.no_effects()

    def test_successful_image_inspect_with_diagnostic_refuses_before_effects(self):
        self.image.stderr = 'WARNING: native inspection diagnostic\n'
        with self.assertRaises(image_identity.IdentityError):
            module.Source(URL)
        self.no_effects()

    def test_successful_retained_inspect_with_diagnostic_refuses_before_effects(self):
        self.inspect_error = result(json.dumps([container(OLD)]), 'WARNING: native diagnostic\n')
        with self.assertRaisesRegex(module.ImportError_, 'diagnostic'):
            module.Source(URL)
        self.no_effects()

    def test_same_digest_wrong_repository_refuses_before_effects(self):
        self.image = result(json.dumps([{'Id': NATIVE, 'RepoDigests': ['other/postgres@' + PIN['id']]}]))
        with self.assertRaises(image_identity.IdentityError):
            module.Source(URL)
        self.no_effects()

    def test_ambiguous_image_records_refuse_before_effects(self):
        self.image = result(json.dumps([{'Id': NATIVE, 'RepoDigests': [REF]}] * 2))
        with self.assertRaises(image_identity.IdentityError):
            module.Source(URL)
        self.no_effects()

    def test_foreign_retained_container_is_not_removed(self):
        self.retained = container(OLD, owner='somebody-else')
        with self.assertRaisesRegex(module.ImportError_, 'ownership collision'):
            module.Source(URL)
        self.no_effects()

    def test_unowned_retained_container_is_not_removed(self):
        self.retained = container(OLD)
        self.retained['Config']['Labels'] = None
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.no_effects()

    def test_wrong_image_retained_container_is_not_removed(self):
        self.retained = container(OLD, image=PIN['id'])
        with self.assertRaisesRegex(module.ImportError_, 'image identity mismatch'):
            module.Source(URL)
        self.no_effects()

    def test_wrong_name_retained_proof_is_not_removed(self):
        self.retained = container(OLD)
        self.retained['Name'] = '/different-tool'
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.no_effects()

    def test_incomplete_retained_identity_is_not_removed(self):
        self.retained = container('c' * 12)
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.no_effects()

    def test_unknown_container_error_does_not_authorize_creation(self):
        self.inspect_error = result('[]', 'permission denied', 1)
        with self.assertRaisesRegex(module.ImportError_, 'inspection unavailable'):
            module.Source(URL)
        self.no_effects()

    def test_observed_lowercase_native_absence_allows_exact_tool_creation(self):
        original_docker = self.docker
        def native_lowercase(*args, **kwargs):
            if args == ('inspect', module.TOOL):
                self.calls.append(args)
                return result('[]\n', f'error: no such object: {module.TOOL}\n', 1)
            return original_docker(*args, **kwargs)
        module.lab.docker.side_effect = native_lowercase
        self.assertEqual(self.source().container_id, CID)

    def test_lowercase_absence_wrong_reference_is_not_absence(self):
        self.inspect_error = result('[]\n', 'error: no such object: different-tool\n', 1)
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.no_effects()

    def test_lowercase_absence_with_extra_diagnostic_is_not_absence(self):
        self.inspect_error = result('[]\n', f'error: no such object: {module.TOOL}\npermission denied\n', 1)
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.no_effects()

    def test_lowercase_absence_with_nonempty_json_is_not_absence(self):
        self.inspect_error = result('[{}]\n', f'error: no such object: {module.TOOL}\n', 1)
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.no_effects()

    def test_owned_retained_native_image_is_removed_by_cid(self):
        self.retained = container(OLD)
        self.source()
        self.assertIn(('rm', '-f', OLD), self.calls)
        self.assertNotIn(('rm', '-f', module.TOOL), self.calls)

    def test_qualified_launch_never_pulls_and_exec_uses_cid(self):
        source = self.source()
        argv = next(call for call in self.calls if call[0] == 'run')
        self.assertIn('--pull=never', argv)
        self.assertEqual(argv[-2:], (REF, 'infinity'))
        self.assertEqual(source.image_id, NATIVE)
        self.assertIn(CID, source.command('psql'))
        self.assertNotIn(module.TOOL, source.command('psql'))
        self.assertEqual(self.calls[0], ('image', 'inspect', REF))

    def test_read_only_environment_and_private_mode_preserved(self):
        source = self.source()
        data = Path(source.env_file).read_text()
        self.assertIn('PGOPTIONS=-c default_transaction_read_only=on\n', data)
        self.assertIn('PGPASSWORD=synthetic\n', data)
        self.assertEqual(os.stat(source.env_file).st_mode & 0o777, 0o600)
        self.assertNotIn('synthetic', ' '.join(source.command('psql')))

    def test_tool_has_finite_portable_limits_without_host_io_inference(self):
        self.source()
        argv = next(call for call in self.calls if call[0] == 'run')
        for flag, value in (('--memory', '512m'), ('--memory-swap', '512m'),
                            ('--cpus', '1'), ('--pids-limit', '128')):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertIn('--cap-drop=ALL', argv)
        self.assertIn('--security-opt=no-new-privileges', argv)
        self.assertFalse(any(arg.startswith('--device') for arg in argv))

    def test_wrong_created_native_identity_does_not_write_credentials(self):
        self.created['Image'] = PIN['id']
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.mkstemp.assert_not_called()
        self.assertFalse(any(call[0] == 'rm' for call in self.calls))

    def test_wrong_created_cid_does_not_write_credentials(self):
        self.created['Id'] = OLD
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.mkstemp.assert_not_called()

    def test_creation_response_requires_complete_cid(self):
        self.create_output = CID[:12]
        with self.assertRaises(module.ImportError_):
            module.Source(URL)
        self.mkstemp.assert_not_called()

    def test_close_uses_created_cid_despite_name_replacement(self):
        source = self.source()
        path = Path(source.env_file)
        self.retained = container(OLD, owner='replacement-owner')
        source.close()
        self.assertEqual(self.calls[-1], ('rm', '-f', CID))
        self.assertFalse(path.exists())
        with self.assertRaises(module.ImportError_):
            source.command('psql')

    def test_close_refuses_changed_owner_and_still_removes_credentials(self):
        source = self.source()
        path = Path(source.env_file)
        self.created['Config']['Labels']['io.sbarbase.owner'] = 'foreign'
        with self.assertRaises(module.ImportError_):
            source.close()
        self.assertFalse(path.exists())
        self.assertFalse(any(call == ('rm', '-f', CID) for call in self.calls))
        self.created['Config']['Labels']['io.sbarbase.owner'] = module.runtime.OWNER

    def test_auto_removed_tool_cleanup_does_not_touch_replacement_name(self):
        source = self.source()
        path = Path(source.env_file)
        self.missing_created = True
        source.close()
        self.assertFalse(path.exists())
        self.assertFalse(any(call[0] == 'rm' for call in self.calls))

    def test_environment_creation_failure_cleans_exact_created_tool(self):
        self.mkstemp.side_effect = OSError('synthetic env failure')
        with self.assertRaisesRegex(OSError, 'synthetic env failure'):
            module.Source(URL)
        self.assertEqual(self.calls[-1], ('rm', '-f', CID))


if __name__ == '__main__':
    unittest.main()
