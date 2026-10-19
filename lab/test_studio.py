"""Studio on demand: the login it opens, the cleanup on failure, and the supervisor's scheduling."""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import studio
import durable_runtime

E = 'e_' + 'a' * 24


class LoginTests(unittest.TestCase):
    def test_the_studio_login_is_scoped_to_its_own_database(self):
        sql = studio.role_sql(E, 'f' * 64)
        role = f'{E}_studio'
        self.assertIn(f'CREATE ROLE {role} NOLOGIN NOINHERIT BYPASSRLS', sql)
        self.assertIn('NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT', sql)
        self.assertIn(f'GRANT CONNECT ON DATABASE {E} TO {role}', sql)
        # Read only on the schemas other services own; never their owner, never a group member.
        self.assertIn(f'GRANT SELECT ON ALL TABLES IN SCHEMA auth, storage TO {role}', sql)
        self.assertNotRegex(sql, r'GRANT (anon|authenticated|service_role|supabase_admin|authenticator|postgres)\b')
        self.assertNotRegex(sql, r'OWNER TO|ALTER SCHEMA|(?<!NO)CREATEDB|(?<!NO)CREATEROLE|(?<!NO)SUPERUSER|(?<!NO)REPLICATION')
        self.assertNotIn('ON DATABASE postgres', sql)
        # Opening the login is the last statement, after every grant has applied.
        self.assertTrue(sql.strip().endswith(f'ALTER ROLE {role} LOGIN;'))

    def test_studio_works_as_the_developer_login_without_a_cycle(self):
        sql = studio.role_sql(E, 'f' * 64)
        studio_role, developer = f'{E}_studio', f'{E}_developer'
        # The reverse grant goes before the new one, since PostgreSQL refuses a membership cycle.
        self.assertLess(sql.index(f'REVOKE {studio_role} FROM {developer}'), sql.index(f'GRANT {developer} TO {studio_role}'))
        self.assertIn(f'REASSIGN OWNED BY {studio_role} TO {developer}', sql)
        self.assertIn(f'ALTER ROLE {studio_role} IN DATABASE {E} SET role = {developer}', sql)
        # Only when both exist; the developer login's own setup no longer makes Studio its group.
        self.assertIn(f"rolname = '{developer}'", sql)
        grants = durable_runtime.Runtime.developer_grants_sql(None, E)
        self.assertIn(f'GRANT {developer} TO {studio_role}', grants)
        self.assertNotIn(f'GRANT {studio_role} TO {developer}', durable_runtime.Runtime.developer_sql(None, E, 'p' * 32))

    def test_the_access_rule_is_published_for_every_environment(self):
        text = durable_runtime.hba_content([E])
        lines = text.splitlines()
        self.assertIn(f'host {E} {E}_studio 0.0.0.0/0 scram-sha-256', lines)
        self.assertEqual(lines[-2:], ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject'])

    def test_the_internal_route_port_is_never_an_ephemeral_port(self):
        # The host's outbound connections take source ports from 32768 upwards on the same address.
        self.assertLess(studio.UPSTREAM_PORT, 32768)

    def test_the_rule_check_accepts_only_this_database(self):
        with patch.object(studio, 'runtime_sql', return_value=f'{E} {E}_studio'):
            studio.require_rule(E)
        with patch.object(studio, 'runtime_sql', return_value=''):
            with self.assertRaisesRegex(studio.StudioError, 'restart'):
                studio.require_rule(E)
        with patch.object(studio, 'runtime_sql', return_value=f'all {E}_studio'):
            with self.assertRaises(studio.StudioError):
                studio.require_rule(E)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / 'endpoints.json').write_text(json.dumps({E: {}}))
        (root / 'private').mkdir()
        (root / 'private' / 'runtime.json').write_text(json.dumps({'environments': {E: {'jwt': 'j' * 64}}}))
        self.statements = []
        self.removed = []
        patches = [patch.object(studio, 'STATE_FILE', root / 'studio.json'), patch.object(studio.runtime, 'STATE', root),
                   patch.object(studio.runtime, 'PRIVATE', root / 'private'),
                   patch.object(studio.source_fence, 'is_fenced', return_value=False),
                   patch.object(studio, 'require_rule'),
                   patch.object(studio, 'ensure_images', return_value={'meta': 'meta-reference', 'studio': 'studio-reference'}),
                   patch.object(studio, 'headroom'),
                   patch.object(studio, 'open_connections'), patch.object(studio, 'network_gateway', return_value='172.18.0.1'),
                   patch.object(studio, 'runtime_sql', side_effect=lambda query, database='postgres': self.statements.append(query) or ''),
                   patch.object(studio, 'remove', side_effect=self.removed.append),
                   patch.object(studio, 'open_network', return_value='10.0.0.2'), patch.object(studio, 'unexposed'),
                   patch.object(studio, 'remove_network', side_effect=lambda e: self.removed.append(studio.session_network(e))),
                   patch.object(studio, 'isolated'), patch.object(studio, 'meta_ready'),
                   patch.object(studio, 'copy_functions')]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def cleanup(self):
        studio_name, meta = studio.names(E)
        return [meta, studio_name, studio.session_network(E)]

    def test_a_failed_start_removes_both_containers_and_closes_the_login(self):
        with patch.object(studio, 'launch', side_effect=[{}, studio.StudioError('meta failed')]):
            with self.assertRaisesRegex(studio.StudioError, 'meta failed'):
                studio.up(E)
        self.assertEqual(self.removed[-3:], self.cleanup())
        self.assertTrue(any(f'ALTER ROLE {E}_studio NOLOGIN' in query for query in self.statements))
        self.assertFalse(studio.STATE_FILE.exists())

    def test_a_start_records_the_session_and_passes_no_superuser(self):
        launched = []
        waited = []
        with patch.object(studio, 'launch', side_effect=lambda name, tier, env, image, network, **options:
                          launched.append((env, network, options)) or {}), \
             patch.object(studio, 'wait_ready', side_effect=lambda url, **options: waited.append(url)), \
             patch.object(studio, 'route_ready', side_effect=lambda name, url: waited.append((name, url))):
            session = studio.up(E)
        # Studio, on its own address, then the console's route to Auth and Storage, asked from Studio.
        self.assertEqual(waited, ['http://10.0.0.2:3000/api/platform/profile',
                                  (studio.names(E)[0], f'http://172.18.0.1:{studio.UPSTREAM_PORT}/')])
        self.assertEqual(session['url'], 'http://10.0.0.2:3000')
        (ui, ui_network, ui_options), (meta, meta_network, meta_options) = launched
        self.assertEqual(meta['PG_META_DB_USER'], f'{E}_studio')
        self.assertEqual(ui['POSTGRES_USER_READ_WRITE'], f'{E}_studio')
        self.assertEqual(meta['CRYPTO_KEY'], ui['PG_META_CRYPTO_KEY'])
        self.assertEqual(ui['SUPABASE_URL'], f'http://172.18.0.1:{studio.UPSTREAM_PORT}/{E}')
        # Neither listens where another environment's code runs: Studio binds only its address on
        # the session network, and postgres-meta binds loopback inside Studio's namespace.
        self.assertEqual(ui_network, studio.session_network(E))
        self.assertEqual(ui_options, {'address': '10.0.0.2', 'attach': (studio.runtime.NETWORK,), 'health': studio.STUDIO_HEALTH})
        self.assertEqual(ui['HOSTNAME'], '10.0.0.2')
        self.assertEqual(meta_network, f'container:{studio.names(E)[0]}')
        self.assertEqual(meta_options, {})
        self.assertEqual(meta['PG_META_HOST'], '127.0.0.1')
        self.assertEqual(ui['STUDIO_PG_META_URL'], 'http://127.0.0.1:8080')
        # Saved SQL and the Edge Functions page read folders inside the container, never a host mount.
        self.assertEqual(ui['SNIPPETS_MANAGEMENT_FOLDER'], '/tmp/snippets')
        self.assertEqual(ui['EDGE_FUNCTIONS_MANAGEMENT_FOLDER'], '/tmp/functions')
        for env, _, _ in launched:
            self.assertFalse(any(value in ('postgres', 'supabase_admin') for value in env.values()))
        state = json.loads(studio.STATE_FILE.read_text())
        self.assertEqual(state['upstream'], {'host': '172.18.0.1', 'port': studio.UPSTREAM_PORT})
        # The session password is never written to disk.
        self.assertNotIn(meta['PG_META_DB_PASSWORD'], studio.STATE_FILE.read_text())

    def test_a_studio_outside_its_network_is_stopped(self):
        with patch.object(studio, 'launch', return_value={}), \
             patch.object(studio, 'isolated', side_effect=studio.StudioError('Studio is not confined to its own network')):
            with self.assertRaisesRegex(studio.StudioError, 'confined'):
                studio.up(E)
        self.assertEqual(self.removed[-3:], self.cleanup())
        self.assertTrue(any(f'ALTER ROLE {E}_studio NOLOGIN' in query for query in self.statements))

    def test_stopping_removes_the_session_network(self):
        studio.down(E)
        self.assertEqual(self.removed, self.cleanup())

    def test_a_route_that_never_opens_stops_studio_again(self):
        with patch.object(studio, 'launch', return_value={}), patch.object(studio, 'wait_ready'), \
             patch.object(studio, 'route_ready', side_effect=studio.StudioError('Studio cannot reach its route')):
            with self.assertRaisesRegex(studio.StudioError, 'route'):
                studio.up(E)
        self.assertEqual(self.removed[-3:], self.cleanup())
        self.assertNotIn(E, json.loads(studio.STATE_FILE.read_text())['sessions'])

    def test_an_unpublished_or_invalid_environment_is_refused_before_any_change(self):
        with self.assertRaises(studio.StudioError):
            studio.up('e_' + 'b' * 24)
        with self.assertRaises(studio.StudioError):
            studio.up('not-a-runtime')
        self.assertEqual(self.statements, [])

    def test_image_failure_preserves_retained_session_and_login(self):
        with patch.object(studio, 'ensure_images', side_effect=studio.StudioError('image proof refused')), \
             patch.object(studio, 'open_connections') as opened:
            with self.assertRaisesRegex(studio.StudioError, 'image proof refused'):
                studio.up(E)
        self.assertEqual(self.removed, [])
        self.assertEqual(self.statements, [])
        opened.assert_not_called()

    def test_up_uses_prepared_references_without_rereading_lock(self):
        images = []
        with patch.object(studio, 'pins', side_effect=AssertionError('lock must not be read twice')), \
             patch.object(studio, 'launch', side_effect=lambda name, tier, env, image, network, **options: images.append(image) or {}), \
             patch.object(studio, 'wait_ready'), patch.object(studio, 'route_ready'):
            studio.up(E)
        self.assertEqual(images, ['studio-reference', 'meta-reference'])


