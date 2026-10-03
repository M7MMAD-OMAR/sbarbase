"""Behavioral cutover tests with a controlled native boundary, not native proof."""
import contextlib
import datetime
import json
import io
import re
import tarfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import backup
import restore_cutover
import restore_operation
from test_backup import Fixture, E

NAME = '20260901T030000Z'
NOW = datetime.datetime(2026, 10, 3, 6, tzinfo=datetime.UTC)
COUNTS = {'auth.users': 1, 'auth.identities': 1, 'storage.buckets': 1, 'storage.objects': 1}


class NativeModel:
    """Only Docker, SQL transport, file helpers and service health are doubles.

    Archive planner, restore orchestration, journal validation/publication,
    checkpoint persistence and explicit recovery all execute production code.
    Unexpected boundary operations fail, instead of accepting unknown queries.
    """
    def __init__(self, fixture, scope=E, fail=None, actual=None):
        self.fixture, self.scope, self.fail = fixture, scope, fail
        self.database = backup.STORAGE_DATABASE if scope == backup.STORAGE else scope
        self.dbs = {self.database: {'oid': '100', 'allow_connections': True}}
        self.running = True
        self.cid = 'd' * 64
        self.events = []
        self.actual = actual
        self.prepared_count = '0'
        self.backends = '0'
        self.pid = '900'
        self.roles = ['supabase_admin']
        self.archived_search_path = None
        self.database_settings = {'search_path': 'live_changed_schema'}
        self.retained_search_path = 'public'
        self.held = None
        self.trees = {E: self.identity('1')}
        self.stack = contextlib.ExitStack()

    @staticmethod
    def identity(inode):
        return {'exists': True, 'device': '1', 'inode': inode, 'sha256': inode * 64}

    @staticmethod
    def missing():
        return {'exists': False, 'device': None, 'inode': None, 'sha256': None}

    def journal(self):
        path = restore_operation.journal_path(backup.STATE, self.scope)
        return restore_operation.read_journal(backup.STATE, self.scope) if path.exists() else None

    def event(self, name):
        journal = self.journal()
        self.events.append((name, journal['phase'] if journal else None,
                            {key: dict(value) for key, value in self.dbs.items()}, self.running))

    def record(self):
        return {'Id': self.cid, 'Name': '/' + backup.STORAGE_CONTAINER, 'Image': 'sha256:' + 'f' * 64,
                'State': {'Running': self.running, 'Paused': False, 'Restarting': False},
                'Config': {'Labels': {'io.sbarbase.owner': backup.DATABASE_OWNER}, 'Env': [
                    'STORAGE_BACKEND=file', 'GLOBAL_S3_BUCKET=' + backup.TENANT_PARENT,
                    'FILE_STORAGE_BACKEND_PATH=/tmp/storage-data', 'MULTI_TENANT=true',
                    'S3_PROTOCOL_ENABLED=false', 'PG_QUEUE_ENABLE=false']},
                'Mounts': [{'Destination': '/tmp/storage-data', 'Type': 'volume',
                            'Name': backup.OBJECTS_VOLUME, 'RW': True}]}

    def run(self, argv, **kwargs):
        if argv[:3] == ['docker', 'container', 'inspect']:
            return SimpleNamespace(stderr='', stdout=json.dumps([self.record()]))
        if 'pg_restore' in argv:
            database = self.database
            sql = ('CREATE DATABASE ' + database + " WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'C';\n"
                   + 'ALTER DATABASE ' + database + ' OWNER TO supabase_admin;\n'
                   + ('' if self.archived_search_path is None else 'ALTER DATABASE ' + database
                      + " SET search_path TO '" + self.archived_search_path + "';\n")
                   + '\\connect ' + database + "\nSELECT pg_catalog.set_config('search_path', '', false);\nCREATE TABLE payload(id integer);\n")
            kwargs['stdout'].write(sql.encode())
            return SimpleNamespace(stderr='')
        if argv[:2] == ['docker', 'stop']:
            self.event('stop')
            if self.cid in argv:
                self.running = False
                if self.fail == 'storage-cid':
                    self.cid = 'e' * 64
                if self.fail == 'target-replaced':
                    self.dbs[self.database] = {'oid': '999', 'allow_connections': True}
            return SimpleNamespace(stderr='', stdout='')
        raise AssertionError('Unexpected native command: ' + repr(argv))

    def sql(self, query, database='postgres'):
        if query == restore_operation.WORKER_QUERY:
            return json.dumps({'version': 170000, 'shared': '', 'session': '', 'local': '',
                               'workers': ['checkpointer'], 'subscriptions': 0, 'scoped_preloads': []})
        if 'FROM pg_roles' in query:
            return json.dumps(self.roles)
        if 'to_regprocedure' in query:
            return 't'
        if 'pg_nextoid' in query:
            return '20000'
        if 'pg_prepared_xacts' in query:
            return self.prepared_count
        if 'pg_stat_activity' in query:
            return self.backends
        if 'json_object_agg' in query:
            return json.dumps(self.dbs)
        if 'SELECT oid FROM pg_database' in query:
            return self.dbs[re.search("datname='([^']+)'", query)[1]]['oid']
        if 'SELECT datallowconn' in query:
            return 't' if next(row for row in self.dbs.values() if row['oid'] == query.split('oid=')[1].rstrip(';'))['allow_connections'] else 'f'
        if 'SELECT count(*) FROM pg_database' in query:
            return str(sum(name in self.dbs for name in re.findall("'([^']+)'", query)))
        if query.startswith('CREATE DATABASE'):
            self.event('create')
            name = re.match(r'CREATE DATABASE "?([a-z0-9_]+)', query)[1]
            self.dbs[name] = {'oid': '20000', 'allow_connections': True}
            if self.fail == 'create':
                raise backup.BackupError('injected after creation')
            return ''
        match = re.fullmatch(r'ALTER DATABASE ([a-z0-9_]+) (ALLOW_CONNECTIONS (true|false)|RENAME TO ([a-z0-9_]+));', query)
        if match:
            name, _, allow, target = match.groups()
            self.event('open' if allow == 'true' else 'fence' if allow else 'rename')
            if allow:
                if allow == 'true' and self.dbs[name]['oid'] == '20000':
                    if self.fail == 'open':
                        raise backup.BackupError('injected')
                    assert backup.completion_path(self.scope).exists(), 'checkpoint must precede reopening'
                self.dbs[name]['allow_connections'] = allow == 'true'
            else:
                assert not self.dbs[name]['allow_connections'], 'rename requires closed target'
                self.dbs[target] = self.dbs.pop(name)
                if self.fail == 'rename':
                    raise backup.BackupError('injected')
            return ''
        if query.startswith('DROP DATABASE '):
            self.event('drop')
            self.dbs.pop(query.split()[2].rstrip(';'))
            return ''
        raise AssertionError('Unexpected SQL: ' + query)

    def helper(self, script, *args, **kwargs):
        self.event('files')
        assert not self.running, 'Storage must stay stopped through object operations'
        assert all(not row['allow_connections'] for row in self.dbs.values()), 'both database OIDs must stay fenced through object operations'
        if script == 'mkdir "$1"':
            return SimpleNamespace(stderr='', stdout='')
        if script.startswith('cd "$1"'):
            stage = args[0].split('/')[-1]
            self.trees[stage + '/' + E] = self.identity('2')
            if self.fail == 'files':
                raise backup.BackupError('injected')
            return SimpleNamespace(stderr='', stdout='')
        match = re.fullmatch(r'node -e "\$1" (identity|sync|rename|remove) "\$2" "\$3" "\$4"', script)
        assert match is not None, 'unknown file helper command'
        action = match[1]
        name, expected, target = args[1:]
        current = self.trees.get(name, self.missing())
        if action in ('rename', 'remove'):
            assert current == json.loads(expected), 'tree identity must match before mutation'
            self.trees.pop(name, None)
            if action == 'rename':
                self.trees[target] = current
                if self.fail == 'file-replace' and target == E:
                    raise backup.BackupError('injected after object rename')
        return SimpleNamespace(stderr='', stdout=json.dumps(current))

    def session(self, argv):
        model = self
        stage = argv[-1]
        class Session:
            def __enter__(self):
                model.held = stage
                return self
            def __exit__(self, *args):
                model.held = None
            def execute(self, query):
                if query.startswith('SELECT pg_backend_pid(),'):
                    return model.pid + '|' + model.dbs[stage]['oid']
                model.event('held')
                assert not model.dbs[stage]['allow_connections'], 'held replay and validation require fenced stage'
                if model.retained_search_path == '' and re.search(r'FROM\s+tenants\b', query, re.I):
                    raise backup.BackupError('relation tenants does not exist with empty search_path')
                if query == backup.COUNTS:
                    return model.actual or '1|1|1|1'
                if query == backup.STORAGE_COUNTS:
                    return model.actual or '1|' + E
                if query == 'SELECT pg_backend_pid();':
                    return model.pid
                if query.startswith('SET max_parallel_workers_per_gather=0;') or query.lstrip().startswith('ALTER DATABASE '):
                    assert chr(92) + 'n' not in query, 'metadata must use SQL newlines, never a psql backslash command'
                    setting = re.search(r"SET search_path TO '([^']*)'", query)
                    if setting:
                        model.database_settings['search_path'] = setting[1]
                    return ''
                raise AssertionError('Unexpected held SQL: ' + query)
            def replay(self, source):
                model.event('replay')
                assert not model.dbs[stage]['allow_connections']
                body = source.read()
                assert b'CREATE TABLE payload' in body
                if b"set_config('search_path', '', false)" in body:
                    model.retained_search_path = ''
                if model.fail == 'replay':
                    raise backup.BackupError('injected')
                return ''
        return Session()

    def start_storage(self):
        self.event('start-storage')
        self.running = True
        return '10.0.0.9'

    def __enter__(self):
        patches = {'run': self.run, 'sql': self.sql, 'helper': self.helper,
                   'resolve_storage_image': lambda: ('qualified-pin', 'sha256:' + 'f' * 64),
                   'start_storage': self.start_storage, 'wait_storage': lambda *a: None,
                   'start_services': lambda *a: self.event('start-services'), 'wait_healthy': lambda *a: None}
        for name, value in patches.items():
            self.stack.enter_context(patch.object(backup, name, value))
        self.stack.enter_context(patch.object(restore_cutover, 'HeldSession', self.session))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def restore(self):
        return restore_cutover.restore(backup, self.scope, NAME, NOW)

    def recover(self):
        return restore_cutover.recover(backup, self.scope, NAME, self.journal()['stamp'])


