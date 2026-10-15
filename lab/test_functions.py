"""Edge Functions per environment: the container, its mounts and networks, the keys it gets, and the apply."""
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
# The real helper, kept before the tests patch it.
ADDRESS = durable_runtime.functions_address
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
                     patch.object(durable_runtime, 'functions_address', return_value=('172.30.4.1', '172.30.4.0/24')),
                     patch.object(durable_runtime.lab, 'docker', side_effect=lambda *args, **kw: self.docker.append(args) or SimpleNamespace(stdout='', returncode=0)),
                     patch.object(durable_runtime.lab, 'secure_file', side_effect=lambda path, text: Path(path).write_text(text))]:
            item.start()
            self.addCleanup(item.stop)

    def test_functions_get_only_their_own_environment_keys_and_services(self):
        config = self.runtime.functions_configuration(E, self.runtime.values['environments'][E], '172.30.4.1')
        self.assertEqual(config['SB_REST_URL'], f'http://sbarbase-durable-{E}-rest:3000')
        self.assertEqual(config['SB_AUTH_URL'], f'http://sbarbase-durable-{E}-auth:9999')
        # Storage only through the host listener, which sets the tenant; never the shared container.
        self.assertEqual(config['SB_STORAGE_URL'], f'http://172.30.4.1:{durable_runtime.FUNCTIONS_STORAGE_PORT}')
        self.assertNotIn('SB_STORAGE_HOST', config)
        self.assertNotIn('storage.internal', json.dumps(config))
        self.assertEqual(config['SB_JWT_SECRET'], 'j' * 64)
        self.assertNotIn('k' * 64, json.dumps(config))
        claims = json.loads(durable_runtime.base64.urlsafe_b64decode(config['SUPABASE_SERVICE_ROLE_KEY'].split('.')[1] + '=='))
        self.assertEqual(claims['role'], 'service_role')
        self.assertGreater(claims['exp'] - claims['iat'], 9 * 365 * 24 * 3600)

    def test_the_container_mounts_its_own_code_and_secrets_read_only_and_reaches_the_internet_on_its_own_network(self):
        with patch.object(self.runtime, 'launch') as launch, patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000'), \
             patch.object(durable_runtime, 'http', return_value=(404, b'{}')):
            entry = self.runtime.functions_start(E)
        self.assertEqual(entry, {'url': 'http://10.0.0.9:9000', 'image': 'sha256:fn',
                                 'storage': {'host': '172.30.4.1', 'port': durable_runtime.FUNCTIONS_STORAGE_PORT, 'subnet': '172.30.4.0/24'}})
        name, component = launch.call_args[0][:2]
        self.assertEqual((name, component), (f'sbarbase-durable-{E}-functions', 'functions'))
        binds = dict((str(destination), str(source)) for source, destination in launch.call_args.kwargs['binds'])
        self.assertTrue(binds['/home/deno/functions'].endswith(f'functions/{E}'))
        self.assertTrue(binds['/run/sbarbase'].startswith(str(self.root / 'private')))
        self.assertNotIn(OTHER, json.dumps(binds))
        self.assertEqual(launch.call_args.kwargs['tier'], 'production.functions')
        self.assertIn(('network', 'connect', durable_runtime.EGRESS, name), self.docker)
        self.assertEqual(json.loads((self.root / 'private' / 'functions' / E / 'secrets.json').read_text()), {})

    def test_functions_join_only_their_own_environment_network_and_an_outbound_network_without_container_traffic(self):
        network = f'sbarbase-durable-{E}-fn'
        with patch.object(self.runtime, 'launch') as launch, patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000') as endpoint, \
             patch.object(durable_runtime, 'http', return_value=(404, b'{}')):
            self.runtime.functions_start(E)
        self.assertEqual(launch.call_args.kwargs['network'], network)
        self.assertNotEqual(launch.call_args.kwargs['network'], durable_runtime.NETWORK)
        self.assertEqual(endpoint.call_args[0][2], network)
        create = next(args for args in self.docker if args[:3] == ('network', 'create', '--internal'))
        self.assertEqual(create[-1], network)
        self.assertIn('io.sbarbase.functions=' + E, create)
        connected = {args[3] for args in self.docker if args[:3] == ('network', 'connect', network)}
        self.assertEqual(connected, {f'sbarbase-durable-{E}-auth', f'sbarbase-durable-{E}-rest'})
        self.assertFalse(any(OTHER in ' '.join(args) for args in self.docker))
        outbound = next(args for args in self.docker if args[:2] == ('network', 'create') and args[-1] == durable_runtime.EGRESS)
        self.assertIn('com.docker.network.bridge.enable_icc=false', outbound)
        self.assertNotIn('--internal', outbound)

    def test_an_outbound_network_that_lets_containers_reach_each_other_is_refused(self):
        shared = {'Name': durable_runtime.EGRESS, 'Options': {}, 'Internal': False, 'Labels': {'io.sbarbase.owner': durable_runtime.OWNER}}
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: shared if name == durable_runtime.EGRESS else None):
            with self.assertRaises(RuntimeError):
                durable_runtime.ensure_egress()

    def test_the_earlier_shared_outbound_network_is_removed_only_once_no_container_names_it(self):
        isolated = {'Options': {durable_runtime.NO_ICC: 'false'}, 'Internal': False}
        found = lambda kind, name: isolated if name == durable_runtime.EGRESS else {'Name': name}
        for listed, removed in (('abc\n', False), ('', True)):
            self.docker.clear()
            def docker(*args, **kw):
                self.docker.append(args)
                return SimpleNamespace(stdout=listed if args[0] == 'ps' else '', returncode=0)
            with patch.object(durable_runtime, 'inspect', side_effect=found), patch.object(durable_runtime.lab, 'docker', side_effect=docker):
                durable_runtime.ensure_egress()
            self.assertEqual(('network', 'rm', durable_runtime.LEGACY_EGRESS) in self.docker, removed)

    def test_the_old_functions_container_is_removed_before_the_earlier_outbound_network_is_judged(self):
        name = f'sbarbase-durable-{E}-functions'
        isolated = {'Options': {durable_runtime.NO_ICC: 'false'}, 'Internal': False}
        old = {'Id': 'old', 'Name': name}
        def found(kind, item):
            if item == name:
                return old if not any(args[:2] == ('rm', '-f') for args in self.docker) else None
            return {durable_runtime.EGRESS: isolated, durable_runtime.LEGACY_EGRESS: {'Name': item}}.get(item)
        def docker(*args, **kw):
            self.docker.append(args)
            named = args[0] == 'ps' and not any(call[:2] == ('rm', '-f') for call in self.docker)
            return SimpleNamespace(stdout='old\n' if named else '', returncode=0)
        with patch.object(durable_runtime, 'inspect', side_effect=found), patch.object(durable_runtime.lab, 'docker', side_effect=docker), \
             patch.object(self.runtime, 'launch'), patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000'), \
             patch.object(durable_runtime, 'http', return_value=(404, b'{}')):
            self.runtime.functions_start(E)
        self.assertLess(self.docker.index(('rm', '-f', name)), self.docker.index(('network', 'rm', durable_runtime.LEGACY_EGRESS)))

    def test_a_retained_storage_is_taken_off_the_environment_network(self):
        network = f'sbarbase-durable-{E}-fn'
        storage = {'Name': 'sbarbase-durable-storage', 'NetworkSettings': {'Networks': {durable_runtime.NETWORK: {}, network: {}}}}
        found = lambda kind, name: storage if name == 'sbarbase-durable-storage' else None
        with patch.object(durable_runtime, 'inspect', side_effect=found), patch.object(self.runtime, 'launch'), \
             patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000'), patch.object(durable_runtime, 'http', return_value=(404, b'{}')):
            self.runtime.functions_start(E)
        self.assertIn(('network', 'disconnect', '--force', network, 'sbarbase-durable-storage'), self.docker)
        self.assertNotIn(('network', 'connect', network, 'sbarbase-durable-storage'), self.docker)

    def test_the_listener_address_is_the_environment_networks_ipv4_gateway(self):
        network = f'sbarbase-durable-{E}-fn'
        for config, expected in (([{'Subnet': 'fd00:1::/64', 'Gateway': 'fd00:1::1'}, {'Subnet': '172.30.4.0/24', 'Gateway': '172.30.4.1'}], ('172.30.4.1', '172.30.4.0/24')),
                                 ([{'Subnet': '172.30.4.0/24'}], None), ([], None)):
            item = {'Name': network, 'IPAM': {'Config': config}}
            with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: item if name == network else None):
                if expected:
                    self.assertEqual(ADDRESS(network), expected)
                else:
                    with self.assertRaises(RuntimeError):
                        ADDRESS(network)

    def test_auth_and_rest_join_their_own_environment_network_and_storage_joins_none(self):
        networks = {f'sbarbase-durable-{E}-fn'}
        found = lambda kind, name: {'Name': name} if name in networks else None
        listing = f'sbarbase-durable-{E}-fn\nsbarbase-durable-{OTHER}-fn\nsomething-else\n'
        with patch.object(durable_runtime, 'inspect', side_effect=found), \
             patch.object(durable_runtime.lab, 'docker', return_value=SimpleNamespace(stdout=listing, returncode=0)):
            self.assertEqual(durable_runtime.functions_links(f'sbarbase-durable-{E}-auth', 'auth'), [f'sbarbase-durable-{E}-fn'])
            self.assertEqual(durable_runtime.functions_links(f'sbarbase-durable-{E}-rest', 'rest'), [f'sbarbase-durable-{E}-fn'])
            self.assertEqual(durable_runtime.functions_links(f'sbarbase-durable-{OTHER}-auth', 'auth'), [])
            self.assertEqual(durable_runtime.functions_links(f'sbarbase-durable-{E}-realtime', 'realtime'), [])
            self.assertEqual(durable_runtime.functions_links('sbarbase-durable-management-auth', 'auth'), [])
            self.assertEqual(durable_runtime.functions_links('sbarbase-durable-storage', 'storage'), [])

    def test_a_recreated_auth_rejoins_its_environment_network(self):
        digest = 'sha256:' + 'a' * 64
        reference = 'docker.io/supabase/gotrue@' + digest
        self.runtime.pins['auth'] = {'id': digest, 'tag': 'supabase/gotrue:v2', 'digests': [reference]}
        name, network = f'sbarbase-durable-{E}-auth', f'sbarbase-durable-{E}-fn'
        def docker(*args, **kwargs):
            self.docker.append(args)
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps([{'Id': 'sha256:' + 'b' * 64, 'RepoDigests': [reference]}])
                                   if args[:2] == ('image', 'inspect') else '')
        found = lambda kind, item: {'Name': item} if item == network else None
        with patch.object(durable_runtime.resource_policy, 'container_flags', return_value={'memory': None, 'cpus': None, 'label': 'production', 'pids': 256, 'shares': 512, 'weight': 400}), \
             patch.object(durable_runtime.resource_policy, 'io_flags', return_value=[]), patch.object(durable_runtime, 'inspect', side_effect=found), \
             patch.object(durable_runtime.lab, 'secure_file'), patch.object(durable_runtime.lab, 'docker', docker):
            self.runtime.launch(name, 'auth', {}, '256m', .25, tier='production')
        run = next(args for args in self.docker if args and args[0] == 'run')
        self.assertEqual(run[run.index('--network') + 1], durable_runtime.NETWORK)
        self.assertIn(('network', 'connect', network, name), self.docker)

    def test_turning_functions_off_takes_every_member_off_the_environment_network_before_removing_it(self):
        network = f'sbarbase-durable-{E}-fn'
        item = {'Name': network, 'Containers': {'x': {'Name': f'sbarbase-durable-{E}-rest'}}}
        with patch.object(durable_runtime, 'inspect', side_effect=lambda kind, name: item if name == network else None):
            self.runtime.functions_network_remove(E)
        disconnected = [args[4] for args in self.docker if args[:2] == ('network', 'disconnect')]
        self.assertEqual(set(disconnected), {f'sbarbase-durable-{E}-auth', f'sbarbase-durable-{E}-rest', 'sbarbase-durable-storage'})
        self.assertEqual(self.docker[-1], ('network', 'rm', network))

    def test_a_runtime_that_never_answers_fails_the_start(self):
        with patch.object(self.runtime, 'launch'), patch.object(self.runtime, 'endpoint', return_value='http://10.0.0.9:9000'), \
             patch.object(durable_runtime, 'http', side_effect=OSError), patch.object(durable_runtime.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                self.runtime.functions_start(E)

    def test_binds_are_read_only(self):
        digest = 'sha256:' + 'a' * 64
        reference = 'docker.io/supabase/edge-runtime@' + digest
        self.runtime.pins['functions'] = {'id': digest, 'tag': 'supabase/edge-runtime:v1', 'digests': [reference]}
        def docker(*args, **kwargs):
            self.docker.append(args)
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps([{'Id': 'sha256:' + 'b' * 64, 'RepoDigests': [reference]}])
                                   if args[:2] == ('image', 'inspect') else '')
        with patch.object(durable_runtime.resource_policy, 'container_flags', return_value={'memory': '384m', 'cpus': .5, 'label': 'production', 'pids': 256, 'shares': 512, 'weight': 400}), \
             patch.object(durable_runtime.resource_policy, 'io_flags', return_value=[]), \
             patch.object(durable_runtime.lab, 'secure_file'), patch.object(durable_runtime.lab, 'docker', docker):
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
