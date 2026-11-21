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
             patch.object(self.runtime.auth_settings, 'write_applied', side_effect=(fail_after if isinstance(fail_after, BaseException) else RuntimeError('boom')) if fail_after else None) as kept, \
             patch.object(self.runtime.auth_settings, 'DIRECTORY', self.state), \
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
        message = durable_runtime.restore_custom_providers('http://auth.invalid', 'secret', {'custom:telegram': None}, send=down)
        self.assertIn('connection refused', message)

    def test_a_garbled_response_during_restore_is_reported_not_raised(self):
        import http.client
        def garbled(*args, **kwargs):
            raise http.client.IncompleteRead(b'')
        message = durable_runtime.restore_custom_providers('http://auth.invalid', 'secret', {'custom:telegram': None}, send=garbled)
        self.assertIn('IncompleteRead', message)

    def test_a_restore_that_raises_never_hides_the_original_failure(self):
        fake = FakeAuth()
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            self.reconcile(settings(), None, fake, fail_after=True, restore_error=KeyError('endpoint'))

    def test_a_good_apply_leaves_no_journal_and_a_failed_one_with_a_good_restore_none(self):
        self.reconcile(settings(), None, FakeAuth())
        self.assertFalse((self.state / f'{E}-auth.journal.json').exists())
        with self.assertRaises(RuntimeError):
            self.reconcile(settings(client_id='5'), None, FakeAuth(), fail_after=True)
        self.assertFalse((self.state / f'{E}-auth.journal.json').exists())

    def test_the_undo_plan_is_journaled_before_the_first_row_changes_and_replayed_after_a_crash(self):
        applied = settings(client_id='111', secret='old-secret')
        fake = FakeAuth([self.row()])
        seen = {}

        def spy(method, url, body, headers):
            if method in ('PUT', 'POST', 'DELETE'):
                journal = self.state / f'{E}-auth.journal.json'
                seen.setdefault('before_first_change', journal.exists())
                seen.setdefault('mode', oct(journal.stat().st_mode & 0o777))
            return fake(method, url, body, headers)
        # A crash is a BaseException nothing in reconcile_auth catches: the supervisor dies after the row was written.
        with self.assertRaises(SystemExit):
            self.reconcile(settings(), applied, spy, fail_after=SystemExit())
        self.assertTrue(seen['before_first_change'])
        self.assertEqual(seen['mode'], '0o600')
        self.assertEqual(fake.rows['custom:telegram']['client_id'], '123456789')
        # The next start brings the old Auth back and replays the journal.
        with patch.object(self.runtime.auth_settings, 'DIRECTORY', self.state), \
             patch.object(self.runtime, 'apply_custom_providers', lambda *a, **k: _real_apply(*a, **{**k, 'send': fake})):
            self.target.replay_custom_journal(E, 'auth')
        self.assertEqual((fake.rows['custom:telegram']['client_id'], fake.rows['custom:telegram']['secret']), ('111', 'old-secret'))
        self.assertFalse((self.state / f'{E}-auth.journal.json').exists())

    def test_a_replay_that_is_refused_keeps_the_journal_and_never_raises(self):
        (self.state / f'{E}-auth.journal.json').write_text('{"undo": {"custom:telegram": null}}')
        (self.state / f'{E}-auth.journal.json').chmod(0o600)
        down = FakeAuth([self.row()], refuse=(('GET', '/admin/custom-providers'), 503))
        with patch.object(self.runtime.auth_settings, 'DIRECTORY', self.state), \
             patch.object(self.runtime, 'apply_custom_providers', lambda *a, **k: _real_apply(*a, **{**k, 'send': down})):
            self.target.replay_custom_journal(E, 'auth')
        self.assertTrue((self.state / f'{E}-auth.journal.json').exists())

    def test_the_admin_calls_ignore_proxy_variables(self):
        import http.server
        import os
        import threading
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"providers": []}')

            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        # A proxy that nothing listens on: a call that honoured it would fail instead of reaching the server.
        proxies = {'http_proxy': 'http://127.0.0.1:9', 'HTTP_PROXY': 'http://127.0.0.1:9', 'no_proxy': '', 'NO_PROXY': ''}
        with patch.dict(os.environ, proxies):
            durable_runtime.apply_custom_providers(f'http://127.0.0.1:{server.server_address[1]}', 'secret',
                                                   {'custom:telegram': None})
        self.assertEqual(seen, ['/admin/custom-providers'])


