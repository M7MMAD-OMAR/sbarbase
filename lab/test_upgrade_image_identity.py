"""Historical upgrade images require exact immutable proof before lifecycle effects."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import upgrade

INDEX = 'sha256:' + 'a' * 64
CONFIG = 'sha256:' + 'b' * 64
REF = 'docker.io/example/rest@' + INDEX
OTHER = 'docker.io/example/auth@' + INDEX
PIN = {'id': INDEX, 'tag': 'example/rest:v1', 'digests': [REF]}


def result(stdout='', stderr='', code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def proof(ref, identity=CONFIG):
    return result(json.dumps([{'Id': identity, 'RepoDigests': [ref]}]))


class ImageAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.locks = {'first': copy.deepcopy(PIN), 'second': {'auth': {'id': INDEX, 'tag': 'example/auth:v1', 'digests': [OTHER]}}}
        self.calls = []
        self.pulled = []
        self.responses = {}
        self.stack.enter_context(patch.object(upgrade.install_server, 'LOCKS', ('first', 'second', 'optional')))
        self.stack.enter_context(patch.object(upgrade, 'historical_lock_at', side_effect=lambda commit, name: self.locks.get(name)))
        self.stack.enter_context(patch.object(upgrade.lab, 'docker', side_effect=self.docker))
        self.stack.enter_context(patch.object(upgrade.install_server, 'pull_image', side_effect=lambda *args: self.pulled.append(args)))

    def docker(self, *args, **options):
        self.calls.append(args)
        self.assertEqual(args[:2], ('image', 'inspect'))
        self.assertEqual(options, {'check': False})
        queue = self.responses.get(args[2], [])
        return queue.pop(0) if queue else proof(args[2])

    def absent(self, ref):
        return result('[]', 'Error response from daemon: No such image: '+ref+'\n', 1)

    def test_canonical_references_preserve_declared_logical_ids(self):
        self.assertEqual(upgrade.images_at('history'), [('first:default', INDEX, REF), ('second:auth', INDEX, OTHER)])
        self.assertEqual(self.calls, [])

    def test_all_locks_validate_before_inventory(self):
        self.locks['second']['auth']['digests'] = ['unrelated/auth@'+INDEX]
        with self.assertRaisesRegex(upgrade.UpgradeError, 'second:auth'): upgrade.pull('history')
        self.assertEqual(self.calls, []); self.assertEqual(self.pulled, [])

    def test_malformed_nested_entries_are_not_filtered(self):
        for value in (None, {}, [], 'bad', {'tag': 'example/auth:v1'}, {'id': INDEX}, {'id': INDEX, 'tag': 'example/auth:v1', 'digests': []}):
            with self.subTest(value=value):
                self.locks['second'] = {'auth': value}
                with self.assertRaises(upgrade.UpgradeError): upgrade.pull('history')
                self.assertEqual(self.calls, []); self.assertEqual(self.pulled, [])

    def test_invalid_present_lock_shapes_refuse(self):
        for value in ({}, [], 'bad', 3):
            with self.subTest(value=value):
                self.locks['second'] = value
                with self.assertRaises(upgrade.UpgradeError): upgrade.pull('history')
                self.assertEqual(self.calls, [])

    def test_entirely_absent_version_refuses(self):
        self.locks.clear()
        with self.assertRaisesRegex(upgrade.UpgradeError, 'no image locks'): upgrade.pull('history')
        self.assertEqual(self.calls, [])

    def test_optional_historical_feature_lock_can_be_absent(self):
        upgrade.pull('history')
        self.assertEqual(self.calls, [('image', 'inspect', REF), ('image', 'inspect', OTHER)])
        self.assertEqual(self.pulled, [])

    def test_both_store_identity_shapes_admit_exact_repository(self):
        for identity in (INDEX, CONFIG):
            with self.subTest(identity=identity):
                self.responses[REF] = [proof(REF, identity)]
                upgrade.pull('history')
        self.assertEqual(self.pulled, [])

    def test_bare_id_and_tag_never_become_pull_authority(self):
        for value in ([], [INDEX], ['example/rest:v1']):
            self.locks['first']['digests'] = value
            with self.assertRaises(upgrade.UpgradeError): upgrade.pull('history')
        self.assertEqual(self.calls, []); self.assertEqual(self.pulled, [])

    def test_exact_missing_pulls_qualified_reference_then_proves_it(self):
        self.responses[REF] = [self.absent(REF), proof(REF)]
        upgrade.pull('history')
        self.assertEqual(self.pulled, [('first:default', REF, '1')])
        self.assertEqual(self.calls, [('image', 'inspect', REF), ('image', 'inspect', OTHER), ('image', 'inspect', REF)])

    def test_later_failure_blocks_all_accumulated_pulls(self):
        for response in (result('[]', 'permission denied', 1), result('[]', 'daemon unavailable', 1), self.absent(REF),
                         result('[]', 'Error response from daemon: No such image: '+OTHER+'\npermission denied', 1),
                         proof(REF), result('bad json'), result('[]'), result(json.dumps([{'Id': 'bad', 'RepoDigests': [OTHER]}]))):
            with self.subTest(response=response):
                self.responses[REF] = [self.absent(REF)]
                self.responses[OTHER] = [response]
                with self.assertRaises(upgrade.UpgradeError): upgrade.pull('history')
                self.assertEqual(self.pulled, [])

    def test_post_pull_missing_or_wrong_repository_refuses(self):
        for response in (self.absent(REF), proof(OTHER), result('[]'), result('bad json')):
            with self.subTest(response=response):
                self.responses[REF] = [self.absent(REF), response]
                with self.assertRaises(upgrade.UpgradeError): upgrade.pull('history')
        self.assertEqual(len(self.pulled), 4)

    def test_post_pull_failure_stops_before_later_pull(self):
        self.responses[REF] = [self.absent(REF), proof(OTHER)]
        self.responses[OTHER] = [self.absent(OTHER)]
        with self.assertRaises(upgrade.UpgradeError): upgrade.pull('history')
        self.assertEqual(self.pulled, [('first:default', REF, '1')])

    def test_apply_admission_refusal_precedes_all_lifecycle_effects(self):
        details = {'target': 'target', 'current': 'current', 'refusals': []}
        self.responses[REF] = [result('[]', 'permission denied', 1)]
        with ExitStack() as stack:
            stack.enter_context(patch.object(upgrade, 'plan', return_value=details))
            stack.enter_context(patch.object(upgrade, 'report'))
            stack.enter_context(patch.object(upgrade, 'pins_at', return_value={'rest': INDEX}))
            mutations = [stack.enter_context(patch.object(upgrade, name, side_effect=AssertionError('unexpected mutation '+name)))
                         for name in ('record_outcome', 'back_up', 'snapshot', 'install_guard', 'save_state', 'checkout')]
            with self.assertRaisesRegex(upgrade.UpgradeError, 'permission denied'): upgrade.apply('target')
            for mutation in mutations: mutation.assert_not_called()

    def test_rollback_admission_refusal_precedes_state_or_checkout_effects(self):
        state = {'from': 'current', 'to': 'target', 'phase': 'confirmed', 'way_back': {'pins': {'rest': INDEX}}}
        self.responses[REF] = [result('[]', 'permission denied', 1)]
        with ExitStack() as stack:
            stack.enter_context(patch.object(upgrade, 'load_state', return_value=state))
            stack.enter_context(patch.object(upgrade, 'rollback_refusal', return_value=None))
            mutations = [stack.enter_context(patch.object(upgrade, name, side_effect=AssertionError('unexpected mutation '+name)))
                         for name in ('exclusive', 'save_state', 'move_back')]
            begin = stack.enter_context(patch.object(upgrade.upgrade_guard, 'begin_way_back', side_effect=AssertionError('unexpected guard mutation')))
            with self.assertRaisesRegex(upgrade.UpgradeError, 'permission denied'): upgrade.go_back(False)
            for mutation in mutations: mutation.assert_not_called()
            begin.assert_not_called()


    def test_pins_use_validated_logical_id_and_refuse_missing_service_key(self):
        with patch.object(upgrade, 'SERVICES', {'rest': ('first', None), 'auth': ('second', 'auth'), 'realtime': ('optional', None)}):
            self.assertEqual(upgrade.pins_at('history'), {'rest': INDEX, 'auth': INDEX})
            self.locks['second'] = {'unexpected': self.locks['second']['auth']}
            with self.assertRaisesRegex(upgrade.UpgradeError, 'no pin for auth'): upgrade.pins_at('history')
        self.assertEqual(self.calls, [])

    def test_pins_validate_nonservice_locks_too(self):
        self.locks['second']['auth']['id'] = 'malformed'
        with patch.object(upgrade, 'SERVICES', {'rest': ('first', None)}):
            with self.assertRaisesRegex(upgrade.UpgradeError, 'invalid image lock'): upgrade.pins_at('history')
        self.assertEqual(self.calls, [])

    def test_malformed_current_version_blocks_apply_before_any_effect(self):
        details = {'target': 'target', 'current': 'current', 'refusals': []}
        valid = copy.deepcopy(self.locks)
        for malformed in ('bad', 'sha256:'+'f'*63, INDEX.upper()):
            with self.subTest(malformed=malformed), ExitStack() as stack:
                def historical(commit, name):
                    value = copy.deepcopy(valid.get(name))
                    if commit == 'current' and name == 'second': value['auth']['id'] = malformed
                    return value
                stack.enter_context(patch.object(upgrade, 'historical_lock_at', side_effect=historical))
                stack.enter_context(patch.object(upgrade, 'SERVICES', {'rest': ('first', None)}))
                stack.enter_context(patch.object(upgrade, 'plan', return_value=details))
                stack.enter_context(patch.object(upgrade, 'report'))
                mutations = [stack.enter_context(patch.object(upgrade, name, side_effect=AssertionError('unexpected mutation '+name)))
                             for name in ('record_outcome', 'back_up', 'snapshot', 'install_guard', 'save_state', 'checkout')]
                with self.assertRaisesRegex(upgrade.UpgradeError, 'invalid image lock'): upgrade.apply('target')
                for mutation in mutations: mutation.assert_not_called()
                self.assertEqual(self.calls, []); self.assertEqual(self.pulled, [])


class HistoricalReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch = patch.object(upgrade, 'ROOT', self.root); self.patch.start(); self.addCleanup(self.patch.stop)
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Upgrade fixture')
        (self.root / 'lab').mkdir()
        self.write('images.lock.json', {'rest': PIN})

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()

    def write(self, name, value):
        (self.root / 'lab' / name).write_text(json.dumps(value))
        self.git('add', '--', 'lab/'+name)
        self.git('commit', '-q', '-m', 'fixture lock')
        self.commit = self.git('rev-parse', 'HEAD')

    def test_native_exact_path_present_and_optional_absent(self):
        self.assertEqual(upgrade.historical_lock_at(self.commit, 'images.lock.json'), {'rest': PIN})
        self.assertIsNone(upgrade.historical_lock_at(self.commit, 'realtime-image.lock.json'))

    def test_unavailable_commit_is_not_feature_absence(self):
        with self.assertRaisesRegex(upgrade.UpgradeError, 'ls-tree'):
            upgrade.historical_lock_at('f'*40, 'realtime-image.lock.json')

    def test_json_null_and_empty_and_nonmapping_are_not_absence(self):
        for value in (None, {}, [], 4):
            self.write('realtime-image.lock.json', value)
            with self.assertRaisesRegex(upgrade.UpgradeError, 'invalid image lock'):
                upgrade.historical_lock_at(self.commit, 'realtime-image.lock.json')

    def test_invalid_json_refuses(self):
        path = self.root / 'lab' / 'realtime-image.lock.json'
        path.write_text('{broken')
        self.git('add', '--', 'lab/realtime-image.lock.json'); self.git('commit', '-q', '-m', 'invalid fixture')
        with self.assertRaisesRegex(upgrade.UpgradeError, 'invalid JSON'):
            upgrade.historical_lock_at(self.git('rev-parse', 'HEAD'), 'realtime-image.lock.json')

    def test_missing_core_lock_refuses(self):
        self.git('rm', '-q', '--', 'lab/images.lock.json'); self.git('commit', '-q', '-m', 'remove fixture core')
        with self.assertRaisesRegex(upgrade.UpgradeError, 'no core image lock'):
            upgrade.historical_lock_at(self.git('rev-parse', 'HEAD'), 'images.lock.json')

    def test_native_show_failure_never_becomes_absence(self):
        def git(*args, **options):
            if args[0] == 'ls-tree': return 'lab/realtime-image.lock.json'
            raise upgrade.UpgradeError('git show failed: fixture transport denied')
        with patch.object(upgrade, 'git', side_effect=git):
            with self.assertRaisesRegex(upgrade.UpgradeError, 'transport denied'):
                upgrade.historical_lock_at(self.commit, 'realtime-image.lock.json')

    def test_ambiguous_path_proof_refuses_before_show(self):
        with patch.object(upgrade, 'git', return_value='lab/realtime-image.lock.json\nlab/other') as git:
            with self.assertRaisesRegex(upgrade.UpgradeError, 'ambiguous'):
                upgrade.historical_lock_at(self.commit, 'realtime-image.lock.json')
            self.assertEqual(git.call_count, 1)


if __name__ == '__main__':
    unittest.main()
