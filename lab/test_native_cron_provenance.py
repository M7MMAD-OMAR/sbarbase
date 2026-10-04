"""Pure regression cases for the frozen public Cron provenance contract."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy' / 'verify'))
import native_cron_provenance as provenance

# Exact public commit witnesses, not generated from the admission implementation.
BODY = "\nBEGIN\n  IF EXISTS (\n    SELECT\n    FROM pg_event_trigger_ddl_commands() AS ev\n    JOIN pg_extension AS ext\n    ON ev.objid = ext.oid\n    WHERE ext.extname = 'pg_cron'\n  )\n  THEN\n    grant usage on schema cron to postgres with grant option;\n\n    alter default privileges in schema cron grant all on tables to postgres with grant option;\n    alter default privileges in schema cron grant all on functions to postgres with grant option;\n    alter default privileges in schema cron grant all on sequences to postgres with grant option;\n\n    alter default privileges for user supabase_admin in schema cron grant all\n        on sequences to postgres with grant option;\n    alter default privileges for user supabase_admin in schema cron grant all\n        on tables to postgres with grant option;\n    alter default privileges for user supabase_admin in schema cron grant all\n        on functions to postgres with grant option;\n\n    grant all privileges on all tables in schema cron to postgres with grant option;\n    revoke all on table cron.job from postgres;\n    grant select on table cron.job to postgres with grant option;\n    revoke trigger on cron.job_run_details from postgres;\n  END IF;\nEND;\n"
SCRIPT = b'grant usage on schema cron to postgres with grant option;\ngrant all on all functions in schema cron to postgres with grant option;\n\nalter default privileges for user supabase_admin in schema cron grant all\n    on sequences to postgres with grant option;\nalter default privileges for user supabase_admin in schema cron grant all\n    on tables to postgres with grant option;\nalter default privileges for user supabase_admin in schema cron grant all\n    on functions to postgres with grant option;\n\ngrant all privileges on all tables in schema cron to postgres with grant option;\nrevoke all on table cron.job from postgres;\ngrant select on table cron.job to postgres with grant option;\nrevoke trigger on cron.job_run_details from postgres;\n'
# Captured original-image list, independently checked against public Supabase config.
PRIVILEGED_EXTENSIONS = 'address_standardizer, address_standardizer_data_us, autoinc, bloom, btree_gin, btree_gist, citext, cube, dblink, dict_int, dict_xsyn, earthdistance, fuzzystrmatch, hstore, http, hypopg, index_advisor, insert_username, intarray, isn, ltree, moddatetime, orioledb, pg_buffercache, pg_cron, pg_graphql, pg_hashids, pg_jsonschema, pg_net, pg_prewarm, pg_repack, pg_stat_monitor, pg_stat_statements, pg_tle, pg_trgm, pg_walinspect, pgaudit, pgcrypto, pgjwt, pgroonga, pgroonga_database, pgrouting, pgrowlocks, pgsodium, pgstattuple, pgtap, plcoffee, pljava, plls, plpgsql_check, postgis, postgis_raster, postgis_sfcgal, postgis_tiger_geocoder, postgis_topology, postgres_fdw, refint, rum, seg, sslinfo, supabase_vault, supautils, tablefunc, tcn, tsm_system_rows, tsm_system_time, unaccent, uuid-ossp, vector, wrappers'
CID = 'a' * 64


def fixture():
    hook = {'oid': '42', 'namespace_oid': '13', 'namespace': 'extensions',
            'name': 'grant_pg_cron_access', 'owner_oid': '10', 'owner': 'supabase_admin',
            'language': 'plpgsql', 'security_definer': False, 'configuration': None,
            'kind': 'f', 'argument_types': '', 'return_type': 'event_trigger',
            'body': BODY, 'body_bytes': 1194}
    trigger = {'oid': '43', 'name': 'issue_pg_cron_access', 'event': 'ddl_command_end',
               'owner_oid': '10', 'enabled': 'O', 'function_oid': '42', 'tags': ['CREATE EXTENSION']}
    value = {'hook_count': 1, 'hooks': [hook], 'trigger_count': 1, 'triggers': [trigger],
             'current_user': 'supabase_admin', 'session_user': 'supabase_admin',
             'settings': {'supautils.extension_custom_scripts_path': '/etc/postgresql-custom/extension-custom-scripts',
                          'supautils.privileged_extensions': 'pg_net, pg_cron',
                          'supautils.privileged_extensions_superuser': 'supabase_admin'}}
    identity = {'roles': [{'oid': 10, 'rolname': 'supabase_admin', 'rolsuper': True}]}
    projection = {'routines': [copy.deepcopy(hook)], 'event_triggers': [copy.deepcopy(trigger)]}
    return value, identity, projection


class CronProvenanceTests(unittest.TestCase):
    def file_record(self, folder):
        def call(args, **kwargs):
            raw = json.dumps([{'Id': CID, 'Image': provenance.ORIGINAL_IMAGE}]).encode() if args[1] == 'container' else SCRIPT
            return SimpleNamespace(stdout=raw, stderr=b'', returncode=0)
        record = {}
        provenance.observe_file(record, call, CID, Path(folder))
        return record

    def test_public_witness_and_typed_catalog_connection(self):
        value, identity, projection = fixture()
        provenance.admit_file(SCRIPT)
        provenance.admit_sql(value, identity, projection)

    def test_original_privileged_list_accepts_exact_uuid_ossp(self):
        value, identity, projection = fixture()
        value['settings']['supautils.privileged_extensions'] = PRIVILEGED_EXTENSIONS
        provenance.admit_sql(value, identity, projection)

    def test_privileged_list_malformed_names_and_bounds_refuse(self):
        lists = ('pg_cron, pg-cron', 'pg_cron, uuid-ossp-extra',
                 'pg_cron, uuid.ossp', 'pg_cron,', 'pg_cron, , uuid-ossp',
                 'pg_cron, uuid-ossp, uuid-ossp', 'uuid-ossp', '',
                 'pg_cron, ' + 'x' * 4096,
                 'pg_cron, ' + ', '.join('extension_' + str(i) for i in range(128)))
        for wrong in lists:
            value, identity, projection = fixture()
            value['settings']['supautils.privileged_extensions'] = wrong
            with self.subTest(length=len(wrong)), self.assertRaises(RuntimeError):
                provenance.admit_sql(value, identity, projection)

    def test_script_byte_damage_and_growth(self):
        for raw in (SCRIPT[:-1], SCRIPT + b' ', b'x' + SCRIPT[1:], SCRIPT.decode()):
            with self.subTest(raw_type=type(raw)), self.assertRaises(RuntimeError):
                provenance.admit_file(raw)

    def test_catalog_context_and_source_refusals(self):
        cases = [('hook_count', 2), ('hook_count', True), ('trigger_count', 0),
                 ('current_user', 'postgres'), ('session_user', 'postgres')]
        for field, wrong in cases:
            value, identity, projection = fixture()
            value[field] = wrong
            with self.subTest(field=field, wrong=wrong), self.assertRaises(RuntimeError):
                provenance.admit_sql(value, identity, projection)
        hook_cases = {'owner': 'postgres', 'owner_oid': '20', 'language': 'sql',
                      'security_definer': True, 'configuration': [], 'kind': 'p',
                      'argument_types': 'integer', 'return_type': 'void', 'oid': '0',
                      'namespace_oid': '4294967296', 'body': BODY.replace('grant usage', 'GRANT usage'),
                      'body_bytes': 1195}
        for field, wrong in hook_cases.items():
            value, identity, projection = fixture()
            value['hooks'][0][field] = wrong
            with self.subTest(hook=field), self.assertRaises(RuntimeError):
                provenance.admit_sql(value, identity, projection)
        for field, wrong in {'tags': ['ALTER EXTENSION'], 'event': 'sql_drop', 'function_oid': '99',
                             'owner_oid': '20', 'enabled': 'D'}.items():
            value, identity, projection = fixture()
            value['triggers'][0][field] = wrong
            with self.subTest(trigger=field), self.assertRaises(RuntimeError):
                provenance.admit_sql(value, identity, projection)
        for field, wrong in {'supautils.extension_custom_scripts_path': '/tmp/scripts',
                             'supautils.privileged_extensions': 'pg_net',
                             'supautils.privileged_extensions_superuser': 'postgres'}.items():
            value, identity, projection = fixture()
            value['settings'][field] = wrong
            with self.subTest(setting=field), self.assertRaises(RuntimeError):
                provenance.admit_sql(value, identity, projection)

    def test_overload_projection_and_role_binding(self):
        for mode in ('overload', 'projection', 'trigger_projection', 'role'):
            value, identity, projection = fixture()
            if mode == 'overload':
                value['hooks'].append(copy.deepcopy(value['hooks'][0]))
            elif mode == 'projection':
                projection['routines'][0]['oid'] = '99'
            elif mode == 'trigger_projection':
                projection['event_triggers'][0]['function_oid'] = '99'
            else:
                identity['roles'][0]['rolsuper'] = False
            with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                provenance.admit_sql(value, identity, projection)

    def test_image_refusal_precedes_native_file_read(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def call(args, **kwargs):
                calls.append(args)
                return SimpleNamespace(stdout=json.dumps([{'Id': CID, 'Image': 'sha256:' + '0' * 64}]).encode(), stderr=b'', returncode=0)
            with self.assertRaises(RuntimeError):
                provenance.observe_file({}, call, CID, Path(folder))
            self.assertEqual(calls, [['docker', 'container', 'inspect', '--format', provenance.METADATA_TEMPLATE, CID]])
            self.assertTrue((Path(folder) / 'cron-provenance-container.json.bin').is_file())

    def test_configured_probe_wrong_image_never_executes_sql(self):
        import native_worker_inspect as worker
        with tempfile.TemporaryDirectory() as folder:
            calls, queries = [], []
            def native(args, **kwargs):
                calls.append(args)
                if kwargs.get('allow_absence'):
                    return None
                return SimpleNamespace(stdout=json.dumps([{'Id': CID, 'Image': 'sha256:' + '0' * 64}]).encode(), stderr=b'', returncode=0)
            def sql(statement, **kwargs):
                queries.append(statement)
                raise AssertionError('SQL must not run before original image admission')
            report = {'bootstrap': {'passed': True, 'helper_cleanup': [],
                                   'positive_authentication': True, 'negative_authentication': True}}
            with patch.dict('os.environ', {'SBARBASE_FIXTURE_ID': 'identity-test', 'SBARBASE_VERIFY_IMAGE_ID': 'sha256:' + 'b' * 64}), \
                    patch.object(worker, 'Path', return_value=Path(folder) / 'artifacts'):
                with self.assertRaisesRegex(RuntimeError, 'Actual database image differs'):
                    worker.configured_probe(report, sql, native, CID)
            self.assertEqual(queries, [])
            self.assertEqual(len(calls), 2)
            self.assertFalse(report['worker_effects']['passed'])

    def test_native_read_command_and_refused_file_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            calls, record = [], {}
            def call(args, **kwargs):
                calls.append(args)
                if len(calls) == 1:
                    return SimpleNamespace(stdout=json.dumps([{'Id': CID, 'Image': provenance.ORIGINAL_IMAGE}]).encode(), stderr=b'', returncode=0)
                return SimpleNamespace(stdout=SCRIPT[:-1], stderr=b'', returncode=0)
            with self.assertRaises(RuntimeError):
                provenance.observe_file(record, call, CID, Path(folder))
            self.assertEqual(calls[1][2:12], ['--user', '100:101', CID, '/usr/bin/timeout', '-s', 'KILL', '5', '/bin/sh', '-c', provenance.FILE_PAYLOAD])
            self.assertEqual((Path(folder) / record['file']['artifact']).read_bytes(), SCRIPT[:-1])
            self.assertFalse(record['passed'])

    def test_both_sql_phase_artifacts_survive_setup_refusal(self):
        with tempfile.TemporaryDirectory() as folder:
            value, identity, projection = fixture()
            record = self.file_record(folder)
            provenance.observe_sql(record, 'before', lambda sql: json.dumps(value), identity, projection, Path(folder))
            value['settings']['supautils.privileged_extensions'] = 'pg_net,pg_cron,pgcrypto'
            with self.assertRaises(RuntimeError):
                provenance.observe_sql(record, 'setup', lambda sql: json.dumps(value), identity, projection, Path(folder))
            for label in ('before', 'setup'):
                self.assertTrue((Path(folder) / record[label]['artifact']).is_file())
            self.assertTrue(record['before']['passed'])
            self.assertFalse(record['setup']['passed'])

    def test_sql_byte_bound_retains_raw_refusal(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = ' ' * 16385
            record = self.file_record(folder)
            with self.assertRaises(RuntimeError):
                provenance.observe_sql(record, 'before', lambda sql: raw, {}, {}, Path(folder))
            self.assertEqual((Path(folder) / record['before']['artifact']).read_text(), raw)

    def test_metadata_failure_bounds_and_extra_fields_prevent_file_execution(self):
        valid = json.dumps([{'Id': CID, 'Image': provenance.ORIGINAL_IMAGE}]).encode()
        extra = json.dumps([{'Id': CID, 'Image': provenance.ORIGINAL_IMAGE, 'Config': {}}]).encode()
        for raw, code, stderr in ((valid, 1, b''), (valid, 0, b'warning'), (extra, 0, b''), (b' ' * 513, 0, b'')):
            with self.subTest(code=code, bytes=len(raw), stderr=stderr), tempfile.TemporaryDirectory() as folder:
                calls = []
                def call(args, **kwargs):
                    calls.append(args)
                    return SimpleNamespace(stdout=raw, stderr=stderr, returncode=code)
                with self.assertRaises(RuntimeError):
                    provenance.observe_file({}, call, CID, Path(folder))
                self.assertEqual(len(calls), 1)
                self.assertEqual((Path(folder) / 'cron-provenance-container.json.bin').read_bytes(), raw)

    def test_sql_phase_prerequisites_and_duplicates_precede_query(self):
        with tempfile.TemporaryDirectory() as folder:
            value, identity, projection = fixture()
            record = self.file_record(folder)
            queries = []
            def query(statement):
                queries.append(statement)
                return json.dumps(value)
            for candidate, phase in (({}, 'before'), (record, 'setup')):
                with self.assertRaises(RuntimeError):
                    provenance.observe_sql(candidate, phase, query, identity, projection, Path(folder))
            self.assertEqual(queries, [])
            provenance.observe_sql(record, 'before', query, identity, projection, Path(folder))
            with self.assertRaises(RuntimeError):
                provenance.observe_sql(record, 'before', query, identity, projection, Path(folder))
            self.assertEqual(len(queries), 1)

    def test_numeric_projection_boolean_does_not_match_typed_hook(self):
        value, identity, projection = fixture()
        projection['routines'][0]['security_definer'] = 0
        with self.assertRaises(RuntimeError):
            provenance.admit_sql(value, identity, projection)


if __name__ == '__main__':
    unittest.main()
