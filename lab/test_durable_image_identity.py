"""Durable image proof precedes lifecycle effects and preserves logical pins."""
from contextlib import ExitStack
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from host_test_fixture import IsolatedHostCase
from unittest.mock import Mock, patch
import durable_runtime as runtime
import image_identity

INDEX = 'sha256:' + 'a' * 64
CONFIG = 'sha256:' + 'b' * 64
REF = 'docker.io/postgrest/postgrest@' + INDEX
PIN = {'id': INDEX, 'tag': 'postgrest/postgrest:v1', 'digests': [REF]}


class DurableIdentityTests(IsolatedHostCase):
    def setUp(self):
        super().setUp()
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.state = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(runtime, 'PRIVATE', self.state))
        self.stack.enter_context(patch.object(runtime, 'UPGRADE_INTENT', self.state / 'intent.json'))
        self.stack.enter_context(patch.object(runtime.resource_policy, 'io_flags', return_value=[]))
        self.source = runtime.Runtime.__new__(runtime.Runtime)
        self.source.pins = {'rest': dict(PIN), 'db': dict(PIN)}
        self.record = {'Id': CONFIG, 'RepoDigests': [REF]}
        self.actual = {'Id': 'owned-container', 'Image': CONFIG, 'Config': {'Env': ['A=1']},
                       'Mounts': [], 'NetworkSettings': {'Networks': {runtime.NETWORK: {}}}}
        self.calls = []
        self.failure = None

    def docker(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:2] == ('image', 'inspect'):
            if self.failure is not None: return self.failure
            return SimpleNamespace(returncode=0, stdout=json.dumps([self.record]), stderr='')
        return SimpleNamespace(returncode=0, stdout='created-container\n', stderr='')

    def launch(self, component='rest', volumes=()):
        with patch.object(runtime.lab, 'docker', self.docker), patch.object(runtime, 'inspect', return_value=self.actual):
            return self.source.launch('owned', component, {'A': '1'}, '256m', .25,
                                      tier='production', volumes=volumes)

    def test_index_resolves_to_distinct_config_identity_for_retained_start(self):
        self.assertEqual(self.launch(), ('owned-container', False))
        self.assertEqual(self.calls[0], (('image', 'inspect', REF), {'check': False}))
        self.assertEqual(self.calls[-1][0], ('start', 'owned-container'))

    def test_creation_uses_exact_repository_and_forbids_pull(self):
        self.actual = None
        self.assertEqual(self.launch(), ('created-container', True))
        self.assertEqual(self.calls[-1][0][-1], REF)
        self.assertIn('--pull=never', self.calls[-1][0])

    def test_proof_failures_precede_resource_inspection_and_all_mutation(self):
        variants = [{}, {'Id': CONFIG, 'RepoDigests': []},
                    {'Id': CONFIG, 'RepoDigests': ['elsewhere/image@' + INDEX]},
                    {'Id': 'sha256:short', 'RepoDigests': [REF]}]
        for record in variants:
            with self.subTest(record=record):
                self.calls = []; self.record = record
                with patch.object(runtime, 'inspect') as lookup, patch.object(runtime.lab, 'docker', self.docker), patch.object(runtime.lab, 'secure_file') as write:
                    with self.assertRaises(image_identity.IdentityError):
                        self.source.launch('owned', 'rest', {'A': '1'}, '256m', .25, tier='production')
                    lookup.assert_not_called(); write.assert_not_called()
                self.assertEqual(len(self.calls), 1)

    def test_daemon_failure_is_retained_and_never_pulls(self):
        self.failure = SimpleNamespace(returncode=1, stdout='[]', stderr='cannot connect to daemon')
        with self.assertRaisesRegex(image_identity.IdentityError, 'cannot connect to daemon'): self.launch()
        self.assertEqual(len(self.calls), 1)

    def test_incomplete_logical_pin_never_inspects_or_mutates(self):
        self.source.pins['rest'] = {'id': INDEX}
        with self.assertRaises(image_identity.IdentityError): self.launch()
        self.assertEqual(self.calls, [])

    def test_config_identity_cannot_authorize_logical_upgrade(self):
        self.actual['Image'] = 'sha256:' + 'c' * 64
        runtime.UPGRADE_INTENT.write_text(json.dumps({'pins': {'rest': CONFIG}}))
        with self.assertRaisesRegex(RuntimeError, 'explicit reconciliation'): self.launch()
        self.assertEqual(len(self.calls), 1)

    def test_logical_upgrade_allows_stateless_replacement_with_exact_reference(self):
        self.actual['Image'] = 'sha256:' + 'c' * 64
        runtime.UPGRADE_INTENT.write_text(json.dumps({'pins': {'rest': INDEX}}))
        self.assertEqual(self.launch(), ('created-container', True))
        self.assertIn((('rm', '-f', 'owned-container'), {}), self.calls)
        self.assertEqual(self.calls[-1][0][-1], REF)

    def test_database_is_never_authorized_by_stateless_upgrade_intent(self):
        self.actual['Image'] = 'sha256:' + 'c' * 64
        runtime.UPGRADE_INTENT.write_text(json.dumps({'pins': {'db': INDEX}}))
        with self.assertRaisesRegex(RuntimeError, 'explicit reconciliation'): self.launch('db')
        self.assertEqual(len(self.calls), 1)

    def test_volume_and_network_guards_remain_required(self):
        with self.assertRaisesRegex(RuntimeError, 'volume mismatch'): self.launch(volumes=[('data', '/data')])
        self.actual['NetworkSettings']['Networks'] = {}
        with self.assertRaisesRegex(RuntimeError, 'network mismatch'): self.launch()
        self.assertTrue(all(args[0] == 'image' for args, kwargs in self.calls))

    def test_hba_receives_daemon_id_and_existing_authority_is_not_rewritten(self):
        startup = Mock(); writer = Mock()
        private = self.state / '.secrets' / 'upstream'
        private.parent.mkdir(mode=0o700); private.mkdir(mode=0o700)
        (private / 'runtime.json').write_text(json.dumps({'management': {}}))
        with patch.object(runtime, 'PRIVATE', private), patch.object(runtime, 'STATE', self.state), \
             patch.object(runtime.lab, 'ROOT', self.state), \
             patch.object(runtime.lab, 'docker', self.docker), \
             patch('subprocess.run', return_value=SimpleNamespace(returncode=0)), \
             patch.object(runtime.hba_runtime, 'SourceHBA', return_value=writer) as hba, \
             patch.object(runtime, 'inspect', return_value={'Id': 'owned'}), patch.object(runtime, 'atomic') as write:
            # The aggregate lock and individual locks have different structures.
            values = [{'rest': PIN}, PIN, PIN, PIN, PIN, {'management': {}}]
            with patch.object(Path, 'read_text', side_effect=[json.dumps(v) for v in values]):
                source = runtime.Runtime(startup=startup)
            self.assertEqual(hba.call_args.args[4], CONFIG)
            writer.before_start.assert_called_once()
            write.assert_not_called()
            self.assertIs(source.hba_writer, writer)


if __name__ == '__main__': unittest.main()
