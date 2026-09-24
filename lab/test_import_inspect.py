"""Import phase 0: what is refused, warned and left manual, from fixed readings and no database."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import import_inspect as inspect


def reading(**changes):
    base = {'major': 17, 'extensions': {'plpgsql': '1.0', 'pgcrypto': '1.3', 'uuid-ossp': '1.1'},
            'available_extensions': ['pg_cron', 'pg_graphql', 'pg_net', 'pgcrypto', 'plpgsql', 'uuid-ossp', 'vector'],
            'roles': ['anon', 'authenticated', 'authenticator', 'postgres', 'service_role', 'supabase_admin'],
            'auth_migrations': ['20240101', '20250101'], 'storage_migrations': ['create-migrations-table', 'add-bucket'],
            'app_migrations': 3, 'publications': {'supabase_realtime': 0}, 'vault_secrets': 0, 'cron_jobs': None, 'auth_users': 12,
            'auth_identities': {'email': 12}, 'buckets': 1, 'objects': {'count': 4, 'bytes': 2048},
            'schemas': ['auth', 'public', 'storage']}
    base.update(changes)
    return base


class AssessTests(unittest.TestCase):
    def test_a_plain_project_is_importable_and_still_lists_manual_steps(self):
        result = inspect.assess(reading(), reading())
        self.assertTrue(result['importable'])
        self.assertEqual(result['refusals'], [])
        self.assertTrue(any('SMTP' in line for line in result['manual']))
        self.assertEqual(result['summary']['auth_users'], 12)

    def test_a_newer_source_is_refused_before_any_dump(self):
        result = inspect.assess(reading(major=18, auth_migrations=['20240101', '20250101', '20260901']), reading())
        self.assertFalse(result['importable'])
        self.assertTrue(any('PostgreSQL 18' in line for line in result['refusals']))
        self.assertTrue(any('Auth' in line and '20260901' in line for line in result['refusals']))

    def test_an_older_source_is_brought_forward_by_the_pinned_services(self):
        result = inspect.assess(reading(storage_migrations=['create-migrations-table']), reading())
        self.assertTrue(result['importable'])
        self.assertTrue(any('Storage schema is 1 migration(s) behind' in line for line in result['warnings']))

    def test_a_database_without_supabase_schemas_is_refused(self):
        result = inspect.assess(reading(auth_migrations=None), reading())
        self.assertFalse(result['importable'])
        self.assertTrue(any('not a Supabase database' in line for line in result['refusals']))

    def test_a_source_where_storage_never_ran_is_importable_with_a_warning(self):
        result = inspect.assess(reading(storage_migrations=None, buckets=None, objects=None), reading())
        self.assertTrue(result['importable'])
        self.assertTrue(any('Storage never ran' in line for line in result['warnings']))

    def test_an_empty_realtime_publication_is_not_reported(self):
        result = inspect.assess(reading(publications={'supabase_realtime': 0}), reading())
        self.assertFalse(any('Realtime' in line for line in result['warnings']))

    def test_missing_and_inactive_extensions(self):
        extensions = {'plpgsql': '1.0', 'postgis': '3.4', 'pg_cron': '1.6'}
        result = inspect.assess(reading(extensions=extensions), reading())
        self.assertTrue(any('postgis' in line for line in result['refusals']))
        self.assertTrue(any('pg_cron' in line for line in result['warnings']))

    def test_custom_roles_vault_realtime_and_oauth(self):
        result = inspect.assess(reading(roles=['anon', 'postgres', 'reporting', 'etl_writer'], vault_secrets=2,
                                        publications={'supabase_realtime': 2}, cron_jobs=3,
                                        auth_identities={'email': 3, 'google': 2, 'github': 1}), reading())
        self.assertFalse(result['importable'])
        self.assertTrue(any('Vault' in line for line in result['refusals']))
        self.assertTrue(any('2 custom role(s)' in line and 'reporting' in line for line in result['warnings']))
        self.assertTrue(any('Realtime' in line for line in result['warnings']))
        self.assertTrue(any('3 cron job(s)' in line for line in result['warnings']))
        self.assertTrue(any('github, google' in line for line in result['manual']))


class GatherTests(unittest.TestCase):
    def test_absent_optional_tables_read_as_none_and_are_never_queried(self):
        asked = []

        def execute(sql):
            asked.append(sql)
            if sql.startswith('SELECT to_regclass'):
                return [['f' if 'vault' in sql or 'cron' in sql else 't']]
            answers = {
                "SELECT current_setting('server_version_num')": [['170006']],
                'SELECT extname, extversion FROM pg_extension ORDER BY 1': [['plpgsql', '1.0']],
                'SELECT name FROM pg_available_extensions ORDER BY 1': [['plpgsql']],
            }
            if sql in answers:
                return answers[sql]
            if 'auth.identities' in sql:
                return [['email', '5']]
            if 'storage.objects' in sql:
                return [['2', '100']]
            if 'pg_publication' in sql:
                return [['supabase_realtime', '0']]
            if sql.startswith('SELECT count(*)'):
                return [['5']]
            return [['x']]
        facts = inspect.gather(execute)
        self.assertEqual(facts['major'], 17)
        self.assertIsNone(facts['vault_secrets'])
        self.assertIsNone(facts['cron_jobs'])
        self.assertFalse(any('vault.secrets' in sql and 'to_regclass' not in sql for sql in asked))
        self.assertEqual(facts['objects'], {'count': 2, 'bytes': 100})
        self.assertEqual(facts['auth_identities'], {'email': 5})


class ConnectionTests(unittest.TestCase):
    def test_the_url_becomes_libpq_variables_and_the_session_is_read_only(self):
        environment = inspect.connection_environment('postgresql://postgres.ref:p%40ss@db.example.com:6543/postgres?sslmode=require')
        self.assertEqual(environment['PGHOST'], 'db.example.com')
        self.assertEqual(environment['PGPORT'], '6543')
        self.assertEqual(environment['PGUSER'], 'postgres.ref')
        self.assertEqual(environment['PGPASSWORD'], 'p@ss')
        self.assertEqual(environment['PGSSLMODE'], 'require')
        self.assertIn('default_transaction_read_only=on', environment['PGOPTIONS'])

    def test_anything_but_a_postgres_url_is_refused(self):
        for value in ('mysql://h/db', 'postgres:///nohost', 'not a url'):
            with self.assertRaises(ValueError):
                inspect.connection_environment(value)

    def test_the_url_is_never_an_argument(self):
        with self.assertRaises(SystemExit):
            inspect.read_url(io.StringIO(''), {})
        self.assertEqual(inspect.read_url(io.StringIO('postgres://h/db\n'), {}), 'postgres://h/db')


class MainTests(unittest.TestCase):
    def test_a_refused_import_exits_three_and_the_report_holds_no_password(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / 'reference.json'
            reference.write_text(json.dumps(reading()))
            report = Path(directory) / 'report.json'
            with patch.object(inspect, 'gather', return_value=reading(major=18)), \
                 patch.object(inspect.sys, 'stdin', io.StringIO('postgres://u:secret-value@h/db\n')), \
                 patch('sys.stdout', new=io.StringIO()):
                code = inspect.main(['--reference', str(reference), '--report', str(report)])
            self.assertEqual(code, 3)
            text = report.read_text()
            self.assertNotIn('secret-value', text)
            self.assertFalse(json.loads(text)['importable'])


if __name__ == '__main__':
    unittest.main()
