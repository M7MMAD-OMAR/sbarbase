"""Data API settings: validation, the settings PostgREST applies, the exposed schema guard, the
runtime's apply and resume paths, Studio's view and what a restore admits (lab/data_api.py)."""
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import data_api
import durable_runtime

E = 'e_' + 'a' * 24


def catalog(path, schemas='public,i2s', search='public,extensions', rows=None, state='pending', applied=None, failure=None):
    """The catalog table as src/control/catalog.ts creates it; `applied` is (schemas, search path, rows)."""
    with sqlite3.connect(path) as database:
        database.execute('CREATE TABLE data_api_settings(runtime TEXT PRIMARY KEY, schemas TEXT NOT NULL, extra_search_path TEXT NOT NULL, '
                         'max_rows INTEGER, applied_schemas TEXT, applied_extra_search_path TEXT, applied_max_rows INTEGER, '
                         'served_schemas TEXT, served_extra_search_path TEXT, served_max_rows INTEGER, start_report TEXT, '
                         'state TEXT NOT NULL, failure TEXT, actor TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        done = applied or (None, None, None)
        database.execute('INSERT INTO data_api_settings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
                         (E, schemas, search, rows, *done, *done, None, state, failure, 'user:x'))


def stored(path):
    with sqlite3.connect(path) as database:
        return database.execute('SELECT applied_schemas, state, failure FROM data_api_settings').fetchone()


def report(path):
    with sqlite3.connect(path) as database:
        return database.execute('SELECT start_report FROM data_api_settings').fetchone()[0]


def served(path):
    with sqlite3.connect(path) as database:
        return database.execute('SELECT served_schemas, served_extra_search_path FROM data_api_settings').fetchone()


class ValidationTests(unittest.TestCase):
    def test_keeps_order_and_defaults(self):
        value = data_api.validate({'schemas': ['i2s', 'public']})
        self.assertEqual(value, {'schemas': ['i2s', 'public'], 'extra_search_path': ['public'], 'max_rows': None})

    def test_refuses_platform_and_internal_schemas(self):
        for name in ('auth', 'storage', 'vault', 'cron', 'net', 'pg_catalog', 'pg_temp_1', 'information_schema',
                     'sbarbase_guard', 'supabase_migrations', 'graphql', 'extensions'):
            with self.subTest(name=name), self.assertRaisesRegex(data_api.SettingsError, 'belongs to the platform'):
                data_api.validate({'schemas': ['public', name]})

    def test_extensions_allowed_only_on_search_path(self):
        self.assertEqual(data_api.validate({'schemas': ['public'], 'extra_search_path': ['public', 'extensions']})['extra_search_path'],
                         ['public', 'extensions'])
        with self.assertRaises(data_api.SettingsError):
            data_api.validate({'schemas': ['public'], 'extra_search_path': ['auth']})

    def test_refuses_names_that_need_quoting(self):
        for name in ('App', 'my-schema', '1abc', 'a,b', "x'y", '', 'a' * 64, ' public'):
            with self.subTest(name=name), self.assertRaises(data_api.SettingsError):
                data_api.validate({'schemas': [name]})

    def test_refuses_duplicates_empty_and_too_many(self):
        with self.assertRaisesRegex(data_api.SettingsError, 'twice'):
            data_api.validate({'schemas': ['public', 'public']})
        with self.assertRaises(data_api.SettingsError):
            data_api.validate({'schemas': [f's{i}' for i in range(data_api.MAX_SCHEMAS + 1)]})

    def test_max_rows_bounds(self):
        self.assertEqual(data_api.validate({'schemas': ['public'], 'max_rows': 1000})['max_rows'], 1000)
        for rows in (0, -1, data_api.MAX_ROWS + 1, 1.5, True, '10'):
            with self.subTest(rows=rows), self.assertRaises(data_api.SettingsError):
                data_api.validate({'schemas': ['public'], 'max_rows': rows})

    def test_unknown_field_refused(self):
        with self.assertRaises(data_api.SettingsError):
            data_api.validate({'schemas': ['public'], 'jwt_secret': 'x'})


class SqlTests(unittest.TestCase):
    def test_settings_are_written_in_the_environment_database_never_as_role_settings(self):
        text = data_api.settings_sql(E, {'schemas': ['public', 'i2s'], 'extra_search_path': ['public', 'extensions'], 'max_rows': 500})
        self.assertIn("CREATE OR REPLACE FUNCTION sbarbase_rest.settings()", text)
        self.assertIn("('pgrst.db_schemas', 'public,i2s')", text)
        self.assertIn("('pgrst.db_extra_search_path', 'public,extensions')", text)
        self.assertIn("('pgrst.db_max_rows', '500')", text)
        self.assertNotIn('ALTER ROLE', text)
        self.assertNotIn('jwt', text)

    def test_no_row_limit_leaves_the_key_out(self):
        text = data_api.settings_sql(E, {'schemas': ['public'], 'extra_search_path': ['public'], 'max_rows': None})
        self.assertNotIn('db_max_rows', text)

    def test_a_reset_answers_no_settings(self):
        self.assertIn('WHERE false $settings$', data_api.reset_sql(E))
        self.assertNotIn('ALTER ROLE', data_api.reset_sql(E))

    def test_settings_sql_validates_again(self):
        with self.assertRaises(data_api.SettingsError):
            data_api.settings_sql(E, {'schemas': ["public'; DROP ROLE x; --"], 'extra_search_path': ['public'], 'max_rows': None})
        with self.assertRaises(data_api.SettingsError):
            data_api.settings_sql('e_bad', {'schemas': ['public']})

    def test_reload_asks_for_config_and_schema(self):
        self.assertIn("NOTIFY pgrst, 'reload config'", data_api.RELOAD_SQL)
        self.assertIn("NOTIFY pgrst, 'reload schema'", data_api.RELOAD_SQL)

    def test_guard_protects_exposed_schemas_and_skips_superuser(self):
        text = data_api.guard_sql(E)
        self.assertIn("WHEN TAG IN ('ALTER SCHEMA', 'DROP SCHEMA', 'DROP OWNED', 'CREATE EXTENSION', 'ALTER EXTENSION', 'DROP EXTENSION')", text)
        # An older trigger without DROP EXTENSION is replaced, and the presence check notices it.
        self.assertIn('DROP EVENT TRIGGER sbarbase_exposed_schemas', text)
        self.assertIn("evttags @> ARRAY['DROP EXTENSION']", data_api.guard_present_sql(E))
        self.assertIn(f'GRANT EXECUTE ON FUNCTION sbarbase_rest.settings(), sbarbase_rest.pre_config() TO {E}_rest;', text)
        self.assertIn("FILTER (WHERE key = 'pgrst.db_schemas'), ''), 'public')", text)
        self.assertIn('INTO exposed FROM sbarbase_rest.settings();', text)
        self.assertNotIn('pg_db_role_setting', text)
        self.assertIn('rolname = session_user', text)
        self.assertIn('ENABLE ALWAYS', text)

    def test_turning_database_access_on_installs_the_guard_only_when_it_is_missing(self):
        # Installed every time, under the operation lock and with REST serving, a REST login session
        # holding the platform's catalog rows held every other environment's toggle up (found in review).
        self.assertNotIn('sbarbase_exposed_schemas', durable_runtime.Runtime.developer_grants_sql(None, E))
        source = (Path(__file__).parent / 'durable_runtime.py').read_text()
        self.assertLess(source.index("raise RuntimeError('Data API guard missing; restart Sbarbase once')"),
                        source.index('self.sql(self.developer_grants_sql(e), e)'))
        self.assertNotIn('self.ensure_guard(e)\n', source)
        runtime = object.__new__(durable_runtime.Runtime)
        for present, installed in (('t', False), ('f', True)):
            ran = []
            runtime.ensure_guard(E, lambda query, database='postgres': ran.append(query) or SimpleNamespace(stdout=present))
            self.assertEqual(data_api.guard_sql(E) in ran, installed)
        imported = (Path(__file__).parent / 'import_project.py').read_text()
        self.assertLess(imported.index('runtime.data_api.guard_sql(e)'), imported.index('work.developer_grants_sql(e)'))


class ServingTests(unittest.TestCase):
    def test_reads_settings(self):
        value = data_api.serving('pgrst.db_extra_search_path=public,extensions\npgrst.db_max_rows=100\npgrst.db_schemas=public,i2s\n')
        self.assertEqual(value, {'schemas': ['public', 'i2s'], 'extra_search_path': ['public', 'extensions'], 'max_rows': 100})

    def test_defaults_when_nothing_set(self):
        self.assertEqual(data_api.serving(''), {'schemas': ['public'], 'extra_search_path': ['public'], 'max_rows': None})

    def test_effective_leaves_out_missing_and_keeps_order(self):
        served, missing = data_api.effective({'schemas': ['i2s', 'gone', 'public'], 'extra_search_path': ['public', 'gone'], 'max_rows': None},
                                             {'public', 'i2s'})
        self.assertEqual(served['schemas'], ['i2s', 'public'])
        self.assertEqual(served['extra_search_path'], ['public'])
        self.assertEqual(missing, ['gone'])

    def test_existence_is_decided_by_the_database_for_the_asked_names_only(self):
        settings = {'schemas': ['public', 'ghost'], 'extra_search_path': ['public', 'extensions'], 'max_rows': None}
        text = data_api.present_sql(settings)
        self.assertIn("unnest(ARRAY['extensions', 'ghost', 'public']::text[])", text)
        self.assertIn('nspname = name', text)
        # Output that is not one of the asked identifiers counts for nothing.
        self.assertEqual(data_api.existing('x\nghost x\npublic\n'), {'x', 'public'})
        with self.assertRaises(data_api.SettingsError):
            data_api.present_sql({'schemas': ["a'b"], 'extra_search_path': [], 'max_rows': None})

    def test_write_checks_again_under_the_lock_in_one_transaction(self):
        text = data_api.write_sql(E, {'schemas': ['public', 'i2s'], 'extra_search_path': ['public'], 'max_rows': None})
        self.assertTrue(text.startswith('BEGIN;') and text.endswith('COMMIT;'))
        self.assertLess(text.index('pg_try_advisory_xact_lock'), text.index(data_api.RACE))
        self.assertIn(data_api.BUSY, text)
        self.assertLess(text.index(data_api.RACE), text.index('CREATE OR REPLACE FUNCTION sbarbase_rest.settings()'))
        self.assertIn("lock_timeout = '5s'", text)

    def test_guard_takes_the_same_lock_at_read_committed(self):
        text = data_api.guard_sql(E)
        self.assertIn(f'pg_advisory_xact_lock({data_api.LOCK_KEY})', text)
        self.assertIn("current_setting('transaction_isolation') <> 'read committed'", text)
        self.assertIn(f'pg_advisory_xact_lock({data_api.LOCK_KEY})', data_api.guard_present_sql(E))

    def test_graphql_public_is_refused_with_its_reason(self):
        with self.assertRaisesRegex(data_api.SettingsError, 'GraphQL is not available'):
            data_api.validate({'schemas': ['public', 'graphql_public']})

    def test_left_out_text_says_when_nothing_is_served(self):
        self.assertIn('no table is served', data_api.left_out_text(['api'], True))
        self.assertIn('The rest is served', data_api.left_out_text(['api']))

    def test_superuser_scripts_in_the_environment_database_stay_on_pg_catalog(self):
        # A tenant's public.pg_has_role(text, text, text) once ran as the superuser here.
        grants = durable_runtime.Runtime.developer_grants_sql(None, E)
        self.assertTrue(grants.startswith(data_api.SAFE))
        self.assertNotIn('RESET search_path', grants)
        identity = durable_runtime.studio_identity_sql(E)
        self.assertTrue(identity.startswith('SET search_path TO pg_catalog, pg_temp;'))
        self.assertNotRegex(identity, r'(?<!pg_catalog\.)\b(pg_has_role|current_database)\(')
        import studio
        self.assertTrue(studio.role_sql(E, 'p' * 32).startswith(data_api.SAFE))

    def test_a_start_repairs_an_older_reserved_schema_guard(self):
        self.assertIn('ALTER FUNCTION sbarbase_guard.reserved_schemas() SET search_path = pg_catalog, pg_temp', data_api.guard_sql(E))
        self.assertIn("to_regprocedure('sbarbase_guard.reserved_schemas()')", data_api.guard_present_sql(E))

    def test_any_drop_of_a_schema_is_guarded_through_sql_drop(self):
        text = data_api.guard_sql(E)
        self.assertIn('ON sql_drop EXECUTE FUNCTION sbarbase_guard.exposed_schemas_dropped()', text)
        self.assertIn("pg_event_trigger_dropped_objects() WHERE object_type = 'schema'", text)
        self.assertIn("evtname = 'sbarbase_exposed_schemas_dropped'", data_api.guard_present_sql(E))

    def test_off_ignores_a_missing_search_path_schema(self):
        served, missing = data_api.effective({'schemas': [data_api.EMPTY], 'extra_search_path': ['public'], 'max_rows': None},
                                             {data_api.EMPTY})
        self.assertEqual((served['schemas'], missing), ([data_api.EMPTY], []))

    def test_guards_put_the_temporary_schema_last(self):
        self.assertIn('SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $guard$', data_api.guard_sql(E))
        self.assertNotIn('SET search_path = pg_catalog AS', durable_runtime.Runtime.developer_grants_sql(None, E))
        self.assertIn("search_path=pg_catalog, pg_temp", data_api.guard_present_sql(E))

    def test_the_pin_resets_everything_and_is_bounded(self):
        text = durable_runtime.rest_pin_sql(E)
        self.assertTrue(text.startswith(data_api.SAFE + durable_runtime.REST_PIN_BOUNDS))
        self.assertIn("lock_timeout = '5s'", text)
        self.assertIn(f'ALTER ROLE {E}_rest RESET ALL;', text)
        self.assertNotIn('pg_terminate_backend', text)
        self.assertTrue(text.rstrip().endswith("SET transaction_timeout = '12s'; COMMIT;"))
        # Ending the sessions, the pin and the password in one transaction: a session PostgREST opens again
        # between separate statements could hold the rows the next one writes (found in review).
        both = durable_runtime.rest_pin_sql(E, end_sessions=True, password='a' * 64)
        self.assertLess(both.index('BEGIN;'), both.index('pg_terminate_backend'))
        self.assertLess(both.index('pg_terminate_backend'), both.index('PASSWORD'))
        self.assertLess(both.index('PASSWORD'), both.index('RESET ALL'))
        self.assertTrue(both.rstrip().endswith('COMMIT;'))
        with self.assertRaises(RuntimeError):
            durable_runtime.rest_pin_sql(E, password="x'; DROP ROLE y; --")
        # A path that starts REST ends the login's sessions first, so none holds the rows open.
        ended = durable_runtime.rest_pin_sql(E, end_sessions=True)
        self.assertIn(f"a.usename = '{E}_rest'", ended)
        self.assertLess(ended.index('pg_terminate_backend'), ended.index('RESET ALL'))
        with self.assertRaises(RuntimeError):
            durable_runtime.rest_pin_sql('e_bad')
        # Guard SQL waits for nothing a tenant can hold open.
        self.assertIn("SET lock_timeout = '10s'", data_api.guard_sql(E))

    def test_every_statement_that_writes_the_rest_logins_role_settings_is_bounded(self):
        # A transaction a tenant leaves open on those rows must not hold a platform statement for ever.
        source = (Path(__file__).parent / 'durable_runtime.py').read_text()
        for line in source.splitlines():
            if 'REST_PINS.format' in line and 'def ' not in line:
                self.assertTrue('REST_PIN_BOUNDS' in line or line.strip().startswith('{REST_PINS.format'), line)
        statements = []
        runtime = object.__new__(durable_runtime.Runtime)
        with patch.object(durable_runtime, 'inspect', return_value=None):
            runtime.rest_deadlines(E, executor=lambda query, *a: statements.append(query) or SimpleNamespace(stdout='t'))
        self.assertTrue(statements[-1].startswith(data_api.SAFE + durable_runtime.REST_PIN_BOUNDS), statements[-1][:80])

    def test_a_start_gives_the_rest_login_its_password_back_but_a_save_does_not(self):
        password = 'ab' * 32
        runtime = object.__new__(durable_runtime.Runtime)
        runtime.values = {'environments': {E: {'rest': password}}}
        for end, expected in ((True, 1), (False, 0)):
            statements = []
            runtime.rest_deadlines(E, executor=lambda query, *a: statements.append(query) or SimpleNamespace(stdout=f'{E}:search_path=pg_catalog,{E}:statement_timeout=8s,{E}:transaction_timeout=12s'),
                                   rewrite=True, end_sessions=end)
            given = [q for q in statements if 'PASSWORD' in q]
            self.assertEqual(len(given), expected)
            if expected:
                self.assertTrue(given[0].startswith(data_api.SAFE + durable_runtime.REST_PIN_BOUNDS))
                self.assertIn(f"ALTER ROLE {E}_rest PASSWORD '{password}'", given[0])
        with self.assertRaises(RuntimeError):
            durable_runtime.rest_password_sql(E, "x'; DROP ROLE y; --")
        source = (Path(__file__).parent / 'target_runtime.py').read_text() + (Path(__file__).parent / 'recovery-check-services.py').read_text()
        self.assertEqual(source.count('rest_password_sql('), 2)
        # A start or rotation ends the login's sessions in the same transaction first: a request holding
        # its row would otherwise make the write wait, under the operation lock (found in review).
        runtime_source = (Path(__file__).parent / 'durable_runtime.py').read_text()
        self.assertIn('rest_password_sql(e, password, end_sessions=True)', runtime_source)
        text = durable_runtime.rest_password_sql(E, 'a' * 64, end_sessions=True)
        self.assertLess(text.index('BEGIN;'), text.index('pg_terminate_backend'))
        self.assertLess(text.index('pg_terminate_backend'), text.index('ALTER ROLE'))
        self.assertNotIn('pg_terminate_backend', durable_runtime.rest_password_sql(E, 'a' * 64))
        self.assertNotIn('studio.up(', runtime_source)

    def test_every_path_that_starts_rest_pins_first(self):
        root = Path(__file__).parent
        rotate = (root / 'durable_runtime.py').read_text()
        body = rotate[rotate.index('    def rotate_signing'):rotate.index('    def reconcile_mail')]
        self.assertLess(body.index('self.rest_deadlines(e, rewrite=True, end_sessions=True)'), body.index('self.activate_services'))
        target = (root / 'target_runtime.py').read_text()
        self.assertLess(target.index('runtime.rest_pin_sql(self.environment,end_sessions=True)'),
                        target.index("for kind in ('auth','rest','storage'):lab.docker('start'"))

    def test_pre_config_takes_only_the_managed_keys_from_settings(self):
        # A settings() brought by an archive may answer more (db_anon_role, jwt_secret, db_pre_request ...).
        text = data_api.guard_sql(E)
        body = text[text.index('CREATE OR REPLACE FUNCTION sbarbase_rest.pre_config()'):text.index('$pre$;')]
        self.assertEqual(body.count("WHERE s.key IN ('pgrst.db_schemas', 'pgrst.db_extra_search_path', 'pgrst.db_max_rows')"), 3)
        self.assertIn("WHERE key IN ('pgrst.db_schemas', 'pgrst.db_extra_search_path', 'pgrst.db_max_rows')", data_api.serving_sql(E))

    def test_pre_config_uses_scalar_variables_of_pg_catalog_types_and_checks_the_result(self):
        text = data_api.guard_sql(E)
        body = text[text.index('CREATE OR REPLACE FUNCTION sbarbase_rest.pre_config()'):text.index('$pre$;')]
        self.assertIn('DECLARE k pg_catalog.text; v pg_catalog.text; name pg_catalog.text; given pg_catalog.text[];', body)
        self.assertNotIn('record', body.replace('no record variable', ''))
        self.assertNotIn('item', body)
        # Defaults for an environment without settings, and the values in force checked afterwards.
        self.assertIn("set_config('pgrst.db_schemas', 'public', true)", body)
        self.assertIn('IS DISTINCT FROM v', body)

    def test_the_recovery_export_pins_the_rest_login_before_it_reads_role_settings(self):
        source = (Path(__file__).parent / 'recovery-export.py').read_text()
        self.assertLess(source.index('target.sql(runtime.rest_pin_sql(e)'), source.index('roles=rows(f"SELECT rolname,rolsuper'))
        self.assertLess(source.index('target.sql(runtime.rest_pin_sql(e)'), source.index('settings=rows(settings_query'))

    def test_a_container_is_recognised_by_carrying_the_whole_container_environment(self):
        full = [f'{key}={value}' for key, value in data_api.CONTAINER_ENV.items()] + ['PGRST_DB_POOL=3']
        self.assertTrue(data_api.carries_container_env(full))
        self.assertFalse(data_api.carries_container_env(['PGRST_DB_SCHEMAS=public', 'PGRST_DB_POOL=3']))
        self.assertFalse(data_api.carries_container_env(full[1:]))
        self.assertFalse(data_api.carries_container_env(None))
        target = (Path(__file__).parent / 'target_runtime.py').read_text()
        self.assertLess(target.index('carries_container_env'), target.index("for kind in ('auth','rest','storage'):lab.docker('start'"))

    def test_a_data_api_change_that_finds_the_operation_lock_taken_is_asked_again_not_failed(self):
        source = (Path(__file__).parent / 'durable_runtime.py').read_text()
        self.assertIn("if args.command=='data-api':raise SystemExit(75)", source)
        with patch.object(data_api, 'busy', return_value=False), patch.object(data_api, 'load', return_value={'schemas': ['public'], 'extra_search_path': ['public'], 'max_rows': None}), \
                patch.object(data_api.subprocess, 'run', return_value=SimpleNamespace(returncode=75, stderr='', stdout='')), \
                patch.object(data_api, 'record') as record:
            self.assertEqual(data_api.apply(E), 75)
        record.assert_not_called()

    def test_every_rest_container_falls_back_to_the_empty_schema_not_public(self):
        self.assertEqual(data_api.CONTAINER_ENV['PGRST_DB_SCHEMAS'], data_api.EMPTY)
        self.assertEqual(data_api.CONTAINER_ENV['PGRST_DB_EXTRA_SEARCH_PATH'], data_api.EMPTY)
        self.assertEqual(data_api.CONTAINER_ENV['PGRST_DB_PRE_CONFIG'], data_api.PRE_CONFIG)
        self.assertEqual(durable_runtime.TUNABLE['rest'], frozenset(data_api.CONTAINER_ENV))
        self.assertTrue(durable_runtime.settings_only(
            'rest', {'PGRST_DB_SCHEMAS': 'public', 'PGRST_DB_POOL': '3'}, {**data_api.CONTAINER_ENV, 'PGRST_DB_POOL': '3'}))
        source = (Path(__file__).parent / 'durable_runtime.py').read_text()
        self.assertIn('config = {**config, **data_api.CONTAINER_ENV}', source)

    def test_a_write_neither_notifies_nor_ends_connections(self):
        # A notification is not part of the transaction (a full cluster queue would roll the settings back), and
        # ending the REST login's connections made PostgREST fall back to its empty schema for about 3 s on
        # every save (measured in review). Connections are ended only on paths that start REST (rest_pin_sql).
        text = data_api.write_sql(E, {'schemas': ['public'], 'extra_search_path': ['public'], 'max_rows': None})
        self.assertNotIn('NOTIFY', text)
        self.assertNotIn('pg_terminate_backend', text.replace(data_api.release_sql(), ''))
        self.assertFalse(hasattr(data_api, 'rest_sessions_sql'))

    def test_no_extension_belongs_in_the_platforms_schemas(self):
        text = data_api.guard_sql(E)
        self.assertIn("extnamespace IN (pg_catalog.to_regnamespace('sbarbase_rest'), pg_catalog.to_regnamespace('sbarbase_guard'))", text)
        self.assertIn('hold no extension', text)
        self.assertIn("SET lock_timeout = '5s'", data_api.reset_sql(E))

    def test_the_platforms_schemas_must_belong_to_a_superuser(self):
        text = data_api.guard_sql(E)
        self.assertIn("n.nspname IN ('sbarbase_rest', 'sbarbase_guard', 'sbarbase_empty') AND NOT r.rolsuper", text)
        self.assertIn('must belong to a superuser', text)
        self.assertLess(text.index('must belong to a superuser'), text.index('CREATE SCHEMA IF NOT EXISTS sbarbase_guard'))
        self.assertIn('NOT (EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n', data_api.guard_present_sql(E))

    def test_a_restored_target_may_differ_from_an_old_archive_only_in_the_pin(self):
        import recovery_boundaries
        pinned = [{'role': f'{E}_rest', 'database': E, 'setconfig': ['search_path=pg_catalog', 'statement_timeout=8s']}]
        old = [{'role': f'{E}_rest', 'database': E, 'setconfig': ['statement_timeout=8s']}]
        self.assertEqual(recovery_boundaries.without_pin(pinned, E), recovery_boundaries.without_pin(old, E))
        # What a tenant set on that login before the export is rewritten by the pin, so it is not compared either.
        touched = [{'role': f'{E}_rest', 'database': E, 'setconfig': ['search_path=public', 'statement_timeout=0']}]
        self.assertEqual(recovery_boundaries.without_pin(touched, E), recovery_boundaries.without_pin(old, E))
        # Any other setting, or the same setting on another login, still shows.
        other = [{'role': f'{E}_rest', 'database': E, 'setconfig': ['work_mem=64kB', 'statement_timeout=8s']}]
        self.assertNotEqual(recovery_boundaries.without_pin(other, E), recovery_boundaries.without_pin(old, E))
        elsewhere = [{'role': f'{E}_auth', 'database': E, 'setconfig': ['search_path=public']}]
        self.assertNotEqual(recovery_boundaries.without_pin(elsewhere, E), recovery_boundaries.without_pin(old, E))
        only_pin = [{'role': f'{E}_rest', 'database': E, 'setconfig': ['search_path=pg_catalog']}]
        self.assertEqual(recovery_boundaries.without_pin(only_pin, E), [])

    def test_pre_config_checks_the_callers_search_path_and_pins_none_of_its_own(self):
        text = data_api.guard_sql(E)
        body = text[text.index('CREATE OR REPLACE FUNCTION sbarbase_rest.pre_config()'):text.index('$pre$;')]
        self.assertNotIn('SET search_path', body)
        self.assertIn("pg_catalog.current_setting('search_path') OPERATOR(pg_catalog.<>) 'pg_catalog'", body)

    def test_pre_config_covers_every_key_postgrest_reads_from_the_database(self):
        text = data_api.guard_sql(E)
        self.assertEqual(len(data_api.PGRST_KEYS), 23)
        for key in data_api.PGRST_KEYS:
            self.assertIn(f"'pgrst.{key}'", text)
        # Only the three the platform writes may be empty; the guard presence check knows the new function.
        self.assertIn("name IN ('pgrst.db_schemas', 'pgrst.db_extra_search_path')", text)
        self.assertIn("name OPERATOR(pg_catalog.<>) 'pgrst.db_max_rows'", text)
        self.assertIn('search path is not pg_catalog', data_api.guard_present_sql(E))

    def test_settings_are_reachable_only_by_this_rest_login(self):
        text = data_api.guard_sql(E)
        self.assertIn('REVOKE ALL ON SCHEMA sbarbase_rest FROM PUBLIC;', text)
        self.assertIn('REVOKE ALL ON FUNCTION sbarbase_rest.settings() FROM PUBLIC;', text)
        self.assertIn(f'GRANT USAGE ON SCHEMA sbarbase_rest TO {E}_rest;', text)
        # The guard runs in the tenant's session and reads them as its owner; pre_config runs as the REST login.
        self.assertEqual(text.count('LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp'), 2)
        self.assertIn('LANGUAGE plpgsql AS $pre$', text)
        # Created once: a start never resets what an earlier write left.
        self.assertIn("IF pg_catalog.to_regprocedure('sbarbase_rest.settings()') IS NULL THEN", text)
        present = data_api.guard_present_sql(E)
        self.assertIn('prosecdef', present)
        self.assertIn(f"has_function_privilege('{E}_rest', to_regprocedure('sbarbase_rest.pre_config()'), 'EXECUTE')", present)
        self.assertNotIn('pg_db_role_setting', text + present)

    def test_guard_keeps_the_empty_schema_empty(self):
        text = data_api.guard_sql(E)
        self.assertIn("'CREATE EXTENSION', 'ALTER EXTENSION'", text)
        self.assertIn(f"extnamespace = pg_catalog.to_regnamespace('{data_api.EMPTY}')", text)

    def test_missing_search_path_schema_is_reported(self):
        self.assertEqual(data_api.effective({'schemas': ['public'], 'extra_search_path': ['public', 'gone'], 'max_rows': None},
                                            {'public'})[1], ['gone'])

    def test_effective_never_falls_back_to_public(self):
        # A hardened app exposes only `api`; with `api` gone, `public` must stay unpublished.
        served, missing = data_api.effective({'schemas': ['api'], 'extra_search_path': ['gone'], 'max_rows': 5}, {'public'})
        self.assertEqual((served['schemas'], served['extra_search_path'], served['max_rows'], missing),
                         ([data_api.EMPTY], [data_api.EMPTY], 5, ['api', 'gone']))
        self.assertIn(f"('pgrst.db_schemas', '{data_api.EMPTY}')", data_api.settings_sql(E, served))

    def test_a_missing_public_is_left_out_of_the_search_path_too(self):
        served, missing = data_api.effective({'schemas': ['app'], 'extra_search_path': ['public'], 'max_rows': None}, {'app'})
        self.assertEqual((served['extra_search_path'], missing), ([data_api.EMPTY], ['public']))
        self.assertIn(f"('pgrst.db_extra_search_path', '{data_api.EMPTY}')", data_api.settings_sql(E, served))
        self.assertIn("('pgrst.db_schemas', 'app')", data_api.settings_sql(E, served))

    def test_no_schema_turns_the_data_api_off(self):
        self.assertIn(f'CREATE SCHEMA IF NOT EXISTS {data_api.EMPTY}', data_api.guard_sql(E))
        self.assertEqual(data_api.validate({'schemas': []})['schemas'], [data_api.EMPTY])
        self.assertEqual(data_api.validate({'schemas': [data_api.EMPTY]})['schemas'], [data_api.EMPTY])
        # Never beside other schemas.
        with self.assertRaises(data_api.SettingsError):
            data_api.validate({'schemas': ['public', data_api.EMPTY]})
        self.assertIn(f"('pgrst.db_schemas', '{data_api.EMPTY}')",
                      data_api.settings_sql(E, {'schemas': [], 'extra_search_path': ['public'], 'max_rows': None}))

    def test_every_statement_starts_from_pg_catalog(self):
        # A tenant's public.unnest(text[]) must never run as the superuser.
        settings = {'schemas': ['public'], 'extra_search_path': ['public'], 'max_rows': None}
        for text in (data_api.serving_sql(E), data_api.present_sql(settings), data_api.guard_present_sql(E),
                     data_api.guard_sql(E), data_api.reset_sql(E)):
            self.assertTrue(text.startswith(data_api.SAFE), text[:60])
        for claim in (False, True):
            text = data_api.write_sql(E, settings, claim=claim)
            self.assertTrue(text.startswith('BEGIN; SET LOCAL search_path TO pg_catalog, pg_temp;'))
            self.assertNotRegex(text, r'(?<!pg_catalog\.)\b(unnest|count)\(')


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.catalog = Path(directory.name) / 'control.sqlite'
        self.runtime = object.__new__(durable_runtime.Runtime)
        self.statements = []
        self.existing = 'public\ni2s\nextensions\n'
        # The pgrst settings sbarbase_rest.settings() answers now, and whether the guard is in place.
        self.current = ''
        self.guarded = 't'

        # Answers for successive locked writes: (returncode, stderr); empty means success.
        self.writes_answer = []

        def sql(query, database='postgres', check=True):
            self.statements.append((database, query))
            if query.startswith('BEGIN;'):
                code, error = self.writes_answer.pop(0) if self.writes_answer else (0, '')
                if not code:
                    # A committed write is what the database holds from now on.
                    found = re.findall(r"\('(pgrst\.[a-z_]+)', '([^']*)'\)", query)
                    self.current = ''.join(f'{key}={value}\n' for key, value in sorted(found))
                return SimpleNamespace(stdout='', stderr=error, returncode=code)
            answer = {data_api.serving_sql(E): self.current, data_api.guard_present_sql(E): self.guarded}.get(query, '')
            if "coalesce(d.datname, '*')" in query:
                # The REST login's role settings, as provisioning leaves them (Runtime.rest_deadlines).
                answer = f'{E}:search_path=pg_catalog,{E}:statement_timeout=8s,{E}:transaction_timeout=12s'
            if query.startswith(data_api.SAFE + 'SELECT name FROM pg_catalog.unnest'):
                # As the database answers present_sql: the asked names that exist.
                answer = '\n'.join(name for name in self.existing.split() if f"'{name}'" in query)
            return SimpleNamespace(stdout=answer, returncode=0)
        self.runtime.sql = sql
        self.runtime.tell_rest = lambda e: True
        # The runtime reads the catalog in its own state directory.
        patcher = patch.object(durable_runtime, 'STATE', self.catalog.parent)
        patcher.start()
        self.addCleanup(patcher.stop)

    def joined(self):
        return '\n'.join(query for _, query in self.statements)

    def test_every_settings_write_pins_the_rest_login_first(self):
        # A restored archive from before the pin must not reach REST without it: pre_config would refuse
        # at startup and PostgREST would serve its container's `public`.
        catalog(self.catalog, state='applied', applied=('public,i2s', 'public', None))
        self.current = 'pgrst.db_schemas=public,private\n'
        pins = []
        original = self.runtime.rest_deadlines
        self.runtime.rest_deadlines = lambda e, executor=None, validate_only=False, rewrite=False, end_sessions=False: (pins.append(len(self.statements)), original(e, executor, validate_only, rewrite, end_sessions))[1]
        self.runtime.data_api(E, refuse_missing=False)
        self.assertEqual(len(pins), 1)
        first_write = next(index for index, (_, query) in enumerate(self.statements) if query.startswith('BEGIN;'))
        self.assertLess(pins[0], first_write)

    def test_a_rest_container_that_cannot_be_signalled_leaves_the_settings_recorded_with_a_report(self):
        # The cluster's notification queue is not used at all; the container is signalled. If that fails
        # the write has been committed, so the catalog records what the database now holds.
        catalog(self.catalog)
        self.runtime.tell_rest = lambda e: False
        self.assertEqual(self.runtime.data_api(E), [])
        self.assertTrue(any(q.startswith('BEGIN;') for _, q in self.statements))
        self.assertNotIn('NOTIFY', self.joined())
        self.assertEqual(served(self.catalog)[0], 'public,i2s')
        self.assertIn('could not be told to reload', report(self.catalog))
        self.assertEqual(stored(self.catalog)[1], 'pending')

    def test_tell_rest_signals_the_environments_own_container_only_when_it_runs(self):
        calls = []
        runtime = object.__new__(durable_runtime.Runtime)
        with patch.object(durable_runtime, 'inspect', return_value=None), \
                patch.object(durable_runtime.lab, 'docker', lambda *a, **k: calls.append(a)):
            self.assertTrue(runtime.tell_rest(E))
        self.assertEqual(calls, [])
        running = {'State': {'Running': True}}
        name = durable_runtime.PREFIX + '-' + E + '-rest'
        with patch.object(durable_runtime, 'inspect', return_value=running), \
                patch.object(durable_runtime.lab, 'docker', lambda *a, **k: calls.append(a)):
            self.assertTrue(runtime.tell_rest(E))
        self.assertEqual(calls, [('kill', '-s', 'SIGUSR1', name)])

        def broken(*a, **k):
            raise RuntimeError('docker down')
        with patch.object(durable_runtime, 'inspect', return_value=running), patch.object(durable_runtime.lab, 'docker', broken):
            self.assertFalse(runtime.tell_rest(E))
        # Not being able to look at the container is the same: the committed change is not turned into a failure.
        with patch.object(durable_runtime, 'inspect', side_effect=RuntimeError('Runtime resource inspection unavailable')):
            self.assertFalse(runtime.tell_rest(E))

    def test_a_read_back_that_fails_after_the_commit_does_not_turn_the_change_into_a_failure(self):
        catalog(self.catalog)
        original = self.runtime.sql

        def sql(query, database='postgres', check=True):
            # After the committed write, the read of what is served fails once.
            if query == data_api.serving_sql(E) and any(q.startswith('BEGIN;') for _, q in self.statements) and not getattr(sql, 'failed', False):
                sql.failed = True
                return SimpleNamespace(stdout='', stderr='connection reset', returncode=1)
            return original(query, database, check)
        self.runtime.sql = sql
        self.assertEqual(self.runtime.data_api(E), [])
        self.assertEqual(served(self.catalog)[0], 'public,i2s')
        self.assertIsNone(report(self.catalog))

    def test_the_restore_target_container_is_started_with_the_settings_function_installed_and_pinned(self):
        source = (Path(__file__).parent / 'recovery-check-services.py').read_text()
        self.assertIn("if kind=='rest':config={**config,**data_api.CONTAINER_ENV}", source)
        # An archive from before this change has neither the function nor the empty schema: install first.
        self.assertLess(source.index('query(data_api.guard_sql(e))'), source.index("lab.docker('run','-d','--pull','never'"))
        self.assertLess(source.index('query(runtime.rest_pin_sql(e,end_sessions=True))'), source.index("lab.docker('run','-d','--pull','never'"))
        target = (Path(__file__).parent / 'target_runtime.py').read_text()
        start = target.index("lab.docker('start',self.identities['db'])")
        self.assertLess(target.index('data_api.guard_sql(self.environment)', start), target.index("for kind in ('auth','rest','storage'):lab.docker('start'"))

    def test_applies_saved_settings_in_environment_database(self):
        catalog(self.catalog)
        self.assertEqual(self.runtime.data_api(E), [])
        # The REST login's role settings are cluster wide rows, read and written from the default database.
        self.assertTrue(all(database == E or 'pg_db_role_setting' in query for database, query in self.statements))
        self.assertIn("('pgrst.db_schemas', 'public,i2s')", self.joined())
        self.assertIn('sbarbase_exposed_schemas', self.joined())
        self.assertNotIn('NOTIFY', self.joined())

    def test_console_change_with_missing_schema_refuses_and_changes_nothing(self):
        catalog(self.catalog, schemas='public,app')
        with self.assertRaisesRegex(durable_runtime.DataApiRefusal, 'The schema app does not exist'):
            self.runtime.data_api(E)
        self.assertNotIn("VALUES ('pgrst.db_schemas'", self.joined())

    def writes(self):
        # The session's search path is set first and changes nothing in the database.
        return [query for _, query in self.statements if not query.removeprefix(data_api.SAFE).startswith('SELECT')]

    def test_start_that_matches_only_reads(self):
        catalog(self.catalog, state='applied', applied=('public,i2s', 'public,extensions', None))
        self.current = 'pgrst.db_extra_search_path=public,extensions\npgrst.db_schemas=public,i2s\n'
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), [])
        self.assertEqual(self.writes(), [])

    def test_start_installs_a_missing_guard(self):
        self.guarded = 'f'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertTrue(any('sbarbase_exposed_schemas' in query for query in self.writes()))

    def test_resume_leaves_missing_schema_out_and_records_it(self):
        catalog(self.catalog, schemas='app,public', state='applied', applied=('app,public', 'public', None))
        self.current = 'pgrst.db_schemas=app,public\n'
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), ['app'])
        self.assertIn("('pgrst.db_schemas', 'public')", self.joined())
        self.assertEqual(stored(self.catalog), ('app,public', 'applied', None))
        # The console and GET show what is served, not what was applied, and the start says why.
        self.assertEqual(served(self.catalog), ('public', 'public'))
        self.assertTrue(report(self.catalog).startswith(data_api.LEFT_OUT))
        self.assertIn('app', report(self.catalog))

    def test_refused_change_is_not_served_after_a_restart(self):
        # The reviewer's case: `public,app` is served, `public,typo` was saved and refused.
        self.existing = 'public\napp\n'
        self.current = 'pgrst.db_schemas=public\n'
        catalog(self.catalog, schemas='public,typo', state='failed', applied=('public,app', 'public', None),
                failure='The schema typo does not exist')
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), [])
        self.assertIn("('pgrst.db_schemas', 'public,app')", self.joined())
        self.assertNotIn('typo', self.joined())
        self.assertEqual(stored(self.catalog), ('public,app', 'failed', 'The schema typo does not exist'))

    def test_refusal_survives_a_start_that_leaves_a_schema_out(self):
        # A refused `public,typo` beside a served `public,app`; `app` goes missing, then returns.
        catalog(self.catalog, schemas='public,typo', state='failed', applied=('public,app', 'public', None),
                failure='The schema typo does not exist')
        self.existing, self.current = 'public\n', 'pgrst.db_schemas=public,app\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertEqual(stored(self.catalog)[1:], ('failed', 'The schema typo does not exist'))
        self.existing, self.current = 'public\napp\n', 'pgrst.db_schemas=public\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertEqual(stored(self.catalog)[1:], ('failed', 'The schema typo does not exist'))
        self.assertEqual(served(self.catalog)[0], 'public,app')

    def test_pending_change_keeps_pending_when_a_start_leaves_a_schema_out(self):
        catalog(self.catalog, schemas='public,new', state='pending', applied=('public,app', 'public', None))
        self.existing = 'public\n'
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), ['app'])
        self.assertEqual(stored(self.catalog)[1:], ('pending', None))
        self.assertEqual(served(self.catalog), ('public', 'public'))

    def test_a_schema_dropped_during_the_write_is_worked_out_again(self):
        catalog(self.catalog, state='applied', applied=('public,i2s', 'public', None))
        self.current = 'pgrst.db_schemas=public\n'
        self.writes_answer = [(3, f'ERROR:  {data_api.RACE}')]
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), [])
        locked = [query for _, query in self.statements if query.startswith('BEGIN;')]
        self.assertEqual(len(locked), 2)
        self.assertIn(f'pg_try_advisory_xact_lock({data_api.LOCK_KEY})', locked[0])

    def test_a_start_that_cannot_write_says_so_and_records_what_is_served(self):
        catalog(self.catalog, state='applied', applied=('public,i2s', 'public', None))
        self.current = 'pgrst.db_schemas=public\n'
        self.writes_answer = [(3, 'ERROR:  could not write')]
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), [])
        # Nothing new was written: what the database serves is what is recorded, and the report says why.
        self.assertEqual(served(self.catalog)[0], 'public')
        self.assertIn('could not be written', report(self.catalog))
        self.assertEqual(stored(self.catalog)[1:], ('applied', None))

    def test_a_start_ends_tenant_sessions_that_hold_the_lock(self):
        catalog(self.catalog, state='applied', applied=('public,i2s', 'public', None))
        self.current = 'pgrst.db_schemas=public,private\n'
        self.writes_answer = [(3, data_api.BUSY)]
        with patch.object(durable_runtime.time, 'sleep'):
            self.runtime.data_api(E, refuse_missing=False)
        claimed = [query for _, query in self.statements if query.startswith('BEGIN;')][1]
        self.assertIn(data_api.release_sql(), claimed)
        self.assertLess(claimed.index('pg_terminate_backend'), claimed.index(f'pg_advisory_xact_lock({data_api.LOCK_KEY})'))
        self.assertNotIn('l.granted', data_api.release_sql())
        self.assertEqual(served(self.catalog)[0], 'public,i2s')
        self.assertIsNone(report(self.catalog))

    def test_a_held_lock_is_tried_briefly_and_never_waited_on(self):
        catalog(self.catalog)
        self.writes_answer = [(3, data_api.BUSY)] * 3
        with patch.object(durable_runtime.time, 'sleep') as slept, \
                self.assertRaisesRegex(durable_runtime.DataApiRefusal, 'holds its lock'):
            self.runtime.data_api(E)
        self.assertEqual(slept.call_count, 3)

    def test_guard_covers_the_extra_search_path(self):
        self.assertIn("FILTER (WHERE key = 'pgrst.db_extra_search_path')", data_api.guard_sql(E))

    def test_a_console_change_that_keeps_racing_is_refused(self):
        catalog(self.catalog)
        self.writes_answer = [(3, data_api.RACE)] * 3
        with self.assertRaisesRegex(durable_runtime.DataApiRefusal, 'dropped meanwhile'):
            self.runtime.data_api(E)

    def test_busy_catalog_refuses_rather_than_resetting(self):
        catalog(self.catalog, state='applied', applied=('i2s', 'public', None))
        with patch.object(data_api.sqlite3, 'connect', side_effect=sqlite3.OperationalError('database is locked')), \
                patch.object(data_api.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'catalog could not be read'):
                self.runtime.data_api(E, refuse_missing=False)
        self.assertEqual(self.writes(), [])

    def test_a_moved_database_keeps_valid_settings_it_brings(self):
        # Restored on a new installation: no catalog row, the database's own settings.
        with sqlite3.connect(self.catalog) as database_:
            database_.execute('CREATE TABLE data_api_settings(runtime TEXT PRIMARY KEY, schemas TEXT NOT NULL, extra_search_path TEXT NOT NULL, '
                              'max_rows INTEGER, applied_schemas TEXT, applied_extra_search_path TEXT, applied_max_rows INTEGER, '
                              'served_schemas TEXT, served_extra_search_path TEXT, served_max_rows INTEGER, start_report TEXT, '
                              'state TEXT NOT NULL, failure TEXT, actor TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        self.current = 'pgrst.db_extra_search_path=public\npgrst.db_schemas=public,i2s\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertNotIn('WHERE false $settings$', self.joined())
        self.assertEqual(stored(self.catalog), ('public,i2s', 'applied', None))
        self.assertEqual(data_api.load(E, self.catalog)['schemas'], ['public', 'i2s'])

    def test_a_moved_database_naming_a_missing_schema_is_adopted_and_reported_not_reset(self):
        with sqlite3.connect(self.catalog) as database_:
            database_.execute('CREATE TABLE data_api_settings(runtime TEXT PRIMARY KEY, schemas TEXT NOT NULL, extra_search_path TEXT NOT NULL, '
                              'max_rows INTEGER, applied_schemas TEXT, applied_extra_search_path TEXT, applied_max_rows INTEGER, '
                              'served_schemas TEXT, served_extra_search_path TEXT, served_max_rows INTEGER, start_report TEXT, '
                              'state TEXT NOT NULL, failure TEXT, actor TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        self.existing = f'public\n{data_api.EMPTY}\n'
        self.current = 'pgrst.db_schemas=api\n'
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), ['api'])
        self.assertNotIn('WHERE false $settings$', self.joined())
        # Never `public` in its place: the empty schema is served and the start says why.
        self.assertEqual(served(self.catalog)[0], data_api.EMPTY)
        self.assertTrue(report(self.catalog).startswith(data_api.LEFT_OUT))

    def test_a_moved_database_that_was_turned_off_stays_off(self):
        with sqlite3.connect(self.catalog) as database_:
            database_.execute('CREATE TABLE data_api_settings(runtime TEXT PRIMARY KEY, schemas TEXT NOT NULL, extra_search_path TEXT NOT NULL, '
                              'max_rows INTEGER, applied_schemas TEXT, applied_extra_search_path TEXT, applied_max_rows INTEGER, '
                              'served_schemas TEXT, served_extra_search_path TEXT, served_max_rows INTEGER, start_report TEXT, '
                              'state TEXT NOT NULL, failure TEXT, actor TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        self.existing = f'public\n{data_api.EMPTY}\n'
        self.current = f'pgrst.db_schemas={data_api.EMPTY}\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertNotIn('WHERE false $settings$', self.joined())
        self.assertEqual(stored(self.catalog), (data_api.EMPTY, 'applied', None))
        self.assertEqual(served(self.catalog)[0], data_api.EMPTY)

    def test_a_moved_database_whose_row_appears_meanwhile_keeps_what_it_brought(self):
        self.current = 'pgrst.db_schemas=public,i2s\n'
        with patch.object(data_api, 'adopt', return_value=False), \
                patch.object(data_api, 'row', return_value=(None, None, None, None)):
            self.runtime.data_api(E, refuse_missing=False)
        self.assertEqual(self.writes(), [])

    def test_a_catalog_that_cannot_record_the_adoption_never_resets(self):
        sqlite3.connect(self.catalog).close()
        self.current = 'pgrst.db_schemas=public,i2s\n'
        with patch.object(data_api, 'row', return_value=(None, None, None, None)), \
                patch.object(data_api.sqlite3, 'connect', side_effect=sqlite3.OperationalError('database is locked')):
            with self.assertRaisesRegex(RuntimeError, 'could not record'):
                self.runtime.data_api(E, refuse_missing=False)
        self.assertNotIn('WHERE false $settings$', self.joined())

    def test_a_moved_database_with_invalid_settings_is_reset(self):
        with sqlite3.connect(self.catalog) as database_:
            database_.execute('CREATE TABLE data_api_settings(runtime TEXT PRIMARY KEY, schemas TEXT NOT NULL, extra_search_path TEXT NOT NULL, '
                              'max_rows INTEGER, applied_schemas TEXT, applied_extra_search_path TEXT, applied_max_rows INTEGER, '
                              'served_schemas TEXT, served_extra_search_path TEXT, served_max_rows INTEGER, start_report TEXT, '
                              'state TEXT NOT NULL, failure TEXT, actor TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        self.current = 'pgrst.db_schemas=public,vault\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertIn('WHERE false $settings$', self.joined())

    def test_without_settings_a_missing_public_serves_the_empty_schema(self):
        catalog(self.catalog, schemas='public,new', state='pending')
        self.existing = f'{data_api.EMPTY}\n'
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), ['public'])
        self.assertIn(f"('pgrst.db_schemas', '{data_api.EMPTY}')", self.joined())
        self.assertEqual(served(self.catalog)[0], data_api.EMPTY)
        self.assertIn('no table is served', report(self.catalog))
        self.assertEqual(stored(self.catalog)[1], 'pending')

    def test_never_configured_and_public_missing_serves_public_again_once_it_exists(self):
        # No row: the defaults are recorded, so the empty schema served meanwhile is never later
        # adopted as a moved database's settings and the environment does not stay off.
        catalog(self.catalog)
        with sqlite3.connect(self.catalog) as database_:
            database_.execute('DELETE FROM data_api_settings')
        self.existing = f'{data_api.EMPTY}\n'
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), ['public'])
        self.assertEqual(served(self.catalog)[0], data_api.EMPTY)
        self.assertIn('no table is served', report(self.catalog))
        self.assertEqual(stored(self.catalog), ('public', 'applied', None))
        self.existing = f'public\n{data_api.EMPTY}\n'
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), [])
        self.assertEqual(served(self.catalog), ('public', 'public'))
        self.assertIsNone(report(self.catalog))

    def test_a_missing_public_without_settings_claims_a_held_lock(self):
        catalog(self.catalog)
        with sqlite3.connect(self.catalog) as database_:
            database_.execute('DELETE FROM data_api_settings')
        self.existing = f'{data_api.EMPTY}\n'
        self.current = 'pgrst.db_schemas=public\n'
        self.writes_answer = [(3, data_api.BUSY)]
        with patch.object(durable_runtime.time, 'sleep'):
            self.runtime.data_api(E, refuse_missing=False)
        claimed = [query for _, query in self.statements if query.startswith('BEGIN;')][1]
        self.assertIn(data_api.release_sql(), claimed)
        self.assertEqual(served(self.catalog)[0], data_api.EMPTY)
        self.assertIn('no table is served', report(self.catalog))

    def test_saved_but_never_applied_keeps_container_defaults_at_start(self):
        catalog(self.catalog, schemas='public,i2s', state='pending')
        self.current = 'pgrst.db_schemas=public,i2s\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertIn('WHERE false $settings$', self.joined())

    def test_every_start_replaces_its_own_report(self):
        catalog(self.catalog, state='applied', applied=('public,i2s', 'public', None))
        self.existing = 'public\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertTrue(report(self.catalog).startswith(data_api.LEFT_OUT))
        self.existing = 'public\ni2s\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertIsNone(report(self.catalog))
        # The saved state is the console's and never changes here.
        self.assertEqual(stored(self.catalog), ('public,i2s', 'applied', None))

    def test_a_start_never_touches_a_change_saved_meanwhile(self):
        # The console saves while a start runs: the start writes only served and its report.
        catalog(self.catalog, state='applied', applied=('public,i2s', 'public', None))
        self.existing, self.current = 'public\n', 'pgrst.db_schemas=public,i2s\n'
        original = self.runtime.sql

        def save_meanwhile(query, database='postgres', check=True):
            if query.startswith('BEGIN;'):
                with sqlite3.connect(self.catalog) as database_:
                    database_.execute("UPDATE data_api_settings SET schemas='public,new', state='pending'")
            return original(query, database, check)
        self.runtime.sql = save_meanwhile
        self.runtime.data_api(E, refuse_missing=False)
        self.assertTrue(report(self.catalog).startswith(data_api.LEFT_OUT))
        with sqlite3.connect(self.catalog) as database_:
            self.assertEqual(database_.execute('SELECT schemas, state FROM data_api_settings').fetchone(), ('public,new', 'pending'))

    def test_a_reset_checks_public_again_under_the_lock(self):
        # The reset to the defaults went out without the lock or a second check, so `public` dropped in
        # between was served while gone and PostgREST answered 503 (found in review). Now it is the
        # locked write; a schema gone by then makes the defaults go through the write that serves the empty schema.
        catalog(self.catalog)
        with sqlite3.connect(self.catalog) as database:
            database.execute('DELETE FROM data_api_settings')
        self.current = 'pgrst.db_schemas=public,auth\n'
        self.writes_answer = [(1, data_api.RACE)]
        original = self.runtime.sql

        def drop_meanwhile(query, database='postgres', check=True):
            answer = original(query, database, check)
            if query.startswith('BEGIN;'):
                # The tenant dropped `public` after the first check: the locked write saw it gone.
                self.existing = 'i2s\n'
            return answer
        self.runtime.sql = drop_meanwhile
        self.runtime.data_api(E, refuse_missing=False)
        writes = [query for _, query in self.statements if query.startswith('BEGIN;')]
        self.assertIn('WHERE false $settings$', writes[0])
        self.assertIn('advisory_xact_lock', writes[0])
        self.assertIn(f"('pgrst.db_schemas', '{data_api.EMPTY}')", writes[-1])
        self.assertTrue(report(self.catalog))

    def test_a_busy_reset_is_reported_and_the_start_goes_on(self):
        # A reset that cannot take the lock stopped every start (found in review); it is a write like any other.
        catalog(self.catalog)
        with sqlite3.connect(self.catalog) as database:
            database.execute('DELETE FROM data_api_settings')
        self.current = 'pgrst.db_schemas=public,auth\n'
        self.writes_answer = [(1, data_api.BUSY), (1, 'lock timeout'), (1, 'lock timeout')]
        with patch.object(durable_runtime.time, 'sleep'):
            self.runtime.data_api(E, refuse_missing=False)
        self.assertEqual(len([query for _, query in self.statements if query.startswith('BEGIN;')]), 3)

    def test_without_row_resets_to_container_defaults(self):
        self.assertEqual(self.runtime.data_api(E, refuse_missing=False), [])
        self.assertEqual(self.writes(), [])
        # Settings no rule admits are reset; valid ones a moved database brings are kept.
        self.current = 'pgrst.db_schemas=public,auth\n'
        self.runtime.data_api(E, refuse_missing=False)
        self.assertIn('WHERE false $settings$', self.joined())
        with self.assertRaises(durable_runtime.DataApiRefusal):
            self.runtime.data_api(E)

    def test_console_change_checks_the_environment_first(self):
        catalog(self.catalog)
        self.runtime.values = {'environments': {E: {}}}
        checks = {'settled': [], 'fenced': False, 'published': {E: {}}}
        with patch.object(durable_runtime.effect_receipt, 'require_settled', side_effect=lambda state: checks['settled'].append(1)), \
                patch.object(durable_runtime, 'inspect', return_value={'Id': 'db'}), \
                patch.object(durable_runtime, 'published_endpoints', side_effect=lambda: checks['published']), \
                patch.object(durable_runtime.source_fence, 'is_fenced', side_effect=lambda sql, e: checks['fenced']):
            self.runtime.data_api_change(E)
            self.assertEqual(checks['settled'], [1])
            self.statements.clear()
            checks['fenced'] = True
            with self.assertRaisesRegex(RuntimeError, 'fenced'):
                self.runtime.data_api_change(E)
            checks['fenced'], checks['published'] = False, {}
            with self.assertRaisesRegex(RuntimeError, 'published'):
                self.runtime.data_api_change(E)
        self.assertEqual(self.statements, [])
        self.assertNotIn("VALUES ('pgrst.db_schemas'", self.joined())

    def console_change(self, told=None):
        """durable_runtime.data_api_command against this fixture, with both locks in the test's directory.
        Returns what each statement, check and signal saw: whether the operation lock was held then."""
        import fcntl
        catalog(self.catalog)
        seen = []
        lock = patch.object(data_api, 'OPERATION_LOCK', self.catalog.parent / 'operation.lock')
        lock.start()
        self.addCleanup(lock.stop)

        def held():
            with (self.catalog.parent / 'operation.lock').open('a') as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return False
                except BlockingIOError:
                    return True
        original = self.runtime.sql

        def sql(query, database='postgres', check=True):
            seen.append(('sql', query, held()))
            return original(query, database, check)
        self.runtime.sql = sql
        self.runtime.data_api_checks = lambda e: seen.append(('checks', None, held()))
        self.runtime.tell_rest = lambda e: seen.append(('told', None, held())) or (told() if told else True)
        with patch.object(durable_runtime, 'studio_running', return_value=False), \
                patch.object(durable_runtime, 'restart_studio', side_effect=lambda e: seen.append(('studio', None, held())) or True):
            durable_runtime.data_api_command(E, runtime=self.runtime)
        return seen

    def test_a_console_change_runs_what_a_tenant_can_hold_up_without_the_operation_lock(self):
        # A tenant that holds its schema lock or leaves a transaction open delays the guard, the pin and the
        # write, each within its bound. Under the installation's lock that held every other environment's
        # change and start too (found in review); now only the checks and the recording hold it.
        seen = self.console_change()
        self.assertEqual([step for step in seen if step[0] == 'checks'], [('checks', None, True)])
        # PostgREST is told right after the commit, not after waiting for the lock: until it is told, the
        # guard would let a schema it still serves be dropped (found in review). Studio restarts through
        # the supervisor, outside the lock.
        self.assertEqual([step[0] for step in seen if step[0] != 'sql'], ['checks', 'told', 'studio'])
        self.assertFalse(any(step[2] for step in seen if step[0] in ('told', 'studio')))
        self.assertTrue(seen[-2][0] == 'sql' and seen[-2][1] == data_api.serving_sql(E) and seen[-2][2])
        blockable = [step for step in seen if step[0] == 'sql' and (step[1].startswith('BEGIN;') or 'ALTER ROLE' in step[1]
                                                                    or 'CREATE EVENT TRIGGER' in step[1])]
        self.assertTrue(any(step[1].startswith('BEGIN;') for step in blockable))
        self.assertFalse(any(step[2] for step in blockable))
        self.assertEqual(served(self.catalog)[0], 'public,i2s')
        self.assertEqual(stored(self.catalog), ('public,i2s', 'applied', None))

    def test_a_console_change_whose_read_back_fails_is_asked_again(self):
        # A failed read could hide a start that wrote the earlier settings in between (found in review).
        original, wrote = self.runtime.sql, []

        def sql(query, database='postgres', check=True):
            if query.startswith('BEGIN;'):
                wrote.append(1)
            elif wrote and query == data_api.serving_sql(E):
                return SimpleNamespace(stdout='', stderr='gone', returncode=2)
            return original(query, database, check)
        self.runtime.sql = sql
        with self.assertRaises(durable_runtime.DataApiBusy):
            self.console_change()
        self.assertEqual(stored(self.catalog)[1], 'pending')

    def test_a_console_change_asks_again_when_a_start_wrote_other_settings_meanwhile(self):
        def start_meanwhile():
            # A start ran between the write and the recording, and wrote the settings last applied.
            self.current = 'pgrst.db_schemas=public\n'
            return True
        with self.assertRaises(durable_runtime.DataApiBusy):
            self.console_change(told=start_meanwhile)
        self.assertIsNone(served(self.catalog)[0])
        self.assertEqual(stored(self.catalog)[1], 'pending')

    def test_a_console_change_that_cannot_record_says_it_was_written(self):
        # A catalog that refuses the write, through the real writer: an error it passed over left the row
        # pending, or applied beside a stale served list, and the command exited 0 (found in review).
        original = data_api.record_applied

        def refusing(*args):
            with sqlite3.connect(self.catalog) as database:
                database.execute("CREATE TRIGGER busy BEFORE UPDATE ON data_api_settings BEGIN SELECT RAISE(ABORT, 'locked'); END")
            return original(*args)
        with patch.object(data_api, 'record_applied', refusing), self.assertRaises(durable_runtime.DataApiUnrecorded):
            self.console_change()
        with sqlite3.connect(self.catalog) as database:
            self.assertEqual(database.execute('SELECT state, served_schemas, applied_schemas FROM data_api_settings').fetchone(),
                             ('pending', None, None))
        with patch.object(data_api, 'load', return_value={'schemas': ['public'], 'extra_search_path': ['public'], 'max_rows': None}), \
                patch.object(data_api, 'busy', return_value=False), patch.object(data_api, 'record') as record, \
                patch.object(data_api.subprocess, 'run', return_value=SimpleNamespace(returncode=data_api.UNRECORDED, stderr='', stdout='')):
            self.assertEqual(data_api.apply(E), 1)
        record.assert_called_once_with(E, 'failed', data_api.UNRECORDED_TEXT)

    def test_a_console_change_that_writes_nothing_is_refused_with_its_reason(self):
        # A session holding the schema lock three times over writes nothing: the reason reaches the console
        # (exit 3), never "written but not recorded" (found in review).
        self.writes_answer = [(1, data_api.BUSY)] * 3
        with patch.object(durable_runtime.time, 'sleep'), self.assertRaises(durable_runtime.DataApiRefusal):
            self.console_change()
        self.assertEqual(stored(self.catalog)[1], 'pending')
        source = (Path(__file__).parent / 'durable_runtime.py').read_text()
        self.assertIn('except (DataApiBusy, DataApiRefusal):', source)

    def test_nothing_waits_for_an_environment_lock_while_holding_the_operation_lock(self):
        # A write pause holds the operation lock when its restore tries the environment's lock; a console
        # change that kept that lock while it waited for the operation lock made the pause wait (found in review).
        import fcntl
        environment = self.catalog.parent / f'data-api-{E}.lock'
        waits = []
        original = durable_runtime.operation_lock

        def lock(block=False):
            if block:
                with environment.open('a') as handle:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        waits.append('free')
                    except BlockingIOError:
                        waits.append('held')
            return original(block)
        with patch.object(durable_runtime, 'operation_lock', lock):
            self.console_change()
        self.assertEqual(waits, ['free'])
        # Tried, never waited for, both by the console's recording and by a restore.
        backup = (Path(__file__).parent / 'backup.py').read_text()
        self.assertIn('with data_api.environment_lock(e):\n            durable_runtime.Runtime().data_api(e, refuse_missing=False)', backup)
        with patch.object(data_api, 'OPERATION_LOCK', self.catalog.parent / 'operation.lock'):
            with data_api.environment_lock(E), durable_runtime.operation_lock(), self.assertRaises(BlockingIOError):
                data_api.environment_lock(E)

    def test_a_restart_of_studio_goes_through_the_supervisor(self):
        import sqlite3 as sqlite
        with sqlite.connect(self.catalog) as database:
            database.execute('CREATE TABLE studio_sessions(runtime TEXT PRIMARY KEY, desired TEXT, state TEXT, failure TEXT, actor TEXT, updated_at INTEGER)')
            database.execute("INSERT INTO studio_sessions VALUES (?, 'running', 'running', NULL, 'user:x', 0)", (E,))
        self.assertTrue(durable_runtime.restart_studio(E, self.catalog))
        with sqlite.connect(self.catalog) as database:
            self.assertEqual(database.execute('SELECT state, failure FROM studio_sessions').fetchone(), ('failed', None))
        # Not running: nothing to restart, and a Studio starting or stopped is left to the supervisor.
        self.assertFalse(durable_runtime.restart_studio(E, self.catalog))
        dev = (Path(__file__).parent / 'dev.py').read_text()
        self.assertIn("desired == 'running' and (state == 'stopped' or (state == 'failed' and failure is None))", dev)
        self.assertIn("desired == 'stopped' and state in ('running', 'starting', 'failed')", dev)

    def test_a_change_of_one_environment_does_not_make_another_busy(self):
        other = 'e_' + 'b' * 24
        with patch.object(data_api, 'OPERATION_LOCK', self.catalog.parent / 'operation.lock'):
            with data_api.environment_lock(E):
                self.assertTrue(data_api.busy(E))
                self.assertFalse(data_api.busy(other))
                with self.assertRaises(BlockingIOError):
                    durable_runtime.data_api_command(E, runtime=self.runtime)
            self.assertFalse(data_api.busy(E))
            with durable_runtime.operation_lock():
                self.assertTrue(data_api.busy(other))
        source = (Path(__file__).parent / 'durable_runtime.py').read_text()
        self.assertIn('except DataApiBusy:\n        # Written, but a start or restore wrote other settings before this change could record it: ask again.\n'
                      '        raise SystemExit(75)', source)

    def test_resume_applies_before_services(self):
        order = []
        runtime = object.__new__(durable_runtime.Runtime)
        runtime.values = {'environments': {E: {}}}
        runtime.sql = lambda *args, **kwargs: SimpleNamespace(stdout='', returncode=0)
        runtime.rest_deadlines = lambda e, rewrite=False, end_sessions=False: order.append('deadlines')
        runtime.data_api = lambda e, refuse_missing=True: order.append(('data_api', refuse_missing))
        runtime.activate_services = lambda e, v, creating: order.append(('services', creating))
        with tempfile.TemporaryDirectory() as state, \
                patch.object(durable_runtime, 'STATE', Path(state)), \
                patch.object(durable_runtime.effect_receipt, 'require_settled'), \
                patch.object(durable_runtime.source_fence, 'is_fenced', return_value=False), \
                patch.object(durable_runtime, 'inspect', return_value={'Id': 'x'}):
            (Path(state) / 'endpoints.json').write_text('{"%s": {}}' % E)
            runtime.resume(E)
        self.assertEqual(order, ['deadlines', ('data_api', False), ('services', False)])


