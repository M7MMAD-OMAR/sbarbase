"""Tempfile fixtures exercise report provenance independently of native services."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ci_artifacts', Path(__file__).resolve().parents[1] / 'deploy/ci-artifacts.py')
artifact = importlib.util.module_from_spec(spec); spec.loader.exec_module(artifact)


class ArtifactScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'checkout'; self.root.mkdir()
        self.evidence = self.root / 'docs/evidence'; self.evidence.mkdir(parents=True)
        self.directory = Path(self.temp.name) / 'run'
        self.old = json.dumps({'count': 1, 'passed': True, 'checks': [{'ok': True}], 'recorded': 'old'})
        for name in [*artifact.REPORTS.values(), artifact.HISTORICAL]:
            (self.evidence / name).write_text(self.old)
        artifact.prepare(self.root, self.directory, '42', '2', 'base-commit')

    def report(self, name, passed=True):
        raw = json.dumps({'count': 1, 'passed': passed, 'checks': [{'ok': passed}], 'recorded': 'fresh'})
        (self.evidence / name).write_text(raw)
        return raw.encode()

    def manifest(self):
        return json.loads((self.directory / 'reports/run-manifest.json').read_text())

    def test_early_failure_never_packages_old_passes_or_history(self):
        self.assertTrue(artifact.collect(self.root, self.directory, {'first_project': {'outcome': 'failure'}}))
        self.assertEqual([p.name for p in (self.directory / 'reports').iterdir()], ['run-manifest.json'])
        self.assertTrue(all(not r['included'] for r in self.manifest()['reports']))
        self.assertEqual((self.evidence / artifact.HISTORICAL).read_text(), self.old)

    def test_fresh_success_and_failed_partial_retain_honest_outcomes(self):
        good = self.report('docker-first-project.json')
        bad = self.report('docker-upgrade-checks.json', passed=False)
        outcomes = {'first_project': {'outcome': 'success'}, 'upgrade': {'outcome': 'failure'}}
        self.assertTrue(artifact.collect(self.root, self.directory, outcomes))
        self.assertEqual((self.directory / 'reports/docker-first-project.json').read_bytes(), good)
        self.assertEqual((self.directory / 'reports/docker-upgrade-checks.json').read_bytes(), bad)
        rows = {r['step']: r for r in self.manifest()['reports']}
        self.assertFalse(rows['upgrade']['report_passed'])
        self.assertEqual(rows['upgrade']['step_outcome'], 'failure')
        self.assertEqual(self.manifest()['workflow_head'], 'base-commit')

    def test_success_without_new_report_fails_packaging(self):
        self.assertFalse(artifact.collect(self.root, self.directory, {'studio': {'outcome': 'success'}}))
        self.assertFalse(self.manifest()['packaging_valid'])
        self.assertFalse((self.directory / 'reports/docker-studio-checks.json').exists())

    def test_invalid_schema_and_symlink_do_not_upload_payloads(self):
        (self.evidence / 'docker-studio-checks.json').write_text('{"count":9,"checks":[],"passed":true}')
        target = self.root / 'foreign'; target.write_text('private fixture')
        path = self.evidence / 'docker-first-project.json'; path.unlink(); path.symlink_to(target)
        self.assertFalse(artifact.collect(self.root, self.directory, {'studio': {'outcome': 'success'}, 'first_project': {'outcome': 'success'}}))
        self.assertEqual([p.name for p in (self.directory / 'reports').iterdir()], ['run-manifest.json'])
        self.assertEqual(target.read_text(), 'private fixture')

    def test_changed_unreached_report_and_foreign_wildcard_are_excluded(self):
        self.report('docker-studio-checks.json')
        (self.evidence / 'docker-unlisted-secret.json').write_text('private fixture')
        self.assertTrue(artifact.collect(self.root, self.directory, {'studio': {'outcome': 'skipped'}}))
        self.assertEqual([p.name for p in (self.directory / 'reports').iterdir()], ['run-manifest.json'])

    def test_empty_or_contradictory_pass_report_is_not_accepted(self):
        for checks in ([], [{'ok': False}]):
            with self.subTest(checks=checks):
                (self.evidence / 'docker-studio-checks.json').write_text(json.dumps({
                    'count': len(checks), 'passed': True, 'checks': checks}))
                with tempfile.TemporaryDirectory() as output:
                    directory = Path(output)
                    (directory / 'baseline.json').write_bytes((self.directory / 'baseline.json').read_bytes())
                    (directory / 'staging').mkdir()
                    self.assertFalse(artifact.collect(self.root, directory, {'studio': {'outcome': 'success'}}))
                    self.assertFalse((directory / 'reports/docker-studio-checks.json').exists())

    def test_existing_run_directory_is_not_adopted(self):
        before = (self.directory / 'baseline.json').read_bytes()
        with self.assertRaises(FileExistsError):
            artifact.prepare(self.root, self.directory, '99', '1', 'another-head')
        self.assertEqual((self.directory / 'baseline.json').read_bytes(), before)

    def test_interrupted_collection_cannot_publish_without_manifest(self):
        from unittest.mock import patch
        self.report('docker-first-project.json')
        original = Path.write_text
        def interrupt(path, *args, **kwargs):
            if path.name == 'run-manifest.json': raise RuntimeError('fixture interruption')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'write_text', new=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'fixture interruption'):
                artifact.collect(self.root, self.directory, {'first_project': {'outcome': 'success'}})
        self.assertFalse((self.directory / 'reports').exists())
        self.assertTrue((self.directory / 'staging/docker-first-project.json').exists())


class EmptyHostArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'checkout'; self.root.mkdir()
        self.evidence = self.root / 'docs/evidence'; self.evidence.mkdir(parents=True)
        self.directory = Path(self.temp.name) / 'run'
        for name in artifact.EMPTY_HOST_REPORTS:
            (self.evidence / name).write_text('{"historical":true}')
        (self.evidence / artifact.HISTORICAL).write_text('{"historical":true}')
        artifact.prepare(self.root, self.directory, '42', '1', 'main-source', 'empty-host')

    def fresh(self):
        check = {'checks': [{'ok': True}], 'count': 1, 'passed': True, 'recorded': 'fresh'}
        values = {name: check for name in artifact.EMPTY_HOST_REPORTS}
        values['console-build.json'] = {'passed': True, 'problems': [], 'build': {'exit': 0}, 'page': {'assets': {}}}
        values['supervisor-unit.json'] = {'passed': True, 'verify': 'passed', 'applied': True,
                                         'running_as_root': True, 'start_deferred': True}
        for name, value in values.items(): (self.evidence / name).write_text(json.dumps(value))

    def manifest(self):
        return json.loads((self.directory / 'reports/run-manifest.json').read_text())

    def test_fresh_six_report_contract_accepts_actual_distinct_schemas(self):
        self.fresh()
        self.assertTrue(artifact.collect(self.root, self.directory, {'empty_host': {'outcome': 'success'}}))
        self.assertEqual(len(list((self.directory / 'reports').iterdir())), 7)
        rows = self.manifest()['reports']
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row['included'] and row['report_passed'] for row in rows))
        self.assertNotIn('count', next(row for row in rows if row['file'] == 'console-build.json'))
        self.assertNotIn('count', next(row for row in rows if row['file'] == 'supervisor-unit.json'))

    def test_failed_before_any_writer_publishes_manifest_only(self):
        self.assertTrue(artifact.collect(self.root, self.directory, {'empty_host': {'outcome': 'failure'}}))
        self.assertEqual([f.name for f in (self.directory / 'reports').iterdir()], ['run-manifest.json'])
        self.assertTrue(all(not row['included'] for row in self.manifest()['reports']))

    def test_failure_after_earlier_writer_keeps_reached_report_with_parent_failure(self):
        raw = json.dumps({'passed': True, 'problems': [], 'page': {}, 'build': None}).encode()
        (self.evidence / 'console-build.json').write_bytes(raw)
        self.assertTrue(artifact.collect(self.root, self.directory, {'empty_host': {'outcome': 'failure'}}))
        self.assertEqual((self.directory / 'reports/console-build.json').read_bytes(), raw)
        row = next(row for row in self.manifest()['reports'] if row['included'])
        self.assertEqual(row['step_outcome'], 'failure')
        self.assertTrue(row['report_passed'])

    def test_success_requires_fresh_copy_equal_to_rehearsal(self):
        self.fresh()
        (self.evidence / 'server-acceptance-latest.json').write_text(json.dumps({
            'checks': [{'ok': True}], 'count': 1, 'passed': True, 'recorded': 'different'}))
        self.assertFalse(artifact.collect(self.root, self.directory, {'empty_host': {'outcome': 'success'}}))
        self.assertFalse(self.manifest()['packaging_valid'])

    def test_fresh_failed_structured_evidence_is_retained_and_never_counted_as_checks(self):
        raw = json.dumps({'passed': False, 'problems': ['fixture failure'], 'page': {}, 'build': {'exit': 1}}).encode()
        (self.evidence / 'console-build.json').write_bytes(raw)
        self.assertTrue(artifact.collect(self.root, self.directory, {'empty_host': {'outcome': 'failure'}}))
        row = next(row for row in self.manifest()['reports'] if row['included'])
        self.assertFalse(row['report_passed'])
        self.assertNotIn('count', row)

    def test_success_cannot_reuse_old_ancillary_passes(self):
        self.fresh()
        (self.evidence / 'tls-termination.json').write_text('{"historical":true}')
        self.assertFalse(artifact.collect(self.root, self.directory, {'empty_host': {'outcome': 'success'}}))
        row = next(row for row in self.manifest()['reports'] if row['file'] == 'tls-termination.json')
        self.assertFalse(row['included'])
        self.assertFalse(self.manifest()['packaging_valid'])


if __name__ == '__main__': unittest.main()
