"""The import's pure parts: the schema it restores, the settings it accepts, and the developer login it leaves alone."""
import io
import unittest
from unittest.mock import patch

import import_project

E = 'e_' + 'a' * 24

DUMP = """SET statement_timeout = 0;
\\restrict abc123
CREATE SCHEMA public;
ALTER SCHEMA public OWNER TO pg_database_owner;
COMMENT ON SCHEMA public IS 'standard public schema';
CREATE TABLE public.notes (id bigint NOT NULL, body text);
ALTER TABLE public.notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "own notes" ON public.notes USING ((owner = auth.uid()));
GRANT ALL ON TABLE public.notes TO anon;
GRANT ALL ON TABLE public.notes TO postgres;
GRANT SELECT ON TABLE public.notes TO authenticated, service_role;
GRANT SELECT ON TABLE public.notes TO authenticated, custom_role;
REVOKE ALL ON TABLE public.notes FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT ALL ON TABLES TO anon;
\\unrestrict abc123
"""


class SchemaTests(unittest.TestCase):
    def test_the_target_keeps_its_own_schema_and_only_grants_to_roles_it_has(self):
        script = import_project.schema_script(DUMP)
        self.assertIn('CREATE TABLE public.notes', script)
        self.assertIn('CREATE POLICY "own notes"', script)
        self.assertIn('GRANT ALL ON TABLE public.notes TO anon;', script)
        self.assertIn('GRANT SELECT ON TABLE public.notes TO authenticated, service_role;', script)
        self.assertIn('REVOKE ALL ON TABLE public.notes FROM PUBLIC;', script)
        for gone in ('CREATE SCHEMA public;', 'ALTER SCHEMA public OWNER', 'COMMENT ON SCHEMA public', 'TO postgres;', 'custom_role',
                     'ALTER DEFAULT PRIVILEGES', '\\restrict', '\\unrestrict'):
            self.assertNotIn(gone, script)


class SettingsTests(unittest.TestCase):
    def test_settings_come_as_json_and_storage_needs_a_key(self):
        good = {'database_url': 'postgresql://postgres:x@db.example.supabase.co:5432/postgres', 'api_url': 'https://ref.supabase.co',
                'service_role_key': 'key'}
        self.assertEqual(import_project.read_settings(io.StringIO(__import__('json').dumps(good)))['api_url'], good['api_url'])
        for bad in ('not json', '{}', '{"database_url": 1}', '{"database_url": "postgresql://x", "api_url": "ftp://x", "service_role_key": "k"}',
                    '{"database_url": "postgresql://x", "api_url": "https://ref.supabase.co"}'):
            with self.assertRaises(import_project.ImportError_):
                import_project.read_settings(io.StringIO(bad))


class DeveloperTests(unittest.TestCase):
    def test_an_open_developer_login_keeps_its_password(self):
        statements = []
        with patch.object(import_project, 'target_sql', side_effect=lambda e, sql, **kw: statements.append(sql) or ''), \
             patch.object(import_project.runtime, 'direct_on', return_value=True), \
             patch.object(import_project.runtime, 'Runtime') as work:
            work.return_value.developer_grants_sql.return_value = 'GRANT USAGE;'
            import_project.ensure_developer(E)
        work.return_value.developer_sql.assert_not_called()
        self.assertEqual(statements, ['GRANT USAGE;'])

    def test_a_closed_developer_login_is_made_but_cannot_sign_in(self):
        statements = []
        with patch.object(import_project, 'target_sql', side_effect=lambda e, sql, **kw: statements.append(sql) or ''), \
             patch.object(import_project.runtime, 'direct_on', return_value=False), \
             patch.object(import_project.runtime, 'Runtime') as work:
            work.return_value.developer_sql.return_value = 'CREATE ROLE;\n'
            work.return_value.developer_grants_sql.return_value = 'GRANT USAGE;'
            import_project.ensure_developer(E)
        self.assertTrue(statements[0].endswith(f'ALTER ROLE {E}_developer NOLOGIN;\n'))


if __name__ == '__main__':
    unittest.main()