class ReadinessTests(unittest.TestCase):
    def test_postgres_meta_counts_as_ready_on_any_reply_and_studio_only_on_200(self):
        import urllib.error
        refusal = urllib.error.HTTPError('http://meta:8080/', 404, 'Not Found', {}, None)
        self.addCleanup(refusal.close)
        with patch.object(studio.urllib.request, 'urlopen', side_effect=[OSError('refused'), refusal]), patch.object(studio.time, 'sleep'):
            studio.wait_ready('http://meta:8080/', any_answer=True, what='postgres-meta')
        with patch.object(studio.urllib.request, 'urlopen', side_effect=refusal), patch.object(studio.time, 'sleep'), \
             patch.object(studio.time, 'monotonic', side_effect=[0, 0, 1000]):
            with self.assertRaisesRegex(studio.StudioError, 'Studio did not become ready'):
                studio.wait_ready('http://studio:3000/api/platform/profile')

    def test_studio_is_recorded_running_only_after_postgres_meta_answers(self):
        source = Path(studio.__file__).read_text()
        self.assertLess(source.index('meta_ready(meta)'), source.index("/api/platform/profile')"))


    def test_the_route_is_asked_from_inside_studio_and_a_blocked_one_names_the_firewall(self):
        calls = []
        ok = type('R', (), {'returncode': 0})()
        with patch.object(studio.lab, 'docker', side_effect=lambda *args, **options: calls.append(args) or ok):
            studio.route_ready('studio-container', 'http://172.18.0.1:25432/')
        self.assertEqual(calls[0][:4], ('exec', 'studio-container', 'node', '-e'))
        self.assertEqual(calls[0][-1], 'http://172.18.0.1:25432/')
        failed = type('R', (), {'returncode': 1})()
        with patch.object(studio.lab, 'docker', return_value=failed), patch.object(studio.time, 'sleep'), \
             patch.object(studio.time, 'monotonic', side_effect=[0, 0, 1000]):
            with self.assertRaisesRegex(studio.StudioError, 'firewall'):
                studio.route_ready('studio-container', 'http://172.18.0.1:25432/')