class SupervisorTests(unittest.TestCase):
    def test_an_environment_whose_change_is_held_up_does_not_keep_another_waiting(self):
        # One child at a time for the whole installation let a tenant who held its own change up within
        # its bounds delay every other environment's change too (found in review).
        import dev
        supervisor = dev.Supervisor()
        other = 'e_' + 'b' * 24
        spawned, statuses = [], {}
        with patch.object(supervisor, 'data_api_requests', return_value=[E, other]), \
                patch.object(supervisor, 'paused', return_value=False), \
                patch.object(supervisor, 'spawn', side_effect=lambda command: spawned.append(command[-1]) or command[-1]), \
                patch.object(dev, 'child_status', side_effect=lambda process: statuses.get(process)), \
                patch.object(dev, 'terminate_group'):
            supervisor.schedule_data_api()
            self.assertEqual(spawned, [E, other])
            # E is still held up; the other one finishes, is asked again after a busy answer, and E is not spawned twice.
            statuses[other] = 75
            supervisor.schedule_data_api()
            self.assertEqual(spawned, [E, other])
            self.assertIn(other, supervisor.data_api_after)
            supervisor.data_api_after[other] = 0.0
            statuses.pop(other)
            supervisor.schedule_data_api()
            self.assertEqual(spawned, [E, other, other])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.catalog = Path(directory.name) / 'control.sqlite'
        catalog(self.catalog)
        for item in (patch.object(data_api, 'CATALOG', self.catalog), patch.object(data_api, 'busy', return_value=False)):
            item.start()
            self.addCleanup(item.stop)

    def row(self):
        with sqlite3.connect(self.catalog) as database:
            return database.execute('SELECT state, failure FROM data_api_settings').fetchone()

    def test_refusal_reason_reaches_catalog(self):
        result = SimpleNamespace(returncode=data_api.REFUSED, stderr='noise\nThe schema app does not exist in this environment\'s database.\n')
        with patch.object(data_api.subprocess, 'run', return_value=result):
            self.assertEqual(data_api.apply(E), 1)
        self.assertEqual(self.row(), ('failed', "The schema app does not exist in this environment's database."))
        self.assertIsNone(data_api.load_applied(E, self.catalog))

    def test_other_failure_is_generic(self):
        with patch.object(data_api.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='secret detail')):
            self.assertEqual(data_api.apply(E), 1)
        self.assertNotIn('secret', self.row()[1])

    def test_success_is_recorded_by_the_command_and_busy_waits(self):
        # The command records the change as applied under the operation lock (Runtime.data_api_finish);
        # recorded here, after it released the lock, a start in between could have written the earlier
        # settings back first (found in review).
        with patch.object(data_api.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stderr='')):
            self.assertEqual(data_api.apply(E), 0)
        self.assertEqual(self.row(), ('pending', None))
        with patch.object(data_api, 'busy', return_value=True):
            self.assertEqual(data_api.apply(E), 75)

    def test_load_reads_row_and_tolerates_old_catalog(self):
        self.assertEqual(data_api.load(E, self.catalog)['schemas'], ['public', 'i2s'])
        with tempfile.TemporaryDirectory() as other:
            old = Path(other) / 'control.sqlite'
            sqlite3.connect(old).close()
            self.assertIsNone(data_api.load(E, old))


