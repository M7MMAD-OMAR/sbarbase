"""Edge Functions per environment: the container, its mounts and network, the keys it gets, and the apply."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import durable_runtime
import realtime
import resource_policy

E = 'e_' + 'a' * 24
OTHER = 'e_' + 'b' * 24


class FunctionsRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.runtime = object.__new__(durable_runtime.Runtime)
        self.runtime.values = {'environments': {E: {'jwt': 'j' * 64}, OTHER: {'jwt': 'k' * 64}}}
        self.runtime.pins = {'functions': {'id': 'sha256:fn'}}
        self.docker = []
        for item in [patch.object(durable_runtime, 'STATE', self.root / 'state'), patch.object(durable_runtime, 'PRIVATE', self.root / 'private'),
                     patch.object(durable_runtime, 'inspect', return_value=None),
                     patch.object(durable_runtime.lab, 'docker', side_effect=lambda *args, **kw: self.docker.append(args) or SimpleNamespace(stdout='', returncode=0)),
                     patch.object(durable_runtime.lab, 'secure_file', side_effect=lambda path, text: Path(path).write_text(text))]:
            item.start()
            self.addCleanup(item.stop)

    def test_functions_get_only_their_own_environment_keys_and_services(self):
        config = self.runtime.functions_configuration(E, self.runtime.values['environments'][E])
        self.assertEqual(config['SB_REST_URL'], f'http://sbarbase-durable-{E}-rest:3000')
        self.assertEqual(config['SB_AUTH_URL'], f'http://sbarbase-durable-{E}-auth:9999')
        self.assertEqual(config['SB_STORAGE_HOST'], f'{E}.storage.internal')
        self.assertEqual(config['SB_JWT_SECRET'], 'j' * 64)
        self.assertNotIn('k' * 64, json.dumps(config))
        claims = json.loads(durable_runtime.base64.urlsafe_b64decode(config['SUPABASE_SERVICE_ROLE_KEY'].split('.')[1] + '=='))
        self.assertEqual(claims['role'], 'service_role')
        self.assertGreater(claims['exp'] - claims['iat'], 9 * 365 * 24 * 3600)

    def test_the_container_mounts_its_own_code_and_secrets_read_only_and_reaches_the_internet_on_its_own_network(self):
        with patch.object(self.runtime, 'launch') as launch, patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000'), \
             patch.object(durable_runtime, 'http', return_value=(404, b'{}')):
            entry = self.runtime.functions_start(E)
        self.assertEqual(entry, {'url': 'http://10.0.0.9:9000', 'image': 'sha256:fn'})
        name, component = launch.call_args[0][:2]
        self.assertEqual((name, component), (f'sbarbase-durable-{E}-functions', 'functions'))
        binds = dict((str(destination), str(source)) for source, destination in launch.call_args.kwargs['binds'])
        self.assertTrue(binds['/home/deno/functions'].endswith(f'functions/{E}'))
        self.assertTrue(binds['/run/sbarbase'].startswith(str(self.root / 'private')))
        self.assertNotIn(OTHER, json.dumps(binds))
        self.assertEqual(launch.call_args.kwargs['tier'], 'production.functions')
        self.assertIn(('network', 'connect', durable_runtime.EGRESS, name), self.docker)
        self.assertEqual(json.loads((self.root / 'private' / 'functions' / E / 'secrets.json').read_text()), {})

    def test_a_runtime_that_never_answers_fails_the_start(self):
        with patch.object(self.runtime, 'launch'), patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000'), \
             patch.object(durable_runtime, 'http', side_effect=OSError), patch.object(durable_runtime.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                self.runtime.functions_start(E)

    def test_binds_are_read_only(self):
        with patch.object(durable_runtime.resource_policy, 'container_flags', return_value={'memory': '384m', 'cpus': .5, 'label': 'production', 'pids': 256, 'shares': 512, 'weight': 400}), \
             patch.object(durable_runtime.resource_policy, 'io_flags', return_value=[]), \
             patch.object(durable_runtime.lab, 'secure_file'):
            self.runtime.launch('c', 'functions', {}, '384m', .5, tier='production.functions', binds=[('/src', '/dst')])
        run = next(args for args in self.docker if args and args[0] == 'run')
        self.assertIn('/src:/dst:ro', run)

    def test_each_environment_with_functions_adds_its_own_row(self):
        base, cpus = resource_policy.start_placement(2, 0)
        with_one, cpus_one = resource_policy.start_placement(2, 0, 1)
        self.assertEqual(with_one - base, resource_policy.memory_mib(resource_policy.TIERS['production.functions'].memory))
        self.assertGreater(cpus_one, cpus)


class FunctionsApplyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.catalog = root / 'control.sqlite'
        with closing(sqlite3.connect(self.catalog)) as database, database:
            database.execute('CREATE TABLE functions_settings(runtime TEXT PRIMARY KEY, desired TEXT, state TEXT, failure TEXT, actor TEXT, updated_at INTEGER)')
            database.execute("INSERT INTO functions_settings VALUES (?, 'on', 'pending', NULL, 'alice', 0)", (E,))
        for item in [patch.object(realtime, 'CATALOG', self.catalog), patch.object(realtime, 'OPERATION_LOCK', root / 'operation.lock')]:
            item.start()
            self.addCleanup(item.stop)

    def row(self):
        with closing(sqlite3.connect(self.catalog)) as database:
            return database.execute('SELECT state, failure FROM functions_settings').fetchone()

    def test_the_apply_runs_the_functions_command_and_records_it(self):
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(realtime.main(['apply', E, '--service', 'functions']), 0)
        self.assertEqual(run.call_args[0][0][-2:], ['functions', E])
        self.assertEqual(self.row(), ('on', None))

    def test_a_full_server_names_edge_functions(self):
        with patch.object(realtime.subprocess, 'run', return_value=SimpleNamespace(returncode=75)):
            realtime.apply(E, 'functions')
        state, failure = self.row()
        self.assertEqual(state, 'failed')
        self.assertIn('Edge Functions', failure)


if __name__ == '__main__':
    unittest.main()