class FunctionsTests(unittest.TestCase):
    def test_each_function_is_copied_from_its_current_version_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'functions' / E
            for version, name in [('v1a-0000000a', 'hello'), ('v1b-0000000b', 'hello'), ('v1b-0000000b', 'mail'),
                                  ('v1b-0000000b', '_shared')]:
                (root / version / name).mkdir(parents=True)
                (root / version / name / 'index.ts').write_text('export default 1')
            (root / 'functions.json').write_text(json.dumps({'functions': {
                'hello': {'version': 'v1b-0000000b'}, 'mail': {'version': 'v1b-0000000b'},
                'gone': {'version': 'v1c-0000000c'}, '../up': {'version': 'v1b-0000000b'},
                'odd': {'version': '../v1b-0000000b'}}}))
            calls = []
            with patch.object(studio.runtime, 'STATE', Path(directory)), \
                 patch.object(studio.lab, 'docker', side_effect=lambda *args, **options: calls.append(args)):
                studio.copy_functions('studio-container', E)
        self.assertEqual(calls[0], ('exec', 'studio-container', 'mkdir', '-p', '/tmp/functions'))
        self.assertEqual([call[2] for call in calls[1:]], ['studio-container:/tmp/functions/hello', 'studio-container:/tmp/functions/mail'])
        self.assertTrue(calls[1][1].endswith(f'{E}/v1b-0000000b/hello/.'))

    def test_no_deployed_functions_leaves_an_empty_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            with patch.object(studio.runtime, 'STATE', Path(directory)), \
                 patch.object(studio.lab, 'docker', side_effect=lambda *args, **options: calls.append(args)):
                studio.copy_functions('studio-container', E)
        self.assertEqual(calls, [('exec', 'studio-container', 'mkdir', '-p', '/tmp/functions')])


