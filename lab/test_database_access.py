"""Direct database access: the developer login's rights, the saved password, the connection budget and turning it off."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import connection_budget
import durable_runtime

E = 'e_' + 'a' * 24
PASSWORD = 'p' * 32


class DatabaseAccessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'state').mkdir()
        (self.root / 'private' / 'database').mkdir(parents=True)
        (self.root / 'state' / 'endpoints.json').write_text(json.dumps({E: {'auth': 'http://a'}}))
        (self.root / 'private' / 'database' / f'{E}.json').write_text(json.dumps({'password': PASSWORD}))
        self.runtime = object.__new__(durable_runtime.Runtime)
        self.runtime.values = {'environments': {E: {'jwt': 'j' * 64}}}
        self.statements = []
        self.hba = '1'

        def sql(query, database='postgres', check=True):
            self.statements.append((database, query))
            if 'pg_hba_file_rules' in query:
                return SimpleNamespace(stdout=self.hba, returncode=0)
            if "current_setting('max_connections')" in query:
                return SimpleNamespace(stdout='100|40', returncode=0)
            return SimpleNamespace(stdout='', returncode=0)
        self.runtime.sql = sql
        for item in [patch.object(durable_runtime, 'STATE', self.root / 'state'), patch.object(durable_runtime, 'PRIVATE', self.root / 'private'),
                     patch.object(durable_runtime, 'inspect', return_value={'Id': 'db'}),
                     patch.object(durable_runtime.effect_receipt, 'require_settled'),
                     patch.object(durable_runtime.source_fence, 'is_fenced', return_value=False)]:
            item.start()
            self.addCleanup(item.stop)

    def endpoints(self):
        return json.loads((self.root / 'state' / 'endpoints.json').read_text())

    def test_the_access_rule_admits_the_developer_login_to_its_own_database_only(self):
        lines = durable_runtime.hba_content([E]).splitlines()
        self.assertIn(f'host {E} {E}_developer 0.0.0.0/0 scram-sha-256', lines)
        self.assertFalse(any(line.startswith('host all ') and 'developer' in line for line in lines))

    def test_the_developer_login_is_not_a_superuser_and_owns_auth_and_storage_through_membership(self):
        text = self.runtime.developer_sql(E, PASSWORD)
        self.assertIn('NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', text)
        self.assertIn(f'GRANT {E}_auth, {E}_storage, anon, authenticated, service_role TO {E}_developer', text)
        self.assertIn(f'CONNECTION LIMIT {connection_budget.DIRECT_CONNECTIONS}', text)
        self.assertIn(f"PASSWORD '{PASSWORD}'", text)
        self.assertNotIn('supabase_admin', text)

    def test_the_login_may_create_schemas_only_after_the_reserved_schema_guard_is_in_place(self):
        role_text = self.runtime.developer_sql(E, PASSWORD)
        self.assertNotIn('CREATE ON DATABASE', role_text)
        text = self.runtime.developer_grants_sql(E)
        guard = text.index('CREATE EVENT TRIGGER sbarbase_reserved_schemas ON ddl_command_end')
        self.assertLess(guard, text.index(f'GRANT CREATE ON DATABASE {E} TO {E}_developer'))
        self.assertIn("WHEN TAG IN ('CREATE SCHEMA', 'ALTER SCHEMA', 'CREATE EXTENSION', 'ALTER EXTENSION')", text)
        self.assertIn('rolname = session_user', text)
        self.assertNotIn('rolname = current_user', text)
        self.assertIn('ALTER EVENT TRIGGER sbarbase_reserved_schemas ENABLE ALWAYS', text)
        self.assertIn('SET search_path = pg_catalog', text)
        for name in ('auth', 'storage', 'extensions', 'realtime', '_realtime', 'vault', 'cron', 'net', 'graphql'):
            self.assertIn(name, durable_runtime.RESERVED_SCHEMAS)
            self.assertIn(f"'{name}'", text[:guard])
        self.assertIn("LIKE 'sbarbase%'", text)
        self.assertIn("LIKE 'supabase%'", text)

    def test_vault_is_installed_and_granted_before_the_login_may_create_schemas(self):
        text = self.runtime.developer_grants_sql(E)
        vault = text.index('CREATE EXTENSION IF NOT EXISTS supabase_vault')
        self.assertLess(vault, text.index(f'GRANT CREATE ON DATABASE {E}'))
        self.assertIn(f'GRANT USAGE ON SCHEMA vault TO {E}_developer;', text)
        self.assertNotIn('WITH GRANT OPTION', text)
        self.assertIn('vault.create_secret, vault.update_secret, vault._crypto_aead_det_decrypt TO service_role', text)
        self.assertNotIn('anon', text[vault:text.index("-- The API's default grants")])

    def test_extensions_that_reach_the_server_are_refused_and_the_realtime_publication_name_is_kept(self):
        text = self.runtime.developer_grants_sql(E)
        for name in ('dblink', 'postgres_fdw', 'http', 'pg_net', 'wrappers', 'pg_cron', 'pgsodium', 'pg_stat_statements', 'pg_walinspect', 'pgroonga'):
            self.assertIn(name, durable_runtime.RESERVED_EXTENSIONS)
            self.assertIn(f"'{name}'", text)
        for name in ('pg_trgm', 'btree_gist', 'vector', 'pgtap', 'pg_graphql', 'supabase_vault'):
            self.assertNotIn(name, durable_runtime.RESERVED_EXTENSIONS)
        publication = text.index('CREATE PUBLICATION supabase_realtime FOR TABLES IN SCHEMA public')
        self.assertLess(publication, text.index(f'ALTER PUBLICATION supabase_realtime OWNER TO {E}_developer'))

    def test_the_api_default_grants_are_given_only_before_migration_history_exists(self):
        text = self.runtime.developer_grants_sql(E)
        condition = text.index("IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'supabase_migrations') THEN")
        defaults = text.index(f'ALTER DEFAULT PRIVILEGES FOR ROLE {E}_developer IN SCHEMA public GRANT ALL ON TABLES')
        self.assertLess(condition, defaults)
        self.assertLess(defaults, text.index('CREATE SCHEMA IF NOT EXISTS supabase_migrations'))
        self.assertEqual(text.count('ALTER DEFAULT PRIVILEGES'), 3)

    def test_realtime_refuses_schemas_another_login_made(self):
        self.runtime.realtime_database(E, {'realtime': 'r' * 64})
        text = '\n'.join(query for database, query in self.statements if database == E)
        refusal = text.index("n.nspname IN ('realtime', '_realtime')\n      AND r.rolname <> '" + E + "_realtime' AND NOT r.rolsuper")
        self.assertLess(refusal, text.index('CREATE SCHEMA IF NOT EXISTS _realtime'))
        self.assertLess(text.index("p.pubname = 'supabase_realtime'\n      AND NOT r.rolsuper AND r.rolname <> '" + E + "_developer'"), text.index('CREATE SCHEMA IF NOT EXISTS _realtime'))
        self.assertIn(f'ALTER PUBLICATION supabase_realtime OWNER TO {E}_developer', text)

    def test_turning_it_on_applies_the_saved_password_room_and_grants_then_publishes(self):
        self.runtime.database_turn(E, on=True)
        text = '\n'.join(query for _, query in self.statements)
        self.assertIn(f"PASSWORD '{PASSWORD}'", text)
        self.assertIn(f'ALTER DATABASE {E} CONNECTION LIMIT {connection_budget.database_limit(direct=True)}', text)
        self.assertTrue(any(database == E and 'GRANT USAGE, CREATE ON SCHEMA public' in query for database, query in self.statements))
        self.assertEqual(self.endpoints()[E]['database'], {'user': f'{E}_developer', 'database': E})

    def test_it_refuses_without_the_access_rule_or_a_valid_saved_password(self):
        self.hba = '0'
        with self.assertRaises(RuntimeError):
            self.runtime.database_turn(E, on=True)
        self.hba = '1'
        (self.root / 'private' / 'database' / f'{E}.json').write_text(json.dumps({'password': "x'; drop"}))
        with self.assertRaises(RuntimeError):
            self.runtime.database_turn(E, on=True)
        self.assertNotIn('database', self.endpoints()[E])

    def test_resume_turns_it_on_with_a_password_reset_during_the_pause_and_never_waits_on_its_own_lock(self):
        import fcntl
        import write_pause
        state = self.root / 'state'
        write_pause.write_record(write_pause.record_path(E, state),
                                 {'version': 1, 'runtime': E, 'phase': 'resuming', 'paused_at': 900.0})
        # Turning access on from the console is refused while the pause is recorded.
        with self.assertRaisesRegex(RuntimeError, 'paused for writes'):
            self.runtime.database_turn(E, on=True)
        self.assertNotIn('database', self.endpoints()[E])
        # The owner reset the password meanwhile; resume, holding the pause lock, applies it.
        (self.root / 'private' / 'database' / f'{E}.json').write_text(json.dumps({'password': 'r' * 32}))
        with (state / 'write-pause.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.runtime.developer_on(E)
        text = '\n'.join(query for _, query in self.statements)
        self.assertIn("PASSWORD '" + 'r' * 32 + "'", text)
        self.assertNotIn(f"PASSWORD '{PASSWORD}'", text)
        self.assertEqual(self.endpoints()[E]['database'], {'user': f'{E}_developer', 'database': E})

    def test_a_full_cluster_refuses_with_the_capacity_error(self):
        def sql(query, database='postgres', check=True):
            if 'pg_hba_file_rules' in query:
                return SimpleNamespace(stdout='1', returncode=0)
            return SimpleNamespace(stdout='100|95', returncode=0)
        self.runtime.sql = sql
        with self.assertRaises(durable_runtime.AdmissionLimitError):
            self.runtime.database_turn(E, on=True)

    def test_turning_it_off_closes_the_login_and_gives_the_room_back(self):
        self.runtime.database_turn(E, on=True)
        self.statements.clear()
        self.runtime.database_turn(E, on=False)
        text = '\n'.join(query for _, query in self.statements)
        self.assertIn(f'ALTER ROLE {E}_developer NOLOGIN', text)
        self.assertIn('pg_terminate_backend', text)
        self.assertIn(f'ALTER DATABASE {E} CONNECTION LIMIT {connection_budget.database_limit()}', text)
        self.assertNotIn('database', self.endpoints()[E])

    def test_the_database_limit_counts_every_extra_login(self):
        self.assertEqual(connection_budget.database_limit(studio=True, realtime=True, direct=True),
                         connection_budget.ENVIRONMENT_LIMIT + connection_budget.STUDIO_CONNECTIONS
                         + connection_budget.REALTIME_CONNECTIONS + connection_budget.DIRECT_CONNECTIONS)


if __name__ == '__main__':
    unittest.main()
