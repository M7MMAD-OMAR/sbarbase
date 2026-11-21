"""Log In With Telegram: a custom provider row of Auth, not a variable (docs/engineering/plans/2026-11-21-telegram-login.md)."""
import json
import unittest
from unittest.mock import Mock, patch

import auth_settings
import durable_runtime

E = 'e_' + 'd' * 24
BASE = {'revision': 1, 'site_url': 'https://app.example.com', 'redirect_urls': []}


def settings(**telegram):
    entry = {'enabled': True, 'client_id': '123456789', 'secret': 'bot-secret', 'url': '', **telegram}
    return auth_settings.validate({**BASE, 'providers': {'telegram': entry}})


class Recorder:
    """A fake Auth admin API: remembers the calls, answers from a table."""
    def __init__(self, present=(), refuse=None):
        self.calls, self.present, self.refuse = [], list(present), refuse

    def __call__(self, method, url, body, headers):
        path = url.split('http://auth', 1)[1]
        self.calls.append((method, path, body, headers['authorization'].startswith('Bearer ')))
        if self.refuse and (method, path) == self.refuse[0]:
            return self.refuse[1], json.dumps({'error_code': 'validation_failed', 'msg': 'secret-in-body'}).encode()
        if method == 'GET':
            return 200, json.dumps({'providers': [{'identifier': i, 'provider_type': 'oidc', 'issuer': 'https://oauth.telegram.org'} for i in self.present]}).encode()
        return {'POST': 201, 'PUT': 200, 'DELETE': 204}[method], b'{}'


class FakeAuth:
    """A stateful fake of Auth's custom provider admin API."""
    def __init__(self, rows=(), refuse=None):
        self.rows = {r['identifier']: dict(r) for r in rows}
        self.calls, self.refuse = [], refuse

    def __call__(self, method, url, body, headers):
        path = url.split('http://auth', 1)[1]
        self.calls.append((method, path, body))
        if self.refuse and (method, path) == self.refuse[0]:
            return self.refuse[1], b'{}'
        if method == 'GET':
            return 200, json.dumps({'providers': list(self.rows.values())}).encode()
        identifier = body['identifier'] if method == 'POST' else path.rsplit('/', 1)[1].replace('%3A', ':')
        if method == 'POST':
            self.rows[identifier] = {k: v for k, v in body.items() if k != 'client_secret'} | {'secret': body['client_secret']}
            return 201, b'{}'
        if method == 'PUT':
            self.rows[identifier].update({('secret' if k == 'client_secret' else k): v for k, v in body.items()})
            return 200, b'{}'
        del self.rows[identifier]
        return 204, b''


class ValidationTests(unittest.TestCase):
    def test_the_bot_id_is_digits_the_address_and_nonce_switch_are_refused_and_email_is_optional(self):
        self.assertTrue(settings()['providers']['telegram']['email_optional'])
        self.assertTrue(settings(email_optional=False)['providers']['telegram']['email_optional'])
        for change in ({'client_id': 'bot-1'}, {'client_id': '1' * 21}, {'url': 'https://oauth.telegram.org'}, {'skip_nonce_check': True}):
            with self.assertRaises(auth_settings.SettingsError) as error:
                settings(**change)
            self.assertEqual(str(error.exception), 'telegram')
            self.assertNotIn('bot-secret', str(error.exception))
        with self.assertRaises(auth_settings.SettingsError):
            settings(secret='')