class NetworkTests(unittest.TestCase):
    def test_the_session_network_is_internal_owned_and_made_with_its_subnet_named(self):
        calls = []
        inspected = {'IPAM': {'Config': [{'Subnet': '172.30.5.0/24', 'Gateway': '172.30.5.1'}]}}
        with patch.object(studio.lab, 'docker', side_effect=lambda *args, **options: calls.append(args)), \
             patch.object(studio.runtime, 'inspect', side_effect=[None, inspected]):
            address = studio.open_network(E)
        self.assertEqual(address, '172.30.5.2')
        name = studio.session_network(E)
        create = ('network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + studio.runtime.OWNER)
        self.assertEqual(calls, [(*create, name), ('network', 'rm', name),
                                 (*create, '--subnet', '172.30.5.0/24', '--gateway', '172.30.5.1', name)])

    def test_a_studio_on_any_other_network_or_address_is_refused(self):
        net = studio.session_network(E)
        good = {'NetworkSettings': {'Networks': {net: {'IPAddress': '172.30.5.2'}, studio.runtime.NETWORK: {'IPAddress': '172.16.1.9'}}}}
        studio.isolated(good, E, '172.30.5.2')
        with self.assertRaises(studio.StudioError):
            studio.isolated(good, E, '172.30.5.3')
        extra = {'NetworkSettings': {'Networks': {**good['NetworkSettings']['Networks'], 'bridge': {'IPAddress': '172.17.0.2'}}}}
        with self.assertRaises(studio.StudioError):
            studio.isolated(extra, E, '172.30.5.2')

    def test_isolated_returns_the_shared_address(self):
        net = studio.session_network(E)
        item = {'NetworkSettings': {'Networks': {net: {'IPAddress': '172.30.5.2'}, studio.runtime.NETWORK: {'IPAddress': '172.16.1.9'}}}}
        self.assertEqual(studio.isolated(item, E, '172.30.5.2'), '172.16.1.9')

    def test_a_studio_answering_on_the_shared_network_is_refused(self):
        class Open:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
        with patch.object(studio.socket, 'create_connection', side_effect=[ConnectionRefusedError(), Open()]):
            with self.assertRaisesRegex(studio.StudioError, 'port 8080'):
                studio.unexposed('172.16.1.9')
        with patch.object(studio.socket, 'create_connection', side_effect=[ConnectionRefusedError(), TimeoutError()]):
            studio.unexposed('172.16.1.9')

    def test_an_exposed_studio_is_stopped_before_it_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'endpoints.json').write_text(json.dumps({E: {}}))
            (root / 'private').mkdir()
            (root / 'private' / 'runtime.json').write_text(json.dumps({'environments': {E: {'jwt': 'j' * 64}}}))
            removed = []
            with patch.object(studio, 'STATE_FILE', root / 'studio.json'), patch.object(studio.runtime, 'STATE', root), \
                 patch.object(studio.runtime, 'PRIVATE', root / 'private'), \
                 patch.object(studio.source_fence, 'is_fenced', return_value=False), patch.object(studio, 'require_rule'), \
                 patch.object(studio, 'ensure_images', return_value={'meta': 'm', 'studio': 's'}), patch.object(studio, 'headroom'), \
                 patch.object(studio, 'open_connections'), patch.object(studio, 'network_gateway', return_value='172.18.0.1'), \
                 patch.object(studio, 'runtime_sql', return_value=''), patch.object(studio, 'remove', side_effect=removed.append), \
                 patch.object(studio, 'remove_network', side_effect=lambda e: removed.append('net')), \
                 patch.object(studio, 'open_network', return_value='10.0.0.2'), patch.object(studio, 'launch', return_value={}), \
                 patch.object(studio, 'isolated', return_value='172.16.1.9'), patch.object(studio, 'meta_ready'), \
                 patch.object(studio, 'wait_ready'), patch.object(studio, 'copy_functions'), \
                 patch.object(studio, 'unexposed', side_effect=studio.StudioError('Studio answers on the shared runtime network')):
                with self.assertRaisesRegex(studio.StudioError, 'shared runtime network'):
                    studio.up(E)
            self.assertEqual(removed[-1], 'net')
            self.assertFalse((root / 'studio.json').exists())

    def test_a_subnet_taken_in_between_is_tried_again(self):
        with patch.object(studio, 'open_network', side_effect=[RuntimeError('Docker operation network failed'), '10.0.0.2']), \
             patch.object(studio.time, 'sleep'):
            self.assertEqual(studio.open_network_retrying(E), '10.0.0.2')
        with patch.object(studio, 'open_network', side_effect=RuntimeError('Docker operation network failed')), \
             patch.object(studio.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                studio.open_network_retrying(E)

    def test_reset_finds_a_session_left_only_as_its_network(self):
        listings = {'ps': '', 'network': f'{studio.session_network(E)}\n{studio.runtime.NETWORK}\n'}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(studio, 'STATE_FILE', Path(directory) / 'studio.json'), \
             patch.object(studio.lab, 'docker', side_effect=lambda *args, **options:
                          type('R', (), {'stdout': listings[args[0]]})()), \
             patch.object(studio, 'down') as down:
            self.assertEqual(studio.reset(), [E])
        down.assert_called_once_with(E)


class ScheduleTests(unittest.TestCase):
    def test_the_supervisor_starts_stops_and_never_loops_on_a_failure(self):
        import dev
        supervisor = dev.Supervisor()
        spawned = []
        rows = [(E, 'running', 'stopped', None), ('e_' + 'b' * 24, 'running', 'failed', 'Not enough free memory'),
                ('e_' + 'c' * 24, 'stopped', 'running', None), ('e_' + 'd' * 24, 'running', 'running', None)]
        with patch.object(supervisor, 'studio_requests', return_value=rows), \
             patch.object(supervisor, 'spawn', side_effect=lambda command: spawned.append(command) or object()), \
             patch.object(dev, 'child_status', return_value=None):
            supervisor.schedule_studios()
            supervisor.schedule_studios()
        self.assertEqual(sorted(command[2:] for command in spawned), [['down', 'e_' + 'c' * 24], ['up', E]])

    def test_a_restart_brings_back_only_recently_used_sessions(self):
        import dev
        import sqlite3
        import time as clock
        now = int(clock.time() * 1000)
        recent, stale, stopped, new = ('e_' + c * 24 for c in 'abcd')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'control.sqlite'
            with sqlite3.connect(path) as database:
                database.execute('CREATE TABLE studio_sessions(runtime TEXT PRIMARY KEY, desired TEXT, state TEXT, failure TEXT, actor TEXT, updated_at INTEGER)')
                database.execute('CREATE TABLE studio_activity(runtime TEXT PRIMARY KEY, seen_at INTEGER)')
                old = now - 3 * dev.STUDIO_RESTORE_MS
                database.executemany('INSERT INTO studio_sessions VALUES (?,?,?,?,?,?)', [
                    (recent, 'running', 'running', None, 'alice', old), (stale, 'running', 'running', None, 'alice', old),
                    (stopped, 'stopped', 'stopped', None, 'alice', now), (new, 'running', 'starting', 'x', 'alice', now - 1000)])
                database.executemany('INSERT INTO studio_activity VALUES (?,?)', [(recent, now - 60_000), (stopped, now)])
            with patch.object(dev, 'STATE', Path(directory)), patch.object(dev.subprocess, 'run') as reset:
                dev.Supervisor().reset_studios()
            reset.assert_any_call(['/usr/bin/python3', 'lab/studio.py', 'reset'], cwd=dev.ROOT, timeout=300, check=False)
            with sqlite3.connect(path) as database:
                rows = dict((runtime, (desired, state, failure)) for runtime, desired, state, failure in
                            database.execute('SELECT runtime, desired, state, failure FROM studio_sessions'))
        # Used in the last eight hours (by the proxy, or opened from the console): asked for again, from stopped.
        self.assertEqual(rows[recent], ('running', 'stopped', None))
        self.assertEqual(rows[new], ('running', 'stopped', None))
        # Idle for longer, or stopped by the operator: stays stopped.
        self.assertEqual(rows[stale], ('stopped', 'stopped', None))
        self.assertEqual(rows[stopped], ('stopped', 'stopped', None))

    def test_a_catalog_without_the_activity_table_uses_the_console_request_time(self):
        # The first restart into this release runs before the server creates studio_activity.
        import dev
        import sqlite3
        import time as clock
        recent, old = E, 'e_' + 'f' * 24
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'control.sqlite'
            with sqlite3.connect(path) as database:
                database.execute('CREATE TABLE studio_sessions(runtime TEXT PRIMARY KEY, desired TEXT, state TEXT, failure TEXT, actor TEXT, updated_at INTEGER)')
                database.execute("INSERT INTO studio_sessions VALUES (?, 'running', 'running', NULL, 'alice', ?)", (recent, int(clock.time() * 1000) - 60_000))
                database.execute("INSERT INTO studio_sessions VALUES (?, 'running', 'running', NULL, 'alice', 0)", (old,))
            with patch.object(dev, 'STATE', Path(directory)), patch.object(dev.subprocess, 'run'):
                dev.Supervisor().reset_studios()
            with sqlite3.connect(path) as database:
                rows = dict((runtime, (desired, state)) for runtime, desired, state in database.execute('SELECT runtime, desired, state FROM studio_sessions'))
        self.assertEqual(rows, {recent: ('running', 'stopped'), old: ('stopped', 'stopped')})


if __name__ == '__main__':
    unittest.main()