class StudioTests(unittest.TestCase):
    def test_studio_gets_what_postgrest_serves(self):
        import studio
        answer = 'pgrst.db_extra_search_path=public,extensions\npgrst.db_schemas=i2s,public\n'
        with patch.object(studio, 'runtime_sql', return_value=answer) as sql:
            self.assertEqual(studio.rest_settings(E), {'PGRST_DB_SCHEMAS': 'i2s,public', 'PGRST_DB_EXTRA_SEARCH_PATH': 'public,extensions'})
        self.assertEqual(sql.call_args.args, (data_api.serving_sql(E), E))
        # Nothing left: Studio shows the empty schema PostgREST serves, never `public`.
        with patch.object(studio, 'runtime_sql', return_value=f'pgrst.db_schemas={data_api.EMPTY}\n'):
            self.assertEqual(studio.rest_settings(E)['PGRST_DB_SCHEMAS'], data_api.EMPTY)

    def test_studio_up_uses_it(self):
        import inspect
        import studio
        self.assertIn('**served', inspect.getsource(studio.up))


class InPlaceRestoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        lock = patch.object(data_api, 'OPERATION_LOCK', Path(directory.name) / 'operation.lock')
        lock.start()
        self.addCleanup(lock.stop)

    def test_restore_writes_the_catalog_settings_before_rest_starts(self):
        # pg_restore --create replays the archive's settings; the catalog decides what is served.
        import backup
        order = []

        import scheduled_jobs

        class Fake:
            def sql(self, query, database='postgres', check=True):
                order.append(('revoke' if query == scheduled_jobs.revoke_sql(E) else 'guard', database))

            def rest_deadlines(self, e, rewrite=False, end_sessions=False):
                order.append(('pin', e, rewrite, end_sessions))

            def data_api(self, e, refuse_missing=True):
                # Under the environment's Data API lock, so a console change never interleaves with it.
                self.assertTrue(data_api.busy(e))
                order.append(('data_api', e, refuse_missing))
        Fake.assertTrue = lambda fake, value: self.assertTrue(value)

        def run(command, *args, **kwargs):
            order.append(tuple(command[:2]))
            if command[1] == 'start':
                raise RuntimeError('stop here')
        with patch.object(backup, 'require_service_images'), patch.object(backup, 'run', run), \
                patch.object(durable_runtime, 'Runtime', Fake), patch.object(durable_runtime, 'cron_on', return_value=False), \
                self.assertRaisesRegex(RuntimeError, 'stop here'):
            backup.start_services(E)
        # REST stopped and its sessions ended with the pin before the guard, which they could hold up (found in
        # review); Scheduled jobs, off here, have the scheduler's rights taken back before the services start.
        self.assertEqual(order, [('docker', 'stop'), ('pin', E, True, True), ('guard', E), ('data_api', E, False),
                                 ('revoke', E), ('docker', 'start')])

    def test_restore_does_not_start_rest_when_the_login_cannot_be_pinned(self):
        import backup
        started = []

        class Unpinned:
            def sql(self, query, database='postgres', check=True):
                pass

            def rest_deadlines(self, e, rewrite=False, end_sessions=False):
                raise RuntimeError('database busy')

            def data_api(self, e, refuse_missing=True):
                started.append('data_api')

        with patch.object(backup, 'require_service_images'), patch.object(backup, 'run', lambda command, *a, **k: started.append(command[1])), \
                patch.object(durable_runtime, 'Runtime', Unpinned), self.assertRaisesRegex(backup.BackupError, 'stay stopped'):
            backup.start_services(E)
        self.assertEqual(started, ['stop'])

    def test_restore_does_not_start_rest_when_the_settings_function_cannot_be_installed(self):
        import backup
        started = []

        class NoGuard:
            def sql(self, query, database='postgres', check=True):
                raise RuntimeError('catalog busy')

            def rest_deadlines(self, e, rewrite=False, end_sessions=False):
                started.append('pin')

        with patch.object(backup, 'require_service_images'), patch.object(backup, 'run', lambda command, *a, **k: started.append(command[1])), \
                patch.object(durable_runtime, 'Runtime', NoGuard), self.assertRaisesRegex(backup.BackupError, 'stay stopped'):
            backup.start_services(E)
        self.assertEqual(started, ['stop', 'pin'])

    def test_restore_starts_services_even_when_the_settings_cannot_be_written(self):
        import backup
        started = []

        class Broken:
            def sql(self, query, database='postgres', check=True):
                pass

            def rest_deadlines(self, e, rewrite=False, end_sessions=False):
                pass

            def data_api(self, e, refuse_missing=True):
                raise RuntimeError('catalog busy')

        def run(command, *args, **kwargs):
            started.append(tuple(command[:2]))
            if command[1] == 'start':
                raise RuntimeError('stop here')
        observed = []
        Broken.data_api_observe = lambda self, e: observed.append(e)
        with patch.object(backup, 'require_service_images'), patch.object(backup, 'run', run), \
                patch.object(durable_runtime, 'Runtime', Broken), self.assertRaisesRegex(RuntimeError, 'stop here'):
            backup.start_services(E)
        self.assertEqual(started, [('docker', 'stop'), ('docker', 'start')])
        # What the restored database serves is read back for the console.
        self.assertEqual(observed, [E])


class RestoreTests(unittest.TestCase):
    def test_an_export_leaves_out_settings_another_environment_left_in_its_database(self):
        import recovery_boundaries
        selected = f"'{E}_auth','{E}_rest','{E}_storage'"
        query = recovery_boundaries.settings_query(E, selected)
        self.assertIn(f"(d.datname='{E}' OR (s.setdatabase=0 AND r.rolname IN ({selected})))", query)
        # Other environments' logins are left out; every other role stays, so the restore still refuses it.
        self.assertIn(f"r.rolname IS NULL OR r.rolname IN ({selected}) OR left(r.rolname, {len(E) + 1}) = '{E}_' OR r.rolname !~ '^e_[a-f0-9]{{24}}_'", query)
        self.assertIn('settings_query(e,selected)', Path(__file__).with_name('recovery-export.py').read_text())

    def test_role_settings_for_postgrest_are_never_admitted(self):
        # The settings travel in the dump, as sbarbase_rest.settings(); none is ever a role setting.
        source = Path(__file__).with_name('recovery-restore-db.py').read_text()
        self.assertNotIn('pgrst.', source)
        self.assertIn("('search_path','statement_timeout','transaction_timeout')", source)


if __name__ == '__main__':
    unittest.main()
