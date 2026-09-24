"""Upgrades: what they refuse, the images a start may replace, and the automatic way back."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import durable_runtime
import upgrade



def pin(tag, digit):
    return {'tag': tag, 'id': 'sha256:' + digit * 64}


class Repository:
    """A throwaway git checkout holding only the lock files an upgrade reads."""

    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'test')
        (root / 'lab').mkdir()
        self.locks = {'distro-image.lock.json': pin('postgres:17.6', '1'),
                      'images.lock.json': {'db': pin('postgres:17', '2'), 'auth': pin('gotrue:v1', '3'), 'rest': pin('postgrest:v1', '4')},
                      'storage-image.lock.json': pin('storage:v1', '5'),
                      'studio-image.lock.json': {'studio': pin('studio:1', '6'), 'meta': pin('meta:1', '7')}}
        self.commit('first')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        for name, value in self.locks.items():
            (self.root / 'lab' / name).write_text(json.dumps(value))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.repo = Repository(root / 'checkout')
        self.first = self.repo.git('rev-parse', 'HEAD')
        self.repo.locks['images.lock.json']['rest'] = pin('postgrest:v2', '8')
        self.second = self.repo.commit('second')
        self.repo.git('checkout', '-q', '--detach', self.first)
        state = root / 'state'
        state.mkdir()
        self.pulled = []
        self.backups = []
        for item in [patch.object(upgrade, 'ROOT', self.repo.root), patch.object(upgrade, 'STATE_FILE', state / 'upgrades' / 'state.json'),
                     patch.object(upgrade, 'INTENT', state / 'upgrade-intent.json'),
                     patch.object(upgrade, 'pull', side_effect=self.pulled.append),
                     patch.object(upgrade, 'back_up', side_effect=lambda: self.backups.append(True)),
                     patch.object(upgrade, 'install_dependencies')]:
            item.start()
            self.addCleanup(item.stop)

    def head(self):
        return self.repo.git('rev-parse', 'HEAD')

    def intent(self):
        return json.loads(upgrade.INTENT.read_text())

    def test_check_names_the_changed_images_and_refuses_a_database_change(self):
        details = upgrade.plan(self.second)
        self.assertEqual(details['refusals'], [])
        self.assertEqual(details['changes'], [('images.lock.json:rest', 'postgrest:v1', 'postgrest:v2')])
        self.repo.git('checkout', '-q', '--detach', self.second)
        self.repo.locks['distro-image.lock.json'] = pin('postgres:18.0', '9')
        database = self.repo.commit('database')
        self.repo.git('checkout', '-q', '--detach', self.first)
        self.assertTrue(any('migrate-generation' in refusal for refusal in upgrade.plan(database)['refusals']))

    def test_check_refuses_local_changes_and_the_same_version(self):
        (self.repo.root / 'lab' / 'images.lock.json').write_text('{}')
        refusals = upgrade.plan(self.second)['refusals']
        self.assertTrue(any('local changes' in refusal for refusal in refusals))
        self.repo.git('checkout', '--', '.')
        self.assertIn('Already at this version', upgrade.plan(self.first)['refusals'])

    def test_a_refused_start_changes_nothing(self):
        (self.repo.root / 'lab' / 'images.lock.json').write_text('{}')
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.start(self.second)
        self.assertEqual((self.pulled, self.backups), ([], []))
        self.assertFalse(upgrade.INTENT.exists())
        self.assertIsNone(upgrade.load_state())

    def test_start_backs_up_pulls_and_names_only_the_target_images(self):
        upgrade.start(self.second)
        self.assertEqual(self.head(), self.second)
        self.assertEqual(self.pulled, [self.second])
        self.assertEqual(self.backups, [True])
        self.assertEqual(self.intent()['pins'], {'auth': 'sha256:' + '3' * 64, 'rest': 'sha256:' + '8' * 64,
                                                 'storage': 'sha256:' + '5' * 64})
        self.assertEqual(upgrade.load_state()['phase'], 'applied')
        # A second start waits until the first one has been started.
        self.assertTrue(any('restart' in refusal for refusal in upgrade.plan(self.first)['refusals']))

    def test_a_good_start_confirms_the_upgrade_and_closes_the_intent(self):
        upgrade.start(self.second)
        self.assertFalse(upgrade.after_start(True))
        self.assertEqual(upgrade.load_state()['phase'], 'confirmed')
        self.assertFalse(upgrade.INTENT.exists())
        self.assertEqual(self.head(), self.second)

    def test_a_failed_start_moves_back_and_the_previous_version_confirms_it(self):
        upgrade.start(self.second)
        self.assertTrue(upgrade.after_start(False))
        self.assertEqual(self.head(), self.first)
        self.assertEqual(self.intent()['pins']['rest'], 'sha256:' + '4' * 64)
        state = upgrade.load_state()
        self.assertEqual((state['phase'], state['automatic']), ('rolling_back', True))
        self.assertFalse(upgrade.after_start(True))
        self.assertEqual(upgrade.load_state()['phase'], 'rolled_back')
        self.assertFalse(upgrade.INTENT.exists())

    def test_a_failed_rollback_stops_instead_of_looping(self):
        upgrade.start(self.second)
        upgrade.after_start(False)
        self.assertFalse(upgrade.after_start(False))
        self.assertEqual(upgrade.load_state()['phase'], 'rollback_failed')
        self.assertEqual(self.head(), self.first)
        # Nothing pending: a later start has nothing to do.
        self.assertFalse(upgrade.after_start(False))

    def test_an_operator_rolls_back_a_confirmed_upgrade(self):
        upgrade.start(self.second)
        upgrade.after_start(True)
        upgrade.rollback()
        self.assertEqual(self.head(), self.first)
        self.assertEqual(upgrade.load_state()['automatic'], False)
        with self.assertRaises(upgrade.UpgradeError):
            upgrade.rollback()

    def test_a_start_without_an_upgrade_does_nothing(self):
        self.assertFalse(upgrade.after_start(False))
        self.assertIsNone(upgrade.load_state())

    def test_a_checkout_that_cannot_install_its_dependencies_goes_back(self):
        with patch.object(upgrade, 'install_dependencies', side_effect=upgrade.UpgradeError('bun install failed')):
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.start(self.second)
        self.assertEqual(self.head(), self.first)
        self.assertFalse(upgrade.INTENT.exists())
        self.assertEqual(upgrade.load_state()['phase'], 'failed')


class ReplacementTests(unittest.TestCase):
    """Startup replaces a changed service only for the exact image an upgrade names."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.intent = Path(directory.name) / 'upgrade-intent.json'
        patcher = patch.object(durable_runtime, 'UPGRADE_INTENT', self.intent)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runtime = object.__new__(durable_runtime.Runtime)
        self.runtime.pins = {'rest': {'id': 'sha256:new'}, 'db': {'id': 'sha256:db'}}
        self.calls = []

    def docker(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('image', 'inspect'):
            return SimpleNamespace(returncode=0, stdout=json.dumps([{'Id': 'sha256:new-local'}]))
        return SimpleNamespace(returncode=0, stdout='new-container\n')

    def launch(self, component='rest'):
        retained = {'Id': 'old-container', 'Image': 'sha256:old-local', 'Config': {'Env': ['A=1']}, 'Mounts': [],
                    'NetworkSettings': {'Networks': {durable_runtime.NETWORK: {}}}}
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: retained if kind == 'container' else {'Name': name}), \
             patch.object(durable_runtime.lab, 'docker', self.docker), patch.object(durable_runtime.lab, 'secure_file'), \
             patch.object(durable_runtime.resource_policy, 'io_flags', return_value=[]):
            return self.runtime.launch('sbarbase-durable-e-rest', component, {'A': '1'}, '256m', .25, existing_only=True, tier='production')

    def test_a_changed_image_without_an_upgrade_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'explicit reconciliation'):
            self.launch()
        self.assertNotIn(('rm', '-f', 'old-container'), self.calls)

    def test_an_upgrade_replaces_the_service_with_its_named_image(self):
        self.intent.write_text(json.dumps({'pins': {'rest': 'sha256:new'}}))
        self.assertEqual(self.launch(), ('new-container', True))
        self.assertIn(('rm', '-f', 'old-container'), self.calls)
        self.assertEqual(self.calls[-1][-1], 'sha256:new')

    def test_an_upgrade_for_another_image_or_the_database_is_refused(self):
        self.intent.write_text(json.dumps({'pins': {'rest': 'sha256:other', 'db': 'sha256:db'}}))
        with self.assertRaises(RuntimeError):
            self.launch()
        self.assertFalse(durable_runtime.upgrade_allows('db', 'sha256:db'))
        self.intent.write_text('not json')
        self.assertFalse(durable_runtime.upgrade_allows('rest', 'sha256:new'))


if __name__ == '__main__':
    unittest.main()