class ConfigurationTests(unittest.TestCase):
    def test_telegram_adds_no_variable_and_gives_auth_a_way_out(self):
        config = auth_settings.configuration(E, settings())
        self.assertFalse([k for k in config if 'TELEGRAM' in k])
        self.assertFalse([v for v in config.values() if v == 'bot-secret'])
        self.assertTrue(durable_runtime.egress_wanted(None, settings(), E))
        self.assertFalse(durable_runtime.egress_wanted(None, settings(enabled=False), E))
        # GOTRUE_CUSTOM_OAUTH_* are the operator's own variables, never Sbarbase's: no prefix claims them.
        self.assertFalse(auth_settings.owned('GOTRUE_CUSTOM_OAUTH_ENABLED'))

    def test_the_payload_is_the_fixed_oidc_provider_with_the_user_id_claim(self):
        payload = auth_settings.custom_providers(settings())['custom:telegram']
        self.assertEqual(payload, {'provider_type': 'oidc', 'identifier': 'custom:telegram', 'name': 'Telegram', 'client_id': '123456789',
                                   'client_secret': 'bot-secret', 'issuer': 'https://oauth.telegram.org', 'scopes': ['openid', 'profile'],
                                   'pkce_enabled': True, 'email_optional': True, 'enabled': True, 'custom_claims_allowlist': ['id']})
        self.assertNotIn('discovery_url', payload)

    def test_disabled_deletes_and_absent_touches_nothing(self):
        self.assertEqual(auth_settings.custom_providers(settings(enabled=False)), {'custom:telegram': None})
        self.assertEqual(auth_settings.custom_providers(auth_settings.validate({**BASE, 'providers': {}})), {})
        self.assertEqual(auth_settings.custom_providers(None), {})

    def test_an_entry_the_last_apply_had_and_these_settings_dropped_is_deleted(self):
        applied = settings()
        empty = auth_settings.validate({**BASE, 'providers': {}})
        self.assertEqual(auth_settings.custom_providers(empty, applied), {'custom:telegram': None})
        self.assertEqual(auth_settings.custom_providers(None, applied), {'custom:telegram': None})
        self.assertEqual(auth_settings.custom_providers(None, empty), {})
        self.assertEqual(auth_settings.custom_providers(settings(), applied)['custom:telegram']['issuer'], 'https://oauth.telegram.org')


class ApplyTests(unittest.TestCase):
    def test_a_row_of_another_type_or_issuer_is_deleted_and_created_again(self):
        wanted = auth_settings.custom_providers(settings())
        for foreign in ({'identifier': 'custom:telegram', 'provider_type': 'oauth2'},
                        {'identifier': 'custom:telegram', 'provider_type': 'oidc', 'issuer': 'https://evil.example.com'}):
            fake = FakeAuth([foreign])
            durable_runtime.apply_custom_providers('http://auth', 'j', wanted, send=fake)
            self.assertEqual([m for m, *_ in fake.calls], ['GET', 'DELETE', 'POST'])
            self.assertEqual(fake.rows['custom:telegram']['issuer'], 'https://oauth.telegram.org')

    def test_a_deletion_the_server_refuses_and_a_failed_list_raise(self):
        with self.assertRaises(RuntimeError) as error:
            self.run_apply(auth_settings.custom_providers(settings(enabled=False)), present=['custom:telegram'],
                           refuse=(('DELETE', '/admin/custom-providers/custom:telegram'), 500))
        self.assertIn('removal', str(error.exception))
        self.assertIn('HTTP 500', str(error.exception))
        with self.assertRaises(RuntimeError) as error:
            self.run_apply(auth_settings.custom_providers(settings()), refuse=(('GET', '/admin/custom-providers'), 503))
        self.assertIn('list', str(error.exception))

    def test_two_applies_in_a_row_create_once_then_update(self):
        fake = FakeAuth()
        wanted = auth_settings.custom_providers(settings())
        durable_runtime.apply_custom_providers('http://auth', 'j', wanted, send=fake)
        durable_runtime.apply_custom_providers('http://auth', 'j', wanted, send=fake)
        self.assertEqual([m for m, *_ in fake.calls], ['GET', 'POST', 'GET', 'PUT'])

    def run_apply(self, wanted, **kw):
        fake = Recorder(**kw)
        durable_runtime.apply_custom_providers('http://auth', 'jwt-secret', wanted, send=fake)
        return fake.calls

    def test_nothing_wanted_calls_nothing(self):
        self.assertEqual(self.run_apply({}), [])

    def test_a_new_provider_is_created_with_the_service_token(self):
        calls = self.run_apply(auth_settings.custom_providers(settings()))
        self.assertEqual([(m, p) for m, p, _, _ in calls], [('GET', '/admin/custom-providers'), ('POST', '/admin/custom-providers')])
        self.assertTrue(all(auth for _, _, _, auth in calls))
        self.assertEqual(calls[1][2]['issuer'], 'https://oauth.telegram.org')

    def test_the_list_has_no_type_filter(self):
        self.assertEqual(self.run_apply(auth_settings.custom_providers(settings()))[0][1], '/admin/custom-providers')

    def test_an_existing_provider_is_updated_without_the_issuer_so_nothing_is_fetched(self):
        calls = self.run_apply(auth_settings.custom_providers(settings()), present=['custom:telegram', 'custom:mine'])
        self.assertEqual([(m, p) for m, p, _, _ in calls], [('GET', '/admin/custom-providers'), ('PUT', '/admin/custom-providers/custom:telegram')])
        for forbidden in ('issuer', 'identifier', 'provider_type'):
            self.assertNotIn(forbidden, calls[1][2])
        self.assertEqual(calls[1][2]['client_secret'], 'bot-secret')

    def test_disabled_deletes_only_its_own_row_and_only_when_present(self):
        wanted = auth_settings.custom_providers(settings(enabled=False))
        calls = self.run_apply(wanted, present=['custom:telegram', 'custom:mine'])
        self.assertEqual([m for m, *_ in calls], ['GET', 'DELETE'])
        self.assertEqual(calls[1][1], '/admin/custom-providers/custom:telegram')
        self.assertEqual([m for m, *_ in self.run_apply(wanted, present=['custom:mine'])], ['GET'])

    def test_a_refusal_names_status_and_code_but_never_the_body(self):
        with self.assertRaises(RuntimeError) as error:
            self.run_apply(auth_settings.custom_providers(settings()), refuse=(('POST', '/admin/custom-providers'), 400))
        self.assertIn('HTTP 400', str(error.exception))
        self.assertIn('validation_failed', str(error.exception))
        self.assertNotIn('secret-in-body', str(error.exception))
        self.assertNotIn('bot-secret', str(error.exception))


