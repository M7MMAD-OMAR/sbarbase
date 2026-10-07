"""Real cryptography and file refusal checks, never native recovery admission."""
import copy
import hashlib
import io
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

import fresh_host_recovery as recovery
import recovery_inventory as inventory
from test_recovery_inventory import manifest

KEY = bytes(range(32))


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / 'inputs'; self.inputs.mkdir(mode=0o700)
        self.work = self.root / 'work'; self.work.mkdir(mode=0o700)
        self.output = self.root / 'set.sbr'
        self.value = manifest(2); self.members = {}; self.payloads = {}
        for i, member in enumerate(self.value['materials']):
            data = b'private-material-' + str(i).encode() + b'\x00' + member['kind'].encode()
            path = self.inputs / str(i); path.write_bytes(data); path.chmod(0o600)
            member.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            self.members[member['name']] = path; self.payloads[member['name']] = data
        self.digest = inventory.recovery_set_digest(self.value)

    def seal(self):
        return recovery.seal_recovery(self.value, self.members, KEY, self.output)

    def stage(self, artifact, **kwargs):
        return recovery.stage_recovery(self.output, artifact, kwargs.pop('key', KEY),
                                       kwargs.pop('digest', self.digest), self.work,
                                       max_bytes=kwargs.pop('max_bytes', artifact['bytes']))

    def assert_denied(self, artifact, **kwargs):
        with self.assertRaises(inventory.Refused):
            with self.stage(artifact, **kwargs): self.fail('Invalid recovery exposed staging')
        self.assertEqual(list(self.work.iterdir()), [])

    def descriptor(self):
        return {'format': recovery.FORMAT, 'bytes': self.output.stat().st_size,
                'sha256': hashlib.sha256(self.output.read_bytes()).hexdigest()}

    def raw_package(self, header, payload=None):
        with self.output.open('wb') as output:
            writer = recovery.Encrypting(output, KEY, self.digest)
            writer.write(struct.pack('>I', len(header))); writer.write(header)
            for member in self.value['materials']:
                writer.write((payload or self.payloads)[member['name']])
            result = writer.finish()
        self.output.chmod(0o600)
        return result

    def test_complete_two_runtime_round_trip_and_private_cleanup(self):
        artifact = self.seal()
        self.assertEqual(artifact, self.descriptor())
        with self.stage(artifact) as staged:
            self.assertEqual(staged.manifest, self.value)
            for expected, actual in zip(self.value['environments'], staged.manifest['environments']):
                self.assertEqual(inventory.catalog_inventory_digest(actual['resources']), expected['binding']['inventoryDigest'])
                self.assertEqual(list(actual['resources'][0]), list(expected['resources'][0]))
            self.assertEqual(set(staged.members), set(self.members))
            self.assertEqual(stat.S_IMODE(staged.directory.stat().st_mode), 0o700)
            for name, path in staged.members.items():
                self.assertEqual(path.read_bytes(), self.payloads[name])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)

    def test_wrong_key_and_digest_refuse_before_plaintext_directory_creation(self):
        artifact = self.seal()
        for changes in ({'key': b'x' * 32}, {'digest': 'd' * 64}):
            with patch.object(recovery.os, 'mkdir', side_effect=AssertionError('Plaintext staging too early')):
                self.assert_denied(artifact, **changes)

    def test_independent_nonce_and_salt_produce_distinct_ciphertext(self):
        first = self.seal(); second_path = self.root / 'second.sbr'
        second = recovery.seal_recovery(self.value, self.members, KEY, second_path)
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual(first['bytes'], second['bytes'])

    def test_key_shape_refuses_without_publication(self):
        for key in (b'x' * 16, b'x' * 31, bytearray(KEY), 'x' * 32):
            with self.assertRaises(inventory.Refused):
                recovery.seal_recovery(self.value, self.members, key, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_ciphertext_even_with_rebound_file_hash_fails_authentication(self):
        self.seal(); raw = bytearray(self.output.read_bytes()); raw[-20] ^= 1
        self.output.write_bytes(raw)
        self.assert_denied(self.descriptor())

    def test_truncation_terminal_removal_and_trailing_bytes_refuse(self):
        self.seal(); original = self.output.read_bytes()
        for raw in (original[:4], original[:-1], original[:-(recovery.FRAME.size + recovery.TAG)], original + b'x'):
            self.output.write_bytes(raw); self.assert_denied(self.descriptor())

    def test_reordering_and_duplicate_frames_refuse(self):
        large = self.value['materials'][0]; data = b'x' * (recovery.CHUNK * 3)
        self.members[large['name']].write_bytes(data)
        large.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.digest = inventory.recovery_set_digest(self.value)
        self.seal(); raw = self.output.read_bytes()
        start = len(recovery.MAGIC) + recovery.SALT + 8
        frame_size = recovery.FRAME.size + recovery.CHUNK + recovery.TAG
        a = raw[start:start + frame_size]; b = raw[start + frame_size:start + 2 * frame_size]
        for changed in (raw[:start] + b + a + raw[start + 2 * frame_size:],
                        raw[:start] + a + a + raw[start + 2 * frame_size:]):
            self.output.write_bytes(changed); self.assert_denied(self.descriptor())

    def test_duplicate_json_keys_refuse_after_real_authentication(self):
        header = b'{"schema":0,' + recovery.manifest_header(self.value)[1:]
        self.assert_denied(self.raw_package(header))

    def test_authenticated_wrong_manifest_digest_refuses(self):
        changed = copy.deepcopy(self.value); changed['source']['sha256'] = 'e' * 64
        self.assert_denied(self.raw_package(recovery.manifest_header(changed)))

    def test_authenticated_member_hash_mismatch_never_exposes_stage(self):
        changed = self.payloads.copy(); name = self.value['materials'][0]['name']
        changed[name] = b'x' * len(changed[name])
        self.assert_denied(self.raw_package(recovery.manifest_header(self.value), changed))

    def test_missing_and_extra_member_paths_refuse_before_publication(self):
        for names in (dict(list(self.members.items())[1:]), {**self.members, 'unknown': self.root / 'foreign'}):
            with self.assertRaises(inventory.Refused):
                recovery.seal_recovery(self.value, names, KEY, self.output)
        self.assertFalse(self.output.exists())

    def test_traversal_manifest_refuses_before_source_open(self):
        changed = copy.deepcopy(self.value); changed['materials'][0]['name'] = '../foreign'
        with patch.object(recovery, 'open_path', side_effect=AssertionError('Source opened too early')):
            with self.assertRaises(inventory.Refused):
                recovery.seal_recovery(changed, self.members, KEY, self.output)

    def test_source_symlink_parent_symlink_and_hardlink_refuse(self):
        name = self.value['materials'][0]['name']; original = self.members[name]
        linked = self.inputs / 'linked'; linked.symlink_to(original)
        self.members[name] = linked
        with self.assertRaises(inventory.Refused): self.seal()
        linked.unlink(); parent = self.root / 'alias'; parent.symlink_to(self.inputs, target_is_directory=True)
        self.members[name] = parent / original.name
        with self.assertRaises(inventory.Refused): self.seal()
        self.members[name] = original; os.link(original, linked)
        with self.assertRaises(inventory.Refused): self.seal()
        self.assertFalse(self.output.exists())

    def test_nonprivate_source_and_root_refuse(self):
        name = self.value['materials'][0]['name']; self.members[name].chmod(0o644)
        with self.assertRaises(inventory.Refused): self.seal()
        self.members[name].chmod(0o600); self.root.chmod(0o755)
        with self.assertRaises(inventory.Refused): self.seal()
        self.assertFalse(self.output.exists())

    def test_existing_output_and_symlink_output_are_never_replaced(self):
        self.output.write_bytes(b'retained'); self.output.chmod(0o600)
        with self.assertRaises(inventory.Refused): self.seal()
        self.assertEqual(self.output.read_bytes(), b'retained')
        self.output.unlink(); original = self.inputs / 'retained'; original.write_bytes(b'foreign')
        self.output.symlink_to(original)
        with self.assertRaises(inventory.Refused): self.seal()
        self.assertEqual(original.read_bytes(), b'foreign')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['inputs', 'set.sbr', 'work'])

    def test_changed_source_size_and_hash_remove_partial_ciphertext(self):
        name = self.value['materials'][0]['name']; original = self.payloads[name]
        for data in (original[:-1], b'x' * len(original)):
            self.members[name].write_bytes(data)
            with self.assertRaises(inventory.Refused): self.seal()
            self.assertFalse(self.output.exists())
            self.assertFalse(any(p.name.startswith('.recovery-cipher-') for p in self.root.iterdir()))

    def test_input_mutation_during_packaging_refuses(self):
        path = self.members[self.value['materials'][0]['name']]; original_read = os.read; changed = False
        def mutate(fd, count):
            nonlocal changed
            block = original_read(fd, count)
            if not changed and block:
                changed = True; path.write_bytes(b'x' * path.stat().st_size)
            return block
        with patch.object(recovery.os, 'read', side_effect=mutate):
            with self.assertRaises(inventory.Refused): self.seal()
        self.assertFalse(self.output.exists())

    def test_ciphertext_path_replacement_between_passes_cannot_supply_new_bytes(self):
        artifact = self.seal(); original = recovery.decrypted_blocks; passes = 0
        def replacing(handle, key, digest):
            nonlocal passes
            passes += 1
            yield from original(handle, key, digest)
            if passes == 1:
                self.output.unlink(); self.output.write_bytes(b'foreign replacement')
        with patch.object(recovery, 'decrypted_blocks', side_effect=replacing):
            with self.stage(artifact) as staged:
                self.assertEqual(staged.members[self.value['materials'][0]['name']].read_bytes(),
                                 self.payloads[self.value['materials'][0]['name']])
        self.assertEqual(self.output.read_bytes(), b'foreign replacement')

    def test_transport_budget_and_artifact_identity_refuse(self):
        artifact = self.seal()
        self.assert_denied(artifact, max_bytes=artifact['bytes'] - 1)
        self.assert_denied({**artifact, 'sha256': '0' * 64})
        self.assert_denied({**artifact, 'bytes': artifact['bytes'] + 1})
        self.assert_denied({**artifact, 'bytes': float(artifact['bytes'])})

    def test_consumer_exception_cleans_private_plaintext(self):
        artifact = self.seal()
        with self.assertRaisesRegex(RuntimeError, 'consumer interrupted'):
            with self.stage(artifact): raise RuntimeError('consumer interrupted')
        self.assertEqual(list(self.work.iterdir()), [])

    def test_cleanup_refuses_replaced_member_instead_of_deleting_foreign_bytes(self):
        artifact = self.seal(); foreign = self.root / 'foreign'; foreign.write_bytes(b'retained')
        with self.assertRaises(inventory.Refused):
            with self.stage(artifact) as staged:
                path = next(iter(staged.members.values())); path.unlink(); path.symlink_to(foreign)
        self.assertEqual(foreign.read_bytes(), b'retained')
        self.assertTrue(path.is_symlink())

    def test_package_operations_never_invoke_native_effects(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('Native effect forbidden')):
            artifact = self.seal()
            with self.stage(artifact): pass

    def test_diagnostics_and_ciphertext_do_not_include_private_material(self):
        artifact = self.seal()
        for data in self.payloads.values(): self.assertNotIn(data, self.output.read_bytes())
        try:
            with self.stage(artifact, key=b'x' * 32): pass
        except inventory.Refused as error:
            for data in self.payloads.values(): self.assertNotIn(data.decode(errors='ignore'), str(error))
            self.assertNotIn(KEY.hex(), str(error))

    def test_large_member_keeps_streaming_memory_bounded(self):
        member = self.value['materials'][0]; path = self.members[member['name']]
        block = b'z' * recovery.CHUNK; digest = hashlib.sha256()
        with path.open('wb') as output:
            for _ in range(32): output.write(block); digest.update(block)
        member.update(bytes=32 * recovery.CHUNK, sha256=digest.hexdigest())
        self.digest = inventory.recovery_set_digest(self.value)
        tracemalloc.start()
        try:
            artifact = self.seal()
            # A fresh host receives ciphertext without the original plaintext.
            # Release this owned fixture input before staging the complete member.
            path.unlink()
            with self.stage(artifact) as staged:
                self.assertEqual(staged.members[member['name']].stat().st_size, member['bytes'])
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 16 * recovery.CHUNK)

    def test_large_declared_inventory_does_not_inherit_single_gcm_limit(self):
        value = copy.deepcopy(self.value)
        value['materials'][0]['bytes'] = 1 << 37
        inventory.validate_manifest(value)
        self.assertGreater(recovery.package_size(value), 1 << 37)

    def test_exhausted_frame_budget_refuses_before_output_effect(self):
        value = copy.deepcopy(self.value)
        value['materials'][0]['bytes'] = 1 << 52
        inventory.validate_manifest(value)
        with patch.object(recovery, 'open_path', side_effect=AssertionError('Output opened too early')):
            with self.assertRaises(inventory.Refused):
                recovery.seal_recovery(value, self.members, KEY, self.output)

    def test_empty_member_has_authenticated_identity_and_survives(self):
        member = self.value['materials'][0]
        self.members[member['name']].write_bytes(b'')
        member.update(bytes=0, sha256=hashlib.sha256(b'').hexdigest())
        self.digest = inventory.recovery_set_digest(self.value)
        artifact = self.seal()
        with self.stage(artifact) as staged:
            self.assertEqual(staged.members[member['name']].read_bytes(), b'')
