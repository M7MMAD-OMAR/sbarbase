"""Upgrades: what they refuse, the images a start may replace, and the automatic way back."""
import contextlib
import fcntl
import json
import os
import sqlite3
import stat
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

    def commit(self, message, files=None):
        for name, value in self.locks.items():
            (self.root / 'lab' / name).write_text(json.dumps(value))
        for name, text in (files or {}).items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')


class Checkout(unittest.TestCase):
    """A throwaway checkout at the first version, with every upgrade path in a private directory."""

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
        self.upstream = state / 'upstream'
        self.upstream.mkdir()
        self.secrets = root / 'secrets'
        self.secrets.mkdir()
        upgrades = state / 'upgrades'
        self.pulled = []
        self.backups = []
        # Every path an upgrade touches points into this test's own directory.
        for item in [patch.object(upgrade, 'ROOT', self.repo.root), patch.object(upgrade, 'UPGRADES', upgrades),
                     patch.object(upgrade, 'STATE_FILE', upgrades / 'state.json'), patch.object(upgrade, 'LOCK', upgrades / 'upgrade.lock'),
                     patch.object(upgrade, 'SNAPSHOTS', upgrades / 'snapshots'), patch.object(upgrade, 'HOLD', upgrades / 'hold'),
                     patch.object(upgrade, 'UPSTREAM', self.upstream), patch.object(upgrade, 'KEY_STORE', self.secrets / 'managed-keys.sqlite'),
                     patch.object(upgrade, 'SUPERVISOR_LOCK', self.upstream / 'supervisor.lock'),
                     patch.object(upgrade, 'BACKUP_LOCK', self.upstream / 'backup.lock'),
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


class UpgradeTests(Checkout):
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

    def test_evidence_written_on_this_server_is_set_aside_not_refused(self):
        """Found in the rehearsal VM: the acceptance rewrites tracked evidence, and upgrade then refused."""
        self.repo.git('checkout', '-q', '--detach', self.second)
        target = self.repo.commit('evidence', files={'docs/evidence/acceptance.json': 'shipped\n',
                                                      'docs/evidence/first-project.json': 'shipped\n'})
        self.repo.git('checkout', '-q', '--detach', self.first)
        self.repo.commit('evidence here too', files={'docs/evidence/acceptance.json': 'older\n'})
        current = self.head()
        (self.repo.root / 'docs/evidence/acceptance.json').write_text('this server\n')
        (self.repo.root / 'docs/evidence/first-project.json').write_text('this server too\n')
        (self.repo.root / 'docs/evidence/extra.json').write_text('only here\n')
        self.assertEqual(upgrade.plan(target)['refusals'], [])
        upgrade.start(target)
        self.assertEqual(self.head(), target)
        aside = next(upgrade.UPGRADES.glob('evidence-*'))
        self.assertEqual((aside / 'docs/evidence/acceptance.json').read_text(), 'this server\n')
        self.assertEqual((aside / 'docs/evidence/first-project.json').read_text(), 'this server too\n')
        self.assertEqual((self.repo.root / 'docs/evidence/acceptance.json').read_text(), 'shipped\n')
        self.assertEqual((self.repo.root / 'docs/evidence/extra.json').read_text(), 'only here\n',
                         'an untracked file the new version does not ship stays where it is')
        self.assertNotEqual(current, target)

    def test_a_local_change_outside_evidence_still_refuses(self):
        self.repo.commit('evidence', files={'docs/evidence/acceptance.json': 'shipped\n'})
        self.repo.git('checkout', '-q', '--detach', self.first)
        (self.repo.root / 'lab' / 'images.lock.json').write_text('{}')
        self.assertTrue(any('local changes' in refusal for refusal in upgrade.plan(self.second)['refusals']))

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


def store(path, version, rows):
    """A small SQLite store at a schema version holding `rows` rows."""
    with contextlib.closing(sqlite3.connect(path)) as database, database:
        database.execute('CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY)')
        database.execute('DELETE FROM items')
        database.executemany('INSERT INTO items VALUES (?)', [(n,) for n in range(rows)])
        database.execute(f'PRAGMA user_version={version}')


def contents(path):
    with contextlib.closing(sqlite3.connect(path)) as database:
        return (database.execute('PRAGMA user_version').fetchone()[0],
                database.execute('SELECT count(*) FROM items').fetchone()[0])


@contextlib.contextmanager
def locked(path):
    """Another process holding an flock on path (a second open file description conflicts too)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


class ControlStateTests(Checkout):
    """The control state snapshot, the way back that restores it, preconditions and the hold."""

    def setUp(self):
        super().setUp()
        self.catalog = self.upstream / 'control.sqlite'
        self.keys = self.secrets / 'managed-keys.sqlite'
        store(self.catalog, 2, 3)
        store(self.keys, 0, 1)

    def snapshot(self):
        return upgrade.SNAPSHOTS / upgrade.load_state()['snapshot']

    def test_start_snapshots_every_control_store_privately_with_a_manifest(self):
        upgrade.start(self.second)
        folder = self.snapshot()
        manifest = json.loads((folder / 'manifest.json').read_text())
        self.assertEqual(manifest['from'], self.first)
        self.assertEqual([(item['home'], item['file'], item['user_version']) for item in manifest['files']],
                         [('upstream', 'control.sqlite', 2), ('keys', 'managed-keys.sqlite', 0)])
        for item in manifest['files']:
            self.assertEqual(upgrade.sha256(folder / item['file']), item['sha256'])
            self.assertEqual(stat.S_IMODE((folder / item['file']).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
        self.assertEqual(contents(folder / 'control.sqlite'), (2, 3))
        # The old version keeps serving until the restart: nothing is held yet.
        self.assertFalse(upgrade.HOLD.exists())

    def test_the_first_start_of_the_new_version_takes_the_snapshot_the_way_back_uses_once(self):
        upgrade.start(self.second)
        store(self.catalog, 2, 5)  # the old version kept writing until the restart
        self.assertTrue(upgrade.before_start())
        state = upgrade.load_state()
        self.assertTrue(state['attempted_at'])
        self.assertEqual(contents(self.snapshot() / 'control.sqlite'), (2, 5))
        self.assertTrue(upgrade.HOLD.exists())
        first = state['snapshot']
        # A second restart before confirmation keeps it: the catalog may be migrated by now.
        store(self.catalog, 9, 7)
        self.assertTrue(upgrade.before_start())
        self.assertEqual(upgrade.load_state()['snapshot'], first)

    def test_the_automatic_way_back_restores_the_control_state_before_moving(self):
        upgrade.start(self.second)
        store(self.catalog, 2, 5)
        upgrade.before_start()
        # The new version migrates the catalog, then fails; a crashed writer left a journal.
        store(self.catalog, 9, 8)
        store(self.keys, 4, 6)
        (self.upstream / 'control.sqlite-journal').write_bytes(b'hot')
        self.catalog.chmod(0o640)
        self.assertTrue(upgrade.after_start(False))
        self.assertEqual(self.head(), self.first)
        self.assertEqual(contents(self.catalog), (2, 5))
        self.assertEqual(contents(self.keys), (0, 1))
        self.assertFalse((self.upstream / 'control.sqlite-journal').exists())
        # The restored store keeps the mode of the one it replaced.
        self.assertEqual(stat.S_IMODE(self.catalog.stat().st_mode), 0o640)
        state = upgrade.load_state()
        self.assertEqual((state['phase'], state['restored']), ('rolling_back', state['snapshot']))
        self.assertFalse(upgrade.HOLD.exists())
        # The previous version holds traffic until its own health checks pass, then confirms.
        self.assertTrue(upgrade.before_start())
        self.assertTrue(upgrade.HOLD.exists())
        self.assertFalse(upgrade.after_start(True))
        self.assertEqual(upgrade.load_state()['phase'], 'rolled_back')
        self.assertFalse(upgrade.HOLD.exists())

    def test_a_snapshot_that_does_not_match_its_manifest_is_never_restored(self):
        upgrade.start(self.second)
        upgrade.before_start()
        store(self.catalog, 9, 8)
        with (self.snapshot() / 'control.sqlite').open('ab') as handle:
            handle.write(b'x')
        self.assertFalse(upgrade.after_start(False))
        self.assertEqual(upgrade.load_state()['phase'], 'rollback_failed')
        self.assertEqual(contents(self.catalog), (9, 8))
        self.assertEqual(self.head(), self.second)

    def test_a_snapshot_that_cannot_be_taken_on_start_moves_back_with_nothing_touched(self):
        upgrade.start(self.second)
        with patch.object(upgrade, 'snapshot', side_effect=upgrade.UpgradeError('disk full')):
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.before_start()
        self.assertNotIn('attempted_at', upgrade.load_state())
        store(self.catalog, 2, 4)
        self.assertTrue(upgrade.after_start(False))
        self.assertEqual(contents(self.catalog), (2, 4))
        self.assertNotIn('restored', upgrade.load_state())

    def test_a_manual_rollback_before_confirmation_restores_only_with_sbarbase_stopped(self):
        upgrade.start(self.second)
        upgrade.before_start()
        store(self.catalog, 9, 8)
        with locked(upgrade.SUPERVISOR_LOCK):
            with self.assertRaisesRegex(upgrade.UpgradeError, 'running'):
                upgrade.rollback()
        self.assertEqual((self.head(), contents(self.catalog)), (self.second, (9, 8)))
        self.assertEqual(upgrade.load_state()['phase'], 'applied')
        upgrade.rollback()
        self.assertEqual((self.head(), contents(self.catalog)), (self.first, (2, 3)))

    def test_a_manual_rollback_before_the_new_version_started_keeps_the_control_state(self):
        upgrade.start(self.second)
        store(self.catalog, 2, 6)
        with locked(upgrade.SUPERVISOR_LOCK):
            upgrade.rollback()
        self.assertEqual((self.head(), contents(self.catalog)), (self.first, (2, 6)))

    def test_after_confirmation_rollback_keeps_later_writes_and_refuses_a_newer_catalog(self):
        self.repo.git('checkout', '-q', '--detach', self.first)
        base = self.repo.commit('base', {'src/control/catalog.ts': 'export const CATALOG_SCHEMA_VERSION = 2;\n'})
        self.repo.locks['images.lock.json']['rest'] = pin('postgrest:v3', '9')
        target = self.repo.commit('target', {'src/control/catalog.ts': 'export const CATALOG_SCHEMA_VERSION=3;\n'})
        self.repo.git('checkout', '-q', '--detach', base)
        upgrade.start(target)
        upgrade.before_start()
        store(self.catalog, 3, 9)
        self.assertFalse(upgrade.after_start(True))
        with self.assertRaisesRegex(upgrade.UpgradeError, 'forward only'):
            upgrade.rollback()
        self.assertEqual((self.head(), upgrade.load_state()['phase']), (target, 'confirmed'))
        store(self.catalog, 2, 9)
        upgrade.rollback()
        self.assertEqual((self.head(), contents(self.catalog)), (base, (2, 9)))
        self.assertNotIn('restored', upgrade.load_state())

    def test_pending_records_a_running_backup_or_another_upgrade_refuse_before_anything_moves(self):
        for name in ('worker-effect.json', 'hba-operation.json', 'hba-migration'):
            (self.upstream / name).write_text('{}')
            self.assertTrue(any(name in refusal for refusal in upgrade.plan(self.second)['refusals']))
            (self.upstream / name).unlink()
        with locked(upgrade.BACKUP_LOCK):
            self.assertTrue(any('backup or restore' in refusal for refusal in upgrade.plan(self.second)['refusals']))
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.start(self.second)
        self.assertEqual(upgrade.plan(self.second)['refusals'], [])
        with locked(upgrade.LOCK):
            with self.assertRaisesRegex(upgrade.UpgradeError, 'Another upgrade'):
                upgrade.start(self.second)
        self.assertEqual((self.pulled, self.backups, self.head()), ([], [], self.first))
        self.assertIsNone(upgrade.load_state())
        self.assertFalse(upgrade.SNAPSHOTS.exists() and any(upgrade.SNAPSHOTS.iterdir()))
        self.assertFalse(upgrade.HOLD.exists())

    def test_a_stale_hold_marker_is_cleared_when_no_start_is_pending(self):
        upgrade.UPGRADES.mkdir(parents=True)
        upgrade.HOLD.write_text('{}')
        self.assertFalse(upgrade.before_start())
        self.assertFalse(upgrade.HOLD.exists())
        upgrade.start(self.second)
        upgrade.before_start()
        upgrade.after_start(True)
        upgrade.HOLD.write_text('{}')
        self.assertFalse(upgrade.after_start(True))
        self.assertFalse(upgrade.HOLD.exists())

    def test_old_snapshots_are_pruned_but_never_the_current_one(self):
        upgrade.start(self.second)
        current = upgrade.load_state()['snapshot']
        (upgrade.SNAPSHOTS / 'x.partial').mkdir()
        names = []
        for _ in range(4):
            names.append(upgrade.snapshot(self.first))
        os.utime(self.snapshot() / 'manifest.json', ns=(0, 0))
        upgrade.prune_snapshots()
        self.assertEqual(sorted(path.name for path in upgrade.SNAPSHOTS.iterdir()), sorted(names[1:] + [current]))


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
