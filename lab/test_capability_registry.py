"""Synthetic proof fixtures exercise integrity, not actual runtime acceptance."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capability_verifier', ROOT / 'deploy/verify/capability_registry.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def copy_inputs(destination):
    """Copy the declared validation inputs, with all fixed source roots present."""
    registry = json.loads((ROOT / verifier.REGISTRY).read_text())
    names = set(verifier.SOURCE_FILES) | {verifier.REGISTRY,
            'deploy/verify/capability_registry.py', 'lab/test_capability_registry.py'}
    names.update(item['path'] for item in registry['bindings'])
    for capability in registry['capabilities']:
        for placement in capability['placements'].values():
            names.update(placement['implementation']['sources'])
            names.update(test['runner'] for test in placement['tests'] if test['runner'])
    for directory in verifier.SOURCE_DIRECTORIES:
        (destination / directory).mkdir(parents=True, exist_ok=True)
    for name in names:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)


def write_synthetic_scope_proofs(root, capability_id):
    """Freeze both local scopes before making self-attested integration fixtures."""
    fixture = CapabilityRegistryTests()
    fixture.root = root
    fixture.registry = json.loads((root / verifier.REGISTRY).read_text())
    fixture.now = datetime.now(timezone.utc)
    capability = next(c for c in fixture.registry['capabilities'] if c['id'] == capability_id)
    for name in ('legacy-shared', 'native-dedicated'):
        placement = capability['placements'][name]
        placement['implementation']['state'] = 'implemented'
        placement['implementation']['sources'] = placement['implementation']['sources'] or ['deploy/verify/reference_bundle.py']
        path = verifier.PROOF_DIRECTORY + '/' + capability_id + '.' + name + '.acceptance.json'
        placement['acceptance'] = {'state': 'claimed', 'evidence': [path]}
        for test in placement['tests']:
            if test['gate'] == 'acceptance' and not test['runner']:
                test['runner'] = 'deploy/verify/reference_bundle.py'
                test['command'] = ['python3', test['runner'], 'verify', '--bundle', verifier.REFERENCE_BUNDLE]
    fixture.write_registry()
    for name in ('legacy-shared', 'native-dedicated'):
        fixture.proof(capability_id, name)


class CapabilityRegistryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        copy_inputs(self.root)
        self.registry = json.loads((self.root / verifier.REGISTRY).read_text())
        self.now = datetime.now(timezone.utc)

    def write_registry(self):
        (self.root / verifier.REGISTRY).write_text(json.dumps(self.registry, indent=2) + '\n')

    def proof(self, capability_id='SB-01', placement_name='legacy-shared', gate='acceptance'):
        capability = next(c for c in self.registry['capabilities'] if c['id'] == capability_id)
        placement = capability['placements'][placement_name]
        placement['implementation']['state'] = 'implemented'
        name = verifier.PROOF_DIRECTORY + '/' + capability_id + '.' + placement_name + '.' + gate + '.json'
        placement[gate] = {'state': 'claimed', 'evidence': [name]}
        for test in placement['tests']:
            if test['gate'] == gate:
                test['command'] = test['command'] or ['bun', test['runner']]
        self.write_registry()
        scope = {'capability': capability_id, 'placement': placement_name, 'gate': gate,
                 'contract': placement['contract']}
        proof = {'schema_version': 1, 'scope': scope, 'source': verifier.source_identity(self.root),
                 'registry_sha256': verifier.sha(verifier.canonical(self.registry)),
                 'bindings_sha256': verifier.checked_bindings(self.root, self.registry),
                 'reference': self.registry['reference'], 'host_profile': placement['host_profile'],
                 'observed_at': (self.now - timedelta(minutes=1)).isoformat(),
                 'expires_at': (self.now + timedelta(days=1)).isoformat(), 'tests': []}
        for required in placement['tests']:
            if required['gate'] != gate:
                continue
            test = {key: required[key] for key in ('id', 'kind', 'contract', 'fixtures', 'command')}
            test.update({'outcome': 'passed', 'exit_code': 0, 'assertions': 1,
                         'failures': 0, 'errors': 0, 'skipped': 0})
            raw = dict(test, execution={key: proof[key] for key in verifier.EXECUTION_IDENTITY_FIELDS})
            path = verifier.PROOF_DIRECTORY + '/' + test['id'] + '.' + placement_name + '.' + gate + '.json'
            file = self.root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(raw) + '\n')
            log = {'path': path, 'sha256': verifier.sha(file.read_bytes())}
            test.update({'logs': [log], 'observation': log})
            proof['tests'].append(test)
        self.proof_name = name
        self.proof_value = proof
        self.write_proof()
        return proof

    def write_proof(self):
        path = self.root / self.proof_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.proof_value) + '\n')

    def errors(self, accept=False, selections=None):
        return verifier.check(self.root, require_acceptance=accept, selections=selections, now=self.now)[1]

    def assert_refusal(self, fragment):
        self.assertTrue(any(fragment in error for error in self.errors()), self.errors())

    def test_honest_unproven_registry_validates_but_cannot_accept(self):
        self.assertEqual(self.errors(), [])
        self.assertTrue(any('proof missing' in error for error in self.errors(True)))
        rows, _ = verifier.check(self.root)
        self.assertTrue(all(row['acceptance'] == row['production'] == 'unproven' for row in rows))

    def test_complete_positive_scoped_fixture_accepts_only_selected_contract(self):
        self.proof()
        rows, errors = verifier.check(self.root, True, {('SB-01', 'legacy-shared')}, self.now)
        self.assertEqual(errors, [])
        accepted = [row for row in rows if row['acceptance'] == 'accepted']
        self.assertEqual([(row['capability'], row['placement']) for row in accepted], [('SB-01', 'legacy-shared')])
        self.assertEqual(accepted[0]['production'], 'unproven')

    def test_identity_is_relocatable_and_binds_inventory_bytes_and_modes(self):
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / 'clone'
            shutil.copytree(self.root, other)
            baseline = verifier.source_identity(self.root)
            self.assertEqual(baseline, verifier.source_identity(other))
            file = other / 'src/new-source.py'
            file.write_text('x = 1\n')
            self.assertNotEqual(baseline, verifier.source_identity(other))
            file.unlink()
            file = other / 'deploy/verify/capability_registry.py'
            file.chmod(file.stat().st_mode ^ 0o100)
            self.assertNotEqual(baseline, verifier.source_identity(other))

    def test_source_registry_and_test_inventory_changes_invalidate_proof(self):
        for target in ('deploy/verify/capability_registry.py', 'lab/test_capability_registry.py'):
            with self.subTest(target=target):
                self.proof()
                file = self.root / target
                original = file.read_bytes()
                file.write_bytes(original + b'\n# changed\n')
                self.assert_refusal('source identity mismatch')
                file.write_bytes(original)
        self.proof()
        self.registry['capabilities'][0]['title'] += ' changed'
        self.write_registry()
        self.assert_refusal('source identity mismatch')

    def test_missing_proof_and_passed_boolean_alone_refuse(self):
        self.proof()
        (self.root / self.proof_name).unlink()
        self.assert_refusal('missing regular file')
        (self.root / self.proof_name).write_text('{"passed": true}')
        self.assert_refusal('missing')

    def test_stale_future_and_overlong_windows_refuse(self):
        self.proof()
        original = copy.deepcopy(self.proof_value)
        for field, value in [('expires_at', self.now.isoformat()),
                             ('observed_at', (self.now + timedelta(days=1)).isoformat()),
                             ('expires_at', (self.now + timedelta(days=31)).isoformat()),
                             ('observed_at', '2026-10-06')]:
            with self.subTest(field=field, value=value):
                self.proof_value = dict(original, **{field: value})
                self.write_proof()
                self.assertTrue(self.errors())

    def test_scope_placement_kind_reference_and_config_mismatches_refuse(self):
        self.proof('sql-rest-rpc-rls')
        original = copy.deepcopy(self.proof_value)
        changes = [('scope', dict(original['scope'], placement='native-dedicated')),
                   ('reference', dict(original['reference'], commit='0' * 40)),
                   ('bindings_sha256', '0' * 64), ('registry_sha256', '0' * 64),
                   ('host_profile', 'unrelated-host')]
        for field, value in changes:
            with self.subTest(field=field):
                self.proof_value = dict(original, **{field: value})
                self.write_proof()
                self.assertTrue(self.errors())
        self.proof_value = copy.deepcopy(original)
        self.proof_value['tests'][0]['kind'] = 'unit'
        self.write_proof()
        self.assert_refusal('test kind')

    def test_failed_skipped_empty_and_boolean_counts_refuse(self):
        self.proof()
        original = copy.deepcopy(self.proof_value)
        for field, value in [('outcome', 'failed'), ('outcome', 'skipped'), ('exit_code', 1),
                             ('failures', 1), ('errors', 1), ('skipped', 1),
                             ('assertions', 0), ('assertions', True)]:
            with self.subTest(field=field, value=value):
                self.proof_value = copy.deepcopy(original)
                self.proof_value['tests'][0][field] = value
                self.write_proof()
                self.assertTrue(self.errors())

    def test_required_test_inventory_cannot_be_empty_extra_or_duplicated(self):
        self.proof()
        original = copy.deepcopy(self.proof_value)
        for tests in ([], original['tests'] * 2,
                      [dict(original['tests'][0], id='arbitrary.unit')]):
            self.proof_value = dict(original, tests=tests)
            self.write_proof()
            self.assertTrue(self.errors())

    def test_altered_command_help_is_not_runtime_execution(self):
        self.proof('sql-rest-rpc-rls')
        self.proof_value['tests'][0]['command'].append('--help')
        self.write_proof()
        self.assert_refusal('execution command')

    def test_missing_tampered_empty_and_mismatched_raw_logs_refuse(self):
        self.proof()
        log = self.proof_value['tests'][0]['logs'][0]
        path = self.root / log['path']
        original = path.read_bytes()
        for content in (b'', b'changed', b'{}'):
            path.write_bytes(content)
            self.assert_refusal('raw log')
        path.write_bytes(b'{}\n')
        log['sha256'] = verifier.sha(path.read_bytes())
        self.write_proof()
        self.assert_refusal('raw observation')
        path.write_bytes(original)
        path.unlink()
        self.assert_refusal('missing regular file')

    def test_unsafe_paths_and_symlinked_logs_refuse(self):
        self.proof()
        original = copy.deepcopy(self.proof_value)
        for name in ('/etc/passwd', '../outside', verifier.PROOF_DIRECTORY + '/../../package.json',
                     verifier.PROOF_DIRECTORY + '/./fixture.json'):
            self.proof_value = copy.deepcopy(original)
            self.proof_value['tests'][0]['logs'][0]['path'] = name
            self.write_proof()
            self.assertTrue(self.errors())
        self.proof_value = original
        self.write_proof()
        path = self.root / original['tests'][0]['logs'][0]['path']
        path.unlink()
        path.symlink_to(self.root / 'package.json')
        self.assert_refusal('symlink')

    def test_unknown_placement_and_partial_inventory_are_refused(self):
        placement = self.registry['capabilities'][0]['placements'].pop('cloud-only')
        self.registry['capabilities'][0]['placements']['mystery'] = placement
        self.write_registry()
        self.assertTrue(self.errors())
        self.assertTrue(verifier.check(self.root, selections={('unknown', 'legacy-shared')})[1])

    def test_roadmap_and_native_requirements_cannot_be_narrowed(self):
        original = copy.deepcopy(self.registry)
        self.registry['capabilities'].pop()
        self.write_registry()
        self.assert_refusal('inventory is incomplete')
        self.registry = original
        capability = next(c for c in self.registry['capabilities'] if c['id'] == 'SB-13a')
        capability['placements']['native-dedicated']['tests'].pop(0)
        self.write_registry()
        self.assert_refusal('native admission')

    def test_complete_slice_mapping_and_test_inventory_cannot_be_narrowed(self):
        original = copy.deepcopy(self.registry)
        for capability_id in ('foundation-reference-distribution', 'public-release'):
            for narrow in ('mapping', 'inventory'):
                with self.subTest(capability=capability_id, narrow=narrow):
                    self.registry = copy.deepcopy(original)
                    capability = next(c for c in self.registry['capabilities'] if c['id'] == capability_id)
                    if narrow == 'mapping':
                        capability['gauntlet_slices'] = []
                    else:
                        capability['placements']['legacy-shared']['tests'].pop(0)
                    self.write_registry()
                    self.assertTrue(self.errors())

    def test_binding_byte_changes_and_missing_config_refuse(self):
        file = self.root / 'lab/images.lock.json'
        original = file.read_bytes()
        file.write_bytes(original + b'\n')
        self.assert_refusal('binding checksum mismatch')
        file.unlink()
        self.assert_refusal('missing regular file')

    def test_malformed_and_duplicate_key_json_errors_are_graceful(self):
        path = self.root / verifier.REGISTRY
        for text in ('[]', 'null', '{', '{"schema_version":1,"schema_version":1}'):
            with self.subTest(text=text):
                path.write_text(text)
                self.assertTrue(self.errors())

    def test_malformed_schema_and_secondary_metadata_refuse_gracefully(self):
        for name in ('deploy/capabilities/registry.schema.json', verifier.REFERENCE, 'package.json'):
            file = self.root / name
            original = file.read_bytes()
            for value in ('[]', 'null'):
                with self.subTest(name=name, value=value):
                    file.write_text(value)
                    self.assertTrue(self.errors())
            file.write_bytes(original)

    def test_native_cron_fragment_and_history_do_not_accept_sb13a(self):
        rows, errors = verifier.check(self.root, True, {('SB-13a', 'native-dedicated')}, self.now)
        self.assertTrue(any('SB-13a/native-dedicated' in error for error in errors))
        row = next(row for row in rows if row['capability'] == 'SB-13a' and row['placement'] == 'native-dedicated')
        self.assertEqual(row['acceptance'], 'unproven')
        self.assertEqual({test['id'].split('.')[-1] for test in row['required_tests'] if test['gate'] == 'acceptance'},
                         {'original-startup', 'native-identities', 'cron-http-effects', 'sdk-flow', 'fenced-recovery'})

    def test_production_cannot_inherit_unit_or_acceptance_proof(self):
        self.proof()
        capability = self.registry['capabilities'][0]
        capability['placements']['legacy-shared']['production'] = {'state': 'claimed', 'evidence': [self.proof_name]}
        self.write_registry()
        self.assertTrue(self.errors())

    def test_source_mutation_during_verification_revokes_accepted_rows(self):
        self.proof()
        original = verifier.verify_proof

        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            with (self.root / 'lab/test_capability_registry.py').open('a') as file:
                file.write('\n# changed during verification\n')
            return result

        with patch.object(verifier, 'verify_proof', side_effect=mutate):
            rows, errors = verifier.check(self.root, now=self.now)
        self.assertTrue(any('changed during verification' in error for error in errors))
        self.assertTrue(all(row['acceptance'] == 'unproven' for row in rows))

    def test_source_deletion_during_verification_revokes_accepted_rows(self):
        self.proof()
        original = verifier.verify_proof

        def remove(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / 'package.json').unlink()
            return result

        with patch.object(verifier, 'verify_proof', side_effect=remove):
            rows, errors = verifier.check(self.root, now=self.now)
        self.assertTrue(errors)
        self.assertTrue(all(row['acceptance'] == 'unproven' for row in rows))

    def test_old_raw_observation_cannot_rebind_to_changed_source(self):
        self.proof()
        log = self.root / self.proof_value['tests'][0]['observation']['path']
        old_bytes = log.read_bytes()
        (self.root / 'src/changed.py').write_text('changed = True\n')
        self.proof_value['source'] = verifier.source_identity(self.root)
        self.write_proof()
        self.assert_refusal('raw observation disagrees')
        self.assertEqual(log.read_bytes(), old_bytes)

    def test_expired_raw_observation_cannot_be_renewed_only_in_envelope(self):
        self.now -= timedelta(days=10)
        self.proof()
        log = self.root / self.proof_value['tests'][0]['observation']['path']
        old_bytes = log.read_bytes()
        self.now += timedelta(days=10)
        self.assert_refusal('stale')
        self.proof_value['observed_at'] = (self.now - timedelta(minutes=1)).isoformat()
        self.proof_value['expires_at'] = (self.now + timedelta(days=1)).isoformat()
        self.write_proof()
        self.assert_refusal('raw observation disagrees')
        self.assertEqual(log.read_bytes(), old_bytes)

    def test_raw_execution_identity_binds_config_reference_and_host_envelope(self):
        for change in ('config', 'reference', 'host'):
            with self.subTest(change=change):
                self.proof()
                log = self.root / self.proof_value['tests'][0]['observation']['path']
                old_bytes = log.read_bytes()
                if change == 'config':
                    file = self.root / 'lab/images.lock.json'
                    file.write_bytes(file.read_bytes() + b'\n')
                    binding = next(b for b in self.registry['bindings'] if b['id'] == 'images')
                    binding['sha256'] = verifier.sha(file.read_bytes())
                elif change == 'reference':
                    file = self.root / verifier.REFERENCE_BUNDLE
                    value = json.loads(file.read_text())
                    value['scope'] += '; changed reference declaration'
                    file.write_text(json.dumps(value) + '\n')
                    binding = next(b for b in self.registry['bindings'] if b['id'] == 'reference-bundle')
                    binding['sha256'] = verifier.sha(file.read_bytes())
                else:
                    capability = next(c for c in self.registry['capabilities'] if c['id'] == 'SB-01')
                    capability['placements']['legacy-shared']['host_profile'] = 'changed-host'
                    self.proof_value['host_profile'] = 'changed-host'
                self.write_registry()
                self.proof_value['source'] = verifier.source_identity(self.root)
                self.proof_value['registry_sha256'] = verifier.sha(verifier.canonical(self.registry))
                self.proof_value['bindings_sha256'] = verifier.checked_bindings(self.root, self.registry)
                self.write_proof()
                self.assert_refusal('raw observation disagrees')
                self.assertEqual(log.read_bytes(), old_bytes)

    def test_complete_looking_foundation_proof_cannot_override_unresolved_reference(self):
        capability = next(c for c in self.registry['capabilities'] if c['id'] == 'foundation-reference-distribution')
        for test in capability['placements']['legacy-shared']['tests']:
            if test['gate'] == 'acceptance':
                test['runner'] = 'deploy/verify/reference_bundle.py'
                test['command'] = ['python3', test['runner'], 'verify', '--bundle', verifier.REFERENCE_BUNDLE]
        self.proof('foundation-reference-distribution')
        self.assert_refusal('reference image packet remains incomplete')


if __name__ == '__main__':
    unittest.main()