class ReconcileTests(unittest.TestCase):
    """reconcile_auth itself, with its daemon calls faked and the custom provider calls going to a stateful fake Auth."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        self.runtime = durable_runtime
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.target = durable_runtime.Runtime.__new__(durable_runtime.Runtime)
        self.target.values = {'environments': {E: {'auth': 'a' * 64, 'rest': 'b' * 64, 'storage': 'c' * 64, 'jwt': 'd' * 64}}}
        for name in ('launch', 'wait', 'publish_auth', 'sql', 'pending'):
            setattr(self.target, name, Mock())
        self.target.endpoint = Mock(return_value='http://auth')
        self.docker = lambda *a, **k: SimpleNamespace(returncode=0, stdout='')

    def inspect(self, kind, name):
        if kind == 'network':
            return {'Name': name, 'Internal': False, 'Labels': {'io.sbarbase.owner': self.runtime.OWNER}, 'Options': {self.runtime.NO_ICC: 'false'}}
        return {'Id': 'cid', 'NetworkSettings': {'Networks': {self.runtime.NETWORK: {'IPAddress': '127.0.0.1'}}}, 'Config': {'Env': []}}

    def reconcile(self, settings, applied, fake, fail_after=False, restore_error=None):
        real_apply, real_restore = durable_runtime.apply_custom_providers, durable_runtime.restore_custom_providers
        with patch.object(self.runtime, 'STATE', self.state), patch.object(self.runtime, 'inspect', self.inspect), \
             patch.object(self.runtime.lab, 'docker', self.docker), \
             patch.object(self.runtime.effect_receipt, 'require_settled'), \
             patch.object(self.runtime.runtime_files, 'read_endpoints', return_value={E: {}}), \
             patch.object(self.runtime.rolling, 'require_settled'), \
             patch.object(self.runtime.source_fence, 'is_fenced', return_value=False), \
             patch.object(self.runtime.auth_settings, 'load', return_value=settings), \
             patch.object(self.runtime.auth_settings, 'load_applied', return_value=applied), \
             patch.object(self.runtime.auth_settings, 'write_applied', side_effect=RuntimeError('boom') if fail_after else None) as kept, \
             patch.object(self.runtime.auth_settings, 'applied_path'), \
             patch.object(self.runtime.mail_config, 'load', return_value=None), \
             patch.object(self.runtime, 'apply_custom_providers', lambda *a, **k: real_apply(*a, **{**k, 'send': fake})), \
             patch.object(self.runtime, 'restore_custom_providers', (lambda *a, **k: (_ for _ in ()).throw(restore_error)) if restore_error else (lambda *a, **k: real_restore(*a, **{**k, 'send': fake}))):
            try:
                self.target.reconcile_auth(E)
            finally:
                self.kept = kept

    def row(self, **changes):
        base = {'identifier': 'custom:telegram', 'provider_type': 'oidc', 'issuer': 'https://oauth.telegram.org', 'client_id': '111', 'secret': 'old-secret'}
        return {**base, **changes}

    def test_a_good_apply_writes_the_row_once_the_settings_say_enabled(self):
        fake = FakeAuth()
        self.reconcile(settings(), None, fake)
        self.assertEqual(fake.rows['custom:telegram']['client_id'], '123456789')
        self.kept.assert_called_once()

    def test_a_failed_enable_removes_the_row_it_made(self):
        fake = FakeAuth()
        with self.assertRaises(RuntimeError):
            self.reconcile(settings(), None, fake, fail_after=True)
        self.assertEqual(fake.rows, {})

    def test_a_failed_enable_of_a_changed_entry_puts_the_old_one_back(self):
        applied = settings(client_id='111', secret='old-secret')
        fake = FakeAuth([self.row()])
        with self.assertRaises(RuntimeError):
            self.reconcile(settings(), applied, fake, fail_after=True)
        self.assertEqual((fake.rows['custom:telegram']['client_id'], fake.rows['custom:telegram']['secret']), ('111', 'old-secret'))

    def test_a_failed_disable_creates_the_deleted_row_again_from_the_applied_settings(self):
        applied = settings(client_id='111', secret='old-secret')
        fake = FakeAuth([self.row()])
        with self.assertRaises(RuntimeError):
            self.reconcile(settings(enabled=False), applied, fake, fail_after=True)
        self.assertEqual((fake.rows['custom:telegram']['client_id'], fake.rows['custom:telegram']['secret']), ('111', 'old-secret'))

    def test_a_good_disable_deletes_the_row_and_removed_settings_do_too(self):
        applied = settings(client_id='111', secret='old-secret')
        fake = FakeAuth([self.row()])
        self.reconcile(settings(enabled=False), applied, fake)
        self.assertEqual(fake.rows, {})
        fake = FakeAuth([self.row()])
        self.reconcile(None, applied, fake)
        self.assertEqual(fake.rows, {})

    def test_settings_that_never_named_telegram_leave_a_hand_made_row_alone(self):
        fake = FakeAuth([self.row()])
        self.reconcile(auth_settings.validate({**BASE, 'providers': {}}), None, fake)
        self.assertEqual([m for m, *_ in fake.calls], [])
        self.assertIn('custom:telegram', fake.rows)

    def test_a_failure_before_any_row_changed_restores_nothing(self):
        fake = FakeAuth([self.row()], refuse=(('GET', '/admin/custom-providers'), 503))
        with self.assertRaises(RuntimeError):
            self.reconcile(settings(), None, fake)
        self.assertEqual([m for m, *_ in fake.calls], ['GET'])
        self.assertIn('custom:telegram', fake.rows)

    def test_a_transport_error_during_restore_is_reported_not_raised(self):
        def down(*args, **kwargs):
            raise OSError('connection refused')
        message = durable_runtime.restore_custom_providers('http://auth.invalid', 'secret', {'custom:telegram': None}, {}, {}, send=down)
        self.assertIn('connection refused', message)

    def test_a_garbled_response_during_restore_is_reported_not_raised(self):
        import http.client
        def garbled(*args, **kwargs):
            raise http.client.IncompleteRead(b'')
        message = durable_runtime.restore_custom_providers('http://auth.invalid', 'secret', {'custom:telegram': None}, {}, {}, send=garbled)
        self.assertIn('IncompleteRead', message)

    def test_a_restore_that_raises_never_hides_the_original_failure(self):
        fake = FakeAuth()
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            self.reconcile(settings(), None, fake, fail_after=True, restore_error=KeyError('endpoint'))


if __name__ == '__main__':
    unittest.main()
