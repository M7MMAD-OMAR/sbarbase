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

    def test_every_case_separately_reports_implementation_and_prerequisites(self):
        rows = drill.case_matrix()
        self.assertEqual([row['id'] for row in rows], list(drill.CASES))
        self.assertEqual(len(rows), 29)
        self.assertEqual(sum(row['implementation'] == 'runnable-helper' for row in rows), 14)
        for row in rows:
            self.assertFalse(row['native_accepted'])
            self.assertIn('assigned-runtime-role', row['missing_prerequisites'])
            if row['implementation'] == 'pending':
                self.assertEqual(row['status'], 'implementation-pending')
                self.assertIsInstance(row['pending_implementation'], str)
            else:
                self.assertEqual(row['status'], 'prerequisite-unavailable')
                self.assertIsNone(row['pending_implementation'])

    def test_executable_source_stages_do_not_complete_original_native_cases(self):
        rows = {row['id']: row for row in drill.case_matrix()}
        for key in ('disconnect-before-body-end', 'controller-interruption', 'multipart-parts'):
            self.assertTrue(rows[key]['implemented_source_stages'])
            self.assertEqual(rows[key]['implementation'], 'pending')
            self.assertFalse(rows[key]['native_accepted'])
        from test_storage_native_authority import candidate
        spec, _ = candidate()
        with patch('socket.create_connection', side_effect=AssertionError('No native socket')):
            for key in ('disconnect-before-body-end', 'multipart-parts'):
                with self.assertRaisesRegex(Refused, 'Installed'):
                    drill.run_protocol_stage(key, spec, 'http://127.0.0.1:8000', 'tenant.unit',
                                             '/private/source-only.json', 'unit', 'x')

    def test_disabled_features_cannot_silently_drop_required_cases(self):
        enabled = sorted({item for row in drill.case_matrix() for item in row['prerequisites']})
        rows = drill.case_matrix(enabled, ['tus', 'queue', 'redis', 'remote_s3'])
        self.assertEqual([row['id'] for row in rows], list(drill.CASES))
        for row in rows:
            if row['feature_disabled']:
                self.assertTrue(row['missing_prerequisites'])
                self.assertNotEqual(row['status'], 'runtime-unrun')
        ready = next(row for row in rows if row['id'] == 'sdk-new-upload')
        self.assertEqual(ready['status'], 'runtime-unrun')
        self.assertFalse(ready['native_accepted'])

    def test_disabled_signed_requires_signed_and_standard_prerequisites(self):
        available = sorted({item for row in drill.case_matrix() for item in row['prerequisites']} |
                           {'standard-enabled', 'signed-enabled'})
        rows = {row['id']: row for row in drill.case_matrix(available, ['signed'])}
        for case_id in ('sdk-signed-upload', 'sdk-signed-download'):
            with self.subTest(case_id=case_id):
                row = rows[case_id]
                self.assertIn('signed-enabled', row['prerequisites'])
                self.assertIn('standard-enabled', row['prerequisites'])
                self.assertTrue(row['feature_disabled'])
                self.assertIn('signed-enabled', row['missing_prerequisites'])
                self.assertEqual(row['status'], 'prerequisite-unavailable')
                self.assertFalse(row['native_accepted'])
        self.assertEqual(rows['sdk-new-upload']['status'], 'runtime-unrun')
        self.assertFalse(rows['sdk-new-upload']['native_accepted'])

    def test_remote_queue_and_redis_keep_separate_bounded_authority_requirements(self):
        for row in drill.case_matrix():
            for feature in ('remote_s3', 'queue', 'redis'):
                if feature + '-enabled' in row['prerequisites']:
                    self.assertIn('separately-bounded-' + feature + '-authority', row['prerequisites'])
                    self.assertEqual(row['implementation'], 'pending')

    def test_actual_fault_conditions_are_required_for_controller_fault_helpers(self):
        rows = {row['id']: row for row in drill.case_matrix()}
        self.assertTrue(rows['lost-stop-acknowledgement']['pending_case_stages'])
        self.assertTrue(rows['changed-generation-refusal']['pending_case_stages'])
        self.assertIn('actual-owned-stop-delivered-and-acknowledgement-lost',
                      rows['lost-stop-acknowledgement']['missing_prerequisites'])
        self.assertIn('actual-installed-generation-changed-after-enrollment',
                      rows['changed-generation-refusal']['missing_prerequisites'])
        self.assertIn('separately-bounded-original-multiple-writer-launches',
                      rows['multiple-writer-processes']['missing_prerequisites'])

    def test_case_matrix_is_detached_and_rejects_ambiguous_input_types(self):
        rows = drill.case_matrix()
        rows[0]['prerequisites'].clear()
        self.assertTrue(drill.case_matrix()[0]['prerequisites'])
        for args in [({'assigned-runtime-role': True}, ()), ((True,), ()), ((), (None,))]:
            with self.assertRaises(Refused):
                drill.case_matrix(*args)

    def test_protocol_helper_negative_units_under_declared_bun_phase(self):
        """Mock unit checks only; these cannot be listed as original-native cases."""
        import tempfile
        stub = "const createClient = () => {throw new Error('No vendor client instantiated by source units')};"
        source = drill.SDK_FIXTURE.replace("import {createClient} from '@supabase/supabase-js';", stub)
        with tempfile.TemporaryDirectory() as root:
            fixture = Path(root) / 'fixture.mjs'
            fixture.write_text(source)
            script = Path(root) / 'units.mjs'
            script.write_text(PROTOCOL_HELPER_SOURCE_UNITS)
            result = subprocess.run(['bun', str(script)], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        units = json.loads(result.stdout)
        self.assertEqual(units['checks'], 10)
        self.assertFalse(units['native_accepted'])

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


PROTOCOL_HELPER_SOURCE_UNITS = r"""
import assert from 'node:assert/strict';
import {exerciseNativeSdk, exerciseLargeStream, exerciseTus, nativeClient} from './fixture.mjs';
globalThis.fetch = () => {throw new Error('Source units prohibit network')};
let checks = 0;
const absent = {error:{status:404}, data:null};
const sdk = (mode='good') => {
  const objects = new Map();
  const storage = {
    upload:async (key, bytes) => {objects.set(key, bytes.slice()); return {data:{path:key}}},
    download:async key => objects.has(key) ? {data:new Blob([objects.get(key)])} : absent,
    createSignedUploadUrl:async () => ({data:{token:'unit-token'}}),
    uploadToSignedUrl:async (key, _token, bytes) => {
      if (mode === 'signed-failure') return {error:{status:500}};
      objects.set(key, bytes.slice()); return {data:{path:key}};
    },
    createSignedUrl:async () => ({data:{signedUrl:'http://unit.invalid/storage/v1/object/sign/unit/signed.bin'}}),
    remove:async keys => {if (mode !== 'delete-failure') keys.forEach(key => objects.delete(key)); return {data:[]}},
  };
  return {client:{storage:{from:() => storage}}, objects,
    signed:async () => new Response(mode === 'signed-wrong-bytes' ? new Uint8Array([1]) : objects.get('signed.bin'))};
};
{
  const s = sdk(); const seen = [];
  const result = await exerciseNativeSdk(s.client, 'unit', id => seen.push(id), s.signed);
  assert.equal(result.observations, 6); assert.equal(seen.length, 6);
  assert.deepEqual(result.observed_ids, seen); assert.equal(result.native_settlement_accepted, false);
  checks++;
}
for (const mode of ['signed-failure', 'signed-wrong-bytes', 'delete-failure']) {
  const s = sdk(mode); const seen = [];
  await assert.rejects(() => exerciseNativeSdk(s.client, 'unit', id => seen.push(id), s.signed));
  if (mode === 'signed-failure') assert(!seen.includes('sdk-signed-upload'));
  if (mode === 'signed-wrong-bytes') assert(!seen.includes('sdk-signed-download'));
  if (mode === 'delete-failure') assert(!seen.includes('sdk-delete'));
  checks++;
}
{
  const s = sdk(); let consumed = 0;
  const result = await exerciseLargeStream(s.client, 'unit', async (_path, init) => {
    const chunks = []; const reader = init.body.getReader();
    for (;;) {const value = await reader.read(); if (value.done) break;
      consumed += value.value.length; chunks.push(value.value)}
    s.objects.set('settlement-stream.bin', new Uint8Array(await new Blob(chunks).arrayBuffer()));
    return new Response('{}');
  });
  assert.equal(consumed, 8 * 1024 * 1024); assert.equal(result.observations, 1);
  assert.equal(result.native_settlement_accepted, false); checks++;
}
for (const mode of ['resume', 'cancel']) {
  const s = sdk(); let offset = 0; let deleted = false; const paths = [];
  const key = 'settlement-tus-' + mode + '.bin';
  const id = Buffer.from('unit/' + key + '/12345678-1234-1234-1234-123456789abc').toString('base64url');
  const location = 'http://unit.invalid/upload/resumable/' + id;
  const request = async (path, init) => {
    if (init.method === 'POST') return new Response(null, {status:201, headers:{location}});
    paths.push(path); assert.equal(path, location);
    if (init.method === 'PATCH') {
      assert.equal(init.headers['upload-offset'], String(offset)); offset += init.body.length;
      if (offset === 32 * 1024) s.objects.set(key, new Uint8Array(offset).fill(43));
      return new Response(null, {status:204, headers:{'upload-offset':String(offset)}});
    }
    if (init.method === 'DELETE') {deleted = true; return new Response(null, {status:204})}
    if (deleted) return new Response(null, {status:404});
    return new Response(null, {status:200, headers:{'upload-offset':String(offset), 'upload-length':String(32 * 1024)}});
  };
  const result = await exerciseTus(s.client, 'unit', request, mode);
  assert.equal(result.observations, 1); assert.equal(result.native_settlement_accepted, false);
  assert.equal(result.observed_ids[0], 'same-version-tus-' + mode);
  assert(paths.length >= 3); checks++;
}
{
  const s = sdk();
  await assert.rejects(() => exerciseTus(s.client, 'unit', async () => new Response(null,
    {status:201, headers:{location:'http://unit.invalid/upload/resumable/' + Buffer.from('unit/settlement-tus-resume.binEVIL/12345678-1234-1234-1234-123456789abc').toString('base64url')}}), 'resume'));
  checks++;
}
{
  assert.throws(() => nativeClient('http://unit.invalid/', 'private-unit-key', 'wrong-tenant'));
  checks++;
}
{
  const s = sdk();
  await assert.rejects(() => exerciseLargeStream(s.client, 'unit', async () => new Response(null, {status:200})));
  checks++;
}
console.log(JSON.stringify({checks, native_accepted:false}));
"""


if __name__ == '__main__':
    unittest.main()
