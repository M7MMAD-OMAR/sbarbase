"""The upgrade check's own parts that need no installation: the candidate versions it builds
from this tree, the catalog digest it compares, and the evidence it writes.

The candidates are edits to anchors in the real source. A refactor that moves an anchor must
fail here, in the unit suite, rather than in the Docker job with a candidate that tests nothing.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import upgrade
from test_release_channel import ssh_signing

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('upgrade_check', ROOT / 'lab' / 'upgrade-check.py')
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def copy_tree(base):
    """The files the candidates edit, plus everything catalog.ts imports, in a temporary root."""
    shutil.copytree(ROOT / 'src', base / 'src')
    (base / 'lab').mkdir()
    for name in ('upstream-server.ts', 'images.lock.json', 'studio-image.lock.json'):
        shutil.copy(ROOT / 'lab' / name, base / 'lab' / name)
    return base


def bun(source, cwd):
    result = subprocess.run(['bun', '-e', source], capture_output=True, text=True, cwd=cwd)
    return result.returncode, (result.stdout + result.stderr).strip()


class CandidateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = copy_tree(Path(directory.name))

    def test_the_migrating_candidate_adds_one_schema_step_and_stops_its_console(self):
        version = int(check.SCHEMA.search((ROOT / 'src' / 'control' / 'catalog.ts').read_text()).group(1))
        check.migrate_then_stop(self.base)
        catalog = (self.base / 'src' / 'control' / 'catalog.ts').read_text()
        self.assertEqual(check.SCHEMA.findall(catalog), [str(version + 1)])
        self.assertIn(f"if(version<{version + 1})this.db.exec('CREATE TABLE IF NOT EXISTS {check.PROBE_TABLE}", catalog)
        server = (self.base / 'lab' / 'upstream-server.ts').read_text()
        self.assertIn(check.SERVER_ANCHOR + check.STOP_AFTER_MIGRATING, server)
        self.assertLess(server.index('process.exit(1)'), server.index('serveLocal('))

    @unittest.skipIf(shutil.which('bun') is None, 'bun is not installed')
    def test_the_migrated_catalog_is_one_the_previous_version_refuses(self):
        path = self.base / 'control.sqlite'
        driver = ("import {{Catalog}} from '{root}/src/control/catalog.ts';"
                  "const c=new Catalog('{path}');console.log(c.schemaVersion());c.close?.();")
        status, before = bun(driver.format(root=ROOT, path=path), self.base)
        self.assertEqual(status, 0, before)
        check.migrate_then_stop(self.base)
        status, after = bun(driver.format(root=self.base, path=path), self.base)
        self.assertEqual((status, int(after.splitlines()[-1])), (0, int(before.splitlines()[-1]) + 1), after)
        with contextlib.closing(sqlite3.connect(path)) as database:
            tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(check.PROBE_TABLE, tables)
        # Without the snapshot, the way back would end here: the previous release refuses it.
        status, output = bun(driver.format(root=ROOT, path=path), self.base)
        self.assertNotEqual(status, 0)
        self.assertIn('Catalog schema is newer than this release', output)

    def test_the_unhealthy_candidate_answers_503_on_health(self):
        check.never_healthy(self.base)
        health = (self.base / 'src' / 'http' / 'health.ts').read_text()
        self.assertNotIn(check.HEALTH_ANCHOR, health)
        self.assertIn('{status:503,headers}', health)

    def test_the_rest_candidates_change_only_the_rest_pin(self):
        lock = json.loads((ROOT / 'lab' / 'images.lock.json').read_text())
        check.newer_rest(self.base)
        newer = json.loads((self.base / 'lab' / 'images.lock.json').read_text())
        self.assertEqual(newer['rest'], check.NEWER_REST)
        self.assertEqual({k: v for k, v in newer.items() if k != 'rest'}, {k: v for k, v in lock.items() if k != 'rest'})
        check.never_answers(self.base)
        meta = json.loads((ROOT / 'lab' / 'studio-image.lock.json').read_text())['meta']
        self.assertEqual(json.loads((self.base / 'lab' / 'images.lock.json').read_text())['rest'], meta)

    def test_a_moved_anchor_stops_the_build_of_a_candidate(self):
        (self.base / 'src' / 'http' / 'health.ts').write_text('export {}\n')
        with self.assertRaises(SystemExit):
            check.never_healthy(self.base)
        catalog = self.base / 'src' / 'control' / 'catalog.ts'
        catalog.write_text(catalog.read_text().replace(check.SCHEMA_ANCHOR, ''))
        with self.assertRaises(SystemExit):
            check.migrate_then_stop(self.base)


ISOLATED = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}


def run_git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=test', '-c', 'user.email=test@example.com', *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@unittest.skipIf(ssh_signing(), ssh_signing())
class InstallationTests(unittest.TestCase):
    """`candidates` and `unsigned` against a shallow clone, as actions/checkout leaves the CI runner.
    upgrade.py runs for real from the clone's own lab/, so the channel's verdict is the real one."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        environment = patch.dict(os.environ, ISOLATED)
        environment.start()
        self.addCleanup(environment.stop)
        upstream = copy_tree(base / 'upstream')
        shutil.copytree(ROOT / 'lab', upstream / 'lab', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        (upstream / 'deploy').mkdir()
        shutil.copy(ROOT / 'deploy' / 'release-signers', upstream / 'deploy' / 'release-signers')
        for name in ('release.json', 'package.json', '.gitignore'):
            shutil.copy(ROOT / name, upstream / name)
        run_git(upstream, 'init', '-q')
        run_git(upstream, 'add', '-A')
        run_git(upstream, 'commit', '-q', '-m', 'installed')
        run_git(upstream, 'commit', '-q', '--allow-empty', '-m', 'installed, one more')
        self.root = base / 'installation'
        run_git(base, 'clone', '-q', '--depth', '1', f'file://{upstream}', str(self.root))
        lab = self.root / '.lab'
        for name, value in (('ROOT', self.root), ('RECORD', lab / 'upgrade-check.json'), ('UPGRADES', lab / 'upgrades'),
                            ('UPSTREAM', lab / 'upstream'), ('CATALOG', lab / 'upstream' / 'control.sqlite'),
                            ('MIGRATED', lab / check.MIGRATED.name)):
            patcher = patch.object(check, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # schema_at reads through lab/upgrade.py, which runs Git in its own checkout.
        patcher = patch.object(upgrade, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def candidates(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            check.candidates()
        return dict(line.split('=', 1) for line in output.getvalue().split())

    def changed(self, before, after):
        return run_git(self.root, 'diff', '--name-only', before, after).split()

    def test_the_candidates_change_what_each_case_needs_and_nothing_else(self):
        self.assertEqual(run_git(self.root, 'rev-parse', '--is-shallow-repository'), 'true')
        names = self.candidates()
        self.assertEqual(set(names), set(check.NAMES))
        self.assertEqual(run_git(self.root, 'rev-parse', 'HEAD'), names['base'])
        self.assertEqual(run_git(self.root, 'status', '--porcelain', '--untracked-files=no'), '')
        self.assertEqual(self.changed('HEAD~1', names['base']), ['deploy/release-signers'])
        self.assertIn('upgrade-check@example.com namespaces="git" ssh-ed25519 ',
                      run_git(self.root, 'show', f"{names['base']}:deploy/release-signers"))
        self.assertTrue(check.signer_key().is_file())
        self.assertEqual(self.changed(names['base'], names['good']), ['lab/images.lock.json'])
        self.assertEqual(self.changed(names['good'], names['bad']), ['lab/images.lock.json'])
        self.assertEqual(self.changed(names['good'], names['migrates']), ['lab/upstream-server.ts', 'src/control/catalog.ts'])
        self.assertEqual(self.changed(names['good'], names['unhealthy']), ['src/http/health.ts'])
        self.assertEqual(check.schema_at(names['migrates']), check.schema_at(names['good']) + 1)

    def unsigned(self, names):
        run_git(self.root, 'checkout', '-q', '--detach', names['good'])
        (self.root / '.lab' / 'upgrades').mkdir(parents=True)
        state = self.root / '.lab' / 'upgrades' / 'state.json'
        state.write_text(json.dumps({'phase': 'rolled_back', 'from': names['good'], 'to': names['unhealthy']}))
        available = self.root / '.lab' / 'upgrades' / 'available.json'
        available.write_text('{"available": null}')
        check.RECORD.write_text(json.dumps({'commits': names, 'observed': {}}))
        with contextlib.redirect_stdout(io.StringIO()):
            status = check.unsigned()
        rows = check.load()['checks']
        self.assertEqual(run_git(self.root, 'rev-parse', 'HEAD'), names['good'])
        self.assertEqual(available.read_text(), '{"available": null}')
        self.assertEqual(run_git(self.root, 'for-each-ref', 'refs/sbarbase-releases'), '')
        return status, rows

    def test_unsigned_and_unlisted_key_tags_are_refused_and_nothing_moves(self):
        status, rows = self.unsigned(self.candidates())
        self.assertEqual([row['ok'] for row in rows], [True] * 5, rows)
        self.assertEqual(status, 0)
        self.assertEqual({row['stage'] for row in rows}, {'unsigned'})

    def test_a_channel_that_refuses_every_release_does_not_pass(self):
        names = self.candidates()
        # With no key listed, every tag is refused for that reason, not for its own signature.
        (self.root / 'deploy' / 'release-signers').write_text((ROOT / 'deploy' / 'release-signers').read_text())
        status, rows = self.unsigned(names)
        self.assertEqual(status, 1)
        failed = [row['check'] for row in rows if not row['ok']]
        self.assertIn('the channel accepts a tag signed by the listed key', failed)
        self.assertIn('upgrade.py start --release refuses the unsigned tag, naming its signature', failed)


def catalog(version, digest='d'):
    return {'version': version, 'tables': ['organizations'], 'rows': {'organizations': 1}, 'digest': digest}


def observation(head, catalog_state, upgrade, rest=True):
    return {'head': head, 'rest_tag': 'postgrest:v14.16', 'environments': {'e_1': {'counts': [1, 1, 1, 1], 'rest_on_pin': rest}},
            'healthy': True, 'console_health': 200, 'held': False, 'catalog': catalog_state, 'upgrade': upgrade}


class StageTests(unittest.TestCase):
    """What `after` and `hold` conclude from what they observe; the observing itself needs Docker."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.names = {name: str(index) * 40 for index, name in enumerate(check.NAMES, 1)}
        for name, value in (('RECORD', self.base / 'record.json'), ('MIGRATED', self.base / 'migrated.json'),
                            ('UPGRADES', self.base / 'upgrades'), ('UPSTREAM', self.base / 'upstream'),
                            ('CATALOG', self.base / 'upstream' / 'control.sqlite')):
            patcher = patch.object(check, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        check.RECORD.write_text(json.dumps({'commits': self.names,
                                            'observed': observation(self.names['base'], catalog(3), {})}))

    def after(self, stage, now, back_to='good'):
        with patch.object(check, 'observe', return_value=now), patch.object(check, 'schema_at', return_value=4), \
                contextlib.redirect_stdout(io.StringIO()):
            status = check.after(stage, back_to)
        rows = [row for row in check.load()['checks'] if row['stage'] == stage]
        return status, [row['check'] for row in rows if not row['ok']]

    def moved_back(self, to, **extra):
        return {'phase': 'rolled_back', 'automatic': True, 'from': self.names['good'], 'to': self.names[to],
                'attempted_at': 't', 'snapshot': 's', 'restored': 's', **extra}

    def test_a_catalog_migration_moved_back_with_its_snapshot_passes(self):
        check.MIGRATED.write_text(json.dumps({'version': 4}))
        now = observation(self.names['good'], catalog(3), self.moved_back('migrates'))
        self.assertEqual(self.after('migration-rolled-back', now), (0, []))

    def test_a_catalog_left_migrated_or_changed_fails(self):
        check.MIGRATED.write_text(json.dumps({'version': 4}))
        migrated = {**catalog(4, 'other'), 'tables': ['organizations', check.PROBE_TABLE]}
        now = observation(self.names['good'], migrated, self.moved_back('migrates', restored=None))
        status, failed = self.after('migration-rolled-back', now)
        self.assertEqual(status, 1)
        self.assertEqual(set(failed), {'the control state snapshot taken when the new version started was restored',
                                       'the table that migration added is gone again',
                                       'the control catalog is at the schema it had before the upgrade',
                                       'organizations, projects, environments and memberships in the control catalog are unchanged'})

    def test_a_migration_that_never_ran_fails(self):
        now = observation(self.names['good'], catalog(3), self.moved_back('migrates'))
        self.assertEqual(self.after('migration-rolled-back', now)[1], ['the new version had migrated the control catalog before it stopped'])

    def test_a_stale_rollback_of_another_version_fails(self):
        now = observation(self.names['good'], catalog(3), self.moved_back('bad'))
        self.assertEqual(self.after('health-rolled-back', now)[1], ['the broken version was moved back automatically'])

    def test_the_operator_rollback_keeps_the_control_state(self):
        state = {'phase': 'rolled_back', 'automatic': False, 'from': self.names['base'], 'to': self.names['good'], 'snapshot': 's'}
        now = observation(self.names['base'], catalog(3), state)
        self.assertEqual(self.after('operator-rollback', now), (0, []))
        now = observation(self.names['base'], catalog(3), {**state, 'restored': 's'})
        self.assertEqual(set(self.after('operator-rollback', now)[1]),
                         {'after confirmation the control state was kept, not restored'})

    def serve(self, health, rest):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, body = (health if self.path == '/health' else rest)()
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        check.UPSTREAM.mkdir()
        (check.UPSTREAM / 'server.json').write_text(json.dumps({'url': f'http://127.0.0.1:{server.server_address[1]}'}))
        with contextlib.closing(sqlite3.connect(check.CATALOG)) as database:
            database.execute('PRAGMA user_version=3')

    def hold(self, phase, marker=True, limit=5):
        check.UPGRADES.mkdir(exist_ok=True)
        (check.UPGRADES / 'state.json').write_text(json.dumps({'phase': phase, 'to': self.names['unhealthy']}))
        if marker:
            (check.UPGRADES / 'hold').write_text('{}')
        with contextlib.redirect_stdout(io.StringIO()):
            status = check.hold(limit)
        return status, [(row['check'], row['ok']) for row in check.load()['checks'] if row['stage'] == 'hold']

    def test_hold_records_the_held_window_of_the_unhealthy_version(self):
        self.serve(lambda: (503, '{}'), lambda: (503, '{"message":"Sbarbase is confirming an upgrade; try again shortly"}'))
        status, rows = self.hold('applied')
        self.assertEqual((status, [ok for _, ok in rows]), (0, [True, True, True]))

    def test_hold_fails_when_traffic_was_not_held_or_the_window_passed(self):
        self.serve(lambda: (503, '{}'), lambda: (401, '{"message":"missing key"}'))
        self.assertEqual(self.hold('applied', limit=2), (1, [(
            'application traffic was answered 503 while the new version waited for its health checks', False)]))
        check.RECORD.write_text(json.dumps({'commits': self.names, 'observed': observation('x', catalog(3), {})}))
        self.assertEqual(self.hold('rolling_back', marker=False, limit=30)[0], 1)


class RecordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.record = self.base / 'upgrade-check.json'
        patcher = patch.object(check, 'RECORD', self.record)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_commits_are_named_and_complete(self):
        commit = 'a' * 40
        self.assertEqual(check.commits([f'base={commit}', f'good={commit}', f'bad={commit}']),
                         {'base': commit, 'good': commit, 'bad': commit})
        for pairs in ([f'base={commit}', f'good={commit}'], [f'base={commit}', f'good={commit}', 'bad=HEAD'],
                      [f'base={commit}', f'good={commit}', f'bad={commit}', f'other={commit}']):
            with self.assertRaises(SystemExit):
                check.commits(pairs)

    def test_the_catalog_digest_covers_rows_that_must_not_change_and_only_those(self):
        path = self.base / 'control.sqlite'
        with contextlib.closing(sqlite3.connect(path)) as database:
            database.executescript("CREATE TABLE organizations(id TEXT PRIMARY KEY,name TEXT);"
                                   "CREATE TABLE notification_outbox(id TEXT);"
                                   "INSERT INTO organizations VALUES('o1','One'),('o2','Two');PRAGMA user_version=3;")
        first = check.catalog_state(path)
        self.assertEqual((first['version'], first['rows']), (3, {'organizations': 2}))
        with contextlib.closing(sqlite3.connect(path)) as database:
            database.execute("INSERT INTO notification_outbox VALUES('n1')")
            database.commit()
        self.assertEqual(check.catalog_state(path)['digest'], first['digest'])
        with contextlib.closing(sqlite3.connect(path)) as database:
            database.execute("UPDATE organizations SET name='Renamed' WHERE id='o2'")
            database.commit()
        self.assertNotEqual(check.catalog_state(path)['digest'], first['digest'])

    def evidence(self, *required):
        path = self.base / 'evidence.json'
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            status = check.evidence(path, list(required))
        return status, json.loads(path.read_text()) if path.exists() else None

    def test_evidence_passes_only_when_every_named_stage_ran_and_passed(self):
        self.assertEqual(self.evidence('upgraded'), (1, None))
        record = {'commits': {}, 'observed': {}}
        for stage, ok in (('upgraded', True), ('unsigned', True)):
            add, finish = check.recorder(stage, record)
            with contextlib.redirect_stdout(io.StringIO()):
                add(f'{stage} check', ok)
                finish()
        status, written = self.evidence('upgraded', 'unsigned')
        self.assertEqual((status, written['passed'], written['stages'], written['count']), (0, True, ['upgraded', 'unsigned'], 2))
        status, written = self.evidence('upgraded', 'hold', 'unsigned')
        self.assertEqual((status, written['passed'], written['missing']), (1, False, ['hold']))
        add, finish = check.recorder('hold', record)
        with contextlib.redirect_stdout(io.StringIO()):
            add('hold check', False)
            self.assertEqual(finish(), 1)
        status, written = self.evidence('upgraded', 'hold', 'unsigned')
        self.assertEqual((status, written['passed'], written['missing']), (1, False, []))


if __name__ == '__main__':
    unittest.main()
