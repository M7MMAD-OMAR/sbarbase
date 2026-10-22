"""Rotating an environment's JWT signing secret: what may change in a retained container, the
order of the rotation, and every service that holds the secret taking the new one."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import durable_runtime
import realtime

E = 'e_' + 'a' * 24


class SigningOnlyTests(unittest.TestCase):
    base = {'GOTRUE_JWT_SECRET': 'old', 'GOTRUE_SITE_URL': 'http://localhost', 'DATABASE_URL': 'postgres://x'}

    def test_a_new_secret_alone_or_with_sign_in_settings_may_replace_auth(self):
        self.assertTrue(durable_runtime.signing_only('auth', self.base, {**self.base, 'GOTRUE_JWT_SECRET': 'new'}))
        self.assertTrue(durable_runtime.signing_only('auth', self.base, {**self.base, 'GOTRUE_JWT_SECRET': 'new',
                                                                         'GOTRUE_SITE_URL': 'https://app.example.com'}))
        self.assertTrue(durable_runtime.signing_only('rest', {'PGRST_JWT_SECRET': 'old', 'PGRST_DB_POOL': '3'},
                                                     {'PGRST_JWT_SECRET': 'new', 'PGRST_DB_POOL': '3'}))

    def test_anything_else_is_still_drift(self):
        self.assertFalse(durable_runtime.signing_only('auth', self.base, dict(self.base)))
        self.assertFalse(durable_runtime.signing_only('auth', self.base, {**self.base, 'GOTRUE_JWT_SECRET': 'new', 'DATABASE_URL': 'postgres://y'}))
        self.assertFalse(durable_runtime.signing_only('rest', {'PGRST_JWT_SECRET': 'old', 'PGRST_DB_POOL': '3'},
                                                      {'PGRST_JWT_SECRET': 'new', 'PGRST_DB_POOL': '9'}))
        self.assertFalse(durable_runtime.signing_only('storage', {'X': '1'}, {'X': '2'}))


class RotationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'state').mkdir()
        (self.root / 'private').mkdir()
        self.endpoints_file = self.root / 'state' / 'endpoints.json'
        self.endpoints_file.write_text(json.dumps({E: {'auth': 'http://a', 'realtime': {'url': 'http://r', 'migrated': 'pin'},
                                                       'functions': {'url': 'http://f'}, 'database': {'user': f'{E}_developer', 'database': E}}}))
        runtime = object.__new__(durable_runtime.Runtime)
        runtime.path = self.root / 'private' / 'runtime.json'
        runtime.values = {'storage_admin': 's', 'environments': {E: {k: k * 8 for k in ('auth', 'rest', 'storage')} | {'jwt': 'old' * 16}}}
        durable_runtime.atomic(runtime.path, runtime.values)
        runtime.pins = {'realtime': {'id': 'pin'}}
        runtime.launch = MagicMock(return_value=('id', True))
        runtime.wait = MagicMock()
        runtime.endpoint = lambda name, port: f'http://{name}:{port}'
        self.statements = []
        runtime.sql = lambda query, database='postgres', check=True: self.statements.append((database, query)) or SimpleNamespace(stdout='t', returncode=0)
        runtime.realtime_start = MagicMock(return_value={'url': 'http://r2', 'migrated': 'pin'})
        runtime.functions_start = MagicMock(return_value={'url': 'http://f2'})
        self.runtime = runtime
        self.requests = []
        self.revoke = MagicMock(return_value=True)

        def http(url, method='GET', data=None, headers=None):
            self.requests.append((method, url, json.loads(data) if data else None))
            return (200 if method == 'GET' else 204), b''
        for item in [patch.object(durable_runtime, 'STATE', self.root / 'state'), patch.object(durable_runtime, 'PRIVATE', self.root / 'private'),
                     patch.object(durable_runtime, 'inspect', return_value={'Id': 'c'}), patch.object(durable_runtime, 'http', side_effect=http),
                     patch.object(durable_runtime.effect_receipt, 'require_settled'),
                     patch.object(durable_runtime.source_fence, 'is_fenced', return_value=False),
                     patch.object(durable_runtime.mail_config, 'load', return_value=None),
                     patch.object(durable_runtime, 'load_settings', return_value=None),
                     patch.object(durable_runtime, 'revoke_auth_sessions', self.revoke)]:
            item.start()
            self.addCleanup(item.stop)

    def saved(self):
        return json.loads(self.runtime.path.read_text())['environments'][E]

    def test_every_service_takes_the_new_secret_and_the_mark_is_cleared_last(self):
        self.runtime.rotate_signing(E)
        secret = self.saved()['jwt']
        self.assertNotEqual(secret, 'old' * 16)
        self.assertEqual(len(secret), 64)
        self.assertNotIn('jwt_rotating', self.saved())
        launched = {call.args[1]: call for call in self.runtime.launch.call_args_list}
        self.assertEqual(set(launched), {'auth', 'rest'})
        self.assertTrue(all(call.kwargs['rotating'] for call in launched.values()))
        self.assertEqual(launched['auth'].args[2]['GOTRUE_JWT_SECRET'], secret)
        self.assertEqual(launched['rest'].args[2]['PGRST_JWT_SECRET'], secret)
        patched = [body for method, url, body in self.requests if method == 'PATCH']
        self.assertEqual(len(patched), 1)
        self.assertEqual(patched[0]['jwtSecret'], secret)
        self.runtime.realtime_start.assert_called_once_with(E, migrate=True)
        self.runtime.functions_start.assert_called_once_with(E)
        # Ended as the Auth login with its own password, never through the superuser's connection.
        self.revoke.assert_called_once_with(E, 'auth' * 8)
        self.assertFalse([query for _, query in self.statements if 'auth.sessions' in query or 'refresh_tokens' in query])
        endpoints = json.loads(self.endpoints_file.read_text())[E]
        self.assertEqual(endpoints['database'], {'user': f'{E}_developer', 'database': E})
        self.assertEqual(endpoints['realtime']['url'], 'http://r2')

    def test_an_interrupted_rotation_is_finished_with_the_same_secret(self):
        values = self.runtime.values['environments'][E]
        values.update(jwt='new' * 16, jwt_rotating=True)
        durable_runtime.atomic(self.runtime.path, self.runtime.values)
        self.runtime.realtime_start.side_effect = RuntimeError('stopped halfway')
        with self.assertRaises(RuntimeError):
            self.runtime.rotate_signing(E)
        self.assertTrue(self.saved()['jwt_rotating'])
        self.runtime.realtime_start.side_effect = None
        self.runtime.rotate_signing(E)
        self.assertEqual(self.saved()['jwt'], 'new' * 16)
        self.assertNotIn('jwt_rotating', self.saved())

    def test_an_ordinary_start_changes_nothing_that_holds_the_secret(self):
        self.runtime.activate_services(E, self.runtime.values['environments'][E], creating=False)
        self.assertFalse(any(call.kwargs['rotating'] for call in self.runtime.launch.call_args_list))
        self.assertFalse([method for method, _, _ in self.requests if method == 'PATCH'])
        self.runtime.realtime_start.assert_called_once_with(E, migrate=False)
        self.assertFalse([query for _, query in self.statements if 'auth.sessions' in query])
        self.revoke.assert_not_called()
        self.assertEqual(self.saved()['jwt'], 'old' * 16)

    def test_sessions_that_cannot_be_ended_fail_the_rotation_and_keep_the_mark(self):
        self.revoke.return_value = False
        with self.assertRaises(RuntimeError):
            self.runtime.rotate_signing(E)
        self.assertTrue(self.saved()['jwt_rotating'])
        self.revoke.return_value = True
        self.runtime.rotate_signing(E)
        self.assertNotIn('jwt_rotating', self.saved())

    def test_a_start_that_cannot_end_them_goes_on_and_keeps_the_mark(self):
        values = self.runtime.values['environments'][E]
        values.update(jwt='new' * 16, jwt_rotating=True)
        durable_runtime.atomic(self.runtime.path, self.runtime.values)
        self.revoke.return_value = False
        with patch('sys.stderr'):
            self.runtime.activate_services(E, values, creating=False)
        self.assertTrue(self.saved()['jwt_rotating'])
        self.assertEqual(self.saved()['jwt'], 'new' * 16)

    def test_rotation_needs_a_published_environment(self):
        self.endpoints_file.write_text('{}')
        with self.assertRaises(RuntimeError):
            self.runtime.rotate_signing(E)
        self.assertEqual(self.saved()['jwt'], 'old' * 16)


class RevokeTests(unittest.TestCase):
    password = 'ab' * 32

    def run_revoke(self, outcome):
        with patch.object(durable_runtime.subprocess, 'run', side_effect=outcome) as run, \
             patch.object(durable_runtime.lab, 'docker') as docker, \
             patch.object(durable_runtime.docker_profile, 'docker_command', side_effect=lambda *args: ['docker', *args]):
            result = durable_runtime.revoke_auth_sessions(E, self.password, 'db')
        return result, run, docker

    def test_it_connects_as_the_auth_login_with_the_password_on_stdin(self):
        result, run, docker = self.run_revoke(lambda *a, **k: SimpleNamespace(returncode=0))
        self.assertTrue(result)
        command, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertNotIn(self.password, ' '.join(command))
        self.assertNotIn('supabase_admin', command)
        self.assertEqual(command[-2:], [f'{E}_auth', E])
        self.assertIn('-h 127.0.0.1', command[command.index('-c') + 1])
        self.assertTrue(kwargs['input'].startswith(self.password + '\n'))
        self.assertEqual(kwargs['timeout'], durable_runtime.SESSIONS_REVOKE_SECONDS)
        docker.assert_not_called()

    def test_the_statement_is_bounded_and_one_transaction(self):
        statement = durable_runtime.SESSIONS_REVOKE
        self.assertIn("SET lock_timeout = '5s'", statement)
        self.assertIn("SET statement_timeout = '30s'", statement)
        self.assertLess(statement.index('statement_timeout'), statement.index('BEGIN'))
        self.assertTrue(statement.endswith('COMMIT;'))

    def test_a_failure_is_reported_not_raised(self):
        result, _, docker = self.run_revoke(lambda *a, **k: SimpleNamespace(returncode=3))
        self.assertFalse(result)
        docker.assert_not_called()

    def test_running_out_of_time_ends_the_auth_logins_sessions(self):
        def slow(command, **kwargs):
            raise durable_runtime.subprocess.TimeoutExpired(command, kwargs['timeout'])
        result, _, docker = self.run_revoke(slow)
        self.assertFalse(result)
        self.assertIn(f"usename = '{E}_auth'", docker.call_args.kwargs['data'])
        self.assertIn('pg_terminate_backend', docker.call_args.kwargs['data'])

    def test_only_a_generated_password_is_used(self):
        for e, password in ((E, 'not hex'), (E, None), ('postgres', self.password)):
            with self.assertRaises(RuntimeError):
                durable_runtime.revoke_auth_sessions(e, password)


class ApplyTests(unittest.TestCase):
    def test_a_rotation_request_runs_the_signing_command_and_records_done(self):
        with patch.object(realtime, 'desired', return_value='rotate'), patch.object(realtime, 'busy', return_value=False), \
                patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
                patch.object(realtime, 'record') as record:
            self.assertEqual(realtime.apply(E, 'signing'), 0)
        self.assertEqual(run.call_args.args[0][-2:], ['signing', E])
        record.assert_called_once_with(E, 'done', None, 'signing')

    def test_a_failed_rotation_says_how_it_finishes(self):
        with patch.object(realtime, 'desired', return_value='rotate'), patch.object(realtime, 'busy', return_value=False), \
                patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
                patch.object(realtime, 'record') as record:
            self.assertEqual(realtime.apply(E, 'signing'), 1)
        self.assertIn('Rotate again', record.call_args.args[2])
        with patch.object(realtime, 'desired', return_value='on'):
            self.assertEqual(realtime.apply(E, 'signing'), 1)


if __name__ == '__main__':
    unittest.main()
