import io
import unittest

from restore_sql import PlanError, plan_archive


SOURCE = 'e_' + 'a' * 24
STAGE = 'e_' + 'a' * 24 + '_stage_20261003t100000z'


def dump(extra='', body='', create=None, connect=None):
    return '-- PostgreSQL dump\n\\restrict abc123\n' \
        + (create or 'CREATE DATABASE ' + SOURCE + " WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'C';\n") \
        + 'ALTER DATABASE ' + SOURCE + ' OWNER TO supabase_admin;\n' \
        + '\\unrestrict abc123\n' + (connect or '\\connect ' + SOURCE + '\n') \
        + '\\restrict abc123\n' + extra + body + '\\unrestrict abc123\n'


def plan(script):
    output = io.StringIO()
    result = plan_archive(io.StringIO(script), output, SOURCE, STAGE)
    return result, output.getvalue()


class ArchiveSqlPlanningTests(unittest.TestCase):
    def test_archive_locale_and_defaults_not_live_metadata(self):
        result, body = plan(dump())
        self.assertIn("ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'C'", result.create)
        self.assertIn('OWNER = supabase_admin CONNECTION LIMIT = 0', result.create)
        self.assertIn('"' + STAGE + '" OWNER TO supabase_admin', result.metadata[0])
        self.assertIn('CONNECTION LIMIT = -1', result.metadata[-1])
        self.assertFalse(result.explicit_acl)
        self.assertNotIn('CREATE DATABASE', body)
        self.assertNotIn('\\connect', body)

    def test_database_acl_comment_and_role_settings_rebased_only_at_db_identifier(self):
        extra = f'''ALTER DATABASE {SOURCE} CONNECTION LIMIT = 0;
COMMENT ON DATABASE {SOURCE} IS 'keep {SOURCE}; \\connect preserve';
ALTER DATABASE {SOURCE} SET search_path TO 'public', 'extensions';
ALTER ROLE "project_owner" IN DATABASE {SOURCE} SET statement_timeout TO '8s';
REVOKE ALL ON DATABASE {SOURCE} FROM PUBLIC;
GRANT CONNECT ON DATABASE {SOURCE} TO "project_owner" WITH GRANT OPTION;
'''
        result, body = plan(dump(extra=extra))
        self.assertTrue(result.explicit_acl)
        self.assertEqual(len(result.metadata), 7)
        self.assertIn("IS 'keep " + SOURCE + '; \\connect preserve', result.metadata[2])
        self.assertTrue(all('"' + STAGE + '"' in item for item in result.metadata))
        self.assertNotIn('CONNECTION LIMIT = -1', ''.join(result.metadata))
        self.assertNotIn('WITH GRANT OPTION', body)

    def test_icu_and_collation_version_retained(self):
        create = f"CREATE DATABASE {SOURCE} WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = icu ICU_LOCALE = 'und' ICU_RULES = '&A < B' COLLATION_VERSION = '153.80';\n"
        result, _ = plan(dump(create=create))
        for literal in ("ICU_LOCALE = 'und'", "ICU_RULES = '&A < B'", "COLLATION_VERSION = '153.80'"):
            self.assertIn(literal, result.create)

    def test_native_builtin_provider_locale_override_preserved(self):
        create = f"CREATE DATABASE {SOURCE} WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = builtin LOCALE = 'C' BUILTIN_LOCALE = 'C.UTF-8';\n"
        result, _ = plan(dump(create=create))
        self.assertIn("LOCALE = 'C' BUILTIN_LOCALE = 'C.UTF-8'", result.create)

    def test_builtin_locale_requires_documented_value_and_provider(self):
        for provider, locale in (('libc', 'C'), ('icu', 'C.UTF-8'), ('builtin', 'PG_UNICODE_FAST')):
            with self.subTest(provider=provider, locale=locale), self.assertRaises(PlanError):
                plan(dump(create=f"CREATE DATABASE {SOURCE} WITH TEMPLATE = template0 LOCALE_PROVIDER = {provider} BUILTIN_LOCALE = '{locale}';\n"))

    def test_literal_and_comment_connection_limit_do_not_change_default(self):
        for extra in (f"COMMENT ON DATABASE {SOURCE} IS 'CONNECTION LIMIT';\n",
                      f"ALTER DATABASE {SOURCE} SET application_name TO 'CONNECTION LIMIT';\n",
                      f"/* CONNECTION LIMIT */ ALTER DATABASE {SOURCE} SET application_name TO 'normal';\n"):
            with self.subTest(extra=extra):
                result, _ = plan(dump(extra=extra))
                self.assertEqual(result.metadata[-1], 'ALTER DATABASE "' + STAGE + '" CONNECTION LIMIT = -1;\n')

    def test_copy_bytes_with_sql_looking_payload_are_unchanged(self):
        body = 'COPY public.messages (value) FROM stdin;\n' \
            + 'CREATE DATABASE ' + SOURCE + ';\\n\\connect attacker\n' \
            + '$body$;\\tvalue\n\\.\n'
        _, actual = plan(dump(body=body))
        self.assertIn(body, actual)

    def test_function_quotes_comments_and_identifier_do_not_rebase(self):
        body = '/* outer /* inner ; */ comment */\n' \
            + 'CREATE FUNCTION public.f() RETURNS text LANGUAGE sql AS $body$\n' \
            + "SELECT 'ALTER DATABASE " + SOURCE + ";';\n$body$;\n" \
            + 'CREATE TABLE "' + SOURCE + '" ("semi;colon" text);\n'
        _, actual = plan(dump(body=body))
        self.assertIn(body, actual)

    def test_escaped_sql_string_does_not_split(self):
        body = "SELECT E'quote\\\'; semicolon; \\\\';\n"
        _, actual = plan(dump(body=body))
        self.assertIn(body, actual)

    def test_libpq_reconnect_is_allowed_only_for_exact_source(self):
        _, body = plan(dump(connect='\\connect -reuse-previous=on "dbname=\'' + SOURCE + '\'"\n'))
        self.assertNotIn('\\connect', body)

    def test_native_restriction_exit_reconnect_and_reentry(self):
        text = dump(extra=f'ALTER DATABASE {SOURCE} SET application_name TO \'CONNECTION LIMIT\';\n'
                    + '\\unrestrict abc123\n\\connect ' + SOURCE + '\n\\restrict abc123\n')
        result, body = plan(text)
        self.assertEqual(result.original, SOURCE)
        self.assertNotIn('\\restrict', body)
        self.assertNotIn('\\unrestrict', body)
        with self.assertRaises(PlanError):
            plan(text.replace('\\restrict abc123\n\\unrestrict', '\\restrict different\n\\unrestrict'))

    def test_extension_worker_contract_refuses_unclassified_archive_before_replay(self):
        _, body = plan(dump(body='CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;\n'))
        self.assertIn('CREATE EXTENSION IF NOT EXISTS pgcrypto', body)
        for extra in ('CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA public;\n',
                      'CREATE EXTENSION pg_cron;\n', 'CREATE EXTENSION arbitrary;\n',
                      "CREATE SUBSCRIPTION subscriber CONNECTION 'host=other' PUBLICATION writer;\n",
                      "SET session_preload_libraries = 'arbitrary';\n"):
            with self.subTest(extra=extra), self.assertRaises(PlanError):
                plan(dump(body=extra))

    def test_unsupported_commands_properties_and_other_database_refuse(self):
        scripts = {
            'file include': dump(body='\\i malicious.sql\n'),
            'shell escape': dump(body='\\! true\n'),
            'reconnect another database': dump(body='\\connect postgres\n'),
            'reconnect twice': dump(body='\\connect ' + SOURCE + '\n'),
            'inline metacommand': dump(body='SELECT 1 \\gexec\n'),
            'another database ddl': dump(extra='ALTER DATABASE postgres SET search_path TO public;\n'),
            'database drop': dump(body='DROP DATABASE postgres;\n'),
            'rename': dump(extra='ALTER DATABASE ' + SOURCE + ' RENAME TO x;\n'),
            'allow connections': dump(extra='ALTER DATABASE ' + SOURCE + ' ALLOW_CONNECTIONS true;\n'),
            'template': dump(extra='ALTER DATABASE ' + SOURCE + ' IS_TEMPLATE = true;\n'),
            'custom tablespace': dump(create='CREATE DATABASE ' + SOURCE + ' WITH TEMPLATE template0 TABLESPACE exotic;\n'),
            'nonempty template': dump(create='CREATE DATABASE ' + SOURCE + ' WITH TEMPLATE template1;\n'),
            'security label': dump(extra='SECURITY LABEL FOR provider ON DATABASE ' + SOURCE + " IS 'label';\n"),
            'cluster role alteration': dump(extra='ALTER ROLE arbitrary SUPERUSER;\n'),
            'worker settings': dump(extra='ALTER DATABASE ' + SOURCE + " SET session_preload_libraries TO 'exotic';\n"),
            'two database ACL': dump(extra='GRANT CONNECT ON DATABASE ' + SOURCE + ', postgres TO PUBLIC;\n'),
        }
        for name, script in scripts.items():
            with self.subTest(case=name), self.assertRaises(PlanError):
                plan(script)

    def test_truncated_quote_comment_copy_and_restriction_refuse(self):
        for script in (dump(body="SELECT 'unterminated"), dump(body='/* unterminated'),
                       dump(body='COPY public.t (v) FROM stdin;\nvalue\n'),
                       dump().replace('\\unrestrict abc123', '\\unrestrict different'),
                       dump().replace('\\unrestrict abc123\n', '')):
            with self.subTest(script=script[-45:]), self.assertRaises(PlanError):
                plan(script)


if __name__ == '__main__':
    unittest.main()