class FileShapeTests(unittest.TestCase):
    """The main settings file keeps the shape an older build reads; Telegram has a file of its own beside it."""

    OLD_PROVIDERS = tuple(name for name in auth_settings.PROVIDERS if name != 'telegram')

    def setUp(self):
        import tempfile
        from pathlib import Path
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        patcher = patch.object(auth_settings, 'DIRECTORY', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def both(self, **telegram):
        return auth_settings.validate({**BASE, 'providers': {'github': {'enabled': True, 'client_id': 'g', 'secret': 's', 'url': ''},
                                                              'telegram': {'enabled': True, 'client_id': '123456789', 'secret': 'bot-secret', 'url': '', **telegram}}})

    def old_reader_accepts(self, document):
        """What a build from before Telegram does with the main file: refuses any provider it does not know, and any newer version."""
        return (set(document.get('providers', {})) <= set(self.OLD_PROVIDERS)) and document.get('version', 1) == 1

    def test_the_main_file_has_no_telegram_and_stays_version_one_while_nothing_newer_is_on(self):
        for entry in ({}, {'enabled': False}):
            document = auth_settings.file_form(self.both(**entry))
            self.assertNotIn('telegram', document['providers'])
            self.assertEqual(document['version'], 1)
            self.assertTrue(self.old_reader_accepts(document))
            self.assertNotIn('bot-secret', json.dumps(document))

    def test_write_applied_and_load_round_trip_through_both_files(self):
        settings_in = self.both()
        auth_settings.write_applied(E, settings_in)
        main = json.loads((self.root / f'{E}-auth.applied.json').read_text())
        sibling = json.loads((self.root / f'{E}-auth.telegram.applied.json').read_text())
        self.assertTrue(self.old_reader_accepts(main))
        self.assertEqual(sibling['client_id'], '123456789')
        self.assertEqual(oct((self.root / f'{E}-auth.telegram.applied.json').stat().st_mode & 0o777), '0o600')
        self.assertEqual(auth_settings.load_applied(E), settings_in)

    def test_load_merges_the_sibling_and_a_main_file_alone_loads_without_telegram(self):
        settings_in = self.both()
        main, custom = auth_settings.split_telegram(settings_in)
        main_file = self.root / f'{E}-auth.json'
        main_file.write_text(json.dumps(auth_settings.file_form(settings_in)))
        main_file.chmod(0o600)
        self.assertNotIn('telegram', auth_settings.load(E)['providers'])
        sibling = self.root / f'{E}-auth.telegram.json'
        sibling.write_text(json.dumps(custom['telegram']))
        sibling.chmod(0o600)
        self.assertEqual(auth_settings.load(E), settings_in)
        sibling.chmod(0o644)
        with self.assertRaises(auth_settings.SettingsError):
            auth_settings.load(E)

    def test_removing_the_applied_copy_removes_both_files_and_a_save_without_telegram_drops_the_sibling(self):
        auth_settings.write_applied(E, self.both())
        auth_settings.write_applied(E, auth_settings.validate({**BASE, 'providers': {}}))
        self.assertFalse((self.root / f'{E}-auth.telegram.applied.json').exists())
        auth_settings.write_applied(E, self.both())
        auth_settings.remove_applied(E)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [])

    def test_a_telegram_only_file_written_by_the_first_version_still_loads(self):
        # A main file that still carries telegram (the first commit wrote it so) is read as before.
        file = self.root / f'{E}-auth.json'
        file.write_text(json.dumps({**BASE, 'version': 2, 'providers': {'telegram': {'enabled': True, 'client_id': '1', 'secret': 's', 'url': '',
                                                                                  'skip_nonce_check': False, 'email_optional': True}}}))
        file.chmod(0o600)
        self.assertEqual(auth_settings.load(E)['providers']['telegram']['client_id'], '1')


_real_apply = durable_runtime.apply_custom_providers


if __name__ == '__main__':
    unittest.main()
