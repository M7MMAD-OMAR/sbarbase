"""Studio preparation proves all images before rebuilding a credential session."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import studio
import image_identity

INDEX = 'sha256:' + 'a' * 64
CONFIG = 'sha256:' + 'b' * 64
REF = 'docker.io/example/studio@' + INDEX
META = 'docker.io/example/meta@' + INDEX
PIN = {'id': INDEX, 'tag': 'example/studio:v1', 'digests': [REF]}
PINS = {'studio': PIN, 'meta': {'id': INDEX, 'tag': 'example/meta:v1', 'digests': [META]}}


def result(stdout='', stderr='', code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def proof(reference):
    return result(json.dumps([{'Id': CONFIG, 'RepoDigests': [reference]}]))


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.lock = copy.deepcopy(PINS)
        self.responses = {}
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(studio, 'pins', side_effect=lambda: self.lock))
        self.stack.enter_context(patch.object(studio.lab, 'docker', side_effect=self.docker))

    def docker(self, *args, **options):
        self.calls.append(args)
        if args[:2] == ('image', 'inspect'):
            queue = self.responses.get(args[2], [])
            return queue.pop(0) if queue else proof(args[2])
        return result()

    def test_returns_frozen_qualified_references(self):
        self.assertEqual(studio.ensure_images(), {'studio': REF, 'meta': META})
        self.assertEqual(self.calls, [('image', 'inspect', REF), ('image', 'inspect', META)] * 2)

    def test_all_lock_entries_validate_before_any_inventory(self):
        self.lock['meta']['digests'] = ['elsewhere/meta@' + INDEX]
        with self.assertRaises(image_identity.IdentityError): studio.ensure_images()
        self.assertEqual(self.calls, [])

    def test_missing_or_extra_components_refuse_before_inventory(self):
        for lock in ({'studio': PIN}, {**PINS, 'extra': PIN}, []):
            self.lock = lock
            with self.assertRaises(studio.StudioError): studio.ensure_images()
        self.assertEqual(self.calls, [])

    def test_exact_absence_pulls_then_proves_native_identity(self):
        self.responses[REF] = [result('[]', 'Error response from daemon: No such image: '+REF+'\n', 1)]
        self.assertEqual(studio.ensure_images()['studio'], REF)
        self.assertEqual(self.calls, [('image', 'inspect', REF), ('image', 'inspect', META),
                                     ('pull', REF), ('image', 'inspect', REF), ('image', 'inspect', META)])

    def test_later_daemon_failure_prevents_accumulated_pulls(self):
        self.responses[REF] = [result('[]', 'Error response from daemon: No such image: '+REF, 1)]
        self.responses[META] = [result('[]', 'permission denied accessing daemon', 1)]
        with self.assertRaisesRegex(image_identity.IdentityError, 'permission denied'): studio.ensure_images()
        self.assertFalse(any(args[0] == 'pull' for args in self.calls))

    def test_absence_of_other_reference_is_never_pull_authority(self):
        self.responses[REF] = [result('[]', 'Error response from daemon: No such image: '+META, 1)]
        with self.assertRaises(image_identity.IdentityError): studio.ensure_images()
        self.assertEqual(self.calls, [('image', 'inspect', REF)])

    def test_success_without_repository_proof_refuses(self):
        self.responses[REF] = [result(json.dumps([{'Id': CONFIG, 'RepoDigests': [META]}]))]
        with self.assertRaises(image_identity.IdentityError): studio.ensure_images()
        self.assertEqual(self.calls, [('image', 'inspect', REF)])

    def test_post_pull_wrong_image_refuses(self):
        self.responses[REF] = [result('[]', 'Error response from daemon: No such image: '+REF, 1),
                               result(json.dumps([{'Id': CONFIG, 'RepoDigests': [META]}]))]
        with self.assertRaises(image_identity.IdentityError): studio.ensure_images()
        self.assertIn(('pull', REF), self.calls)

    def test_pull_failure_is_retained(self):
        self.responses[REF] = [result('[]', 'Error response from daemon: No such image: '+REF, 1)]
        def docker(*args, **options):
            if args[0] == 'pull': return result('', 'registry refused request', 1)
            return self.docker(*args, **options)
        with patch.object(studio.lab, 'docker', side_effect=docker):
            with self.assertRaisesRegex(studio.StudioError, 'registry refused'): studio.ensure_images()


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(studio.runtime, 'PRIVATE', self.directory))
        self.stack.enter_context(patch.object(studio.resource_policy, 'io_flags', return_value=[]))
        self.calls = []
        self.image = proof(REF)
        self.actual = {'Image': CONFIG, 'Config': {'Labels': {'io.sbarbase.owner': studio.runtime.OWNER}},
                       'NetworkSettings': {'Networks': {studio.runtime.NETWORK: {'IPAddress': '10.0.0.2'}}}}
        self.stack.enter_context(patch.object(studio.lab, 'docker', side_effect=self.docker))
        self.stack.enter_context(patch.object(studio.runtime, 'inspect', side_effect=lambda *args: self.actual))

    def docker(self, *args, **options):
        self.calls.append(args)
        return self.image if args[:2] == ('image', 'inspect') else result('created')

    def launch(self, image=REF):
        return studio.launch('owned-studio', 'operator.meta', {'SESSION': 'temporary'}, image)

    def test_exact_reference_resolves_config_id_and_forbids_implicit_pull(self):
        self.assertEqual(self.launch(), '10.0.0.2')
        self.assertEqual(self.calls[0], ('image', 'inspect', REF))
        self.assertIn('--pull=never', self.calls[1])
        self.assertEqual(self.calls[1][-1], REF)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_bare_identity_refuses_before_inventory_or_secret_file(self):
        with self.assertRaises(image_identity.IdentityError): self.launch(INDEX)
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_failed_image_proof_refuses_before_creation_or_secret_file(self):
        self.image = result('[]', 'daemon unavailable', 1)
        with self.assertRaisesRegex(image_identity.IdentityError, 'daemon unavailable'): self.launch()
        self.assertEqual(self.calls, [('image', 'inspect', REF)])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_created_image_drift_refuses_endpoint(self):
        self.actual['Image'] = INDEX
        with self.assertRaisesRegex(studio.StudioError, 'identity or ownership'): self.launch()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_created_owner_drift_refuses_endpoint(self):
        self.actual['Config']['Labels']['io.sbarbase.owner'] = 'someone-else'
        with self.assertRaisesRegex(studio.StudioError, 'identity or ownership'): self.launch()

    def test_missing_created_container_refuses_endpoint(self):
        self.actual = None
        with self.assertRaisesRegex(studio.StudioError, 'identity or ownership'): self.launch()

    def test_created_network_address_remains_required(self):
        self.actual['NetworkSettings']['Networks'][studio.runtime.NETWORK]['IPAddress'] = ''
        with self.assertRaisesRegex(studio.StudioError, 'no address'): self.launch()
