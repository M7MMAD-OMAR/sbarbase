"""Sign-in settings: the checks, the Auth configuration they become, and when Auth is recreated."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

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


class SessionTests(unittest.TestCase):
    """The three session settings: what they become, and that an unset one adds nothing."""

    def configuration(self, **session):
        settings = auth_settings.validate({**VALID, **({'session': session} if session else {})})
        with patch.dict(os.environ, {'SBARBASE_PUBLIC_URL': 'https://api.example.com/'}):
            return auth_settings.configuration(E, settings)

    def expected(self):
        """The whole dict a settings file without a session key has always produced."""
        external = f'https://api.example.com/{E}/auth/v1'
        return {
            'API_EXTERNAL_URL': external,
            'GOTRUE_SITE_URL': 'https://app.example.com',
            'GOTRUE_URI_ALLOW_LIST': 'https://app.example.com/**,myapp://callback',
            'GOTRUE_DISABLE_SIGNUP': 'true',
            'GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED': 'true',
            'GOTRUE_EXTERNAL_GITHUB_ENABLED': 'true', 'GOTRUE_EXTERNAL_GITHUB_CLIENT_ID': 'Iv1.abc',
            'GOTRUE_EXTERNAL_GITHUB_SECRET': 's3cret', 'GOTRUE_EXTERNAL_GITHUB_REDIRECT_URI': external + '/callback',
            'GOTRUE_EXTERNAL_KEYCLOAK_ENABLED': 'true', 'GOTRUE_EXTERNAL_KEYCLOAK_CLIENT_ID': 'kc',
            'GOTRUE_EXTERNAL_KEYCLOAK_SECRET': 'k', 'GOTRUE_EXTERNAL_KEYCLOAK_REDIRECT_URI': external + '/callback',
            'GOTRUE_EXTERNAL_KEYCLOAK_URL': 'https://id.example.com/realms/a'}

    def test_a_file_without_a_session_key_yields_exactly_the_configuration_it_did_before(self):
        config = self.configuration()
        self.assertEqual(config, self.expected())
        self.assertEqual([key for key in config if key == 'GOTRUE_JWT_EXP' or key.startswith('GOTRUE_SESSIONS_')], [])

    def test_each_member_alone_adds_exactly_its_one_variable(self):
        base = self.configuration()
        for member, raw, variable, value in (
                ('access_token_seconds', 900, 'GOTRUE_JWT_EXP', '900'),
                ('timebox_seconds', 86400, 'GOTRUE_SESSIONS_TIMEBOX', '86400s'),
                ('inactivity_seconds', 7200, 'GOTRUE_SESSIONS_INACTIVITY_TIMEOUT', '7200s')):
            config = self.configuration(**{member: raw})
            self.assertEqual(set(config) - set(base), {variable})
            self.assertEqual(config[variable], value)
            self.assertEqual({key: config[key] for key in base}, base)

    def test_the_three_members_together_add_the_three_variables(self):
        config = self.configuration(access_token_seconds=600, timebox_seconds=315360000, inactivity_seconds=3600)
        self.assertEqual(config['GOTRUE_JWT_EXP'], '600')
        self.assertEqual(config['GOTRUE_SESSIONS_TIMEBOX'], '315360000s')
        self.assertEqual(config['GOTRUE_SESSIONS_INACTIVITY_TIMEOUT'], '3600s')
        self.assertEqual(set(config) - set(self.expected()),
                         {'GOTRUE_JWT_EXP', 'GOTRUE_SESSIONS_TIMEBOX', 'GOTRUE_SESSIONS_INACTIVITY_TIMEOUT'})

    def test_null_members_and_a_partial_object_add_nothing(self):
        self.assertEqual(self.configuration(access_token_seconds=None, timebox_seconds=None, inactivity_seconds=None),
                         self.expected())
        settings = auth_settings.validate({**VALID, 'session': {'timebox_seconds': 3600}})
        self.assertEqual(settings['session'], {'access_token_seconds': None, 'timebox_seconds': 3600,
                                               'inactivity_seconds': None})

    def test_the_session_variables_are_owned_settings_and_the_jwt_secret_is_not(self):
        for key in ('GOTRUE_JWT_EXP', 'GOTRUE_SESSIONS_TIMEBOX', 'GOTRUE_SESSIONS_INACTIVITY_TIMEOUT'):
            self.assertTrue(auth_settings.owned(key))
        for key in ('GOTRUE_JWT_SECRET', 'GOTRUE_JWT_AUD', 'GOTRUE_SESSIONS'):
            self.assertFalse(auth_settings.owned(key))
        # The one caller that matters: a retained Auth may be recreated for these alone.
        base = {'GOTRUE_JWT_SECRET': 'j', 'GOTRUE_SITE_URL': 'http://localhost'}
        self.assertTrue(durable_runtime.settings_only('auth', base, {**base, 'GOTRUE_JWT_EXP': '600'}))
        # In both directions: dropping a session setting is an operator change, not drift.
        self.assertTrue(durable_runtime.settings_only('auth', {**base, 'GOTRUE_SESSIONS_TIMEBOX': '86400s'}, base))
        self.assertFalse(durable_runtime.settings_only('auth', base, {**base, 'GOTRUE_JWT_SECRET': 'other'}))

    def test_a_session_member_outside_its_range_or_not_an_integer_is_refused(self):
        for session, field in (
                ({'access_token_seconds': True}, 'access_token_seconds'),
                ({'access_token_seconds': '900'}, 'access_token_seconds'),
                ({'access_token_seconds': 1.5}, 'access_token_seconds'),
                ({'access_token_seconds': 59}, 'access_token_seconds'),
                ({'access_token_seconds': 86401}, 'access_token_seconds'),
                ({'timebox_seconds': 3599}, 'timebox_seconds'),
                ({'timebox_seconds': 315360001}, 'timebox_seconds'),
                ({'inactivity_seconds': 299}, 'inactivity_seconds'),
                ({'inactivity_seconds': 315360001}, 'inactivity_seconds'),
                ({'token_seconds': 600}, 'session')):
            with self.assertRaises(auth_settings.SettingsError) as error:
                auth_settings.validate({**VALID, 'session': session})
            self.assertEqual(str(error.exception), field)

    def test_a_session_value_that_is_not_an_object_is_refused(self):
        for value in ([], 'session', 600, True):
            with self.assertRaises(auth_settings.SettingsError) as error:
                auth_settings.validate({**VALID, 'session': value})
            self.assertEqual(str(error.exception), 'session')

    def test_load_of_a_file_written_before_sessions_still_returns_a_null_session(self):
        self.assertNotIn('session', VALID)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / f'{E}-auth.json').write_text(json.dumps(VALID))
        (root / f'{E}-auth.json').chmod(0o600)
        with patch.object(auth_settings, 'DIRECTORY', root):
            loaded = auth_settings.load(E) or {}
        self.assertEqual(loaded['session'], {'access_token_seconds': None, 'timebox_seconds': None,
                                             'inactivity_seconds': None})


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
        (root / f'{E}-auth.json').chmod(0o600)

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

    def test_a_lock_taken_after_the_check_leaves_the_request_pending(self):
        with patch.object(auth_settings.subprocess, 'run', return_value=type('R', (), {'returncode': auth_settings.BUSY})()):
            self.assertEqual(auth_settings.apply(E), 75)
        self.assertEqual(self.row()[0], 'pending')

    def test_an_invalid_file_is_reported_without_touching_auth(self):
        (auth_settings.DIRECTORY / f'{E}-auth.json').write_text(json.dumps({**VALID, 'site_url': 'nope'}))
        with patch.object(auth_settings.subprocess, 'run') as run:
            self.assertEqual(auth_settings.apply(E), 1)
        run.assert_not_called()
        self.assertEqual(self.row()[0], 'failed')

    def test_the_applied_copy_is_private_and_is_what_starts_use(self):
        auth_settings.write_applied(E, auth_settings.validate(VALID))
        copy = auth_settings.applied_path(E)
        self.assertEqual(copy.stat().st_mode & 0o777, 0o600)
        self.assertEqual(auth_settings.load_applied(E)['revision'], 3)
        # The saved file moves on and fails; starts still use what was applied.
        (auth_settings.DIRECTORY / f'{E}-auth.json').write_text(json.dumps({**VALID, 'revision': 4, 'site_url': 'nope'}))
        self.assertEqual(durable_runtime.load_settings(E)['revision'], 3)

    def test_a_failed_apply_leaves_the_applied_copy_alone(self):
        auth_settings.write_applied(E, auth_settings.validate(VALID))
        (auth_settings.DIRECTORY / f'{E}-auth.json').write_text(json.dumps({**VALID, 'revision': 4}))
        with patch.object(auth_settings.subprocess, 'run', return_value=type('R', (), {'returncode': 1})()):
            self.assertEqual(auth_settings.apply(E), 1)
        self.assertEqual(auth_settings.load_applied(E)['revision'], 3)
        self.assertEqual(durable_runtime.load_settings(E)['revision'], 3)

    def test_an_applied_copy_is_never_created_through_a_planted_link(self):
        copy = auth_settings.applied_path(E)
        target = auth_settings.DIRECTORY / 'victim'
        target.write_text('x')
        copy.with_name(copy.name + '.pending').symlink_to(target)
        auth_settings.write_applied(E, auth_settings.validate(VALID))
        self.assertEqual(target.read_text(), 'x')
        self.assertEqual(copy.stat().st_mode & 0o777, 0o600)

    def test_removed_settings_are_recorded_as_applied(self):
        (auth_settings.DIRECTORY / f'{E}-auth.json').unlink()
        with patch.object(auth_settings.subprocess, 'run', return_value=type('R', (), {'returncode': 0})()):
            self.assertEqual(auth_settings.apply(E), 0)
        self.assertEqual(self.row()[0], 'applied')

    def test_a_dangling_link_is_a_refusal_not_no_settings(self):
        file = auth_settings.DIRECTORY / f'{E}-auth.json'
        file.unlink()
        file.symlink_to(auth_settings.DIRECTORY / 'missing.json')
        with self.assertRaises(auth_settings.SettingsError):
            auth_settings.load(E)
        with self.assertRaises(RuntimeError):
            durable_runtime.load_settings(E)

    def test_a_save_made_during_an_apply_stays_pending(self):
        def apply_while_a_newer_save_lands(*args, **kwargs):
            with closing(sqlite3.connect(self.catalog)) as database, database:
                database.execute("UPDATE auth_settings SET revision=4, state='pending' WHERE runtime=?", (E,))
            return type('R', (), {'returncode': 0})()
        with patch.object(auth_settings.subprocess, 'run', side_effect=apply_while_a_newer_save_lands):
            auth_settings.apply(E)
        state, failure, applied = self.row()
        self.assertEqual((state, applied), ('pending', 3))
        with closing(sqlite3.connect(self.catalog)) as database:
            self.assertEqual(database.execute('SELECT revision FROM auth_settings').fetchone()[0], 4)

    def test_a_start_refuses_a_bad_file_instead_of_using_defaults(self):
        (auth_settings.DIRECTORY / f'{E}-auth.json').write_text(json.dumps({**VALID, 'site_url': 'nope'}))
        with self.assertRaises(RuntimeError) as raised:
            durable_runtime.load_settings(E)
        self.assertNotIn('nope', str(raised.exception))
        # A file from a newer version is refused the same way, never half read.
        (auth_settings.DIRECTORY / f'{E}-auth.json').write_text(json.dumps({**VALID, 'version': 2}))
        with self.assertRaises(RuntimeError):
            durable_runtime.load_settings(E)

    def test_a_file_open_to_other_users_or_a_link_is_refused(self):
        file = auth_settings.DIRECTORY / f'{E}-auth.json'
        file.chmod(0o644)
        with self.assertRaises(auth_settings.SettingsError):
            auth_settings.load(E)
        file.chmod(0o600)
        target = auth_settings.DIRECTORY / 'other.json'
        file.rename(target)
        file.symlink_to(target)
        with self.assertRaises(auth_settings.SettingsError):
            auth_settings.load(E)

    def test_a_stale_pending_copy_is_removed_at_apply_but_a_fresh_one_is_not(self):
        stale = auth_settings.DIRECTORY / f'{E}-auth.json.pending'
        stale.write_text('{"secret": "left by a crash"}')
        with patch.object(auth_settings, 'busy', return_value=True):
            auth_settings.apply(E)
        # A save is renaming this copy right now; only one that has sat for a minute is a crash's.
        self.assertTrue(stale.exists())
        old = time.time() - 120
        os.utime(stale, (old, old))
        with patch.object(auth_settings, 'busy', return_value=True):
            auth_settings.apply(E)
        self.assertFalse(stale.exists())


class OwnerSetTests(unittest.TestCase):
    def test_no_registry_key_is_claimed_by_the_mail_owner(self):
        registry = json.loads((Path(__file__).resolve().parents[1] / 'src' / 'control' / 'auth-config-registry.json').read_text())
        for entry in registry['core']:
            variable = 'GOTRUE_' + entry['key']
            self.assertTrue(auth_settings.owned(variable), variable)
            self.assertFalse(durable_runtime.is_mail_key(variable), variable)


class StartTests(unittest.TestCase):
    def test_a_stray_previous_auth_comes_back_and_an_unchecked_new_one_goes(self):
        name = durable_runtime.PREFIX + '-' + E + '-auth'
        present = {name, name + '-previous'}
        calls = []
        target = Mock()
        target.endpoint.return_value = 'http://127.0.0.1:1'
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, item: {'Id': item} if item in present else None), \
             patch.object(durable_runtime.lab, 'docker', side_effect=lambda *args, **kwargs: calls.append(args)):
            durable_runtime.Runtime.restore_stray_previous(target, E)
        self.assertEqual(calls, [('rm', '-f', name), ('rename', name + '-previous', name), ('start', name)])
        target.wait.assert_called_once()
        target.publish_auth.assert_called_once_with(E, name)
        present.discard(name + '-previous')
        calls.clear()
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, item: {'Id': item} if item in present else None), \
             patch.object(durable_runtime.lab, 'docker', side_effect=lambda *args, **kwargs: calls.append(args)):
            durable_runtime.Runtime.restore_stray_previous(target, E)
        self.assertEqual(calls, [])

    def test_one_environments_unreadable_settings_do_not_stop_the_others(self):
        first, second = E, 'e_' + 'b' * 24
        target = Mock()
        target.values = {'environments': {first: {}, second: {}}}
        target.held = {}
        target.sql = Mock()
        target.resume.side_effect = [durable_runtime.SettingsRefused('first cannot be read'), None]
        with patch.object(durable_runtime.runtime_files, 'read_endpoints', return_value={first: {}, second: {}}), \
             patch.object(durable_runtime.source_fence, 'is_fenced', return_value=False):
            durable_runtime.Runtime.resume_published_environments(target)
        self.assertEqual([call.args[0] for call in target.resume.call_args_list], [first, second])


if __name__ == '__main__':
    unittest.main()
