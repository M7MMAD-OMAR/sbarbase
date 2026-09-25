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

import backup
import durable_runtime
import release_channel
import upgrade
import upgrade_guard



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
        # `start` copies the guard of the version it leaves; every version here ships one.
        self.commit('first', {'lab/upgrade_guard.py': Path(upgrade_guard.__file__).read_text()})

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
        for item in [patch.object(upgrade, 'ROOT', self.repo.root), patch.object(release_channel, 'ROOT', self.repo.root),
                     patch.object(upgrade, 'UPGRADES', upgrades),
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
        self.assertEqual(details['changes'], [{'image': 'images.lock.json:rest', 'from': 'postgrest:v1', 'to': 'postgrest:v2'}])
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

    def test_a_start_for_a_request_records_its_point_of_no_return_before_the_backup_and_how_it_ended(self):
        # upgrade.main changes into its checkout; come back before the directory goes.
        self.addCleanup(os.chdir, os.getcwd())
        seen = []
        upgrade.back_up.side_effect = lambda: seen.append(json.loads(upgrade.outcome_file().read_text()))
        with patch('builtins.print'):
            self.assertEqual(upgrade.main(['start', '--to', self.second, '--request', 'r1']), 0)
        self.assertEqual([(item['request'], item['kind'], item['passed'], item['changed']) for item in seen],
                         [('r1', 'start', True, False)])
        outcome = json.loads(upgrade.outcome_file().read_text())
        self.assertEqual((outcome['passed'], outcome['changed'], outcome['error']), (True, True, None))
        self.assertEqual(stat.S_IMODE(upgrade.outcome_file().stat().st_mode), 0o600)
        # Refused before anything moved: the refusals, and no point of no return.
        with patch('builtins.print'), patch('sys.stderr'):
            self.assertEqual(upgrade.main(['start', '--to', self.first, '--request', 'r2']), 1)
        outcome = json.loads(upgrade.outcome_file().read_text())
        self.assertEqual((outcome['request'], outcome['passed'], outcome['changed']), ('r2', False, False))
        self.assertTrue(any('restart' in refusal for refusal in outcome['refusals']))
        # From the command line, without a request, nothing is recorded.
        upgrade.outcome_file().unlink()
        with patch('builtins.print'), patch('sys.stderr'):
            upgrade.main(['start', '--to', self.first])
        self.assertFalse(upgrade.outcome_file().exists())

    def test_the_first_start_refuses_a_record_the_previous_version_left_and_marks_the_way_back_retryable(self):
        upgrade.start(self.second)
        (self.upstream / 'worker-effect.json').write_text('{}')
        with self.assertRaisesRegex(upgrade.UpgradeError, 'pending operation record'):
            upgrade.before_start()
        self.assertTrue(upgrade.load_state()['retryable'])
        (self.upstream / 'worker-effect.json').unlink()
        self.assertTrue(upgrade.after_start(False))
        state = upgrade.load_state()
        self.assertEqual((state['phase'], state['retryable']), ('rolling_back', True))

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
            self.assertEqual(backup.digest(folder / item['file']), item['sha256'])
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
        state = upgrade.load_state()
        self.assertEqual(state['phase'], 'rollback_failed')
        self.assertIn('restore from the backups', state['failure'])
        self.assertEqual(contents(self.catalog), (9, 8))
        # The checkout moved back before the restore was tried: the failed version is never what
        # the next (ungated) start runs. The previous version refuses a catalog newer than it opens.
        self.assertEqual(self.head(), self.first)
        self.assertFalse(upgrade.before_start())

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

    def test_a_confirmation_that_cannot_be_saved_keeps_the_hold(self):
        import dev
        upgrade.start(self.second)
        upgrade.before_start()
        with patch.object(upgrade, 'save_state', side_effect=OSError('read-only file system')):
            with self.assertRaises(OSError):
                upgrade.after_start(True)
            with patch.object(dev.updates, 'announce_outcome') as announce:
                self.assertFalse(dev.upgrade_confirmed())
            announce.assert_not_called()
        self.assertEqual(upgrade.load_state()['phase'], 'applied')
        self.assertTrue(upgrade.HOLD.exists())
        self.assertTrue(upgrade.INTENT.exists())
        with patch.object(dev.updates, 'announce_outcome') as announce:
            self.assertTrue(dev.upgrade_confirmed())
        self.assertEqual(announce.call_args.args[1]['phase'], 'confirmed')
        self.assertFalse(upgrade.HOLD.exists())

    def test_a_way_back_that_fails_keeps_what_it_already_recorded(self):
        upgrade.start(self.second)
        upgrade.before_start()
        store(self.catalog, 9, 8)
        with patch.object(upgrade, 'checkout', side_effect=upgrade.UpgradeError('git checkout failed')):
            self.assertFalse(upgrade.after_start(False, 'Runtime startup failed'))
        state = upgrade.load_state()
        # Resumable, never rollback_failed with the failed version checked out.
        self.assertEqual((state['phase'], state['moved_back'], state['restore_pending']), ('rolling_back', False, True))
        self.assertEqual((state['automatic'], state['reason']), (True, 'Runtime startup failed'))
        self.assertTrue(state['rollback_at'])
        self.assertNotIn('restored', state)
        self.assertEqual((self.head(), contents(self.catalog)), (self.second, (9, 8)))
        with self.assertRaisesRegex(upgrade.UpgradeError, 'did not finish'):
            upgrade.before_start()
        # The next start's guard completes it before anything else runs.
        self.assertEqual(upgrade_guard.guard(upgrade.layout(), install=lambda layout: None), 0)
        state = upgrade.load_state()
        self.assertEqual((self.head(), contents(self.catalog)), (self.first, (2, 3)))
        self.assertEqual((state['phase'], state['restored']), ('rolling_back', state['snapshot']))
        self.assertTrue(upgrade.before_start())

    def test_status_says_why_the_automatic_way_back_ran(self):
        import io
        upgrade.start(self.second)
        upgrade.before_start()
        upgrade.after_start(False, 'The new version did not become healthy within 120 s: e rest answered HTTP 503\nmore')
        upgrade.after_start(True)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            upgrade.status()
        text = output.getvalue()
        self.assertIn('rolled_back: back on the previous version (automatic)\n', text)
        self.assertIn('why back The new version did not become healthy within 120 s: e rest answered HTTP 503\n', text)
        self.assertNotIn('did not start', text)
        self.assertNotIn('more', text)

    def confirmed_upgrade(self):
        upgrade.start(self.second)
        upgrade.before_start()
        upgrade.after_start(True)

    def test_a_version_without_a_schema_ladder_opens_only_the_baseline_catalog(self):
        """The first commit has no CATALOG_SCHEMA_VERSION: it never wrote user_version, so the
        only catalog it is known to open is at 0, and a migrated one refuses the rollback."""
        self.confirmed_upgrade()
        self.assertRegex(upgrade.rollback_refusal(), 'control catalog is at schema 2.*opens only up to 0')
        store(self.catalog, 0, 3)
        self.assertIsNone(upgrade.rollback_refusal())
        upgrade.rollback()
        self.assertEqual(self.head(), self.first)

    def test_a_key_store_newer_than_the_previous_version_refuses_the_rollback(self):
        store(self.catalog, 0, 3)
        self.confirmed_upgrade()
        store(self.keys, 1, 1)
        self.assertRegex(upgrade.rollback_refusal(), 'key store is at schema 1.*opens only up to 0')
        with self.assertRaisesRegex(upgrade.UpgradeError, 'forward only'):
            upgrade.rollback()
        self.assertEqual(self.head(), self.second)

    def test_a_previous_version_that_cannot_be_read_refuses_the_rollback(self):
        store(self.catalog, 0, 3)
        self.confirmed_upgrade()
        state = upgrade.load_state()
        upgrade.save_state({**state, 'from': 'f' * 40})
        self.assertIn('cannot be read', upgrade.rollback_refusal())
        self.assertIsNone(upgrade.catalog_support('f' * 40))
        self.assertEqual(upgrade.catalog_support(self.first), 0)

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

    def test_the_probe_token_lives_exactly_as_long_as_the_hold(self):
        upgrade.start(self.second)
        self.assertFalse(upgrade.probe_token().exists())
        upgrade.before_start()
        token = upgrade.probe_token().read_text()
        self.assertRegex(token, r'^[A-Za-z0-9_-]{43}$')
        self.assertEqual(stat.S_IMODE(upgrade.probe_token().stat().st_mode), 0o600)
        # Every gated start gets a fresh one.
        upgrade.before_start()
        self.assertNotEqual(upgrade.probe_token().read_text(), token)
        upgrade.after_start(True)
        self.assertFalse(upgrade.probe_token().exists())
        # A token left by a crash goes with its stale marker.
        upgrade.probe_token().write_text(token)
        self.assertFalse(upgrade.before_start())
        self.assertFalse(upgrade.probe_token().exists())

    def test_the_way_back_removes_the_probe_token(self):
        upgrade.start(self.second)
        upgrade.before_start()
        self.assertTrue(upgrade.after_start(False))
        self.assertFalse(upgrade.probe_token().exists())

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


class CommandTests(unittest.TestCase):
    def test_the_backup_before_an_upgrade_is_marked_as_one(self):
        with patch.object(upgrade.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            upgrade.back_up()
        self.assertEqual(run.call_args.args[0][-4:], ['all', '--local-only', '--reason', 'upgrade'])

    def test_a_release_start_may_allow_a_rebuild_and_a_migrating_release(self):
        self.addCleanup(os.chdir, os.getcwd())
        with patch.object(upgrade, 'start_release') as start, patch.object(upgrade, 'record_outcome'):
            self.assertEqual(upgrade.main(['start', '--release', 'v0.2.0', '--allow-class', 'rebuild',
                                           '--allow-class', 'attended', '--request', 'r1']), 0)
        self.assertEqual(start.call_args.args, ('v0.2.0', ['rebuild', 'attended'], 'cli', 'r1'))

    def test_the_channel_names_the_releases_it_passed_over(self):
        result = {'current': {'version': '0.1.0', 'commit': 'a' * 40}, 'available': None, 'refusals': [],
                  'skipped': ['v0.3.0 was passed over: v0.3.0 needs at least version 0.2.0']}
        with patch('builtins.print') as shown:
            upgrade.show_channel(result)
        self.assertIn('passed     v0.3.0 was passed over: v0.3.0 needs at least version 0.2.0',
                      [call.args[0] for call in shown.call_args_list])


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