def environment_archive(fixture):
    path = fixture.complete(NAME, counts=COUNTS)
    with tarfile.open(path / 'objects.tar', 'w') as archive:
        member = tarfile.TarInfo(E)
        member.type, member.mode = tarfile.DIRTYPE, 0o700
        archive.addfile(member)
    manifest = json.loads((path / 'manifest.json').read_text())
    manifest['objects'].update(bytes=(path / 'objects.tar').stat().st_size, sha256=backup.digest(path / 'objects.tar'))
    (path / 'manifest.json').write_text(json.dumps(manifest))
    return path


class CutoverTests(Fixture):
    def setUp(self):
        super().setUp()
        environment_archive(self)

    def test_intent_fences_storage_and_checkpoint_survive_success(self):
        with NativeModel(self) as model:
            result = model.restore()
            self.assertEqual(model.dbs[E]['oid'], '20000')
            self.assertFalse(model.dbs[result['previous_database']]['allow_connections'])
            self.assertEqual(model.events[0][:2], ('create', 'stage-create-intent'))
            self.assertEqual(next(event for event in model.events if event[0] == 'stop')[1], 'stop-intent')
            self.assertEqual(next(event for event in model.events if event[0] == 'open')[1], 'open-intent')
            self.assertEqual(model.journal()['phase'], 'completed')
            self.assertEqual(model.trees[E], model.identity('2'))

    def test_prepared_transactions_refuse_before_journal_or_mutation(self):
        with NativeModel(self) as model:
            model.prepared_count = '1'
            with self.assertRaisesRegex(backup.BackupError, 'Prepared'):
                model.restore()
            self.assertEqual(model.events, [])
            self.assertIsNone(model.journal())

    def test_held_backend_drain_timeout_preserves_original_and_requires_recovery(self):
        with NativeModel(self) as model, patch.object(restore_cutover.time, 'monotonic', side_effect=[0, 31]):
            model.backends = '1'
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertEqual(model.dbs[E], {'oid': '100', 'allow_connections': True})
            self.assertTrue(model.running)
            self.assertFalse(any(event[0] == 'rename' for event in model.events))

    def test_observed_opening_preserves_new_writes_after_lost_phase_write(self):
        with NativeModel(self) as model:
            model.restore()
            journal = model.journal()
            journal['phase'] = 'open-intent'
            restore_operation.publish(backup.STATE, journal, backup.atomic_private)
            model.trees[E] = model.identity('3')
            before = len(model.events)
            model.recover()
            self.assertEqual(model.trees[E], model.identity('3'))
            self.assertEqual(model.dbs[E]['oid'], '20000')
            self.assertFalse(any(event[0] in ('files', 'drop', 'rename', 'stop') for event in model.events[before:]))

    def test_replaced_storage_cid_refuses_recovery_before_mutation(self):
        with NativeModel(self, fail='rename') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            model.cid = 'e' * 64
            before = len(model.events)
            with self.assertRaisesRegex(RuntimeError, 'identity differs'):
                model.recover()
            self.assertEqual(len(model.events), before)

    def test_missing_archive_role_refuses_before_initial_journal(self):
        with NativeModel(self) as model:
            model.roles = []
            with self.assertRaisesRegex(backup.BackupError, 'role is missing'):
                model.restore()
            self.assertEqual(model.events, [])
            self.assertIsNone(model.journal())

    def test_creation_gap_is_bound_to_durable_oid_and_recoverable(self):
        with NativeModel(self, fail='create') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            journal = model.journal()
            self.assertEqual(journal['phase'], 'stage-create-intent')
            self.assertEqual(model.dbs[journal['stage']]['oid'], journal['stage_oid'])
            self.assertTrue(model.dbs[journal['stage']]['allow_connections'])
            model.fail = None
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertEqual(model.dbs, {E: {'oid': '100', 'allow_connections': True}})

    def test_partial_file_cutover_recovers_original_database_and_objects(self):
        with NativeModel(self, fail='file-replace') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertEqual(model.journal()['phase'], 'files-replace-intent')
            self.assertEqual(model.dbs[E]['oid'], '20000')
            self.assertFalse(model.running)
            model.fail = None
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertEqual(model.dbs, {E: {'oid': '100', 'allow_connections': True}})
            self.assertEqual(model.trees[E], model.identity('1'))
            self.assertFalse(backup.completion_path(E).exists())

    def test_checkpoint_write_failure_keeps_every_writer_fenced(self):
        atomic = backup.atomic_private
        def fail_checkpoint(path, value):
            if path == backup.completion_path(E):
                raise OSError('injected checkpoint persistence failure')
            return atomic(path, value)
        with NativeModel(self) as model, patch.object(backup, 'atomic_private', fail_checkpoint):
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertEqual(model.journal()['phase'], 'checkpoint-intent')
            self.assertFalse(any(row['allow_connections'] for row in model.dbs.values()))
            self.assertFalse(model.running)
            self.assertFalse(backup.completion_path(E).exists())
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertEqual(model.trees[E], model.identity('1'))

    def test_phase_write_failure_after_native_open_preserves_observed_new_writes(self):
        atomic = backup.atomic_private
        def fail_opened(path, value):
            if path == restore_operation.journal_path(backup.STATE, E) and value['phase'] == 'opened':
                raise OSError('injected phase persistence failure')
            return atomic(path, value)
        with NativeModel(self) as model:
            with patch.object(backup, 'atomic_private', fail_opened):
                with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                    model.restore()
            self.assertEqual(model.journal()['phase'], 'open-intent')
            self.assertTrue(model.dbs[E]['allow_connections'])
            model.trees[E] = model.identity('3')
            before = len(model.events)
            model.recover()
            self.assertEqual(model.trees[E], model.identity('3'))
            self.assertFalse(any(event[0] in ('files', 'drop', 'rename', 'stop') for event in model.events[before:]))

    def test_storage_replacement_after_stop_refuses_before_objects_or_target_fence(self):
        with NativeModel(self, fail='storage-cid') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertEqual(model.journal()['phase'], 'stop-intent')
            self.assertEqual(model.dbs[E], {'oid': '100', 'allow_connections': True})
            self.assertFalse(any(event[0] in ('files', 'rename') for event in model.events))

    def test_same_name_replacement_during_stop_is_never_fenced_or_renamed(self):
        with NativeModel(self, fail='target-replaced') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            self.assertEqual(model.dbs[E], {'oid': '999', 'allow_connections': True})
            self.assertFalse(any(event[0] in ('rename', 'files', 'drop') for event in model.events))
            self.assertFalse(any(event[0] == 'fence' and event[2][E]['oid'] == '999' for event in model.events))

    def test_same_name_replacement_during_recovery_stop_refuses_before_mutation(self):
        with NativeModel(self, fail='create') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            model.fail = 'target-replaced'
            before = len(model.events)
            with self.assertRaisesRegex(backup.BackupError, 'identities changed'):
                model.recover()
            self.assertEqual(model.dbs[E], {'oid': '999', 'allow_connections': True})
            self.assertFalse(any(event[0] in ('fence', 'rename', 'files', 'drop') for event in model.events[before:]))

    def test_shared_counts_resolve_with_empty_session_path_and_preserve_archive_setting(self):
        from test_backup import StorageMetadataTests
        StorageMetadataTests.storage(self, NAME)
        for archived_path in ('', 'archive_schema'):
            with self.subTest(archived_path=archived_path), NativeModel(self, scope=backup.STORAGE) as model:
                model.archived_search_path = archived_path
                model.restore()
                self.assertEqual(model.retained_search_path, '')
                self.assertEqual(model.database_settings['search_path'], archived_path)
                self.assertEqual(model.dbs[backup.STORAGE_DATABASE]['oid'], '20000')

    def test_creation_gap_preserves_valid_completed_historical_checkpoint(self):
        with NativeModel(self) as model:
            model.restore()
        previous_checkpoint = backup.completion_path(E).read_bytes()
        with NativeModel(self, fail='create') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                restore_cutover.restore(backup, E, NAME, NOW + datetime.timedelta(minutes=1))
            model.fail = None
            self.assertEqual(model.recover()['status'], 'rolled-back')
            self.assertEqual(backup.completion_path(E).read_bytes(), previous_checkpoint)
            self.assertEqual(model.dbs, {E: {'oid': '100', 'allow_connections': True}})

    def test_foreign_pending_checkpoint_refuses_before_recovery_mutation(self):
        with NativeModel(self) as model:
            model.restore()
        with NativeModel(self, fail='create') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                restore_cutover.restore(backup, E, NAME, NOW + datetime.timedelta(minutes=1))
            saved = json.loads(backup.completion_path(E).read_text())
            saved['status'] = 'readiness-pending'
            backup.atomic_private(backup.completion_path(E), saved)
            before = len(model.events)
            with self.assertRaisesRegex(backup.BackupError, 'Foreign pending'):
                model.recover()
            self.assertEqual(len(model.events), before)
            self.assertTrue(model.dbs[E]['allow_connections'])

    def test_malformed_historical_checkpoint_refuses_before_recovery_mutation(self):
        with NativeModel(self) as model:
            model.restore()
        with NativeModel(self, fail='create') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                restore_cutover.restore(backup, E, NAME, NOW + datetime.timedelta(minutes=1))
            saved = json.loads(backup.completion_path(E).read_text())
            saved['manifest_sha256'] = '0' * 64
            backup.atomic_private(backup.completion_path(E), saved)
            before = len(model.events)
            with self.assertRaisesRegex(backup.BackupError, 'archive identity differs'):
                model.recover()
            self.assertEqual(len(model.events), before)

    def test_status_after_message_free_interruption_prints_exact_command_without_effects(self):
        with NativeModel(self, fail='create') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            journal = model.journal()
            journal['record']['counts'] = {'private-fixture-marker': 'do-not-print'}
            restore_operation.publish(backup.STATE, journal, backup.atomic_private)
            before = {path: path.read_bytes() for path in backup.STATE.rglob('*') if path.is_file()}
            output = io.StringIO()
            with patch.object(backup, 'run', side_effect=AssertionError('status contacted Docker')), \
                    patch.object(backup, 'sql', side_effect=AssertionError('status contacted database')), \
                    patch.object(backup, 'private_dir', side_effect=AssertionError('status wrote directory')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(backup.main(['restore-status']), 0)
            self.assertIn('python3 lab/backup.py recover-restore ' + E + ' ' + NAME + ' ' + journal['stamp'], output.getvalue())
            self.assertNotIn('private-fixture-marker', output.getvalue())
            self.assertNotIn('do-not-print', output.getvalue())
            self.assertEqual({path: path.read_bytes() for path in backup.STATE.rglob('*') if path.is_file()}, before)

    def test_status_malformed_operation_refuses_without_printing_payload_or_effects(self):
        with NativeModel(self, fail='create') as model:
            with self.assertRaisesRegex(backup.BackupError, 'recover-restore'):
                model.restore()
            path = restore_operation.journal_path(backup.STATE, E)
            path.write_text('{"private-fixture-marker":"do-not-print"}')
            output = io.StringIO()
            with contextlib.redirect_stderr(output), patch.object(backup, 'run', side_effect=AssertionError('status contacted Docker')):
                self.assertEqual(backup.main(['restore-status']), 1)
            self.assertIn('identity is unverifiable', output.getvalue())
            self.assertNotIn('do-not-print', output.getvalue())

    def test_status_does_not_create_missing_state(self):
        absent = backup.STATE / 'absent'
        with patch.object(backup, 'STATE', absent), patch.object(backup, 'run', side_effect=AssertionError('status contacted Docker')):
            self.assertEqual(backup.restore_status(), [])
        self.assertFalse(absent.exists())

    def test_status_legacy_readiness_checkpoint_uses_completion_command(self):
        backup.STATE.chmod(0o700)
        with NativeModel(self) as model:
            stamp = NOW.strftime('%Y%m%dt%H%M%Sz')
            record = {'restored_at': stamp, 'backup': NAME, 'previous_database': E + '_pre_' + stamp,
                      'previous_files': '.pre-restore-' + E + '-' + stamp, 'counts': COUNTS}
            backup.checkpoint_restore(E, NAME, stamp, {'db': 'c' * 64}, record)
            self.assertEqual(backup.restore_status(), [{'scope': E, 'phase': 'readiness-pending',
                'command': 'python3 lab/backup.py complete-restore ' + E + ' ' + NAME + ' ' + stamp}])

    def test_status_unprivate_state_refuses(self):
        backup.STATE.chmod(0o755)
        with self.assertRaisesRegex(backup.BackupError, 'not private'):
            backup.restore_status()

    def test_repeat_rollback_recovery_preserves_new_original_files_without_refencing(self):
        with NativeModel(self, fail='create') as model:
            with self.assertRaises(backup.BackupError):
                model.restore()
            model.fail = None
            model.recover()
            model.trees[E] = model.identity('9')
            before = len(model.events)
            model.recover()
            self.assertEqual(model.trees[E], model.identity('9'))
            self.assertFalse(any(event[0] in ('fence', 'rename', 'files', 'drop', 'open') for event in model.events[before:]))

    def test_lost_original_opened_phase_preserves_original_writes_and_remains_discoverable(self):
        original_publish = restore_operation.publish
        def interrupted_publish(state, value, atomic):
            if value['phase'] == 'original-opened':
                raise backup.BackupError('injected original-opened phase loss')
            return original_publish(state, value, atomic)
        with NativeModel(self, fail='create') as model:
            with self.assertRaises(backup.BackupError):
                model.restore()
            model.fail = None
            with patch.object(restore_operation, 'publish', side_effect=interrupted_publish), self.assertRaises(backup.BackupError):
                model.recover()
            self.assertEqual(model.journal()['phase'], 'rollback-open-intent')
            self.assertTrue(model.dbs[E]['allow_connections'])
            self.assertEqual(backup.restore_status()[0]['phase'], 'rollback-open-intent')
            model.trees[E] = model.identity('9')
            before = len(model.events)
            model.recover()
            self.assertEqual(model.trees[E], model.identity('9'))
            self.assertFalse(any(event[0] in ('fence', 'rename', 'files', 'drop', 'open') for event in model.events[before:]))

    def test_status_dangling_state_and_completion_symlinks_refuse_without_effects(self):
        backup.STATE.chmod(0o700)
        dangling = backup.STATE / 'dangling-state'
        dangling.symlink_to(backup.STATE / 'nonexistent-target')
        with patch.object(backup, 'STATE', dangling), self.assertRaisesRegex(backup.BackupError, 'not private'):
            backup.restore_status()
        dangling.unlink()
        folder = backup.STATE / 'restore-completions'
        folder.symlink_to(backup.STATE / 'nonexistent-target')
        with self.assertRaisesRegex(backup.BackupError, 'completion folder'):
            backup.restore_status()
        self.assertTrue(folder.is_symlink())
        self.assertFalse((backup.STATE / 'nonexistent-target').exists())

    def test_original_replacement_during_reopen_intent_is_never_opened(self):
        original_publish = restore_operation.publish
        with NativeModel(self, fail='create') as model:
            with self.assertRaises(backup.BackupError):
                model.restore()
            model.fail = None
            def replace_after_intent(state, value, atomic):
                result = original_publish(state, value, atomic)
                if value['phase'] == 'rollback-open-intent':
                    model.dbs[E] = {'oid': '999', 'allow_connections': False}
                return result
            with patch.object(restore_operation, 'publish', side_effect=replace_after_intent), self.assertRaisesRegex(backup.BackupError, 'identity'):
                model.recover()
            self.assertEqual(model.dbs[E], {'oid': '999', 'allow_connections': False})
