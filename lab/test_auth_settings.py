"""Sign-in settings: the checks, the Auth configuration they become, and when Auth is recreated."""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import auth_settings
import durable_runtime
import run as lab

E = 'e_' + 'a' * 24
VALID = {'revision': 3, 'site_url': 'https://app.example.com', 'redirect_urls': ['https://app.example.com/**', 'myapp://callback'],
         'signup': False, 'anonymous': True,
         'providers': {'github': {'enabled': True, 'client_id': 'Iv1.abc', 'secret': 's3cret', 'url': ''},
                       'keycloak': {'enabled': True, 'client_id': 'kc', 'secret': 'k', 'url': 'https://id.example.com/realms/a'},
                       'google': {'enabled': False, 'client_id': 'g', 'secret': 'x', 'url': ''}}}


class ValidationTests(unittest.TestCase):
    def test_the_same_fields_are_refused_as_in_the_console(self):
        for change, field in [({'site_url': 'javascript:alert(1)'}, 'site_url'),
                              ({'site_url': 'https://user:pass@app.example.com'}, 'site_url'),
                              ({'redirect_urls': ['https://a.example.com,https://b.example.com']}, 'redirect_urls'),
                              ({'redirect_urls': ['no-scheme']}, 'redirect_urls'),
                              ({'providers': {'myspace': {'enabled': True}}}, 'providers'),
                              ({'providers': {'github': {'enabled': True, 'client_id': '', 'secret': 'x'}}}, 'github'),
                              ({'providers': {'keycloak': {'enabled': True, 'client_id': 'a', 'secret': 'b'}}}, 'keycloak'),
                              ({'providers': {'github': {'enabled': True, 'client_id': 'a', 'secret': 'b', 'url': 'https://x.example.com'}}}, 'github'),
                              ({'extra': True}, 'settings')]:
            with self.assertRaises(auth_settings.SettingsError) as error:
                auth_settings.validate({**VALID, **change})
            self.assertEqual(str(error.exception), field)
            self.assertNotIn('s3cret', str(error.exception))


class ConfigurationTests(unittest.TestCase):
    def test_settings_become_auth_variables_for_enabled_providers_only(self):
        with patch.dict(os.environ, {'SBARBASE_PUBLIC_URL': 'https://api.example.com/'}):
            config = auth_settings.configuration(E, auth_settings.validate(VALID))
        external = f'https://api.example.com/{E}/auth/v1'
        self.assertEqual(config['API_EXTERNAL_URL'], external)
        self.assertEqual(config['GOTRUE_SITE_URL'], 'https://app.example.com')
        self.assertEqual(config['GOTRUE_URI_ALLOW_LIST'], 'https://app.example.com/**,myapp://callback')
        self.assertEqual(config['GOTRUE_DISABLE_SIGNUP'], 'true')
        self.assertEqual(config['GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED'], 'true')
        self.assertEqual(config['GOTRUE_EXTERNAL_GITHUB_SECRET'], 's3cret')
        self.assertEqual(config['GOTRUE_EXTERNAL_GITHUB_REDIRECT_URI'], external + '/callback')
        self.assertEqual(config['GOTRUE_EXTERNAL_KEYCLOAK_URL'], 'https://id.example.com/realms/a')
        self.assertFalse(any(key.startswith('GOTRUE_EXTERNAL_GOOGLE') for key in config))
        self.assertTrue(all(auth_settings.owned(key) for key in config))

    def test_without_settings_or_a_public_address_auth_is_configured_as_before(self):
        with patch.dict(os.environ, {'SBARBASE_PUBLIC_URL': ''}):
            config = lab.auth_configuration(E, {'auth': 'a', 'jwt': 'j'}, 'db')
        self.assertEqual(config['API_EXTERNAL_URL'], f'http://localhost/{E}/auth/v1')
        self.assertEqual(config['GOTRUE_SITE_URL'], 'http://localhost')
        self.assertFalse(any(key.startswith('GOTRUE_EXTERNAL_GITHUB') for key in config))

    def test_a_public_address_with_a_path_is_refused(self):
        with patch.dict(os.environ, {'SBARBASE_PUBLIC_URL': 'https://api.example.com/x'}):
            with self.assertRaises(auth_settings.SettingsError):
                auth_settings.public_url()


