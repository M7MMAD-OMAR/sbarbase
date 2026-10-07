"""Receipt contract refusals. No fake native acceptance is minted by these tests."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import hashlib
import json
from types import SimpleNamespace

import recovery_inventory as inventory
import recovery_receipt as receipt
from test_recovery_inventory import manifest, RUNTIME, resource_fixture, shared_fixture


def candidate():
    value = manifest()
    source = value['environments'][0]
    target = resource_fixture(installation='88888888-8888-8888-8888-888888888888', seed=10000)
    return {'schema': 1, 'format': 'sbarbase-recovery-admission-v1', 'binding': copy.deepcopy(source['binding']),
            'recovery_set': value, 'recovery_set_sha256': inventory.recovery_set_digest(value),
            'encrypted_artifact': {'format': 'sbarbase-recovery-stream-v1', 'bytes': 100, 'sha256': 'e' * 64},
            'destination': {'host': {'host': 'target-host', 'daemon': 'target-daemon', 'installation': target[0]['installation']},
                            'resources': target, 'placements': [{'runtime': RUNTIME, 'placement': 'native-dedicated', 'coverage': 'dedicated-resources'}],
                            'empty_inventory_sha256': 'f' * 64},
            'source': value['source'], 'acceptance': {'SB-05:native-dedicated': {}, 'SB-13a:native-dedicated': {}},
            'native_observation': {'path': 'deploy/capabilities/evidence/future-native.json', 'sha256': 'b' * 64}}


class ReceiptTests(unittest.TestCase):
    def denied(self, value, expected=None, provider=None, source=None):
        with tempfile.TemporaryDirectory() as directory:
            provider = receipt.RegistryAcceptance(Path(directory)) if provider is None else provider
            expected = value['binding'] if expected is None else expected
            source = value['recovery_set']['environments'][0]['resources'] if source is None else source
            with self.assertRaises(inventory.Refused):
                receipt.verify_purge_receipt(value, expected, provider, source)

    def test_imports_and_projection_do_not_invoke_native_effects(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('Native effect forbidden')):
            result = receipt.expected_observation(candidate())
        self.assertEqual(result['source_state_access'], 'excluded')
        self.assertEqual(set(result['requirements']), set(receipt.REQUIREMENTS))

    def test_no_canonical_native_acceptance_never_mints_permission(self):
        self.denied(candidate())

    def test_acceptance_bool_dictionary_and_callback_are_forbidden(self):
        for provider in (True, {'acceptance': 'accepted'}, lambda *args: (_ for _ in ()).throw(AssertionError('Callback cannot run'))):
            self.denied(candidate(), provider=provider)

    def test_current_source_epoch_and_placement_changes_deny(self):
        for key, value in (('epoch', 4), ('placement', 'legacy-shared'), ('placementDigest', 'd' * 64)):
            original = candidate(); changed = copy.deepcopy(original['binding']); changed[key] = value
            self.denied(original, expected=changed)

    def test_current_physical_inventory_changes_deny(self):
        value = candidate(); changed = copy.deepcopy(value['recovery_set']['environments'][0]['resources'])
        changed[0]['id'] = 'd' * 64
        self.denied(value, source=changed)

    def test_changed_set_digest_denies(self):
        value = candidate(); value['recovery_set_sha256'] = '0' * 64
        self.denied(value)

    def test_same_host_daemon_or_installation_cannot_count_as_fresh_host(self):
        for key in ('host', 'daemon', 'installation'):
            value = candidate(); value['destination']['host'][key] = value['recovery_set']['source_host'][key]
            with self.assertRaises(inventory.Refused): receipt.expected_observation(value)

    def test_original_resource_cannot_be_reused_in_destination(self):
        value = candidate(); value['destination']['resources'][0]['id'] = value['recovery_set']['environments'][0]['resources'][0]['id']
        with self.assertRaises(inventory.Refused): receipt.expected_observation(value)

    def test_missing_empty_target_observation_denies(self):
        value = candidate(); value['destination']['empty_inventory_sha256'] = ''
        with self.assertRaises(inventory.Refused): receipt.expected_observation(value)

    def test_fixture_only_binding_cannot_authorize_purge(self):
        value = candidate(); value['binding']['coverage'] = 'fixture'
        self.denied(value)

    def test_plain_archive_or_unbound_encrypted_artifact_denies(self):
        for key, replacement in (('format', 'plain-tar'), ('sha256', ''), ('bytes', True)):
            value = candidate(); value['encrypted_artifact'][key] = replacement
            self.denied(value)

    def test_relative_checkout_is_not_a_trusted_acceptance_locator(self):
        with self.assertRaises(inventory.Refused): receipt.RegistryAcceptance(Path('.')).current('native-dedicated')

    def test_unknown_receipt_fields_cannot_smuggle_a_status_override(self):
        value = candidate(); value['accepted'] = True
        self.denied(value)

    def test_destination_unknown_or_omitted_runtime_refuses(self):
        value = candidate()
        for item in value['destination']['resources']:
            item['runtime'] = 'e_' + 'b' * 24
        with self.assertRaises(inventory.Refused): receipt.expected_observation(value)
        value = candidate(); value['destination']['placements'] = []
        with self.assertRaises(inventory.Refused): receipt.expected_observation(value)

    def test_destination_requires_complete_placement_resources(self):
        for kind in ('container', 'volume', 'directory'):
            value = candidate(); value['destination']['resources'] = [r for r in value['destination']['resources'] if r['kind'] != kind]
            with self.assertRaises(inventory.Refused): receipt.expected_observation(value)

    def test_destination_cannot_reuse_source_resource_uuid(self):
        value = candidate(); value['destination']['resources'][0]['resource'] = value['recovery_set']['environments'][0]['resources'][0]['resource']
        with self.assertRaises(inventory.Refused): receipt.expected_observation(value)

    def test_projection_survives_json_round_trip(self):
        observation = receipt.expected_observation(candidate())
        receipt.require_identity(receipt.strict_json(json.dumps(observation)), observation, 'Different observation')

    def test_duplicate_native_observation_keys_refuse(self):
        observation = receipt.expected_observation(candidate())
        raw = b'{"schema":0,' + inventory.canonical(observation)[1:]
        with self.assertRaises(inventory.Refused): receipt.strict_json(raw)
        with self.assertRaises(inventory.Refused): receipt.strict_json(b'{"nested":{"files":1,"files":2}}')

    def test_boolean_and_float_integer_substitutions_refuse(self):
        expected = receipt.expected_observation(candidate())
        changes = [(('schema',), True), (('schema',), 1.0),
                   (('encrypted_artifact', 'bytes'), 100.0),
                   (('restorer_source', 'files'), float(expected['restorer_source']['files']))]
        directory = next(i for i, r in enumerate(expected['target_resources']) if r['kind'] == 'directory')
        changes.extend([(('target_resources', directory, 'device'), True),
                        (('target_resources', directory, 'inode'), float(expected['target_resources'][directory]['inode']))])
        for path, replacement in changes:
            with self.subTest(path=path, replacement=replacement):
                changed = copy.deepcopy(expected); parent = changed
                for part in path[:-1]: parent = parent[part]
                parent[path[-1]] = replacement
                with self.assertRaises(inventory.Refused):
                    receipt.require_identity(receipt.strict_json(json.dumps(changed)), expected, 'Different observation')

    def test_source_and_acceptance_integer_types_are_exact(self):
        source = candidate()['source']
        for replacement in (True, float(source['files'])):
            changed = {**source, 'files': replacement}
            for actual, expected in ((changed, source),
                                     ({'proof': {'source': changed, 'evidence': ['log.json']}},
                                      {'proof': {'source': source, 'evidence': ['log.json']}})):
                with self.assertRaises(inventory.Refused):
                    receipt.require_identity(actual, expected, 'Different source')

    def test_nonfinite_and_oversized_native_json_refuse(self):
        for raw in (b'{"schema":NaN}', b'{"schema":Infinity}', b'{"schema":-Infinity}',
                    b' ' * (inventory.MAX_HEADER + 1)):
            with self.assertRaises(inventory.Refused): receipt.strict_json(raw)

    def test_native_observation_extra_fields_and_container_types_refuse(self):
        expected = receipt.expected_observation(candidate())
        for change in ('extra', 'tuple'):
            actual = copy.deepcopy(expected)
            if change == 'extra': actual['status_override'] = 'accepted'
            else: actual['features'] = tuple(actual['features'])
            with self.assertRaises(inventory.Refused): receipt.require_identity(actual, expected, 'Different observation')

    def test_synthetic_proof_byte_replacement_is_detected_without_admission(self):
        # These helper-only proof shapes cannot supply RegistryAcceptance or purge permission.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); proof = root / 'proof.json'
            observation = {'path': 'observation.json', 'sha256': 'b' * 64}
            rows = {'test-only': {'capability': 'SB-05', 'evidence': {'acceptance': ['proof.json']}}}
            module = SimpleNamespace(safe_file=lambda base, name: base / name)
            value = {'tests': [{'id': 'SB-05.complete-contract', 'logs': [observation]}]}
            proof.write_text(json.dumps(value))
            before = receipt.proof_snapshot(root, module, rows, observation)
            value['unrelated-field'] = 'changed contents at same filename'
            proof.write_text(json.dumps(value))
            after = receipt.proof_snapshot(root, module, rows, observation)
            with self.assertRaises(inventory.Refused): receipt.unchanged_proofs(before, after)
            value['tests'][0]['logs'] = []
            proof.write_text(json.dumps(value))
            with self.assertRaises(inventory.Refused): receipt.proof_snapshot(root, module, rows, observation)

    def test_target_shared_engine_requires_actual_target_daemon_binding(self):
        value = candidate(); target = value['destination']
        target['resources'] = shared_fixture(installation=target['host']['installation'], seed=50000)
        target['placements'][0].update(placement='legacy-shared', coverage='complete-shared-resources')
        with self.assertRaises(inventory.Refused): receipt.expected_observation(value)
        target['resources'][-1]['identity']['engine']['daemon'] = target['host']['daemon']
        # Structural projection only; native RegistryAcceptance remains unavailable.
        self.assertEqual(receipt.expected_observation(value)['target_host'], target['host'])
