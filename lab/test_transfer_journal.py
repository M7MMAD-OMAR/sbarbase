"""Source-only journal boundary and interruption tests. No native services."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

import transfer_journal as journal


def binding():
    runtime = 'e_' + 'a' * 24
    result = {key: str(uuid.uuid4()) for key in
              ('operation', 'environment', 'project', 'source', 'destination', 'actor', 'installation')}
    return {**result, 'runtime': runtime, 'runtime_epoch': 8, 'management_epoch': 12,
            'routing_revision': 4, 'inventory_digest': 'b' * 64, 'daemon_digest': 'c' * 64,
            'engine_id': 'd' * 64, 'database_oid': '17000',
            'roles': {runtime + '_' + kind: str(18000 + index) for index, kind in
                      enumerate(('auth', 'rest', 'storage', 'realtime', 'developer', 'studio'))}}


class TransferJournalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='transfer-journal-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'private'
        self.directory.mkdir(mode=0o700)
        self.binding = binding()
        self.path = self.directory / (self.binding['operation'] + '.' + self.binding['runtime'] + '.json')
        self.metadata = {'resource_kind': 'database', 'resource_id': self.binding['database_oid'], 'before_digest': 'e' * 64}
        self.receipt = {'resource_kind': 'database', 'resource_id': self.binding['database_oid'],
                        'outcome': 'verified', 'observed_digest': 'f' * 64, 'checks': 3,
                        'sessions': 0, 'old_rejected': True, 'replacement_accepted': True,
                        'neighbor_unchanged': True}

    def opened(self, value=None, directory=None):
        return journal.TransferJournal(str(directory or self.directory), value or self.binding)

    def refused(self, work, reason=None):
        with self.assertRaises(journal.TransferJournalError) as caught:
            work()
        if reason:
            self.assertEqual(str(caught.exception), reason)
        return str(caught.exception)

    def seed(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            return store.snapshot()['material_digest']

    def mutate(self, work):
        value = json.loads(self.path.read_text())
        work(value)
        self.path.write_text(json.dumps(value))

    def publication_blocked(self):
        with self.opened() as store:
            self.refused(store.snapshot, 'PUBLICATION_RECONCILIATION_REQUIRED')
            self.refused(lambda: store.reserve(effect_started=False), 'PUBLICATION_RECONCILIATION_REQUIRED')

    def interrupt_publication(self, stage, *, initial=False):
        real_sync, real_open, real_write = journal.os.fsync, journal.os.open, journal.os.write
        syncs, writes = [0], [0]
        with self.opened() as store:
            digest = None
            if not initial:
                store.reserve(effect_started=False)
                digest = store.snapshot()['material_digest']
            def sync(descriptor):
                syncs[0] += 1
                positions = {'marker_sync': 1, 'marker_directory_sync': 2, 'candidate_sync': 3,
                             'candidate_directory_sync': 4, 'rename_directory_sync': 5, 'cleanup_directory_sync': 6}
                if positions.get(stage) == syncs[0]:
                    raise OSError('interruption')
                return real_sync(descriptor)
            def opened(name, *args, **kwargs):
                if stage == 'missing_candidate' and isinstance(name, str) and name.startswith(store.pending_prefix):
                    raise OSError('interruption')
                return real_open(name, *args, **kwargs)
            def written(descriptor, data):
                writes[0] += 1
                if stage == 'partial_candidate' and writes[0] == 2:
                    return real_write(descriptor, data[:9])
                if stage == 'partial_candidate' and writes[0] == 3:
                    raise OSError('interruption')
                return real_write(descriptor, data)
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(journal.os, 'fsync', side_effect=sync))
                stack.enter_context(patch.object(journal.os, 'open', side_effect=opened))
                stack.enter_context(patch.object(journal.os, 'write', side_effect=written))
                if stage == 'rename':
                    stack.enter_context(patch.object(journal.os, 'replace', side_effect=OSError('interruption')))
                if stage == 'marker_remove':
                    stack.enter_context(patch.object(journal.os, 'unlink', side_effect=OSError('interruption')))
                operation = (lambda: store.reserve(effect_started=False)) if initial else (lambda: store.before_effect('auth_sessions', self.metadata))
                self.refused(operation, 'PUBLICATION_RECONCILIATION_REQUIRED')
        return digest

    def test_reservation_private_lengths_and_public_snapshot(self):
        with self.opened() as store:
            material = store.reserve(effect_started=False)
            self.assertEqual(set(material), set(journal.CREDENTIAL_BYTES) | {'developer'})
            for name, size in journal.CREDENTIAL_BYTES.items():
                self.assertEqual(len(material[name]), size * 2)
            self.assertEqual(len(material['developer']), 32)
            view = store.snapshot()
            self.assertNotIn('credentials', view)
            self.assertTrue(all(secret not in json.dumps(view) for secret in material.values()))
            self.assertEqual(view['binding'], self.binding)
            self.assertEqual((self.path.stat().st_mode & 0o777, self.path.stat().st_nlink), (0o600, 1))

    def test_replay_after_process_recreation_never_generates_new_credentials(self):
        with self.opened() as store:
            original = store.reserve(effect_started=False)
            digest = store.snapshot()['material_digest']
        with self.opened() as store, patch.object(journal.secrets, 'token_hex', side_effect=AssertionError('regenerated')), patch.object(journal.secrets, 'token_urlsafe', side_effect=AssertionError('regenerated')):
            self.assertTrue(store.reserve(effect_started=True, expected_material_digest=digest) == original)

    def test_caller_mutation_does_not_change_immutable_binding_or_material(self):
        supplied = copy.deepcopy(self.binding)
        with self.opened(supplied) as store:
            original = store.reserve(effect_started=False)
            supplied['roles'].clear()
            store.binding['roles'].clear()
            original['auth'] = 'x'
            self.assertEqual(store.snapshot()['binding'], self.binding)
            self.assertNotEqual(store.reserve(effect_started=False)['auth'], 'x')

    def test_missing_initial_reservation_is_recoverable_before_effects_only(self):
        with self.opened() as store:
            self.refused(lambda: store.reserve(effect_started=True, expected_material_digest='0' * 64), 'POST_EFFECT_MATERIAL_MISSING')
            self.refused(lambda: store.reserve(effect_started=False, expected_material_digest='a' * 64), 'POST_EFFECT_MATERIAL_MISSING')
            self.assertFalse(self.path.exists())
            store.reserve(effect_started=False)

    def test_missing_reservation_cannot_create_effect_or_receipt(self):
        with self.opened() as store:
            self.refused(lambda: store.before_effect('sessions', self.metadata), 'RESERVATION_MISSING')
            self.refused(lambda: store.after_effect('sessions', self.receipt), 'RESERVATION_MISSING')
        self.assertFalse(self.path.exists())

    def test_every_catalog_and_native_binding_field_is_immutable_on_replay(self):
        digest = self.seed()
        for field in self.binding:
            value = copy.deepcopy(self.binding)
            if field in ('runtime_epoch', 'management_epoch', 'routing_revision'):
                value[field] += 1
            elif field == 'roles':
                value[field][self.binding['runtime'] + '_auth'] = '19999'
            elif field == 'database_oid':
                value[field] = '19999'
            elif field in ('inventory_digest', 'daemon_digest', 'engine_id'):
                value[field] = '9' * 64
            elif field == 'runtime':
                value[field] = 'e_' + '9' * 24
                value['roles'] = {name.replace(self.binding['runtime'], value[field]): oid for name, oid in value['roles'].items()}
            else:
                value[field] = str(uuid.uuid4())
            with self.subTest(field=field), self.opened(value) as store:
                if field in ('operation', 'runtime'):
                    self.refused(lambda: store.reserve(effect_started=True, expected_material_digest=digest), 'POST_EFFECT_MATERIAL_MISSING')
                else:
                    self.refused(lambda: store.reserve(effect_started=True, expected_material_digest=digest), 'BINDING_DIFFERS')

    def test_invalid_binding_rejects_before_creating_lock_or_material(self):
        cases = []
        for field, value in [('operation', '../escape'), ('engine_id', 'short'), ('database_oid', '017'),
                             ('management_epoch', True), ('runtime_epoch', -1), ('runtime_epoch', 0), ('runtime_epoch', 2 ** 53),
                             ('source', self.binding['destination']), ('actor', str(uuid.uuid4()).upper()),
                             ('actor', str(uuid.UUID(int=0))), ('operation', str(uuid.UUID(int=0)))]:
            item = copy.deepcopy(self.binding)
            item[field] = value
            cases.append(item)
        for value in cases:
            self.refused(lambda: self.opened(value), 'BINDING_INVALID')
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_wrong_role_tenant_duplicate_oid_or_missing_required_role_refuses(self):
        for mode in ('tenant', 'duplicate', 'missing', 'privileged'):
            value = copy.deepcopy(self.binding)
            roles = value['roles']
            if mode == 'tenant':
                roles['e_' + '9' * 24 + '_auth'] = '19000'
            elif mode == 'duplicate':
                roles[self.binding['runtime'] + '_auth'] = roles[self.binding['runtime'] + '_rest']
            elif mode == 'missing':
                roles.pop(self.binding['runtime'] + '_auth')
            else:
                roles['supabase_admin'] = '19000'
            self.refused(lambda: self.opened(value), 'BINDING_INVALID')

    def test_public_effect_flag_and_digest_require_exact_types(self):
        with self.opened() as store:
            self.refused(lambda: store.reserve(effect_started=1), 'PUBLIC_STATE_INVALID')
            self.refused(lambda: store.reserve(effect_started=True), 'PUBLIC_MATERIAL_REQUIRED')
            self.refused(lambda: store.reserve(effect_started=False, expected_material_digest='password'), 'PUBLIC_STATE_INVALID')
            store.reserve(effect_started=False)
            self.refused(lambda: store.reserve(effect_started=True, expected_material_digest='0' * 64), 'MATERIAL_DIFFERS')

    def test_pending_effect_survives_interruption_and_blocks_other_effects(self):
        with self.opened() as store:
            original = store.reserve(effect_started=False)
            digest = store.snapshot()['material_digest']
            store.before_effect('auth_sessions', self.metadata)
        with self.opened() as store:
            self.assertTrue(store.reserve(effect_started=True, expected_material_digest=digest) == original)
            replay = store.before_effect('auth_sessions', self.metadata)
            self.assertEqual(replay['state'], 'pending')
            self.assertIsNone(replay['receipt'])
            self.refused(lambda: store.before_effect('sql_roles', self.metadata), 'NATIVE_EFFECT_PENDING')
            self.assertEqual(store.snapshot()['revision'], 1)

    def test_blocked_native_observation_preserves_uncertainty_until_verified(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
            blocked = {**self.receipt, 'outcome': 'blocked'}
            self.assertEqual(store.after_effect('auth_sessions', blocked)['state'], 'pending')
            self.refused(lambda: store.before_effect('sql_roles', self.metadata), 'NATIVE_EFFECT_PENDING')
            self.assertEqual(store.after_effect('auth_sessions', self.receipt)['state'], 'verified')
            self.assertEqual(store.before_effect('sql_roles', self.metadata)['state'], 'pending')

    def test_response_loss_after_settlement_replays_same_receipt_without_publication(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
            store.after_effect('auth_sessions', self.receipt)
            revision = store.snapshot()['revision']
            digest = store.snapshot()['material_digest']
        with self.opened() as store, patch.object(store, '_publish', side_effect=AssertionError('rewrote')):
            store.reserve(effect_started=True, expected_material_digest=digest)
            self.assertEqual(store.after_effect('auth_sessions', self.receipt)['state'], 'verified')
            self.assertEqual(store.before_effect('auth_sessions', self.metadata)['state'], 'verified')
            self.assertEqual(store.snapshot()['revision'], revision)

    def test_changed_after_receipt_or_before_metadata_refuses_settled_replay(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
            store.after_effect('auth_sessions', self.receipt)
            self.refused(lambda: store.after_effect('auth_sessions', {**self.receipt, 'checks': 4}), 'SETTLED_RECEIPT_DIFFERS')
            self.refused(lambda: store.before_effect('auth_sessions', {**self.metadata, 'before_digest': '0' * 64}), 'EFFECT_BINDING_DIFFERS')

    def test_receipt_requires_matching_requested_native_resource(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'EFFECT_NOT_REQUESTED')
            store.before_effect('auth_sessions', self.metadata)
            other = {**self.receipt, 'resource_kind': 'runtime', 'resource_id': self.binding['runtime']}
            self.refused(lambda: store.after_effect('auth_sessions', other), 'EFFECT_BINDING_DIFFERS')
            self.refused(lambda: store.after_effect('auth_sessions', {**self.receipt, 'resource_id': '99999'}), 'RESOURCE_BINDING_DIFFERS')

    def test_crossed_role_and_runtime_identifiers_refuse(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            for kind, identity in [('role', 'e_' + '9' * 24 + '_auth'), ('runtime', 'e_' + '9' * 24),
                                   ('project', str(uuid.uuid4())), ('container', 'short')]:
                self.refused(lambda: store.before_effect('native_step', {'resource_kind': kind, 'resource_id': identity, 'before_digest': '0' * 64}), 'RESOURCE_BINDING_DIFFERS')

    def test_receipts_reject_free_text_unbounded_numbers_and_unknown_fields(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
            cases = [{'message': 'response body'}, {'password': 'private'}, {'checks': 1000001},
                     {'sessions': True}, {'old_rejected': 'yes'}, {'outcome': 'succeeded'},
                     {'observed_digest': '0' * 65}, {'signing_kid': str(uuid.uuid4())}]
            for change in cases:
                self.refused(lambda: store.after_effect('auth_sessions', {**self.receipt, **change}), 'OBSERVATION_INVALID')

    def test_reserved_secret_in_digest_shaped_field_never_reaches_public_receipt(self):
        with self.opened() as store:
            material = store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
            self.refused(lambda: store.after_effect('auth_sessions', {**self.receipt, 'observed_digest': material['auth']}), 'CREDENTIAL_IN_OBSERVATION')
            self.refused(lambda: store.before_effect('next', {**self.metadata, 'before_digest': material['jwt']}), 'CREDENTIAL_IN_OBSERVATION')
            self.assertTrue(all(secret not in json.dumps(store.snapshot()) for secret in material.values()))

    def test_native_storage_kid_is_scoped_receipt_only(self):
        kid = str(uuid.uuid4())
        metadata = {'resource_kind': 'storage_tenant', 'resource_id': self.binding['runtime'], 'before_digest': '0' * 64}
        receipt = {'resource_kind': 'storage_tenant', 'resource_id': self.binding['runtime'], 'outcome': 'verified', 'signing_kid': kid}
        with self.opened() as store:
            material = store.reserve(effect_started=False)
            self.assertNotIn('storage_signing_key', material)
            store.before_effect('storage_signing', metadata)
            self.assertEqual(store.after_effect('storage_signing', receipt)['receipt']['signing_kid'], kid)

    def test_missing_or_mismatched_post_effect_material_refuses(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
            self.path.unlink()
            self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'RESERVATION_MISSING')
        digest = self.seed()
        self.mutate(lambda value: value['credentials'].update(auth='0' * 64))
        with self.opened() as store:
            self.refused(lambda: store.reserve(effect_started=True, expected_material_digest=digest), 'MATERIAL_DIFFERS')

    def test_effect_material_digest_cannot_change_with_replaced_credentials(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
        def change(value):
            value['credentials']['auth'] = '0' * 64
            value['material_digest'] = journal._material_digest(value['credentials'])
        self.mutate(change)
        with self.opened() as store:
            self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'MATERIAL_DIFFERS')

    def test_effect_boundaries_require_explicit_held_material_continuity(self):
        digest = self.seed()
        with self.opened() as store:
            self.refused(lambda: store.before_effect('auth_sessions', self.metadata), 'PUBLIC_MATERIAL_REQUIRED')
            store.reserve(effect_started=False, expected_material_digest=digest)
            store.before_effect('auth_sessions', self.metadata)
        with self.opened() as store:
            self.refused(lambda: store.reserve(effect_started=False), 'PUBLIC_MATERIAL_REQUIRED')
            self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'PUBLIC_MATERIAL_REQUIRED')
            store.reserve(effect_started=True, expected_material_digest=digest)
            self.assertEqual(store.after_effect('auth_sessions', self.receipt)['state'], 'verified')

    def test_self_consistent_rewritten_material_cannot_replace_public_digest(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            digest = store.snapshot()['material_digest']
            store.before_effect('auth_sessions', self.metadata)
        def change(value):
            value['credentials']['auth'] = '0' * 64
            value['material_digest'] = journal._material_digest(value['credentials'])
            value['effects'][0]['material_digest'] = value['material_digest']
        self.mutate(change)
        with self.opened() as store:
            self.refused(lambda: store.reserve(effect_started=True), 'PUBLIC_MATERIAL_REQUIRED')
            self.refused(lambda: store.reserve(effect_started=True, expected_material_digest=digest), 'MATERIAL_DIFFERS')
            self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'PUBLIC_MATERIAL_REQUIRED')

    def test_concurrent_writer_refuses_and_lock_releases_on_close(self):
        with self.opened():
            self.refused(self.opened, 'JOURNAL_BUSY')
        with self.opened() as store:
            store.reserve(effect_started=False)

    def test_lock_symlink_hardlink_or_fifo_refuses_without_blocking(self):
        lock = self.directory / journal.LOCK
        lock.symlink_to(self.root / 'missing')
        self.refused(self.opened)
        lock.unlink()
        lock.write_bytes(b'')
        lock.chmod(0o600)
        os.link(lock, self.root / 'lock-copy')
        self.refused(self.opened, 'FILE_METADATA_REFUSED')
        lock.unlink()
        os.mkfifo(lock, 0o600)
        self.refused(self.opened, 'FILE_METADATA_REFUSED')

    def test_record_symlink_and_fifo_refuse_without_reading_target(self):
        self.path.symlink_to(self.root / 'missing')
        with self.opened() as store:
            self.refused(lambda: store.reserve(effect_started=False), 'RESERVATION_UNAVAILABLE')
        self.path.unlink()
        os.mkfifo(self.path, 0o600)
        with self.opened() as store:
            self.refused(lambda: store.reserve(effect_started=False), 'FILE_METADATA_REFUSED')

    def test_record_hardlink_modes_and_directory_leaf_refuse(self):
        self.seed()
        os.link(self.path, self.root / 'material-copy')
        with self.opened() as store:
            self.refused(store.snapshot, 'FILE_METADATA_REFUSED')
        (self.root / 'material-copy').unlink()
        for mode in (0o644, 0o400, 0o660, 0o1600):
            self.path.chmod(mode)
            with self.opened() as store:
                self.refused(store.snapshot, 'FILE_METADATA_REFUSED')
        self.path.unlink()
        self.path.mkdir()
        with self.opened() as store:
            self.refused(store.snapshot, 'FILE_METADATA_REFUSED')

    def test_record_owner_mismatch_refuses_without_reading_credentials(self):
        self.seed()
        identity = self.path.stat().st_ino
        real_stat = journal.os.fstat
        def other_owner(descriptor):
            value = real_stat(descriptor)
            if value.st_ino != identity:
                return value
            fields = {name: getattr(value, name) for name in dir(value) if name.startswith('st_')}
            fields['st_uid'] = os.geteuid() + 1
            return SimpleNamespace(**fields)
        with self.opened() as store, patch.object(journal.os, 'fstat', side_effect=other_owner):
            self.refused(store.snapshot, 'FILE_METADATA_REFUSED')

    def test_directory_traversal_symlink_owner_and_mode_refuse(self):
        for directory in ('relative', str(self.root) + '/private/../private', str(self.root) + '//private', '/'):
            self.refused(lambda: self.opened(directory=directory), 'DIRECTORY_INVALID')
        linked = self.root / 'linked'
        linked.symlink_to(self.directory, target_is_directory=True)
        self.refused(lambda: self.opened(directory=linked), 'JOURNAL_UNAVAILABLE')
        self.directory.chmod(0o755)
        self.refused(self.opened, 'DIRECTORY_POLICY_REFUSED')
        self.directory.chmod(0o700)
        with patch.object(journal.os, 'geteuid', return_value=os.geteuid() + 1):
            self.refused(self.opened, 'DIRECTORY_POLICY_REFUSED')

    def test_parent_path_swap_refuses_anchored_descriptor_without_escape_write(self):
        with self.opened() as store:
            moved = self.root / 'retained'
            self.directory.rename(moved)
            self.directory.mkdir(mode=0o700)
            self.refused(lambda: store.reserve(effect_started=False), 'DIRECTORY_BINDING_CHANGED')
            self.assertFalse(self.path.exists())

    def test_lock_path_swap_refuses_even_with_original_lock_held(self):
        with self.opened() as store:
            lock = self.directory / journal.LOCK
            lock.rename(self.directory / 'retained-lock')
            lock.write_bytes(b'')
            lock.chmod(0o600)
            self.refused(lambda: store.reserve(effect_started=False), 'LOCK_BINDING_CHANGED')

    def test_invalid_record_json_duplicate_keys_size_and_credential_fields_refuse(self):
        self.seed()
        original = self.path.read_bytes()
        for content in (b'{', b'{"schema":1,"schema":1}', b'', b'x' * (journal.MAX_BYTES + 1)):
            self.path.write_bytes(content)
            with self.opened() as store:
                self.refused(store.snapshot, 'RECORD_INVALID')
        self.path.write_bytes(original)
        self.mutate(lambda value: value['credentials'].update(extra='not-supported'))
        with self.opened() as store:
            self.refused(store.snapshot, 'MATERIAL_INVALID')

    def test_publication_failure_preserves_ambiguous_marker_and_blocks_replay(self):
        with self.opened() as store, patch.object(journal.os, 'replace', side_effect=OSError('private response details')):
            self.refused(lambda: store.reserve(effect_started=False), 'PUBLICATION_RECONCILIATION_REQUIRED')
            self.assertTrue((self.directory / store.marker).exists())
            self.assertTrue(any(path.name.startswith(store.pending_prefix) for path in self.directory.iterdir()))
            self.refused(lambda: store.reserve(effect_started=False), 'PUBLICATION_RECONCILIATION_REQUIRED')
        self.publication_blocked()

    def test_short_write_failure_does_not_publish_or_regenerate_on_retry(self):
        with self.opened() as store, patch.object(journal.os, 'write', return_value=0):
            self.refused(lambda: store.reserve(effect_started=False), 'PUBLICATION_RECONCILIATION_REQUIRED')
        self.assertFalse(self.path.exists())
        self.publication_blocked()

    def test_directory_sync_failure_after_replacement_retains_material_and_marker(self):
        self.seed()
        original = json.loads(self.path.read_text())['credentials']
        real_sync, count = journal.os.fsync, [0]
        with self.opened() as store:
            store.reserve(effect_started=False)
            def sync(descriptor):
                count[0] += 1
                if count[0] == 5:
                    raise OSError('sync unavailable')
                return real_sync(descriptor)
            with patch.object(journal.os, 'fsync', side_effect=sync):
                self.refused(lambda: store.before_effect('auth_sessions', self.metadata), 'PUBLICATION_RECONCILIATION_REQUIRED')
            value = json.loads(self.path.read_text())
            self.assertTrue(value['credentials'] == original)
            self.assertEqual(value['effects'][0]['state'], 'pending')
            self.assertTrue((self.directory / store.marker).exists())
        self.publication_blocked()

    def test_after_effect_failure_leaves_previous_native_effect_pending(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
            with patch.object(journal.os, 'replace', side_effect=OSError('interrupted')):
                self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'PUBLICATION_RECONCILIATION_REQUIRED')
            value = json.loads(self.path.read_text())
            self.assertEqual(value['effects'][0]['state'], 'pending')
        self.publication_blocked()

    def test_target_substitution_during_publication_is_detected(self):
        self.seed()
        real_replace = journal.os.replace
        with self.opened() as store:
            store.reserve(effect_started=False)
            def substitute(*args, **kwargs):
                real_replace(*args, **kwargs)
                self.path.unlink()
                self.path.write_bytes(b'{}')
                self.path.chmod(0o600)
            with patch.object(journal.os, 'replace', side_effect=substitute):
                self.refused(lambda: store.before_effect('auth_sessions', self.metadata), 'PUBLICATION_RECONCILIATION_REQUIRED')
        self.publication_blocked()

    def test_no_stdout_stderr_or_private_error_details(self):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), self.opened() as store:
            material = store.reserve(effect_started=False)
            with patch.object(journal.os, 'replace', side_effect=OSError(material['auth'])):
                reason = self.refused(lambda: store.before_effect('auth_sessions', self.metadata))
            self.assertTrue(all(secret not in reason for secret in material.values()))
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), '')

    def test_closed_handle_cannot_read_or_publish(self):
        store = self.opened()
        store.close()
        self.refused(lambda: store.reserve(effect_started=False), 'JOURNAL_CLOSED')
        self.refused(store.snapshot, 'JOURNAL_CLOSED')

    def test_manifest_binds_exact_generation_and_contains_no_credentials(self):
        digest = self.interrupt_publication('rename')
        with self.opened() as store:
            manifest = json.loads((self.directory / store.marker).read_text())
            candidate = json.loads((self.directory / manifest['candidate']).read_text())
            self.assertEqual(manifest['schema'], 2)
            self.assertEqual(manifest['material_digest'], digest)
            self.assertEqual(manifest['proposed_record_digest'], journal.TransferJournal._record_digest(candidate))
            self.assertTrue(all(secret not in json.dumps(manifest) for secret in candidate['credentials'].values()))
            self.refused(store.snapshot, 'PUBLICATION_RECONCILIATION_REQUIRED')

    def test_full_candidate_adoption_preserves_pending_and_uses_same_material(self):
        digest = self.interrupt_publication('rename')
        original = json.loads(self.path.read_text())['credentials']
        with self.opened() as store, patch.object(journal.secrets, 'token_hex', side_effect=AssertionError('regenerated')), patch.object(journal.secrets, 'token_urlsafe', side_effect=AssertionError('regenerated')):
            status = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            self.assertEqual(status['effects'][0]['state'], 'pending')
            self.assertIsNone(status['effects'][0]['receipt'])
            self.assertTrue(store.reserve(effect_started=True, expected_material_digest=digest) == original)

    def test_valid_candidate_before_file_or_directory_sync_can_be_resynced(self):
        for stage in ('candidate_sync', 'candidate_directory_sync'):
            with self.subTest(stage=stage):
                digest = self.interrupt_publication(stage)
                with self.opened() as store:
                    status = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
                    self.assertEqual(status['effects'][0]['state'], 'pending')
                # Use a new operation/root fixture for the second publication.
                self.binding = binding()
                self.path = self.directory / (self.binding['operation'] + '.' + self.binding['runtime'] + '.json')
                self.metadata['resource_id'] = self.binding['database_oid']

    def test_renamed_generation_survives_sync_and_marker_cleanup_interruptions(self):
        for stage in ('rename_directory_sync', 'marker_remove', 'cleanup_directory_sync'):
            with self.subTest(stage=stage):
                digest = self.interrupt_publication(stage)
                with self.opened() as store:
                    status = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
                    self.assertEqual(status['revision'], 1)
                    self.assertEqual(status['effects'][0]['state'], 'pending')
                self.binding = binding()
                self.path = self.directory / (self.binding['operation'] + '.' + self.binding['runtime'] + '.json')

    def test_initial_candidate_is_adopted_only_without_started_effects(self):
        self.interrupt_publication('rename', initial=True)
        with self.opened() as store:
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=None), 'PUBLIC_MATERIAL_REQUIRED')
            status = store.reconcile_publication(effect_started=False, expected_material_digest=None)
            self.assertEqual((status['revision'], status['effects']), (0, []))
            self.assertTrue(store.reserve(effect_started=False))

    def test_missing_or_partial_initial_material_remains_retained_and_blocked(self):
        self.interrupt_publication('missing_candidate', initial=True)
        with self.opened() as store:
            self.refused(lambda: store.reconcile_publication(effect_started=False, expected_material_digest=None), 'PUBLICATION_GENERATION_MISSING')
            self.assertTrue((self.directory / store.marker).exists())
        self.publication_blocked()

    def test_partial_candidate_never_overwrites_valid_prior_reservation(self):
        digest = self.interrupt_publication('partial_candidate')
        previous = self.path.read_bytes()
        with self.opened() as store:
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'RECORD_INVALID')
            self.assertTrue(self.path.read_bytes() == previous)
            self.assertTrue(any(name.startswith(store.pending_prefix) for name in os.listdir(self.directory)))

    def test_marker_before_candidate_aborts_only_publication_with_durable_receipt(self):
        digest = self.interrupt_publication('missing_candidate')
        previous = self.path.read_bytes()
        with self.opened() as store:
            status = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            self.assertEqual(status['publication']['outcome'], 'publication_aborted')
            self.assertEqual(status['effects'], [])
            self.assertTrue(self.path.read_bytes() == previous)
            receipts = [name for name in os.listdir(self.directory) if '.aborted-' in name]
            self.assertEqual(len(receipts), 1)
            self.assertEqual(json.loads((self.directory / receipts[0]).read_text()), status['publication'])

    def test_unknown_or_legacy_manifest_never_adopted(self):
        digest = self.interrupt_publication('rename')
        with self.opened() as store:
            path = self.directory / store.marker
            path.write_text(json.dumps({'schema': 1, 'revision': 1}))
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_MANIFEST_INVALID')
            self.assertTrue(path.exists())

    def test_multiple_or_orphan_candidates_are_retained(self):
        digest = self.interrupt_publication('rename')
        with self.opened() as store:
            extra = self.directory / (store.pending_prefix + '0' * 32)
            extra.write_bytes(b'{}')
            extra.chmod(0o600)
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_EVIDENCE_AMBIGUOUS')
            (self.directory / store.marker).unlink()
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_EVIDENCE_AMBIGUOUS')
            self.assertTrue(extra.exists())

    def test_reconciliation_rejects_public_digest_binding_and_manifest_substitutions(self):
        digest = self.interrupt_publication('rename')
        with self.opened() as store:
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest='0' * 64), 'MATERIAL_DIFFERS')
            path = self.directory / store.marker
            value = json.loads(path.read_text())
            value['binding_digest'] = '0' * 64
            path.write_text(json.dumps(value))
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'BINDING_DIFFERS')

    def test_reconciliation_fsync_interruption_can_resume_without_new_material(self):
        digest = self.interrupt_publication('rename')
        with self.opened() as store:
            with patch.object(journal.os, 'fsync', side_effect=OSError('interruption')):
                self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_RECONCILIATION_REQUIRED')
            self.refused(store.snapshot, 'PUBLICATION_RECONCILIATION_REQUIRED')
            result = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            self.assertEqual(result['effects'][0]['state'], 'pending')

    def test_interrupted_reconciliation_after_rename_keeps_exact_manifest(self):
        digest = self.interrupt_publication('rename')
        real_sync, count = journal.os.fsync, [0]
        with self.opened() as store:
            def sync(descriptor):
                count[0] += 1
                if count[0] == 3:
                    raise OSError('interruption')
                return real_sync(descriptor)
            with patch.object(journal.os, 'fsync', side_effect=sync):
                self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_RECONCILIATION_REQUIRED')
            self.assertTrue((self.directory / store.marker).exists())
            self.assertEqual(store.reconcile_publication(effect_started=True, expected_material_digest=digest)['effects'][0]['state'], 'pending')

    def test_illegal_candidate_transition_cannot_rewrite_previous_verified_receipt(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            store.before_effect('auth_sessions', self.metadata)
            store.after_effect('auth_sessions', self.receipt)
        with self.opened() as store:
            proposed, witness = store._read()
            proposed['effects'][0]['receipt']['checks'] = 999
            proposed['revision'] += 1
            self.refused(lambda: store._publish(proposed, witness), 'PUBLICATION_TRANSITION_INVALID')

    def test_native_verified_receipt_is_historical_and_not_created_by_adoption(self):
        with self.opened() as store:
            store.reserve(effect_started=False)
            digest = store.snapshot()['material_digest']
            store.before_effect('auth_sessions', self.metadata)
            with patch.object(journal.os, 'replace', side_effect=OSError('interruption')):
                self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'PUBLICATION_RECONCILIATION_REQUIRED')
        with self.opened() as store:
            status = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            self.assertEqual(status['effects'][0]['receipt'], self.receipt)
            self.assertEqual(status['effects'][0]['state'], 'verified')

    def test_after_rename_still_requires_exact_credential_free_previous_state(self):
        digest = self.interrupt_publication('rename_directory_sync')
        with self.opened() as store:
            path = self.directory / store.marker
            value = json.loads(path.read_text())
            value['previous_state']['created_at'] += 1
            path.write_text(json.dumps(value))
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_PREVIOUS_DIFFERS')
            self.assertTrue(path.exists())

    def test_manifest_candidate_directory_escape_refuses_without_following_it(self):
        digest = self.interrupt_publication('rename')
        with self.opened() as store:
            path = self.directory / store.marker
            value = json.loads(path.read_text())
            value['candidate'] = store.pending_prefix + '../outside'
            path.write_text(json.dumps(value))
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_MANIFEST_INVALID')

    def test_reconciliation_candidate_modes_and_symlinks_refuse(self):
        digest = self.interrupt_publication('rename')
        with self.opened() as store:
            manifest = json.loads((self.directory / store.marker).read_text())
            path = self.directory / manifest['candidate']
            path.chmod(0o640)
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'FILE_METADATA_REFUSED')
            path.unlink()
            path.symlink_to(self.path)
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_RECONCILIATION_REQUIRED')

    def test_aborted_publication_receipt_survives_interrupted_marker_removal(self):
        digest = self.interrupt_publication('missing_candidate')
        with self.opened() as store:
            with patch.object(journal.os, 'unlink', side_effect=OSError('interruption')):
                self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_RECONCILIATION_REQUIRED')
            receipts = [name for name in os.listdir(self.directory) if '.aborted-' in name]
            self.assertEqual(len(receipts), 1)
            result = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            self.assertEqual(result['publication']['outcome'], 'publication_aborted')
            self.assertEqual(len([name for name in os.listdir(self.directory) if '.aborted-' in name]), 1)

    def test_interrupted_reconciliation_marker_directory_sync_is_idempotent(self):
        digest = self.interrupt_publication('rename')
        real_sync, count = journal.os.fsync, [0]
        with self.opened() as store:
            def sync(descriptor):
                count[0] += 1
                if count[0] == 5:
                    raise OSError('interruption')
                return real_sync(descriptor)
            with patch.object(journal.os, 'fsync', side_effect=sync):
                self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_RECONCILIATION_REQUIRED')
            self.assertEqual(store.reconcile_publication(effect_started=True, expected_material_digest=digest)['effects'][0]['state'], 'pending')

    def test_aborting_missing_after_effect_candidate_preserves_native_uncertainty(self):
        real_open = journal.os.open
        with self.opened() as store:
            store.reserve(effect_started=False)
            digest = store.snapshot()['material_digest']
            store.before_effect('auth_sessions', self.metadata)
            def opened(name, *args, **kwargs):
                if isinstance(name, str) and name.startswith(store.pending_prefix):
                    raise OSError('interruption')
                return real_open(name, *args, **kwargs)
            with patch.object(journal.os, 'open', side_effect=opened):
                self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'PUBLICATION_RECONCILIATION_REQUIRED')
            result = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            self.assertEqual(result['effects'][0]['state'], 'pending')
            self.assertIsNone(result['effects'][0]['receipt'])
            self.assertEqual(result['publication']['outcome'], 'publication_aborted')

    def test_initial_renamed_generation_recovers_without_inventing_public_digest(self):
        self.interrupt_publication('rename_directory_sync', initial=True)
        with self.opened() as store:
            result = store.reconcile_publication(effect_started=False, expected_material_digest=None)
            self.assertEqual((result['revision'], result['effects']), (0, []))
            self.assertEqual(result['material_digest'], store.snapshot()['material_digest'])

    def test_partial_sole_initial_material_cannot_be_abandoned_or_regenerated(self):
        self.interrupt_publication('partial_candidate', initial=True)
        with self.opened() as store:
            self.refused(lambda: store.reconcile_publication(effect_started=False, expected_material_digest=None), 'RECORD_INVALID')
            self.refused(lambda: store.reserve(effect_started=False), 'PUBLICATION_RECONCILIATION_REQUIRED')
            self.assertFalse(self.path.exists())
            self.assertTrue(any(name.startswith(store.pending_prefix) for name in os.listdir(self.directory)))

    def test_candidate_same_content_inode_substitution_during_directory_sync_refuses(self):
        digest = self.interrupt_publication('rename')
        previous = self.path.read_bytes()
        real_sync, count = journal.os.fsync, [0]
        with self.opened() as store:
            manifest = json.loads((self.directory / store.marker).read_text())
            candidate = self.directory / manifest['candidate']
            original_inode = candidate.stat().st_ino
            original_content = candidate.read_bytes()
            def sync(descriptor):
                real_sync(descriptor)
                count[0] += 1
                if count[0] == 2:
                    substitute = self.directory / 'replacement-candidate'
                    substitute.write_bytes(original_content)
                    substitute.chmod(0o600)
                    os.replace(substitute, candidate)
            with patch.object(journal.os, 'fsync', side_effect=sync):
                self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'FILE_BINDING_CHANGED')
            self.assertNotEqual(candidate.stat().st_ino, original_inode)
            self.assertTrue(self.path.read_bytes() == previous)
            self.assertTrue((self.directory / store.marker).exists())
            self.refused(store.snapshot, 'PUBLICATION_RECONCILIATION_REQUIRED')

    def test_abort_receipt_replays_after_failure_immediately_after_actual_marker_unlink(self):
        digest = self.interrupt_publication('missing_candidate')
        real_unlink = journal.os.unlink
        with self.opened() as store:
            def unlink(name, *args, **kwargs):
                real_unlink(name, *args, **kwargs)
                if name == store.marker:
                    raise OSError('response lost after actual unlink')
            with patch.object(journal.os, 'unlink', side_effect=unlink):
                self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_RECONCILIATION_REQUIRED')
            self.assertFalse((self.directory / store.marker).exists())
            name = next(name for name in os.listdir(self.directory) if '.aborted-' in name)
            expected = json.loads((self.directory / name).read_text())
        with self.opened() as store:
            replay = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            self.assertEqual(replay['publication'], expected)
            self.assertEqual(replay['effects'], [])
            self.assertEqual(store.reconcile_publication(effect_started=True, expected_material_digest=digest)['publication'], expected)

    def test_copied_duplicate_abort_manifest_receipts_refuse_replay(self):
        digest = self.interrupt_publication('missing_candidate')
        with self.opened() as store:
            store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            name = next(name for name in os.listdir(self.directory) if '.aborted-' in name)
            other = self.directory / ('.' + store.name + '.aborted-' + '0' * 32 + '.json')
            other.write_bytes((self.directory / name).read_bytes())
            other.chmod(0o600)
            self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_ABORT_RECEIPT_DIFFERS')
            self.assertTrue(other.exists())

    def test_foreign_or_unbounded_aborted_receipt_fields_refuse_markerless_replay(self):
        digest = self.interrupt_publication('missing_candidate')
        with self.opened() as store:
            store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            name = next(name for name in os.listdir(self.directory) if '.aborted-' in name)
            path = self.directory / name
            original = json.loads(path.read_text())
            for change in ({'binding_digest': '0' * 64}, {'proposed_revision': True}, {'message': 'private response'}):
                path.write_text(json.dumps({**original, **change}))
                self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_ABORT_RECEIPT_DIFFERS')

    def test_repeated_abort_history_replays_second_cleanup_loss_with_native_pending(self):
        real_open, real_unlink = journal.os.open, journal.os.unlink
        with self.opened() as store:
            material = store.reserve(effect_started=False)
            digest = store.snapshot()['material_digest']
            store.before_effect('auth_sessions', self.metadata)
            def opened(name, *args, **kwargs):
                if isinstance(name, str) and name.startswith(store.pending_prefix):
                    raise OSError('interruption before candidate')
                return real_open(name, *args, **kwargs)
            for attempt in range(2):
                with patch.object(journal.os, 'open', side_effect=opened):
                    self.refused(lambda: store.after_effect('auth_sessions', self.receipt), 'PUBLICATION_RECONCILIATION_REQUIRED')
                if attempt == 0:
                    result = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
                    self.assertEqual(len(result['publications']), 1)
                else:
                    def unlink(name, *args, **kwargs):
                        real_unlink(name, *args, **kwargs)
                        if name == store.marker:
                            raise OSError('response lost after second actual unlink')
                    with patch.object(journal.os, 'unlink', side_effect=unlink):
                        self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_RECONCILIATION_REQUIRED')
            self.assertFalse((self.directory / store.marker).exists())
        with self.opened() as store, patch.object(journal.secrets, 'token_hex', side_effect=AssertionError('regenerated')), patch.object(journal.secrets, 'token_urlsafe', side_effect=AssertionError('regenerated')):
            result = store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            self.assertEqual(len(result['publications']), 2)
            self.assertNotIn('publication', result)
            self.assertEqual(result['effects'][0]['state'], 'pending')
            self.assertIsNone(result['effects'][0]['receipt'])
            self.assertTrue(store.reserve(effect_started=True, expected_material_digest=digest) == material)
            retained = sorted(name for name in os.listdir(self.directory) if '.aborted-' in name)
            self.assertEqual(len(retained), 2)
            self.assertEqual(result['publications'], [json.loads((self.directory / name).read_text()) for name in retained])
            self.assertEqual(store.reconcile_publication(effect_started=True, expected_material_digest=digest), result)

    def test_unmatched_same_or_future_abort_revision_is_not_historical(self):
        digest = self.interrupt_publication('missing_candidate')
        with self.opened() as store:
            store.reconcile_publication(effect_started=True, expected_material_digest=digest)
            name = next(name for name in os.listdir(self.directory) if '.aborted-' in name)
            path = self.directory / name
            original = json.loads(path.read_text())
            for revision in (1, 2):
                value = {**original, 'previous_record_digest': '0' * 64, 'proposed_revision': revision}
                path.write_text(json.dumps(value))
                self.refused(lambda: store.reconcile_publication(effect_started=True, expected_material_digest=digest), 'PUBLICATION_ABORT_RECEIPT_DIFFERS')


if __name__ == '__main__':
    unittest.main()