class RecreateTests(unittest.TestCase):
    def test_only_a_difference_in_sign_in_settings_lets_start_recreate_auth(self):
        base = {'GOTRUE_JWT_SECRET': 'j', 'GOTRUE_SITE_URL': 'http://localhost'}
        self.assertTrue(durable_runtime.settings_only('auth', {**base, 'PATH': '/bin'}, {**base, 'GOTRUE_SITE_URL': 'https://app.example.com'}))
        self.assertTrue(durable_runtime.settings_only('auth', {**base, 'GOTRUE_EXTERNAL_GITHUB_ENABLED': 'true'}, base))
        self.assertFalse(durable_runtime.settings_only('auth', base, {**base, 'GOTRUE_JWT_SECRET': 'other'}))
        self.assertFalse(durable_runtime.settings_only('rest', base, {**base, 'GOTRUE_SITE_URL': 'x'}))
        self.assertFalse(durable_runtime.settings_only('auth', base, dict(base)))

    def test_a_new_upload_limit_recreates_storage_and_nothing_else_does(self):
        base = {'FILE_SIZE_LIMIT': '1048576', 'ENCRYPTION_KEY': 'k'}
        self.assertTrue(durable_runtime.settings_only('storage', base, {**base, 'FILE_SIZE_LIMIT': '52428800'}))
        self.assertFalse(durable_runtime.settings_only('storage', base, {**base, 'ENCRYPTION_KEY': 'other'}))
        self.assertFalse(durable_runtime.settings_only('storage', base, {**base, 'FILE_SIZE_LIMIT': '52428800', 'ENCRYPTION_KEY': 'x'}))
        self.assertFalse(durable_runtime.settings_only('rest', base, {**base, 'FILE_SIZE_LIMIT': '52428800'}))

    def test_the_upload_limit_is_50_mib_unless_set(self):
        with patch.dict(os.environ, {'SBARBASE_UPLOAD_LIMIT_MB': ''}):
            self.assertEqual(durable_runtime.upload_limit(), 50 * 1024 * 1024)
        with patch.dict(os.environ, {'SBARBASE_UPLOAD_LIMIT_MB': '500'}):
            self.assertEqual(durable_runtime.upload_limit(), 500 * 1024 * 1024)
        for bad in ('0', '5121', '1.5', 'ten'):
            with patch.dict(os.environ, {'SBARBASE_UPLOAD_LIMIT_MB': bad}), self.assertRaises(RuntimeError):
                durable_runtime.upload_limit()


class ApplyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.catalog = root / 'control.sqlite'
        with closing(sqlite3.connect(self.catalog)) as database, database:
            database.execute('CREATE TABLE auth_settings(runtime TEXT PRIMARY KEY, revision INTEGER, applied INTEGER, state TEXT, '
                             'failure TEXT, actor TEXT, updated_at INTEGER)')
            database.execute("INSERT INTO auth_settings VALUES (?, 3, NULL, 'pending', NULL, 'alice', 0)", (E,))
        for item in [patch.object(auth_settings, 'CATALOG', self.catalog), patch.object(auth_settings, 'DIRECTORY', root),
                     patch.object(auth_settings, 'OPERATION_LOCK', root / 'operation.lock')]:
            item.start()
            self.addCleanup(item.stop)
        (root / f'{E}-auth.json').write_text(json.dumps(VALID))

    def row(self):
        with closing(sqlite3.connect(self.catalog)) as database:
            return database.execute('SELECT state, failure, applied FROM auth_settings').fetchone()

    def test_a_good_apply_records_the_revision(self):
        with patch.object(auth_settings.subprocess, 'run', return_value=type('R', (), {'returncode': 0})()):
            self.assertEqual(auth_settings.apply(E), 0)
        self.assertEqual(self.row(), ('applied', None, 3))

    def test_a_failed_apply_keeps_the_previous_settings_and_says_so(self):
        with patch.object(auth_settings.subprocess, 'run', return_value=type('R', (), {'returncode': 1})()):
            self.assertEqual(auth_settings.apply(E), 1)
        state, failure, applied = self.row()
        self.assertEqual((state, applied), ('failed', None))
        self.assertIn('previous settings stay in use', failure)

    def test_a_busy_runtime_leaves_the_request_pending(self):
        with patch.object(auth_settings, 'busy', return_value=True), patch.object(auth_settings.subprocess, 'run') as run:
            self.assertEqual(auth_settings.apply(E), 75)
        run.assert_not_called()
        self.assertEqual(self.row()[0], 'pending')

    def test_an_invalid_file_is_reported_without_touching_auth(self):
        (auth_settings.DIRECTORY / f'{E}-auth.json').write_text(json.dumps({**VALID, 'site_url': 'nope'}))
        with patch.object(auth_settings.subprocess, 'run') as run:
            self.assertEqual(auth_settings.apply(E), 1)
        run.assert_not_called()
        self.assertEqual(self.row()[0], 'failed')


if __name__ == '__main__':
    unittest.main()
