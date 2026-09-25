"""The upgrade check's own parts that need no installation: the candidate versions it builds
from this tree, the catalog digest it compares, and the evidence it writes.

The candidates are edits to anchors in the real source. A refactor that moves an anchor must
fail here, in the unit suite, rather than in the Docker job with a candidate that tests nothing.
"""
import contextlib
import importlib.util
import io
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
