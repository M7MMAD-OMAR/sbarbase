"""Scheduled jobs: the SQL that makes the schemas and the login, the turn on and off, and the login's footprint
in the role inventories, the access rules, the write pause and the supervisor (docs/engineering/SCHEDULED-JOBS.md)."""
import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cluster_inventory
import cluster_rebuild
import connection_budget
import dev
import durable_runtime
import realtime
import scheduled_jobs
import write_pause

E = 'e_' + 'a' * 24
OTHER = 'e_' + 'b' * 24
PASSWORD = 'c' * 64


class SqlTests(unittest.TestCase):
    def test_every_builder_refuses_a_name_that_is_not_an_environment(self):
        for builder in (scheduled_jobs.shim_sql, scheduled_jobs.grants_sql, scheduled_jobs.open_sql, scheduled_jobs.close_sql,
                        scheduled_jobs.revoke_sql):
            for bad in ('e_x', E + '; DROP', 'postgres', None, E.upper()):
                with self.subTest(builder=builder.__name__, name=bad), self.assertRaises(ValueError):
                    builder(bad)
        with self.assertRaises(ValueError):
            scheduled_jobs.login_sql(E, "x'; ALTER ROLE postgres SUPERUSER; --")
        with self.assertRaises(ValueError):
            scheduled_jobs.login_sql(E, 'C' * 64)

    def test_no_function_runs_with_its_owners_rights_and_each_pins_its_search_path(self):
        sql = scheduled_jobs.shim_sql(E)
        self.assertNotRegex(sql.upper(), r'SECURITY\s+DEFINER')
        functions = re.findall(r'CREATE OR REPLACE FUNCTION ([a-z_.]+)\(', sql)
        self.assertGreaterEqual(len(functions), 14)
        for block in re.split(r'CREATE OR REPLACE FUNCTION ', sql)[1:]:
            header = block.split(' AS $f$')[0]
            with self.subTest(function=header.split('(')[0]):
                self.assertIn('SET search_path = pg_catalog, pg_temp', header)

    def test_the_signatures_and_defaults_are_pg_cron_and_pg_net_ones(self):
        sql = scheduled_jobs.shim_sql(E)
        for signature in ('cron.schedule(schedule text, command text)', 'cron.schedule(job_name text, schedule text, command text)',
                          'cron.unschedule(job_id bigint)', 'cron.unschedule(job_name text)',
                          'cron.schedule_in_database(job_name text, schedule text, command text, database text,\n  username text DEFAULT NULL::text, active boolean DEFAULT true)',
                          "net.http_get(url text, params jsonb DEFAULT '{}'::jsonb, headers jsonb DEFAULT '{}'::jsonb,\n  timeout_milliseconds integer DEFAULT 5000)",
                          "net.http_post(url text, body jsonb DEFAULT '{}'::jsonb, params jsonb DEFAULT '{}'::jsonb,\n  headers jsonb DEFAULT '{\"Content-Type\": \"application/json\"}'::jsonb, timeout_milliseconds integer DEFAULT 5000)",
                          "net.http_delete(url text, params jsonb DEFAULT '{}'::jsonb, headers jsonb DEFAULT '{}'::jsonb,\n  timeout_milliseconds integer DEFAULT 5000, body jsonb DEFAULT NULL::jsonb)"):
            with self.subTest(signature=signature.split('(')[0]):
                self.assertIn(signature, sql)
        self.assertIn('alter_job(job_id bigint, schedule text DEFAULT NULL::text, command text DEFAULT NULL::text,\n  database text DEFAULT NULL::text, username text DEFAULT NULL::text, active boolean DEFAULT NULL::boolean)', sql)

    def test_public_gets_nothing_and_app_roles_reach_only_the_request_functions(self):
        sql = scheduled_jobs.shim_sql(E)
        for line in ('REVOKE ALL ON SCHEMA cron, net FROM PUBLIC;', 'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA cron, net FROM PUBLIC;',
                     'REVOKE ALL ON ALL TABLES IN SCHEMA cron, net FROM PUBLIC;', 'GRANT USAGE ON SCHEMA net TO anon, authenticated, service_role;'):
            self.assertIn(line, sql)
        self.assertNotRegex(sql, r'GRANT [^;]*ON SCHEMA cron[^;]* TO [^;]*(anon|authenticated|service_role)')
        self.assertNotRegex(sql, r'GRANT [^;]*ON net\._http_response TO [^;]*(anon|authenticated)')

    def test_what_a_tenant_can_hold_up_is_bounded_in_one_transaction(self):
        for sql in (scheduled_jobs.shim_sql(E), scheduled_jobs.grants_sql(E), scheduled_jobs.revoke_sql(E)):
            first = sql.split('COMMIT;')[0]
            self.assertTrue(first.startswith('BEGIN;'))
            for bound in ("lock_timeout = '5s'", "statement_timeout = '15s'", "transaction_timeout = '30s'"):
                self.assertIn(bound, first)

    def test_a_cron_or_net_schema_someone_else_owns_is_refused_not_adopted(self):
        sql = scheduled_jobs.shim_sql(E)
        self.assertIn("RAISE EXCEPTION 'A cron or net schema belongs to another login'", sql)
        self.assertLess(sql.index('belongs to another login'), sql.index('CREATE SCHEMA IF NOT EXISTS cron'))

    def test_the_login_acts_as_the_developer_only_by_set_role_and_loses_its_own_settings(self):
        login = scheduled_jobs.login_sql(E, PASSWORD)
        self.assertIn(f'CREATE ROLE {E}_cron NOLOGIN NOINHERIT', login)
        self.assertIn(f'CONNECTION LIMIT {scheduled_jobs.CONNECTIONS}', login)
        self.assertIn(f'ALTER ROLE {E}_cron RESET ALL;', login)
        self.assertIn('IN DATABASE %I RESET ALL', login)
        self.assertNotIn('LOGIN NOINHERIT', login.replace('NOLOGIN NOINHERIT', ''))
        grants = scheduled_jobs.grants_sql(E)
        self.assertIn(f'GRANT {E}_developer TO {E}_cron WITH INHERIT FALSE, SET TRUE', grants)
        self.assertNotIn(f'TO {E}_cron WITH INHERIT TRUE', grants)
        # Only what the scheduler needs: never cron.job writes, never EXECUTE.
        cron_grants = [line for line in grants.splitlines() if f'TO {E}_cron' in line]
        self.assertFalse(any('ON cron.job TO' in line and 'SELECT ON' not in line for line in cron_grants))
        self.assertFalse(any('EXECUTE ON' in line for line in cron_grants))

    def test_off_takes_back_every_grant_so_a_later_dump_names_no_such_role(self):
        sql = scheduled_jobs.revoke_sql(E)
        for line in (f'REVOKE ALL ON ALL TABLES IN SCHEMA cron FROM {E}_cron', f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA net FROM {E}_cron',
                     f'REVOKE ALL ON SCHEMA net FROM {E}_cron', f'REVOKE CONNECT ON DATABASE {E} FROM {E}_cron'):
            self.assertIn(line, sql)
        close = scheduled_jobs.close_sql(E)
        self.assertIn(f'ALTER ROLE {E}_cron NOLOGIN', close)
        self.assertIn(f"usename = '{E}_cron'", close)

    def test_the_database_limit_is_written_within_a_bound(self):
        sql = scheduled_jobs.limit_sql(E, 22)
        self.assertTrue(sql.startswith('BEGIN;'))
        self.assertIn("lock_timeout = '5s'", sql)
        self.assertIn("statement_timeout = '15s'", sql)
        self.assertIn(f'ALTER DATABASE {E} CONNECTION LIMIT 22;', sql)
        for bad in (-1, '22', 2.5, None):
            with self.assertRaises(ValueError):
                scheduled_jobs.limit_sql(E, bad)

    def test_every_change_of_the_login_ends_its_sessions_first_in_one_bounded_transaction(self):
        for sql in (scheduled_jobs.close_sql(E), scheduled_jobs.login_sql(E, PASSWORD), scheduled_jobs.open_sql(E)):
            body = sql[sql.index('BEGIN;'):sql.index('COMMIT;')]
            self.assertIn("lock_timeout = '10s'", body)
            self.assertIn("statement_timeout = '30s'", body)
            self.assertLess(body.index('pg_terminate_backend'), body.index(f'ALTER ROLE {E}_cron '))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.statements = []
        self.answers = {}
        self.runtime = object.__new__(durable_runtime.Runtime)
        self.runtime.values = {'environments': {E: {'jwt': 'j' * 64}}}
        self.runtime.path = self.root / 'runtime.json'

        def sql(query, database='postgres', check=True):
            self.statements.append((database, query))
            for key, value in self.answers.items():
                if key in query:
                    return SimpleNamespace(stdout=value, returncode=0)
            return SimpleNamespace(stdout='', returncode=0)
        self.runtime.sql = sql
        (self.root / 'endpoints.json').write_text(json.dumps({E: {'rest': 'x'}}))
        for item in [patch.object(durable_runtime, 'STATE', self.root), patch.object(durable_runtime, 'inspect', return_value={'Id': 'x'}),
                     patch.object(durable_runtime.effect_receipt, 'require_settled'),
                     patch.object(durable_runtime.source_fence, 'is_fenced', return_value=False),
                     patch.object(write_pause, 'is_paused', return_value=False)]:
            item.start()
            self.addCleanup(item.stop)
        self.answers["pg_hba_file_rules WHERE '" + E + "_cron'"] = '1'
        self.answers["current_setting('max_connections')"] = '197|100'

    def endpoints(self):
        return json.loads((self.root / 'endpoints.json').read_text())

    def test_turning_on_makes_the_login_closed_then_the_schemas_then_opens_it_and_publishes(self):
        self.runtime.scheduled_jobs_turn(E, on=True)
        queries = [query for _, query in self.statements]
        close = next(i for i, q in enumerate(queries) if f'ALTER ROLE {E}_cron NOLOGIN' in q)
        login = next(i for i, q in enumerate(queries) if 'CONNECTION LIMIT 4 PASSWORD' in q)
        shim = next(i for i, q in enumerate(queries) if 'CREATE SCHEMA IF NOT EXISTS cron' in q)
        opened = queries.index(scheduled_jobs.open_sql(E))
        self.assertLess(close, login)
        self.assertLess(login, shim)
        self.assertLess(shim, opened)
        self.assertTrue(any(db == E and 'CREATE SCHEMA IF NOT EXISTS cron' in q for db, q in self.statements))
        limit = connection_budget.ENVIRONMENT_LIMIT + connection_budget.SCHEDULER_CONNECTIONS
        self.assertIn(scheduled_jobs.limit_sql(E, limit), queries)
        self.assertEqual(self.endpoints()[E]['cron'], {'user': f'{E}_cron'})
        password = self.runtime.values['environments'][E]['cron']
        self.assertTrue(scheduled_jobs.password_ok(password))
        # The same password the next time: a rebuild and the scheduler read it from the runtime file.
        self.runtime.scheduled_jobs_turn(E, on=True)
        self.assertEqual(self.runtime.values['environments'][E]['cron'], password)

    def test_turning_on_is_refused_until_the_access_rule_is_published(self):
        self.answers["pg_hba_file_rules WHERE '" + E + "_cron'"] = '0'
        with self.assertRaisesRegex(RuntimeError, 'access rule not published'):
            self.runtime.scheduled_jobs_turn(E, on=True)
        self.assertNotIn('cron', self.endpoints()[E])

    def test_turning_on_is_refused_when_the_connections_do_not_fit(self):
        self.answers["current_setting('max_connections')"] = '197|196'
        with self.assertRaises(durable_runtime.AdmissionLimitError):
            self.runtime.scheduled_jobs_turn(E, on=True)
        self.assertFalse(any('CREATE SCHEMA' in q for _, q in self.statements))

    def test_turning_on_waits_for_a_write_pause_and_a_fence_refuses(self):
        with patch.object(write_pause, 'is_paused', return_value=True), self.assertRaises(durable_runtime.OperationBusy):
            self.runtime.scheduled_jobs_turn(E, on=True)
        with patch.object(durable_runtime.source_fence, 'is_fenced', return_value=True), self.assertRaisesRegex(RuntimeError, 'fenced'):
            self.runtime.scheduled_jobs_turn(E, on=True)
        self.assertFalse(any('CREATE SCHEMA' in q for _, q in self.statements))

    def test_turning_off_closes_revokes_and_keeps_the_schemas(self):
        (self.root / 'endpoints.json').write_text(json.dumps({E: {'rest': 'x', 'cron': {'user': f'{E}_cron'}}}))
        self.runtime.scheduled_jobs_turn(E, on=False)
        queries = [query for _, query in self.statements]
        self.assertTrue(any(f'ALTER ROLE {E}_cron NOLOGIN' in q for q in queries))
        self.assertTrue(any(db == E and f'REVOKE ALL ON SCHEMA cron FROM {E}_cron' in q for db, q in self.statements))
        self.assertFalse(any('DROP SCHEMA' in q or 'DROP TABLE' in q or 'DROP ROLE' in q for q in queries))
        self.assertIn(scheduled_jobs.limit_sql(E, connection_budget.ENVIRONMENT_LIMIT), queries)
        self.assertNotIn('cron', self.endpoints()[E])

    def test_a_start_writes_the_login_again_and_opens_it_unless_paused(self):
        (self.root / 'endpoints.json').write_text(json.dumps({E: {'rest': 'x', 'cron': {'user': f'{E}_cron'}}}))
        self.runtime.values['environments'][E]['cron'] = PASSWORD
        self.runtime.scheduled_jobs_back(E)
        queries = [query for _, query in self.statements]
        self.assertTrue(any(f"PASSWORD '{PASSWORD}'" in q and f'ALTER ROLE {E}_cron RESET ALL' in q for q in queries))
        self.assertEqual(queries[-1], scheduled_jobs.open_sql(E))
        self.statements.clear()
        with patch.object(write_pause, 'is_paused', return_value=True):
            self.runtime.scheduled_jobs_back(E)
        self.assertNotIn(scheduled_jobs.open_sql(E), [q for _, q in self.statements])
        self.statements.clear()
        (self.root / 'endpoints.json').write_text(json.dumps({E: {'rest': 'x'}}))
        self.runtime.scheduled_jobs_back(E)
        self.assertEqual(self.statements, [])

    def test_a_start_writes_the_schemas_and_the_logins_grants_again_and_a_failure_never_stops_it(self):
        (self.root / 'endpoints.json').write_text(json.dumps({E: {'rest': 'x', 'cron': {'user': f'{E}_cron'}}}))
        self.runtime.values['environments'][E]['cron'] = PASSWORD
        self.runtime.scheduled_jobs_back(E)
        repaired = [q for db, q in self.statements if db == E]
        self.assertTrue(any('CREATE SCHEMA IF NOT EXISTS cron' in q and "rolname = '" + E + "_cron' AND true" in q for q in repaired))
        original = self.runtime.sql

        def failing(query, database='postgres', check=True):
            if database == E:
                raise RuntimeError('lock timeout')
            return original(query, database, check)
        self.runtime.sql = failing
        self.statements.clear()
        self.runtime.scheduled_jobs_back(E)
        self.assertEqual(self.statements[-1][1], scheduled_jobs.open_sql(E))

    def test_an_environment_that_is_not_published_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'published environment'):
            self.runtime.scheduled_jobs_turn(OTHER, on=True)
        with self.assertRaisesRegex(RuntimeError, 'Invalid'):
            self.runtime.scheduled_jobs_turn('e_bad', on=True)


