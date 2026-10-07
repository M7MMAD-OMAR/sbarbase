"""Source-only disk settlement fixtures, never native execution or admission."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import storage_write_settlement as settlement


TOKEN = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
ENVIRONMENT = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
RUNTIME = 'e_' + 'a' * 24


def manifest(writer_count=1):
    inventory = [{'kind': 'storage', 'name': 'fixture-storage', 'labels': {'tenant': RUNTIME, 'é': 'ع'}}]
    return {
        'version': 1, 'source': settlement.SOURCE, 'image': settlement.IMAGE,
        'binding': {'environment': ENVIRONMENT, 'runtime': RUNTIME, 'epoch': 7,
                    'coverage': 'disposable-fixture', 'placement': 'native-dedicated',
                    'inventoryDigest': settlement.ordered_inventory_digest(inventory),
                    'placementDigest': 'd' * 64},
        'operation': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'purpose': 'backup',
        'actor': 'fixture-owner', 'management_epoch': 8,
        'installation': 'cccccccc-cccc-cccc-cccc-cccccccccccc',
        'daemon': '3' * 64, 'namespace': 'fixture-native', 'generation': 4, 'routing_revision': 9,
        'backend': 'file',
        'volume': {'id': 'fixture-volume', 'resource': 'dddddddd-dddd-dddd-dddd-dddddddddddd',
                   'root': '/fixture/storage', 'device': 12, 'inode': 34, 'mount_id': 56,
                   'namespace': 'fixture-native', 'tenant_prefix': RUNTIME},
        'database': {'container': '4' * 64, 'image': 'sha256:' + '5' * 64,
                     'name': 'fixture_db', 'oid': 16384},
        'writers': [{'container': str(i + 1) * 64, 'owner': 'fixture-owner', 'image': settlement.IMAGE,
                     'config_sha256': '6' * 64, 'role': 'api' if i == 0 else 'worker',
                     'started_at': '2026-10-06T01:02:03.000Z',
                     'mount_source': 'fixture-volume', 'mount_destination': '/var/lib/storage', 'scope': RUNTIME}
                    for i in range(writer_count)],
        'features': {name: name != 'remote_s3' for name in settlement.FEATURES},
        'launch_paths': ['fixture-compose', 'fixture-worker', 'fixture-direct-storage'],
        'sourceInventory': inventory, 'neighbors': ['7' * 64],
    }


class DiskFixtureAuthority(settlement.FixtureAuthority):
    """Explicit fixture adapter; callbacks and subclasses cannot earn native admission."""
    def __init__(self, declared):
        self.declared = copy.deepcopy(declared)
        self.token = TOKEN
        self.stopped = False
        self.calls = []
        self.observe_count = 0
        self.observe_change = None
        self.reconcile_change = None
        self.stop_lost_ack = False
        self.fence_lost_ack = False
        self.unresolved = []
        self.file_digest = '8' * 64

    def fence(self, declared):
        self.calls.append('fence')
        if self.fence_lost_ack:
            self.fence_lost_ack = False
            raise RuntimeError('Fixture fence applied, acknowledgement lost')
        return self.token

    def observe(self, declared):
        self.calls.append('observe')
        self.observe_count += 1
        value = {key: copy.deepcopy(declared[key]) for key in
                 ('binding', 'installation', 'daemon', 'namespace', 'generation',
                  'routing_revision', 'sourceInventory', 'volume', 'neighbors', 'launch_paths')}
        value.update(manifest_sha256=settlement.digest(declared), fence_token=self.token,
                     ingress='closed', unlisted_writers=[])
        value['writers'] = [dict(copy.deepcopy(writer),
                                 state='stopped' if self.stopped else 'running',
                                 restart_policy='no', pids=[] if self.stopped else [100 + i])
                            for i, writer in enumerate(declared['writers'])]
        value['database'] = dict(copy.deepcopy(declared['database']), writer_sessions=[], prepared=[])
        value['effects'] = {name: {'namespace': declared['namespace'], 'generation': declared['generation'],
                                  'inventory_sha256': '9' * 64, 'pending': [name + ':pending'],
                                  'active': [], 'state': 'quarantined'}
                            for name in ('tus', 'multipart', 'queue', 'redis', 's3_protocol', 'remote_s3')
                            if declared['features'][name]}
        if self.observe_change:
            self.observe_change(value, self.observe_count)
        return value

    def stop(self, declared, token):
        self.calls.append('stop')
        self.stopped = True
        if self.stop_lost_ack:
            self.stop_lost_ack = False
            raise RuntimeError('Fixture stop applied, acknowledgement lost')

    def reconcile(self, declared, token):
        self.calls.append('reconcile')
        value = {'manifest_sha256': settlement.digest(declared), 'rows_sha256': 'a' * 64,
                 'files_sha256': self.file_digest, 'metadata_sha256': 'b' * 64,
                 'effects_sha256': 'c' * 64, 'unresolved': copy.deepcopy(self.unresolved)}
        if self.reconcile_change:
            self.reconcile_change(value)
        return value


class StorageWriteSettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.declared = manifest()
        self.authority = DiskFixtureAuthority(self.declared)
        self.journal = settlement.Journal(self.root)
        self.protocol = settlement.SourceSettlement(self.declared, self.journal, self.authority)

    def records(self):
        with self.journal.locked() as journal:
            return copy.deepcopy(journal.records)

    def write_bytes(self, value):
        target = self.root / 'journal.jsonl'
        target.write_bytes(value)
        target.chmod(0o600)

    def mutate_record(self, index, change, rehash=True):
        records = self.records()
        change(records[index])
        if rehash:
            body = {key: value for key, value in records[index].items() if key != 'sha256'}
            records[index]['sha256'] = settlement.digest(body)
        self.write_bytes(b''.join(json.dumps(record, ensure_ascii=True, separators=(',', ':')).encode() + b'\n'
                                  for record in records))

    def expect_observation_refused(self, change):
        self.authority.observe_change = lambda value, count: change(value)
        with self.assertRaises(settlement.Refused):
            self.protocol.settle()
        self.assertNotIn('settled', [record['phase'] for record in self.records()])

    def test_one_writer_settles_with_disk_receipt_and_all_enabled_effects(self):
        receipt = self.protocol.settle()
        records = self.records()
        self.assertEqual([item['phase'] for item in records],
                         ['enrolled', 'fence-pending', 'fenced', 'stop-pending', 'stopped',
                          'reconcile-pending', 'settled'])
        self.assertEqual(receipt['evidence'], 'fixture')
        self.assertEqual(receipt['journal_sha256'], records[-1]['sha256'])
        self.assertEqual(set(records[-1]['details']['observation']['effects']),
                         {'tus', 'multipart', 'queue', 'redis', 's3_protocol'})
        self.assertEqual(self.protocol.revalidate(receipt), settlement.digest(receipt))

    def test_two_exact_writer_cids_are_stopped_and_observed(self):
        declared = manifest(2)
        authority = DiskFixtureAuthority(declared)
        receipt = settlement.SourceSettlement(declared, self.journal, authority).settle()
        writers = self.records()[-1]['details']['observation']['writers']
        self.assertEqual([item['container'] for item in writers], ['1' * 64, '2' * 64])
        self.assertTrue(all(item['state'] == 'stopped' and item['pids'] == [] for item in writers))
        self.assertEqual(receipt['evidence'], 'fixture')

    def test_default_authority_refuses_and_preserves_pending_disk_record(self):
        protocol = settlement.SourceSettlement(self.declared, self.journal)
        with self.assertRaisesRegex(settlement.Refused, 'fence authority unavailable'):
            protocol.settle()
        self.assertEqual(self.records()[-1]['phase'], 'fence-pending')

    def test_untyped_callback_is_not_authority(self):
        with self.assertRaisesRegex(settlement.Refused, 'Untyped callback'):
            settlement.SourceSettlement(self.declared, self.journal, lambda: True)
        self.assertFalse((self.root / 'journal.jsonl').exists())

    def test_actual_verifier_refuses_fixture_native_relabel_and_callback(self):
        receipt = self.protocol.settle()
        for evidence in ('fixture', 'native'):
            with self.subTest(evidence=evidence):
                relabeled = dict(receipt, evidence=evidence)
                with self.assertRaisesRegex(settlement.Refused, 'verifier unavailable'):
                    settlement.verify_lifecycle_settlement(
                        relabeled, self.declared['binding'], self.declared['sourceInventory'],
                        currentAuthority=lambda *args: True)

    def test_native_relabel_is_not_current_fixture_receipt(self):
        receipt = dict(self.protocol.settle(), evidence='native')
        with self.assertRaises(settlement.Refused):
            self.protocol.revalidate(receipt)

    def test_constructor_clones_input_and_authority_callback_input(self):
        self.declared['volume']['root'] = '/foreign/storage'
        self.declared['writers'][0]['container'] = 'f' * 64
        original_fence = self.authority.fence
        def fence_callback(declared):
            token = original_fence(declared)
            declared['volume']['root'] = '/callback/foreign'
            declared['writers'][0]['scope'] = 'global'
            return token
        self.authority.fence = fence_callback
        receipt = self.protocol.settle()
        self.assertEqual(self.records()[0]['details']['manifest']['volume']['root'], '/fixture/storage')
        receipt['binding']['epoch'] = 100
        self.assertEqual(self.protocol.manifest['binding']['epoch'], 7)

    def test_restart_reobserves_even_when_previous_writers_already_stopped(self):
        first = self.protocol.settle()
        before = self.authority.observe_count
        restarted = settlement.SourceSettlement(self.protocol.manifest, settlement.Journal(self.root), self.authority)
        second = restarted.settle()
        self.assertEqual(self.authority.observe_count - before, 3)
        self.assertEqual(self.authority.calls.count('stop'), 1)
        self.assertGreater(second['sequence'], first['sequence'])

    def test_stop_lost_ack_retains_pending_and_reobserves_already_stopped(self):
        self.authority.stop_lost_ack = True
        with self.assertRaisesRegex(RuntimeError, 'acknowledgement lost'):
            self.protocol.settle()
        self.assertEqual(self.records()[-1]['phase'], 'stop-pending')
        self.assertTrue(self.authority.stopped)
        before = self.authority.observe_count
        restarted = settlement.SourceSettlement(self.protocol.manifest, settlement.Journal(self.root), self.authority)
        receipt = restarted.settle()
        self.assertEqual(self.authority.calls.count('stop'), 1)
        self.assertEqual(self.authority.observe_count - before, 3)
        self.assertEqual(receipt['evidence'], 'fixture')

    def test_fence_lost_ack_retains_pending_and_reobserves_after_retry(self):
        self.authority.fence_lost_ack = True
        with self.assertRaises(RuntimeError):
            self.protocol.settle()
        self.assertEqual(self.records()[-1]['phase'], 'fence-pending')
        before = self.authority.observe_count
        receipt = settlement.SourceSettlement(self.protocol.manifest, settlement.Journal(self.root), self.authority).settle()
        self.assertEqual(self.authority.calls.count('fence'), 1)
        self.assertGreaterEqual(self.authority.observe_count - before, 3)
        self.assertEqual(receipt['fence_token'], TOKEN)

    def test_stop_pending_fsync_lost_ack_has_no_issued_stop_and_reobserves(self):
        real_sync = os.fsync
        failed = False
        def sync(fd):
            nonlocal failed
            if stat.S_ISREG(os.fstat(fd).st_mode):
                raw = os.pread(fd, os.fstat(fd).st_size, 0)
                if raw and json.loads(raw.splitlines()[-1])['phase'] == 'stop-pending' and not failed:
                    real_sync(fd)
                    failed = True
                    raise OSError('Fixture fsync acknowledgement lost')
            return real_sync(fd)
        with patch.object(settlement.os, 'fsync', side_effect=sync):
            with self.assertRaises(settlement.Refused):
                self.protocol.settle()
        self.assertEqual(self.records()[-1]['phase'], 'stop-pending')
        self.assertNotIn('stop', self.authority.calls)
        receipt = settlement.SourceSettlement(self.protocol.manifest, settlement.Journal(self.root), self.authority).settle()
        self.assertEqual(self.authority.calls.count('stop'), 1)
        self.assertEqual(receipt['evidence'], 'fixture')

    def test_malformed_json_refuses_without_authority_calls(self):
        self.write_bytes(b'{not json}\n')
        with self.assertRaises(settlement.Refused):
            self.protocol.settle()
        self.assertEqual(self.authority.calls, [])

    def test_truncated_append_refuses_without_repairing_or_removing_it(self):
        self.protocol.settle()
        target = self.root / 'journal.jsonl'
        damaged = target.read_bytes()[:-1]
        self.write_bytes(damaged)
        with self.assertRaises(settlement.Refused):
            self.protocol.settle()
        self.assertEqual(target.read_bytes(), damaged)

    def test_duplicate_json_keys_refuse(self):
        self.protocol.settle()
        target = self.root / 'journal.jsonl'
        data = target.read_bytes().replace(b'"phase":"enrolled"', b'"phase":"enrolled","phase":"enrolled"', 1)
        self.write_bytes(data)
        with self.assertRaisesRegex(settlement.Refused, 'Duplicate JSON key'):
            self.protocol.settle()

    def test_checksum_corruption_refuses(self):
        self.protocol.settle()
        self.mutate_record(0, lambda record: record['details'].update(tampered=True), rehash=False)
        with self.assertRaisesRegex(settlement.Refused, 'checksum differs'):
            self.protocol.settle()

    def test_chain_corruption_with_valid_record_checksum_refuses(self):
        self.protocol.settle()
        self.mutate_record(-1, lambda record: record.update(previous='f' * 64))
        with self.assertRaisesRegex(settlement.Refused, 'chain differs'):
            self.protocol.settle()

    def test_duplicate_sequence_with_valid_record_checksum_refuses(self):
        self.protocol.settle()
        self.mutate_record(-1, lambda record: record.update(sequence=1))
        with self.assertRaisesRegex(settlement.Refused, 'sequence differs'):
            self.protocol.settle()

    def test_journal_wrong_context_refuses_before_new_effect(self):
        self.protocol.settle()
        self.mutate_record(-1, lambda record: record.update(manifest_sha256='f' * 64))
        calls = list(self.authority.calls)
        with self.assertRaisesRegex(settlement.Refused, 'manifest changed'):
            self.protocol.settle()
        self.assertEqual(self.authority.calls, calls)

    def test_forged_first_settled_record_cannot_skip_pending_and_observation_chain(self):
        record = {'sequence': 1, 'previous': '0' * 64,
                  'manifest_sha256': settlement.digest(self.declared), 'phase': 'settled', 'details': {}}
        record['sha256'] = settlement.digest(record)
        self.write_bytes(json.dumps(record).encode() + b'\n')
        with self.assertRaisesRegex(settlement.Refused, 'Illegal settlement phase'):
            self.protocol.settle()
        self.assertEqual(self.authority.calls, [])

    def test_unknown_phase_detail_and_missing_fence_token_refuse_closed(self):
        self.protocol.settle()
        target = self.root / 'journal.jsonl'
        original = target.read_bytes()
        for change in (lambda record: record['details'].update(extra='asserted'),
                       lambda record: record['details'].pop('fence_token')):
            self.write_bytes(original)
            self.mutate_record(2, change)
            with self.assertRaises(settlement.Refused):
                self.protocol.settle()

    def test_writer_volume_and_owner_are_bound_to_current_exact_manifest(self):
        for key, changed in (('owner', 'foreign-owner'), ('mount_source', 'neighbor-volume')):
            with self.subTest(key=key):
                self.expect_observation_refused(lambda value: value['writers'][0].update({key: changed}))

    def test_ordered_catalog_inventory_survives_persisted_restart(self):
        receipt = self.protocol.settle()
        restarted = settlement.SourceSettlement(self.declared, self.journal, self.authority)
        self.assertEqual(restarted.revalidate(receipt), settlement.digest(receipt))
        self.assertEqual(self.records()[0]['details']['manifest']['sourceInventory'], self.declared['sourceInventory'])

    def test_receipt_boolean_and_float_substitutions_refuse_before_effect(self):
        receipt = self.protocol.settle()
        changed_values = [('version', True), ('version', 1.0), ('management_epoch', 8.0),
                          ('generation', 4.0), ('routing_revision', 9.0),
                          ('sequence', float(receipt['sequence']))]
        for field, changed in changed_values:
            with self.subTest(field=field, changed=changed):
                bad = dict(receipt, **{field: changed})
                with self.assertRaises(settlement.Refused):
                    self.protocol.revalidate(bad)
                with self.assertRaises(settlement.Refused):
                    self.protocol.record_fixture_effect(bad, 'fixture-effect',
                        lambda: self.fail('Invalid receipt must not invoke effect'))

    def test_live_boolean_and_float_identity_substitution_refuses(self):
        for field, changed in [('generation', 4.0), ('routing_revision', 9.0), ('management_epoch', 8.0)]:
            if field == 'management_epoch':
                continue
            with self.subTest(field=field):
                self.expect_observation_refused(lambda value: value.update({field: changed}))
        declared = manifest()
        declared['generation'] = 1
        authority = DiskFixtureAuthority(declared)
        authority.observe_change = lambda value, count: value.update(generation=True)
        with tempfile.TemporaryDirectory() as root:
            protocol = settlement.SourceSettlement(declared, settlement.Journal(Path(root)), authority)
            with self.assertRaises(settlement.Refused):
                protocol.settle()

    def test_malformed_nested_unique_lists_raise_closed_refused(self):
        for field, value in [('launch_paths', [{}]), ('neighbors', [[]])]:
            with self.subTest(field=field):
                declared = manifest()
                declared[field] = value
                with self.assertRaises(settlement.Refused):
                    settlement.SourceSettlement(declared, self.journal, self.authority)

    def test_cached_observation_mutation_during_reconcile_is_detected(self):
        base = self.authority
        cache = base.observe(self.declared)
        def cached_observe(declared):
            for writer in cache['writers']:
                writer['state'] = 'stopped' if base.stopped else 'running'
                writer['pids'] = [] if base.stopped else [100]
            return cache
        original_reconcile = base.reconcile
        def mutate_cached(declared, token):
            result = original_reconcile(declared, token)
            cache['effects']['queue']['inventory_sha256'] = 'f' * 64
            return result
        base.observe = cached_observe
        base.reconcile = mutate_cached
        with self.assertRaisesRegex(settlement.Refused, 'Authority changed'):
            self.protocol.settle()
        self.assertNotIn('settled', [item['phase'] for item in self.records()])

    def test_caller_receipt_is_detached_before_observer_invocation(self):
        receipt = self.protocol.settle()
        original_digest = settlement.digest(receipt)
        def change_caller(value, count):
            receipt['sequence'] = 12345
        self.authority.observe_change = change_caller
        self.assertEqual(self.protocol.revalidate(receipt), original_digest)
        self.assertEqual(receipt['sequence'], 12345)

    def test_symlink_journal_and_lock_refuse_without_target_mutation(self):
        for name in ('journal.jsonl', 'lock'):
            with self.subTest(name=name):
                target = self.root / 'outside'
                target.write_bytes(b'private target')
                target.chmod(0o600)
                link = self.root / name
                link.unlink(missing_ok=True)
                link.symlink_to(target)
                with self.assertRaises(settlement.Refused):
                    self.protocol.settle()
                self.assertEqual(target.read_bytes(), b'private target')
                link.unlink()

    def test_hardlinked_journal_and_lock_refuse(self):
        for name in ('journal.jsonl', 'lock'):
            with self.subTest(name=name):
                target = self.root / 'outside'
                target.write_bytes(b'private target')
                target.chmod(0o600)
                link = self.root / name
                link.unlink(missing_ok=True)
                os.link(target, link)
                with self.assertRaises(settlement.Refused):
                    self.protocol.settle()
                self.assertEqual(target.read_bytes(), b'private target')
                link.unlink()

    def test_symlink_root_refuses_even_with_private_target(self):
        private = self.root / 'private'
        private.mkdir(mode=0o700)
        link = self.root / 'linked'
        link.symlink_to(private, target_is_directory=True)
        protocol = settlement.SourceSettlement(self.declared, settlement.Journal(link), self.authority)
        with self.assertRaises(settlement.Refused):
            protocol.settle()
        self.assertEqual(list(private.iterdir()), [])

    def test_public_journal_directory_refuses(self):
        self.root.chmod(0o755)
        with self.assertRaises(settlement.Refused):
            self.protocol.settle()
        self.assertEqual(self.authority.calls, [])

    def test_public_journal_or_lock_file_refuses(self):
        for name in ('journal.jsonl', 'lock'):
            with self.subTest(name=name):
                target = self.root / name
                target.write_bytes(b'')
                target.chmod(0o644)
                with self.assertRaises(settlement.Refused):
                    self.protocol.settle()
                target.unlink()

    def test_foreign_current_inventory_is_refused(self):
        self.expect_observation_refused(lambda value: value['sourceInventory'][0].update(name='foreign'))

    def test_current_generation_epoch_daemon_namespace_mount_and_root_mismatches(self):
        mutations = {
            'generation': lambda value: value.update(generation=5),
            'management binding epoch': lambda value: value['binding'].update(epoch=8),
            'daemon': lambda value: value.update(daemon='f' * 64),
            'namespace': lambda value: value.update(namespace='foreign'),
            'mount': lambda value: value['volume'].update(mount_id=57),
            'root': lambda value: value['volume'].update(root='/foreign/storage'),
            'routing': lambda value: value.update(routing_revision=10),
            'database OID': lambda value: value['database'].update(oid=16385),
            'process incarnation': lambda value: value['writers'][0].update(started_at='2026-10-06T02:00:00Z'),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                value = self.authority.observe(self.protocol.manifest)
                mutate(value)
                with self.assertRaises(settlement.Refused):
                    settlement.observe_closed(self.protocol.manifest, value, TOKEN, False)

    def test_manifest_reuse_foreign_operation_management_epoch_and_generation_refuses(self):
        self.protocol.settle()
        for key, new in (('operation', ENVIRONMENT), ('management_epoch', 9), ('generation', 5)):
            with self.subTest(key=key):
                declared = copy.deepcopy(self.protocol.manifest)
                declared[key] = new
                other = settlement.SourceSettlement(declared, settlement.Journal(self.root), DiskFixtureAuthority(declared))
                with self.assertRaises(settlement.Refused):
                    other.settle()
                self.assertEqual(other.authority.calls, [])

    def test_unlisted_writer_and_changed_neighbor_inventory_refuse(self):
        for key, value in (('unlisted_writers', ['f' * 64]), ('neighbors', ['e' * 64])):
            with self.subTest(key=key):
                observed = self.authority.observe(self.protocol.manifest)
                observed[key] = value
                with self.assertRaises(settlement.Refused):
                    settlement.observe_closed(self.protocol.manifest, observed, TOKEN, False)

    def test_global_and_neighbor_writer_scopes_refuse_at_enrollment(self):
        for scope in ('global', 'e_' + 'b' * 24):
            with self.subTest(scope=scope):
                declared = manifest()
                declared['writers'][0]['scope'] = scope
                with self.assertRaises(settlement.Refused):
                    settlement.SourceSettlement(declared, self.journal, self.authority)

    def test_extra_or_missing_writer_cid_refuses(self):
        for count in (0, 2):
            with self.subTest(count=count):
                observed = self.authority.observe(self.protocol.manifest)
                observed['writers'] = observed['writers'][:count] if count == 0 else observed['writers'] * 2
                with self.assertRaises(settlement.Refused):
                    settlement.observe_closed(self.protocol.manifest, observed, TOKEN, False)

    def test_launch_path_omission_and_restart_policy_refuse(self):
        observed = self.authority.observe(self.protocol.manifest)
        observed['launch_paths'].pop()
        with self.assertRaises(settlement.Refused):
            settlement.observe_closed(self.protocol.manifest, observed, TOKEN, False)
        observed = self.authority.observe(self.protocol.manifest)
        observed['writers'][0]['restart_policy'] = 'always'
        with self.assertRaises(settlement.Refused):
            settlement.observe_closed(self.protocol.manifest, observed, TOKEN, False)

    def test_every_enabled_effect_must_be_covered_and_have_no_active_work(self):
        self.authority.stopped = True
        for feature in ('tus', 'multipart', 'queue', 'redis'):
            for mutation in ('missing', 'active', 'replay', 'generation'):
                with self.subTest(feature=feature, mutation=mutation):
                    observed = self.authority.observe(self.protocol.manifest)
                    if mutation == 'missing':
                        observed['effects'].pop(feature)
                    elif mutation == 'active':
                        observed['effects'][feature]['active'] = ['active-writer']
                    elif mutation == 'replay':
                        observed['effects'][feature]['state'] = 'replayable'
                    else:
                        observed['effects'][feature]['generation'] += 1
                    with self.assertRaises(settlement.Refused):
                        settlement.observe_closed(self.protocol.manifest, observed, TOKEN, True)

    def test_prepared_transactions_and_writer_sessions_refuse(self):
        for key in ('prepared', 'writer_sessions'):
            with self.subTest(key=key):
                self.authority.stopped = True
                observed = self.authority.observe(self.protocol.manifest)
                observed['database'][key] = ['unresolved-storage-writer']
                with self.assertRaises(settlement.Refused):
                    settlement.observe_closed(self.protocol.manifest, observed, TOKEN, True)

    def test_remote_s3_provider_cannot_be_settled_by_fixture(self):
        declared = manifest()
        declared['backend'] = 's3'
        declared['features']['remote_s3'] = True
        authority = DiskFixtureAuthority(declared)
        protocol = settlement.SourceSettlement(declared, self.journal, authority)
        with self.assertRaisesRegex(settlement.Refused, 'remote provider settlement authority unavailable'):
            protocol.settle()

    def test_partial_same_version_material_remains_unresolved_on_disk(self):
        self.authority.unresolved = ['same-version:partial-bytes-equal-length']
        with self.assertRaisesRegex(settlement.Refused, 'remains unresolved'):
            self.protocol.settle()
        self.assertEqual(self.records()[-1]['phase'], 'reconcile-pending')
        self.assertNotIn('settled', [item['phase'] for item in self.records()])

    def test_observation_change_during_reconciliation_refuses_publication(self):
        def change(value, count):
            if count >= 3:
                value['effects']['queue']['inventory_sha256'] = 'f' * 64
        self.authority.observe_change = change
        with self.assertRaisesRegex(settlement.Refused, 'changed during reconciliation'):
            self.protocol.settle()
        self.assertEqual(self.records()[-1]['phase'], 'reconcile-pending')

    def test_refresh_makes_old_receipt_stale(self):
        old = self.protocol.settle()
        new = self.protocol.settle()
        with self.assertRaisesRegex(settlement.Refused, 'Receipt differs'):
            self.protocol.revalidate(old)
        self.assertEqual(self.protocol.revalidate(new), settlement.digest(new))

    def test_stale_receipt_after_refresh_cannot_issue_consumer_effect(self):
        old = self.protocol.settle()
        self.protocol.settle()
        executed = []
        with self.assertRaisesRegex(settlement.Refused, 'Stale receipt'):
            self.protocol.record_fixture_effect(old, 'fixture-backup', lambda: executed.append(True))
        self.assertEqual(executed, [])
        self.assertEqual(self.records()[-1]['phase'], 'settled')

    def test_changed_data_after_receipt_refuses_revalidation(self):
        receipt = self.protocol.settle()
        self.authority.file_digest = 'f' * 64
        with self.assertRaisesRegex(settlement.Refused, 'Data changed'):
            self.protocol.revalidate(receipt)

    def test_reconciliation_and_observer_changes_before_effect_refuse_execution(self):
        receipt = self.protocol.settle()
        executed = []
        self.authority.file_digest = 'f' * 64
        with self.assertRaisesRegex(settlement.Refused, 'Data changed before effect'):
            self.protocol.record_fixture_effect(receipt, 'fixture-backup', lambda: executed.append(True))
        self.assertEqual(executed, [])
        self.authority.file_digest = '8' * 64
        start = self.authority.observe_count
        self.authority.observe_change = lambda value, count: value['effects']['queue'].update(
            inventory_sha256='f' * 64) if count > start + 1 else None
        with self.assertRaisesRegex(settlement.Refused, 'Fence changed during effect validation'):
            self.protocol.record_fixture_effect(receipt, 'fixture-backup', lambda: executed.append(True))
        self.assertEqual(executed, [])
        self.assertEqual(self.records()[-1]['phase'], 'settled')

    def test_lost_consumer_effect_ack_is_not_repeated_after_restart(self):
        receipt = self.protocol.settle()
        executed = []
        def effect():
            executed.append('fixture-effect-applied')
            raise RuntimeError('Fixture effect acknowledgement lost')
        with self.assertRaises(RuntimeError):
            self.protocol.record_fixture_effect(receipt, 'fixture-backup', effect)
        self.assertEqual(self.records()[-1]['phase'], 'effect-pending')
        restarted = settlement.SourceSettlement(self.protocol.manifest, settlement.Journal(self.root), self.authority)
        with self.assertRaises(settlement.Refused):
            restarted.record_fixture_effect(receipt, 'fixture-backup', effect)
        with self.assertRaises(settlement.Refused):
            restarted.settle()
        self.assertEqual(executed, ['fixture-effect-applied'])

    def test_ambiguous_consumer_outcome_keeps_pending_terminal(self):
        receipt = self.protocol.settle()
        with self.assertRaisesRegex(settlement.Refused, 'Ambiguous effect outcome'):
            self.protocol.record_fixture_effect(receipt, 'fixture-backup', lambda: {'outcome': 'unknown'})
        self.assertEqual(self.records()[-1]['phase'], 'effect-pending')
        with self.assertRaises(settlement.Refused):
            self.protocol.revalidate(receipt)

    def test_consumer_effect_fsync_lost_ack_does_not_execute_or_auto_retry(self):
        receipt = self.protocol.settle()
        real_sync = os.fsync
        executed = []
        def sync(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                raw = os.pread(fd, os.fstat(fd).st_size, 0)
                if raw and json.loads(raw.splitlines()[-1])['phase'] == 'effect-pending':
                    real_sync(fd)
                    raise OSError('Fixture durable pending acknowledgement lost')
            return real_sync(fd)
        with patch.object(settlement.os, 'fsync', side_effect=sync):
            with self.assertRaises(settlement.Refused):
                self.protocol.record_fixture_effect(receipt, 'fixture-backup', lambda: executed.append(True))
        self.assertEqual(executed, [])
        self.assertEqual(self.records()[-1]['phase'], 'effect-pending')
        with self.assertRaises(settlement.Refused):
            self.protocol.record_fixture_effect(receipt, 'fixture-backup', lambda: executed.append(True))
        self.assertEqual(executed, [])

    def test_recorded_consumer_effect_cannot_be_repeated(self):
        receipt = self.protocol.settle()
        executed = []
        def effect():
            executed.append(True)
            return {'outcome': 'recorded'}
        self.assertEqual(self.protocol.record_fixture_effect(receipt, 'fixture-backup', effect), {'outcome': 'recorded'})
        self.assertEqual(self.records()[-1]['phase'], 'effect-recorded')
        with self.assertRaises(settlement.Refused):
            self.protocol.record_fixture_effect(receipt, 'fixture-backup', effect)
        self.assertEqual(executed, [True])

    def test_receipt_exact_common_wire_fields_and_mutations_refuse(self):
        receipt = self.protocol.settle()
        expected = {'version', 'contract', 'evidence', 'binding', 'operation', 'purpose', 'actor',
                    'management_epoch', 'installation', 'daemon', 'namespace', 'generation',
                    'routing_revision', 'manifest_sha256', 'reconciliation_sha256', 'journal_sha256',
                    'sequence', 'fence_token'}
        self.assertEqual(set(receipt), expected)
        self.assertEqual(json.loads(json.dumps(receipt, ensure_ascii=False)), receipt)
        for key in expected:
            with self.subTest(key=key):
                wrong = copy.deepcopy(receipt)
                wrong.pop(key)
                with self.assertRaises(settlement.Refused):
                    self.protocol.revalidate(wrong)
        wrong = dict(receipt, nativeAdmitted=True)
        with self.assertRaises(settlement.Refused):
            self.protocol.revalidate(wrong)

    def test_live_journal_owner_refuses_second_effect_authority(self):
        with self.journal.locked():
            with self.assertRaises(settlement.Refused):
                self.protocol.settle()
        self.assertEqual(self.authority.calls, [])
        self.assertEqual(self.protocol.settle()['evidence'], 'fixture')

    def test_journal_and_lock_created_with_private_regular_modes(self):
        self.protocol.settle()
        for name in ('journal.jsonl', 'lock'):
            info = (self.root / name).stat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(info.st_uid, os.getuid())

    def test_inventory_digest_preserves_nonascii_and_order(self):
        value = [{'é': 'ع', 'alpha': 1, 'nested': {'z': True, 'a': None}}]
        wire = '[{"é":"ع","alpha":1,"nested":{"z":true,"a":null}}]'.encode('utf-8')
        self.assertEqual(settlement.ordered_inventory_digest(value), hashlib.sha256(wire).hexdigest())
        reversed_keys = [{'alpha': 1, 'é': 'ع', 'nested': {'z': True, 'a': None}}]
        self.assertNotEqual(settlement.ordered_inventory_digest(value), settlement.ordered_inventory_digest(reversed_keys))

    def test_inventory_digest_matches_catalog_numeric_property_order(self):
        value = [{'9': 'nine', '2': 'two', 'é': 'ع'}]
        catalog_wire = '[{"2":"two","9":"nine","é":"ع"}]'.encode('utf-8')
        self.assertEqual(settlement.ordered_inventory_digest(value), hashlib.sha256(catalog_wire).hexdigest())

    def test_inventory_digest_refuses_fractional_numbers_outside_resource_codec(self):
        with self.assertRaises(settlement.Refused):
            settlement.ordered_inventory_digest([{'weight': 1.25}])

    def test_inventory_digest_refuses_unpaired_surrogate_keys_and_values(self):
        surrogate = chr(0xd800)
        for value in ([{surrogate: 'value'}], [{'key': surrogate}]):
            with self.subTest(position='key' if surrogate in value[0] else 'value'):
                with self.assertRaises(settlement.Refused):
                    settlement.ordered_inventory_digest(value)

    def test_inventory_digest_refuses_excessive_nested_material(self):
        child = 'value'
        for _ in range(120):
            child = {'next': child}
        with self.assertRaises(settlement.Refused):
            settlement.ordered_inventory_digest([{'nested': child}])

    def test_inventory_digest_rejects_non_json_and_unsafe_values(self):
        for value in ([{'n': float('nan')}], [{'n': float('inf')}], [{'n': 9007199254740992}],
                      [{'n': b'bytes'}], [{1: 'numeric Python key'}]):
            with self.subTest(value=repr(value)):
                with self.assertRaises(settlement.Refused):
                    settlement.ordered_inventory_digest(value)


if __name__ == '__main__':
    unittest.main()
