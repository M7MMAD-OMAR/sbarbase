"""P3 part 3: the cluster rebuilt from one backup run onto a new volume, through doubles only."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cluster_inventory as inventory
import cluster_rebuild as rebuild

E = 'e_' + 'a' * 24
IMAGE = 'sha256:' + '1' * 64
STAMP = '20261016T030000Z'


def role(name, **changes):
    kind, environment = inventory.classify(name)
    value = {**dict(inventory.RULES[kind]), 'name': name, 'kind': kind, 'environment': environment,
             'rolcanlogin': kind != 'supabase_realtime_admin', 'rolconnlimit': 4, 'member_of': [], 'settings': []}
    value.update(changes)
    return value


def recorded():
    roles = [role(f'{E}_auth', settings=['search_path=auth']),
             role(f'{E}_rest'), role(f'{E}_storage', member_of=[{'role': 'anon', 'inherit': True, 'set': True, 'admin': False}]),
             role(f'{E}_studio', rolcanlogin=False), role(f'{E}_developer'),
             role(f'{E}_realtime', member_of=[{'role': 'anon', 'inherit': False, 'set': True, 'admin': False}]),
             role('storage_control'), role('management_auth'), role('supabase_realtime_admin')]
    return {'version': 1, 'image': IMAGE, 'roles': sorted(roles, key=lambda item: item['name']),
            'parameters': [{'parameter': 'log_min_messages', 'grantee': f'{E}_realtime', 'privilege': 'SET'}],
            'refusals': []}


KNOWN = {f'{E}_auth': 'a' * 64, f'{E}_rest': 'b' * 64, f'{E}_storage': 'c' * 64, f'{E}_realtime': 'd' * 64,
         f'{E}_developer': 'e' * 64, 'storage_control': 'f' * 64, 'management_auth': '9' * 64}


class RoleSqlTests(unittest.TestCase):
    def test_every_role_is_created_with_its_attributes_password_settings_grants_and_parameters(self):
        statements, closed = rebuild.role_sql(recorded(), KNOWN)
        text = '\n'.join(statements)
        self.assertEqual(closed, [])
        self.assertIn(f'ALTER ROLE "{E}_realtime" WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB REPLICATION NOBYPASSRLS LOGIN '
                      f"CONNECTION LIMIT 4 PASSWORD '{'d' * 64}';", text)
        self.assertIn('ALTER ROLE "supabase_realtime_admin" WITH NOSUPERUSER NOINHERIT', text)
        self.assertEqual(set(rebuild.KEYWORDS), set(inventory.ATTRIBUTES))
        self.assertNotIn('NOSUPER ', text)
        self.assertIn('NOLOGIN CONNECTION LIMIT 4;', [line for line in statements if 'supabase_realtime_admin" WITH' in line][0])
        self.assertIn(f'GRANT "anon" TO "{E}_realtime" WITH INHERIT FALSE, SET TRUE, ADMIN FALSE;', text)
        self.assertIn(f'GRANT SET ON PARAMETER "log_min_messages" TO "{E}_realtime";', text)
        self.assertIn(f'ALTER ROLE "{E}_auth" SET "search_path" TO auth;', text)
        # Grants come after every role exists, parameters last.
        first_grant = next(i for i, line in enumerate(statements) if line.startswith('GRANT "'))
        self.assertGreater(first_grant, max(i for i, line in enumerate(statements) if line.startswith(('DO ', 'ALTER ROLE'))))
        self.assertTrue(all(line.startswith('GRANT') for line in statements[first_grant:]))
        self.assertTrue(statements[-1].startswith('GRANT SET ON PARAMETER'))

    def test_each_grant_option_and_parameter_privilege_is_written_as_recorded(self):
        record = recorded()
        auth = next(item for item in record['roles'] if item['name'] == f'{E}_auth')
        auth['member_of'] = [{'role': 'authenticator', 'inherit': False, 'set': False, 'admin': True}]
        record['parameters'] = [*record['parameters'],
                                {'parameter': 'work_mem', 'grantee': f'{E}_auth', 'privilege': 'ALTER SYSTEM'}]
        text = '\n'.join(rebuild.role_sql(record, KNOWN)[0])
        self.assertIn(f'GRANT "authenticator" TO "{E}_auth" WITH INHERIT FALSE, SET FALSE, ADMIN TRUE;', text)
        self.assertIn(f'GRANT ALTER SYSTEM ON PARAMETER "work_mem" TO "{E}_auth";', text)

    def test_a_login_without_a_kept_password_is_closed_or_refused_by_kind(self):
        known = {key: value for key, value in KNOWN.items() if key != f'{E}_developer'}
        statements, closed = rebuild.role_sql(recorded(), known)
        self.assertEqual(closed, [f'{E}_developer'])
        self.assertIn('NOLOGIN', [line for line in statements if f'"{E}_developer" WITH' in line][0])
        for missing in (f'{E}_auth', f'{E}_rest', f'{E}_storage', f'{E}_realtime', 'storage_control', 'management_auth'):
            with self.subTest(missing=missing), self.assertRaisesRegex(rebuild.RebuildError, 'No password'):
                rebuild.role_sql(recorded(), {key: value for key, value in KNOWN.items() if key != missing})

    def test_settings_are_written_back_with_their_meaning(self):
        self.assertEqual(rebuild.setting_sql('search_path=public, extensions'), '"search_path" TO public, extensions')
        self.assertEqual(rebuild.setting_sql('search_path="$user", public'), '"search_path" TO "$user", public')
        self.assertEqual(rebuild.setting_sql('statement_timeout=8s'), '"statement_timeout" TO \'8s\'')
        # Every list setting a role can carry is written back as its elements, not as one string.
        for key in ('temp_tablespaces', 'session_preload_libraries', 'local_preload_libraries'):
            self.assertEqual(rebuild.setting_sql(f'{key}=a, "b c"'), f'"{key}" TO a, "b c"')
        self.assertEqual(rebuild.setting_sql('application_name=a, b'), '"application_name" TO \'a, b\'')
        for bad in ('noequals', 'Bad=1', 'search_path=public; DROP ROLE x'):
            with self.subTest(bad=bad), self.assertRaises(rebuild.RebuildError):
                rebuild.setting_sql(bad)

    def test_the_comparison_leaves_out_login_state_only_where_it_opens_and_closes(self):
        base = recorded()
        moved = json.loads(json.dumps(base))
        for item in moved['roles']:
            if item['kind'] in ('studio', 'developer', 'realtime'):
                item['rolcanlogin'] = not item['rolcanlogin']
        self.assertEqual(rebuild.comparable(moved, []), rebuild.comparable(base, []))
        auth = json.loads(json.dumps(base))
        next(item for item in auth['roles'] if item['kind'] == 'auth')['rolcanlogin'] = False
        self.assertNotEqual(rebuild.comparable(auth, []), rebuild.comparable(base, []))
        self.assertEqual(rebuild.comparable(auth, [f'{E}_auth']), rebuild.comparable(base, [f'{E}_auth']))


class PasswordTests(unittest.TestCase):
    def test_passwords_come_from_the_runtime_values_and_the_developer_file(self):
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory)
            (private / 'runtime.json').write_text(json.dumps({
                'admin': 'x', 'storage_control': 's', 'management': {'auth': 'm'},
                'environments': {E: {'auth': 'a', 'rest': 'r', 'storage': 'st', 'realtime': 'rt', 'jwt': 'j'}}}))
            (private / 'database').mkdir()
            (private / 'database' / f'{E}.json').write_text(json.dumps({'password': 'dev'}))
            with patch.object(rebuild.runtime, 'PRIVATE', private):
                found = rebuild.passwords()
        self.assertEqual(found, {'storage_control': 's', 'management_auth': 'm', f'{E}_auth': 'a', f'{E}_rest': 'r',
                                 f'{E}_storage': 'st', f'{E}_realtime': 'rt', f'{E}_developer': 'dev'})

    def test_unreadable_values_refuse(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rebuild.runtime, 'PRIVATE', Path(directory)):
            with self.assertRaisesRegex(rebuild.RebuildError, 'service account'):
                rebuild.passwords()


class RunTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.backups = Path(directory.name)
        install = self.backups / 'installation' / STAMP
        install.mkdir(parents=True)
        self.manifest = {'environments': [E], 'cluster': recorded()}
        (install / 'installation.json').write_text(json.dumps(self.manifest))
        for item in (patch.object(rebuild.backup, 'BACKUPS', self.backups),
                     patch.object(rebuild.backup_offsite, 'verify_installation', lambda path: {}),
                     patch.object(rebuild.backup, 'verify_scope', lambda scope, path: {'counts': {'scope': scope}})):
            item.start()
            self.addCleanup(item.stop)

    def write(self):
        (self.backups / 'installation' / STAMP / 'installation.json').write_text(json.dumps(self.manifest))

    def test_a_run_is_restored_management_first_then_storage_then_each_environment(self):
        scopes, cluster = rebuild.load_run(STAMP)
        self.assertEqual(list(scopes), ['management', 'storage', E])
        self.assertEqual([value[0] for value in scopes.values()], ['management', 'storage_metadata', E])
        self.assertEqual(cluster, recorded())

    def test_every_environment_the_run_lists_is_verified_and_restored_in_order(self):
        other = 'e_' + 'b' * 24
        self.manifest['environments'] = [other, E]
        self.write()
        verified = []
        with patch.object(rebuild.backup, 'verify_scope', lambda scope, path: verified.append(scope) or {'counts': {}}):
            scopes, _ = rebuild.load_run(STAMP)
        self.assertEqual(list(scopes), ['management', 'storage', E, other])
        self.assertEqual(verified, ['management', 'storage', E, other])

    def test_a_run_without_a_usable_inventory_or_backups_refuses(self):
        for change, reason in ((lambda m: m.pop('cluster'), 'cannot be rebuilt'),
                               (lambda m: m['cluster']['refusals'].append('x'), 'cannot be rebuilt'),
                               (lambda m: m.update(environments=['bad']), 'no environments'),
                               (lambda m: m.update(environments=[]), 'did not back up')):
            with self.subTest(reason=reason):
                self.setUp()
                change(self.manifest)
                self.write()
                with self.assertRaisesRegex(rebuild.RebuildError, reason):
                    rebuild.load_run(STAMP)
        self.setUp()
        with patch.object(rebuild.backup, 'verify_scope', side_effect=rebuild.backup.BackupError('digest')):
            with self.assertRaisesRegex(rebuild.RebuildError, 'no complete backup'):
                rebuild.load_run(STAMP)
        with self.assertRaisesRegex(rebuild.RebuildError, 'Not a backup run'):
            rebuild.load_run('yesterday')


class FakeBuild:
    def __init__(self, container):
        self.container = container
        self.statements = []
        self.counts = None

    def execute(self, query, database='postgres'):
        self.statements.append((database, query))
        if 'json_agg(rolname' in query:
            return json.dumps(sorted(['supabase_admin', f'{E}_auth']))
        return ''


class RestoreTests(unittest.TestCase):
    def plan(self, roles=('supabase_admin', f'{E}_auth')):
        plan = SimpleNamespace(create='CREATE DATABASE "e_x_rebuild";', metadata=('GRANT CONNECT ON DATABASE e_x_rebuild TO x;',),
                               roles=tuple(sorted(roles)))

        @contextlib.contextmanager
        def planned(build, archive, database, stage):
            self.stages.append(stage)
            yield plan, io.BytesIO(b'-- replay')
        return planned

    def setUp(self):
        self.stages = []

    def restore(self, counts, roles=('supabase_admin', f'{E}_auth'), replay=0):
        build = self.build = FakeBuild('sbarbase-rebuild-x')
        build.psql = lambda database='postgres': ['psql', database]
        with patch.object(rebuild, 'planned', self.plan(roles)), \
                patch.object(rebuild.subprocess, 'run', return_value=SimpleNamespace(returncode=replay)), \
                patch.object(rebuild.backup, 'restore_counts', lambda scope, execute: counts):
            rebuild.restore_database(build, E, E, Path('/nowhere'), {'counts': {'auth.users': 1}})
        return build

    def test_a_database_is_restored_under_a_stage_checked_and_renamed(self):
        build = self.restore({'auth.users': 1})
        self.assertEqual(self.stages, [E + '_rebuild'])
        queries = [query for _, query in build.statements]
        self.assertEqual(queries[-1], f'ALTER DATABASE "{E}_rebuild" RENAME TO "{E}";')
        self.assertIn('CREATE DATABASE "e_x_rebuild";', queries)
        self.assertIn((E + '_rebuild', 'GRANT CONNECT ON DATABASE e_x_rebuild TO x;'), build.statements)

    def test_wrong_counts_a_missing_role_or_a_failed_replay_refuse_before_the_rename(self):
        with self.assertRaisesRegex(rebuild.RebuildError, 'rows its backup recorded'):
            self.restore({'auth.users': 2})
        with self.assertRaisesRegex(rebuild.RebuildError, 'missing on the rebuilt cluster'):
            self.restore({'auth.users': 1}, roles=('supabase_admin', f'{E}_auth', f'{E}_rest'))
        with self.assertRaisesRegex(rebuild.RebuildError, 'did not replay'):
            self.restore({'auth.users': 1}, replay=3)
        # None of them sent the rename.
        for counts, roles, replay in (({'auth.users': 2}, None, 0), ({'auth.users': 1}, (f'{E}_rest',), 0),
                                      ({'auth.users': 1}, None, 3)):
            with self.subTest(counts=counts, roles=roles, replay=replay), self.assertRaises(rebuild.RebuildError):
                self.restore(counts, roles=('supabase_admin', f'{E}_auth', *(roles or ())), replay=replay)
            self.assertFalse([query for _, query in self.build.statements if 'RENAME' in query])


class PlanningTests(unittest.TestCase):
    def test_the_archive_is_planned_in_the_build_container_as_a_restore_plans_it(self):
        seen = {}

        def run(argv, *, stdin=None, stdout=None, check=True, text=True, timeout=3600):
            seen['argv'] = argv
            stdout.write(b'-- planned\n')
            return SimpleNamespace(returncode=0, stderr=b'')

        def plan_archive(source, output, original, stage):
            seen['plan'] = (source.read(), original, stage)
            output.write('-- replay\n')
            return 'plan'
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)
            (archive / 'database.dump').write_bytes(b'dump')
            with patch.object(rebuild.backup, 'run', run), patch.object(rebuild, 'plan_archive', plan_archive):
                with rebuild.planned(rebuild.Build('sbarbase-rebuild-x'), archive, E, E + '_rebuild') as (plan, replay):
                    self.assertEqual((plan, replay.read()), ('plan', b'-- replay\n'))
        self.assertEqual(seen['argv'], ['docker', 'exec', '-i', 'sbarbase-rebuild-x', 'pg_restore', '--create',
                                        '--exit-on-error', '--file=-'])
        self.assertEqual(seen['plan'], ('-- planned\n', E, E + '_rebuild'))

    def test_diagnostics_from_planning_refuse(self):
        def run(argv, **kwargs):
            return SimpleNamespace(returncode=0, stderr=b'warning')
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)
            (archive / 'database.dump').write_bytes(b'dump')
            with patch.object(rebuild.backup, 'run', run), self.assertRaisesRegex(rebuild.RebuildError, 'could not be planned'):
                with rebuild.planned(rebuild.Build('x'), archive, E, E + '_rebuild'):
                    pass


class RebuildTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.calls = []
        self.existing = set()
        self.inventory_after = recorded()
        self.fail_at = None
        self.failure = rebuild.RebuildError
        self.restored = []
        patches = [
            patch.object(rebuild, 'RECORD', self.root / 'cluster-rebuild.json'),
            patch.object(rebuild, 'held_locks', lambda: contextlib.nullcontext()),
            patch.object(rebuild, 'load_run', lambda stamp: ({'management': ('management', None, {}),
                                                               'storage': ('storage_metadata', None, {}),
                                                               E: (E, None, {})}, recorded())),
            patch.object(rebuild, 'chosen_image', lambda commit=None: ('ref', IMAGE)),
            patch.object(rebuild, 'passwords', lambda: dict(KNOWN)),
            patch.object(rebuild, 'docker', self.docker),
            patch.object(rebuild, 'restore_database', self.restore),
            patch.object(rebuild.Build, 'ready', lambda self: True),
            patch.object(rebuild.Build, 'execute', lambda build, query, database='postgres': self.execute(build, query, database)),
            patch.object(rebuild.resource_policy, 'io_flags', return_value=[]),
            patch.object(rebuild, 'available_mib', lambda: self.memory),
        ]
        self.memory = 8000
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def docker(self, *args, data=None, check=True):
        self.calls.append(args)
        if args[1] == 'inspect':
            return SimpleNamespace(returncode=0 if args[2] in self.existing else 1, stdout='', stderr='')
        if self.fail_at and args[0] == self.fail_at:
            raise self.failure('docker ' + args[0] + ' failed')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def execute(self, build, query, database='postgres'):
        if query == inventory.QUERY:
            return json.dumps({'roles': [{key: value for key, value in item.items() if key not in ('kind', 'environment')}
                                         for item in self.inventory_after['roles']],
                               'parameters': self.inventory_after['parameters']})
        return ''

    def restore(self, build, scope, database, archive, manifest):
        self.restored.append(scope)

    def test_the_run_is_rebuilt_onto_a_new_runtime_owned_volume_and_the_build_container_goes(self):
        record = rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertEqual((record['phase'], record['volume']), ('completed', 'sbarbase-durable-pgdata-0123456789ab'))
        self.assertEqual(self.restored, ['management', 'storage', E])
        volume_create = next(call for call in self.calls if call[:2] == ('volume', 'create'))
        self.assertIn('io.sbarbase.owner=durable-upstream', volume_create)
        run = next(call for call in self.calls if call[0] == 'run')
        self.assertEqual(run[run.index('--name') + 1], 'sbarbase-rebuild-0123456789ab')
        self.assertIn('io.sbarbase.owner=durable-rebuild', run)
        self.assertEqual(run[run.index('--network') + 1], 'none')
        self.assertEqual(run[run.index('--pull') + 1], 'never')
        self.assertIn('sbarbase-durable-pgdata-0123456789ab:/var/lib/postgresql/data', run)
        self.assertIn(IMAGE, run)
        self.assertEqual(self.calls[-1], ('rm', '-f', 'sbarbase-rebuild-0123456789ab'))
        self.assertNotIn('volume', [call[0] for call in self.calls if call[:2] == ('volume', 'rm')])
        self.assertEqual(json.loads(rebuild.RECORD.read_text())['phase'], 'completed')

    def test_the_attempt_is_recorded_before_anything_is_created(self):
        seen = []
        docker = self.docker

        def recording(*args, **kwargs):
            if args[:2] == ('volume', 'create'):
                seen.append(json.loads(rebuild.RECORD.read_text()))
            return docker(*args, **kwargs)
        with patch.object(rebuild, 'docker', recording):
            rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertEqual([(item['phase'], item['volume']) for item in seen], [('started', 'sbarbase-durable-pgdata-0123456789ab')])

    def test_a_failure_removes_the_build_container_keeps_the_volume_and_records_it(self):
        self.fail_at = 'stop'
        with self.assertRaises(rebuild.RebuildError):
            rebuild.rebuild(STAMP, token='0123456789ab')
        saved = json.loads(rebuild.RECORD.read_text())
        self.assertEqual(saved['phase'], 'failed')
        self.assertEqual(saved['restored'], ['management', 'storage', E])
        self.assertEqual(self.calls[-1], ('rm', '-f', 'sbarbase-rebuild-0123456789ab'))
        self.assertFalse([call for call in self.calls if call[:2] == ('volume', 'rm')])

    def test_an_interrupt_or_a_stop_request_is_recorded_as_a_failure_and_the_container_goes(self):
        self.fail_at = 'stop'
        self.failure = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            rebuild.rebuild(STAMP, token='0123456789ab')
        saved = json.loads(rebuild.RECORD.read_text())
        self.assertEqual((saved['phase'], saved['failure']), ('failed', 'KeyboardInterrupt'))
        self.assertEqual(self.calls[-1], ('rm', '-f', 'sbarbase-rebuild-0123456789ab'))
        handlers = {}
        with patch.object(rebuild.signal, 'signal', lambda number, handler: handlers.update({number: handler})), \
                patch.object(rebuild, 'rebuild', side_effect=rebuild.RebuildError('stopped')), \
                contextlib.redirect_stderr(io.StringIO()):
            rebuild.main([STAMP])
        error = io.StringIO()
        with patch.object(rebuild.signal, 'signal'), patch.object(rebuild, 'rebuild', side_effect=KeyboardInterrupt), \
                contextlib.redirect_stderr(error):
            self.assertEqual(rebuild.main([STAMP]), 130)
        self.assertEqual(error.getvalue(), f'failed: KeyboardInterrupt; see {rebuild.RECORD}\n')
        with self.assertRaises(SystemExit) as stopped:
            handlers[rebuild.signal.SIGTERM](rebuild.signal.SIGTERM, None)
        self.assertEqual(stopped.exception.code, 128 + rebuild.signal.SIGTERM)

    def test_the_logins_created_closed_are_recorded_and_reported(self):
        output = io.StringIO()
        without = {name: value for name, value in KNOWN.items() if name != f'{E}_developer'}
        with patch.object(rebuild.signal, 'signal'), patch.object(rebuild, 'passwords', lambda: without), \
                contextlib.redirect_stdout(output):
            self.assertEqual(rebuild.main([STAMP]), 0)
        saved = json.loads(rebuild.RECORD.read_text())
        self.assertEqual(saved['closed_logins'], [f'{E}_developer'])
        self.assertIn(f'Created closed (turn them on again): {E}_developer', output.getvalue())

    def test_a_build_container_that_cannot_be_removed_is_recorded_and_reported(self):
        docker = self.docker

        def refusing(*args, **kwargs):
            result = docker(*args, **kwargs)
            return SimpleNamespace(returncode=1, stdout='', stderr='') if args[0] == 'rm' else result
        for failing in (False, True):
            with self.subTest(failing=failing), patch.object(rebuild, 'docker', refusing), \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                self.fail_at = 'stop' if failing else None
                if failing:
                    with self.assertRaises(rebuild.RebuildError):
                        rebuild.rebuild(STAMP, token='0123456789ab')
                else:
                    self.assertFalse(rebuild.rebuild(STAMP, token='0123456789ab')['container_removed'])
                saved = json.loads(rebuild.RECORD.read_text())
                self.assertEqual((saved['phase'], saved['container_removed']), ('failed' if failing else 'completed', False))
                self.assertIn('docker rm -f sbarbase-rebuild-0123456789ab', error.getvalue())
        self.fail_at = None
        self.assertTrue(rebuild.rebuild(STAMP, token='0123456789ab')['container_removed'])

    def test_a_cluster_that_does_not_hold_the_recorded_roles_fails(self):
        next(item for item in self.inventory_after['roles'] if item['kind'] == 'rest')['rolbypassrls'] = True
        with self.assertRaisesRegex(rebuild.RebuildError, 'does not hold the roles'):
            rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertEqual(json.loads(rebuild.RECORD.read_text())['phase'], 'failed')

    def test_existing_names_refuse_before_anything_is_created(self):
        self.existing = {'sbarbase-durable-pgdata-0123456789ab'}
        with self.assertRaisesRegex(rebuild.RebuildError, 'already exists'):
            rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertFalse([call for call in self.calls if call[1] != 'inspect'])
        self.assertFalse(rebuild.RECORD.exists())

    def test_too_little_memory_refuses_before_anything_is_created(self):
        self.memory = 1000
        with self.assertRaisesRegex(rebuild.RebuildError, 'memory'):
            rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertEqual(self.calls, [])

    def test_the_locks_are_held_from_the_first_step_to_the_last_save(self):
        held = []
        seen = []

        @contextlib.contextmanager
        def locks():
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        docker, save = self.docker, rebuild.save
        with patch.object(rebuild, 'held_locks', locks), \
                patch.object(rebuild, 'docker', lambda *args, **kwargs: seen.append(bool(held)) or docker(*args, **kwargs)), \
                patch.object(rebuild, 'save', lambda record: seen.append(bool(held)) or save(record)):
            rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertTrue(seen)
        self.assertTrue(all(seen))
        self.assertEqual(held, [])

    def test_the_runtime_must_be_stopped(self):
        refused = contextlib.ExitStack()
        with patch.object(rebuild, 'held_locks', side_effect=rebuild.RebuildError('Sbarbase is running; stop it first')):
            with self.assertRaisesRegex(rebuild.RebuildError, 'stop it first'):
                rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertEqual(self.calls, [])
        refused.close()


class LockTests(unittest.TestCase):
    def test_each_lock_held_elsewhere_refuses(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(rebuild.upgrade, 'SUPERVISOR_LOCK', root / 'supervisor.lock'), \
                    patch.object(rebuild.runtime, 'STATE', root), patch.object(rebuild.upgrade, 'BACKUP_LOCK', root / 'backup.lock'):
                for name, reason in (('supervisor.lock', 'Sbarbase is running'), ('worker.lock', 'worker is running'),
                                     ('backup.lock', 'backup or restore')):
                    with self.subTest(name=name), (root / name).open('a') as other:
                        fcntl.flock(other, fcntl.LOCK_EX)
                        with self.assertRaisesRegex(rebuild.RebuildError, reason):
                            with rebuild.held_locks():
                                pass
                with rebuild.held_locks():
                    pass


class EndToEndTests(unittest.TestCase):
    """rebuild() through its real steps; only Docker, psql and pg_restore are doubles."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.docker_calls, self.psql = [], []
        self.after = recorded()
        self.counts = {}
        archive = self.root / 'archive'
        archive.mkdir()
        (archive / 'database.dump').write_bytes(b'dump')
        scopes = {'management': ('management', archive, {'counts': 'm'}), 'storage': ('storage_metadata', archive, {'counts': 's'}),
                  E: (E, archive, {'counts': 'e'})}
        patches = [
            patch.object(rebuild, 'RECORD', self.root / 'cluster-rebuild.json'),
            patch.object(rebuild, 'held_locks', lambda: contextlib.nullcontext()),
            patch.object(rebuild, 'load_run', lambda stamp: (scopes, recorded())),
            patch.object(rebuild, 'chosen_image', lambda commit=None: ('ref', IMAGE)),
            patch.object(rebuild, 'passwords', lambda: dict(KNOWN)),
            patch.object(rebuild, 'available_mib', lambda: 8000),
            patch.object(rebuild, 'docker', self.docker),
            patch.object(rebuild.subprocess, 'run', self.fake_run),
            patch.object(rebuild.backup, 'run', self.plan_run),
            patch.object(rebuild, 'plan_archive', self.plan_archive),
            patch.object(rebuild.backup, 'restore_counts', self.restore_counts),
            patch.object(rebuild.backup, 'expected_counts', lambda scope, manifest: manifest['counts']),
            patch.object(rebuild.resource_policy, 'io_flags', return_value=[]),
            patch.object(rebuild.time, 'sleep', lambda seconds: None),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def docker(self, *args, data=None, check=True):
        self.docker_calls.append(args)
        return SimpleNamespace(returncode=1 if args[1] == 'inspect' else 0, stdout='', stderr='')

    def fake_run(self, argv, input=None, stdin=None, capture_output=False, text=False, timeout=None):
        database = argv[argv.index('-d') + 1]
        query = input if input is not None else stdin.read().decode()
        self.psql.append((argv, database, query))
        if query == inventory.QUERY:
            out = json.dumps({'roles': [{k: v for k, v in item.items() if k not in ('kind', 'environment')}
                                        for item in self.after['roles']], 'parameters': self.after['parameters']})
        elif 'to_regrole' in query:
            out = 't'
        elif 'json_agg(rolname' in query:
            out = json.dumps(['supabase_admin'])
        else:
            out = ''
        failing = getattr(self, 'psql_fails', 0) and 'to_regrole' not in query
        return SimpleNamespace(returncode=1 if failing else 0, stdout=out, stderr='')

    def plan_run(self, argv, *, stdin=None, stdout=None, check=True, text=True, timeout=3600):
        stdout.write(b'-- planned')
        return SimpleNamespace(returncode=0, stderr=b'')

    def plan_archive(self, source, output, original, stage):
        output.write('-- replay ' + stage + '\n')
        return SimpleNamespace(create=f'CREATE DATABASE "{stage}";', metadata=(f'COMMENT ON DATABASE "{stage}" IS \'x\';',),
                               roles=('supabase_admin',))

    def restore_counts(self, scope, execute):
        execute('SELECT counts;')
        return {'management': 'm', 'storage': 's'}.get(scope, 'e')

    def test_the_whole_rebuild_runs_its_steps_in_order_and_in_the_right_databases(self):
        record = rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertEqual(record['phase'], 'completed')
        run = next(call for call in self.docker_calls if call[0] == 'run')
        self.assertEqual(run[run.index('--env-file') + 1], str(rebuild.runtime.PRIVATE / (rebuild.runtime.DB + '.env')))
        argv = self.psql[0][0]
        self.assertEqual(argv[argv.index('-U') + 1], 'supabase_admin')
        self.assertIn('ON_ERROR_STOP=1', argv)
        steps = [(database, query) for _, database, query in self.psql]
        roles_at = next(i for i, (db, q) in enumerate(steps) if db == 'postgres' and 'CREATE ROLE' in q)
        first_create = next(i for i, (db, q) in enumerate(steps) if q.startswith('CREATE DATABASE'))
        self.assertLess(roles_at, first_create, 'the roles exist before any database is restored')
        for database in ('management', 'storage_metadata', E):
            stage = database + '_rebuild'
            self.assertIn((stage, '-- replay ' + stage + '\n'), steps)
            self.assertIn((stage, 'SELECT counts;'), steps)
            self.assertIn((stage, f'COMMENT ON DATABASE "{stage}" IS \'x\';'), steps)
            self.assertIn(('postgres', f'ALTER DATABASE "{stage}" RENAME TO "{database}";'), steps)
        order = [q for db, q in steps if q.startswith('ALTER DATABASE')]
        self.assertEqual(order, [f'ALTER DATABASE "{d}_rebuild" RENAME TO "{d}";' for d in ('management', 'storage_metadata', E)])
        self.assertEqual(steps[-1], ('postgres', inventory.QUERY))
        self.assertIn(('exec', 'sbarbase-rebuild-0123456789ab', 'pg_isready', '-h', '127.0.0.1'), self.docker_calls)

    def test_a_failing_statement_fails_the_rebuild(self):
        self.psql_fails = 1
        with self.assertRaises(rebuild.RebuildError):
            rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertEqual(json.loads(rebuild.RECORD.read_text())['phase'], 'failed')

    def test_a_planning_error_is_recorded_with_its_sentence_and_refused_by_main(self):
        with patch.object(rebuild, 'plan_archive', side_effect=rebuild.PlanError('Unsupported extension')):
            with self.assertRaises(rebuild.PlanError):
                rebuild.rebuild(STAMP, token='0123456789ab')
            self.assertEqual(json.loads(rebuild.RECORD.read_text())['failure'], 'Unsupported extension')
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(rebuild.main([STAMP]), 1)
            self.assertIn('refused: Unsupported extension', error.getvalue())
        with patch.object(rebuild, 'plan_archive', side_effect=KeyError('private')), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(rebuild.main([STAMP]), 1)
        self.assertIn('failed: KeyError', error.getvalue())
        self.assertNotIn('private', error.getvalue())
        self.assertEqual(json.loads(rebuild.RECORD.read_text())['failure'], 'KeyError')

    def test_a_refusal_on_the_rebuilt_cluster_fails_even_when_the_roles_compare_equal(self):
        taken = rebuild.cluster_inventory.from_rows
        with patch.object(rebuild.cluster_inventory, 'from_rows',
                          lambda rows, image: {**taken(rows, image), 'refusals': ["unexpected row {'name': 'other'}"]}), \
                self.assertRaisesRegex(rebuild.RebuildError, 'does not hold the roles'):
            rebuild.rebuild(STAMP, token='0123456789ab')

    def test_refusals_or_other_parameters_on_the_rebuilt_cluster_fail(self):
        self.after['parameters'] = []
        with self.assertRaisesRegex(rebuild.RebuildError, 'does not hold the roles'):
            rebuild.rebuild(STAMP, token='0123456789ab')
        self.after = recorded()
        next(item for item in self.after['roles'] if item['kind'] == 'auth')['rolsuper'] = True
        with self.assertRaisesRegex(rebuild.RebuildError, 'does not hold the roles'):
            rebuild.rebuild(STAMP, token='111111111111')


class PreconditionTests(unittest.TestCase):
    def test_the_image_is_the_current_lock_or_the_lock_of_a_version_proven_on_the_daemon(self):
        seen = []
        with patch.object(rebuild.backup, 'image_pin', lambda name: {'id': 'current'} if name == 'distro-image.lock.json' else None), \
                patch.object(rebuild.backup, 'resolve_image', lambda pin: seen.append(pin) or ('ref', IMAGE)):
            self.assertEqual(rebuild.chosen_image(), ('ref', IMAGE))
            with patch.object(rebuild.upgrade, 'resolve', lambda ref: 'c' * 40), \
                    patch.object(rebuild.release_channel, 'lock_at',
                                 lambda commit, name: {'id': 'old'} if (commit, name) == ('c' * 40, rebuild.release_channel.DATABASE_LOCK)
                                 else None):
                rebuild.chosen_image('v1')
            self.assertEqual(seen, [{'id': 'current'}, {'id': 'old'}])
            with patch.object(rebuild.upgrade, 'resolve', lambda ref: 'c' * 40), \
                    patch.object(rebuild.release_channel, 'lock_at', lambda commit, name: None), \
                    self.assertRaisesRegex(rebuild.RebuildError, 'no PostgreSQL lock'):
                rebuild.chosen_image('v0')
        for error in (rebuild.upgrade.UpgradeError('Unknown version v9'), rebuild.release_channel.ReleaseError('unreadable')):
            with patch.object(rebuild.upgrade, 'resolve', side_effect=error), \
                    self.assertRaisesRegex(rebuild.RebuildError, 'cannot be read'):
                rebuild.chosen_image('v9')
        with patch.object(rebuild.backup, 'image_pin', return_value={}), \
                patch.object(rebuild.backup, 'resolve_image', side_effect=rebuild.backup.BackupError('missing')), \
                self.assertRaisesRegex(rebuild.RebuildError, 'pull it first'):
            rebuild.chosen_image()

    def test_an_installation_manifest_that_does_not_verify_refuses(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(rebuild.backup, 'BACKUPS', Path(directory)):
            # A readable, well formed manifest: only the failed verification refuses it.
            installation = Path(directory) / rebuild.backup_offsite.INSTALLATION / STAMP
            installation.mkdir(parents=True)
            (installation / 'installation.json').write_text(json.dumps({'cluster': recorded(), 'environments': [E]}))
            with patch.object(rebuild.backup_offsite, 'verify_installation', side_effect=rebuild.backup.BackupError('digest')), \
                    self.assertRaisesRegex(rebuild.RebuildError, r'no verified installation manifest \(digest\)'):
                rebuild.load_run(STAMP)

    def test_failures_of_a_statement_or_of_planning_are_detected(self):
        build = rebuild.Build('x')
        with patch.object(rebuild.subprocess, 'run', return_value=SimpleNamespace(returncode=3, stdout='', stderr='')):
            with self.assertRaisesRegex(rebuild.RebuildError, 'statement failed'):
                build.execute('SELECT 1;')
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)
            (archive / 'database.dump').write_bytes(b'dump')
            with patch.object(rebuild.backup, 'run', lambda argv, **kwargs: SimpleNamespace(returncode=1, stderr=b'')), \
                    self.assertRaisesRegex(rebuild.RebuildError, 'could not be planned'):
                with rebuild.planned(build, archive, E, E + '_rebuild'):
                    pass

    def test_only_the_logins_that_open_and_close_are_compared_without_login_state(self):
        self.assertEqual(rebuild.TOGGLED, {'studio', 'developer', 'realtime'})

    def test_a_studio_login_without_its_session_password_is_created_closed(self):
        roles = recorded()
        next(item for item in roles['roles'] if item['kind'] == 'studio')['rolcanlogin'] = True
        statements, closed = rebuild.role_sql(roles, KNOWN)
        self.assertEqual(closed, [f'{E}_studio'])
        self.assertIn('NOLOGIN', next(line for line in statements if f'"{E}_studio" WITH' in line))

    def test_an_existing_build_container_name_refuses(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(rebuild, 'RECORD', Path(directory) / 'r.json'), \
                patch.object(rebuild, 'held_locks', lambda: contextlib.nullcontext()), \
                patch.object(rebuild, 'load_run', lambda stamp: ({}, recorded())), \
                patch.object(rebuild, 'chosen_image', lambda commit=None: ('ref', IMAGE)), \
                patch.object(rebuild, 'passwords', lambda: dict(KNOWN)), patch.object(rebuild, 'available_mib', lambda: 8000), \
                patch.object(rebuild, 'docker', lambda *args, **kwargs: calls.append(args) or SimpleNamespace(
                    returncode=0 if args[:3] == ('container', 'inspect', 'sbarbase-rebuild-0123456789ab') else 1)):
            with self.assertRaisesRegex(rebuild.RebuildError, 'already exists'):
                rebuild.rebuild(STAMP, token='0123456789ab')
        self.assertTrue(all(call[1] == 'inspect' for call in calls))


if __name__ == '__main__':
    unittest.main()