class RealtimeBoundTests(unittest.TestCase):
    def test_realtime_scripts_under_the_operation_lock_are_bounded_transactions(self):
        runtime = object.__new__(durable_runtime.Runtime)
        statements = []
        runtime.sql = lambda query, database='postgres', check=True: statements.append(query) or SimpleNamespace(stdout='', returncode=0)
        with patch.object(durable_runtime, 'current_database_limit', return_value=40):
            runtime.realtime_database(E, {'realtime': 'r' * 64})
        self.assertEqual(len(statements), 3)
        for sql in statements:
            with self.subTest(sql=sql[:40]):
                self.assertTrue(sql.startswith('BEGIN;'))
                self.assertIn("lock_timeout = '5s'", sql)
                self.assertIn("statement_timeout = '15s'", sql)
                self.assertTrue(sql.rstrip().endswith('COMMIT;'))
        self.assertIn(f'GRANT CONNECT, CREATE, TEMPORARY ON DATABASE {E}', statements[0])
        statements.clear()
        runtime.realtime_grants(E)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith('BEGIN;'))
        self.assertIn("lock_timeout = '5s'", statements[0])
        self.assertTrue(statements[0].rstrip().endswith('COMMIT;'))


class FootprintTests(unittest.TestCase):
    def test_the_access_rule_admits_the_login_to_its_own_database_only(self):
        lines = durable_runtime.hba_content([E, OTHER]).splitlines()
        self.assertIn(f'host {E} {E}_cron 0.0.0.0/0 scram-sha-256', lines)
        self.assertIn(f'host {OTHER} {OTHER}_cron 0.0.0.0/0 scram-sha-256', lines)
        self.assertFalse(any(f'{E}_cron' in line and not line.startswith(f'host {E} ') for line in lines))
        self.assertLess(lines.index(f'host {E} {E}_cron 0.0.0.0/0 scram-sha-256'), lines.index('host all all 0.0.0.0/0 reject'))

    def test_the_database_limit_grows_by_the_logins_four_while_on(self):
        self.assertEqual(connection_budget.SCHEDULER_CONNECTIONS, scheduled_jobs.CONNECTIONS)
        self.assertEqual(connection_budget.database_limit(cron=True), connection_budget.ENVIRONMENT_LIMIT + 4)
        self.assertEqual(connection_budget.database_limit(), connection_budget.ENVIRONMENT_LIMIT)

    def test_the_inventory_knows_the_login_and_its_rule(self):
        self.assertEqual(cluster_inventory.classify(f'{E}_cron'), ('cron', E))
        self.assertIn('cron', cluster_inventory.ROLE_NAMES)
        role = {'name': f'{E}_cron', 'kind': 'cron', 'environment': E, **dict.fromkeys(cluster_inventory.ATTRIBUTES, False),
                'rolcanlogin': True, 'rolconnlimit': 3, 'member_of': [{'role': f'{E}_developer', 'inherit': False, 'set': True, 'admin': False}],
                'settings': []}
        self.assertEqual(cluster_inventory.role_refusals(role), [])
        self.assertTrue(cluster_inventory.role_refusals({**role, 'rolinherit': True}))
        self.assertTrue(cluster_inventory.role_refusals({**role, 'rolbypassrls': True}))

    def test_a_rebuild_reads_its_password_and_closes_it_without_one(self):
        self.assertIn('cron', cluster_rebuild.TOGGLED)
        inventory = {'parameters': [], 'roles': [{'name': f'{E}_cron', 'kind': 'cron', 'environment': E, **dict.fromkeys(cluster_inventory.ATTRIBUTES, False),
                                'rolcanlogin': True, 'rolconnlimit': 3, 'member_of': [], 'settings': []}]}
        statements, closed = cluster_rebuild.role_sql(inventory, {})[:2]
        self.assertEqual(closed, [f'{E}_cron'])
        statements, closed = cluster_rebuild.role_sql(inventory, {f'{E}_cron': PASSWORD})[:2]
        self.assertEqual(closed, [])
        self.assertTrue(any(f"PASSWORD '{PASSWORD}'" in line for line in statements))

    def test_the_console_toggle_runs_through_the_same_child(self):
        self.assertEqual(realtime.SERVICES['cron'], ('scheduled_jobs', 'Scheduled jobs'))
        self.assertIn('cron', dev.TOGGLES)
        self.assertIn('cron', durable_runtime.CONSOLE_CHANGES)
        self.assertIn('scheduled jobs', realtime.failure_text('cron', realtime.CAPACITY, 'on'))

    def test_a_write_pause_closes_the_login_and_resume_opens_it_only_when_on(self):
        class Database:
            def __init__(self):
                self.queries = []

            def __call__(self, query):
                self.queries.append(query)
                return '0'
        database = Database()
        write_pause.close_logins(E, database, sleep=lambda _: None)
        self.assertEqual(database.queries[0], scheduled_jobs.close_sql(E))
        self.assertNotIn(f'{E}_cron', database.queries[1])
        self.assertIn(f"'{E}_cron'", database.queries[2])


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.supervisor = object.__new__(dev.Supervisor)
        self.supervisor.scheduler = None
        self.supervisor.scheduler_after = 0.0
        self.supervisor.drain = None
        self.supervisor.update = None
        self.spawned = []
        self.supervisor.spawn = lambda command, **options: self.spawned.append(command) or SimpleNamespace(pid=1, returncode=None)

    def test_it_starts_the_scheduler_and_stops_it_for_an_update_drain(self):
        with patch.object(dev, 'child_status', return_value=None), patch.object(dev, 'terminate_group') as terminate:
            self.supervisor.schedule_scheduler()
            self.assertEqual(self.spawned, [['bun', 'lab/scheduler.ts']])
            self.supervisor.schedule_scheduler()
            self.assertEqual(len(self.spawned), 1)
            self.supervisor.drain = {'kind': 'apply'}
            self.supervisor.schedule_scheduler()
            terminate.assert_called_once()
            self.assertIsNone(self.supervisor.scheduler)
            self.supervisor.schedule_scheduler()
            self.assertEqual(len(self.spawned), 1)
            self.supervisor.drain = None
            self.supervisor.schedule_scheduler()
            self.assertEqual(len(self.spawned), 2)

    def test_one_that_exits_starts_again_after_a_pause(self):
        with patch.object(dev, 'child_status', return_value=1), patch.object(dev, 'terminate_group'):
            self.supervisor.scheduler = SimpleNamespace(pid=1, returncode=None)
            self.supervisor.schedule_scheduler()
            self.assertIsNone(self.supervisor.scheduler)
            self.assertEqual(self.spawned, [])
        self.supervisor.scheduler_after = 0.0
        with patch.object(dev, 'child_status', return_value=None):
            self.supervisor.schedule_scheduler()
        self.assertEqual(len(self.spawned), 1)

    def test_a_running_scheduler_keeps_the_supervisor_from_being_idle(self):
        self.supervisor.worker = self.supervisor.backup = None
        self.supervisor.sign_in = {}
        self.supervisor.studios = {}
        self.supervisor.data_api = {}
        self.supervisor.toggles = {}
        self.supervisor.scheduler = SimpleNamespace(pid=1)
        self.assertFalse(self.supervisor.idle())

    def test_a_pending_turn_on_waits_for_a_write_pause(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state = Path(directory.name)
        with closing(sqlite3.connect(state / 'control.sqlite')) as database, database:
            database.execute('CREATE TABLE scheduled_jobs(runtime TEXT, desired TEXT, state TEXT, updated_at INTEGER)')
            database.execute("INSERT INTO scheduled_jobs VALUES (?, 'on', 'pending', 1), (?, 'off', 'pending', 2)", (E, OTHER))
        (state / 'write-pauses').mkdir()
        (state / 'write-pauses' / f'{E}.json').write_text('{}')
        with patch.object(dev, 'STATE', state):
            self.assertEqual(self.supervisor.toggle_requests('cron'), [OTHER])


if __name__ == '__main__':
    unittest.main()
