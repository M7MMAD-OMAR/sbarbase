"""Source preparation gates, never native runtime acceptance."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from storage_write_settlement import Refused


SPEC = importlib.util.spec_from_file_location('storage_settlement_drill',
                                             Path(__file__).with_name('disposable-storage-settlement-drill.py'))
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)


class NativePreparationTests(unittest.TestCase):
    def test_plan_has_no_runtime_effect_and_exact_owned_resources(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('No external effects')):
            plan = drill.plan('12345678-1234-1234-1234-123456789abc')
        self.assertEqual(plan['status'], 'source-preparation-native-unrun')
        resources = plan['resources']
        self.assertEqual(resources['network'], 'sbarbase-settlement-12345678123412341234123456789abc-net')
        self.assertEqual(resources['published_ports'], [])
        self.assertEqual(len(set(resources['containers'])), 4)
        self.assertEqual(resources['local_phase'], {'max_running': 2, 'memory_mib': 512, 'cpus': .5})
        self.assertEqual(resources['multiwriter_phase'], {'max_running': 3, 'memory_mib': 768, 'cpus': .75})

    def test_collision_free_plan_requires_actual_random_operation_input(self):
        one = drill.plan('12345678-1234-1234-1234-123456789abc')
        two = drill.plan('abcdefab-abcd-abcd-abcd-abcdefabcdef')
        self.assertNotEqual(one['resources']['prefix'], two['resources']['prefix'])
        with self.assertRaises(Refused):
            drill.plan('../escape')

    def observations(self):
        return {'version': 1, 'operation': '12345678-1234-1234-1234-123456789abc',
                'source_sha256': 'a' * 64, 'unresolved_resources': [],
                'observations': [{'id': key, 'artifact_sha256': 'b' * 64,
                                  'expected': 'met', 'observed': 'met'} for key in drill.CASES]}

    def test_complete_structural_inventory_never_grants_actual_native_acceptance(self):
        result = drill.validate_native_observations(self.observations())
        self.assertTrue(result['complete_inventory'])
        self.assertFalse(result['native_accepted'])

    def test_each_required_actual_case_is_individually_mandatory(self):
        for key in drill.CASES:
            with self.subTest(key=key):
                value = self.observations()
                value['observations'] = [row for row in value['observations'] if row['id'] != key]
                with self.assertRaises(Refused):
                    drill.validate_native_observations(value)

    def test_duplicate_success_cannot_replace_a_required_native_case(self):
        value = self.observations()
        value['observations'][-1] = copy.deepcopy(value['observations'][0])
        with self.assertRaises(Refused):
            drill.validate_native_observations(value)

    def test_missing_cleanup_and_negative_observations_are_retained_as_failure(self):
        for field, changed in [('unresolved_resources', ['owned-resource']), ('version', True)]:
            value = self.observations()
            value[field] = changed
            with self.assertRaises(Refused):
                drill.validate_native_observations(value)
        for changed in ('unrun', 'failed', 'skipped'):
            value = self.observations()
            value['observations'][0]['observed'] = changed
            with self.assertRaises(Refused):
                drill.validate_native_observations(value)

    def test_execute_refuses_before_subprocess_or_allocation(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('No allocation')):
            with self.assertRaisesRegex(Refused, 'unassigned'):
                drill.main(['--execute'])

    def test_fixture_export_is_pure_and_plan_binds_its_exact_bytes(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(drill.main(['--sdk-fixture']), 0)
        import hashlib
        self.assertEqual(hashlib.sha256(output.getvalue().encode()).hexdigest(), drill.plan()['sdk_fixture_sha256'])

    def test_sdk_fixture_syntax_is_valid_without_importing_or_running_vendor_services(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'native-sdk-fixture.js'
            source.write_text(drill.SDK_FIXTURE)
            result = subprocess.run(['bun', 'build', '--target=bun', '--external=*', str(source)],
                                    text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_fixture_receipt_roundtrips_strict_control_codec_without_native_admission(self):
        import tempfile
        from test_storage_write_settlement import manifest, DiskFixtureAuthority
        from storage_write_settlement import Journal, SourceSettlement
        with tempfile.TemporaryDirectory() as root:
            declared = manifest()
            receipt = SourceSettlement(declared, Journal(Path(root)), DiskFixtureAuthority(declared)).settle()
        expected = {key: value for key, value in receipt.items()
                    if key not in ('reconciliation_sha256', 'journal_sha256', 'sequence')}
        observations = {'sourceInventory': declared['sourceInventory'], 'liveInventory': declared['sourceInventory'],
                        'liveFenceToken': receipt['fence_token'],
                        'currentJournalEntry': {key: receipt[key] for key in
                                                ('reconciliation_sha256', 'journal_sha256', 'sequence')}}
        script = ("import {compareSourceSettlementReceipt} from './src/control/storage-settlement-contract.ts';"
                  "const p=JSON.parse(await Bun.stdin.text());"
                  "console.log(JSON.stringify(compareSourceSettlementReceipt(p.receipt,p.expected,p.observations)));")
        result = subprocess.run(['bun', '-e', script],
                                input=json.dumps({'receipt': receipt, 'expected': expected, 'observations': observations}),
                                text=True, capture_output=True, timeout=15, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        compared = json.loads(result.stdout)
        self.assertTrue(compared['sourceChecksPassed'], compared['mismatches'])
        self.assertFalse(compared['nativeAdmitted'])


if __name__ == '__main__':
    unittest.main()
