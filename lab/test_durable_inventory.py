"""The exact inventory of a durable upstream environment: derived from facts, refused when anything is
ambiguous or foreign, and never touching another environment. A stub host replaces Docker and PostgreSQL."""
import copy
import hashlib
import re
import unittest

import durable_inventory as inventory
from durable_inventory import Refused

RUNTIME = 'e_' + 'a1' * 12
OTHER = 'e_' + 'b2' * 12
INSTALLATION = '11111111-2222-4333-8444-555555555555'
OWNER = 'durable-upstream'


class Crash(RuntimeError):
    pass


def cid(name):
    return hashlib.sha256(name.encode()).hexdigest()


class FakeHost:
    """What `durable_probe.LiveProbe` reads and writes, as plain dictionaries. `crash` is (n, when): the n-th
    mutating operation raises Crash before ('before') or after ('after') it takes effect."""

    def __init__(self, runtimes=(RUNTIME,)):
        self.items = {}
        self.roles = {}
        self.databases = {}
        self.published = {}
        self.secrets = set()
        self.journals = {}
        self.log = []
        self.crash = None
        self.count = 0
        self.healthy = True
        for runtime in runtimes:
            self.add_environment(runtime)

    def add_container(self, name, owner=OWNER, running=True, labels=None):
        self.items[name] = {'Id': cid(name), 'Name': '/' + name, 'Config': {'Labels': {'io.sbarbase.owner': owner, **(labels or {})}},
                            'State': {'Running': running},
                            'NetworkSettings': {'Networks': {inventory.NETWORK: {'IPAddress': '172.20.0.' + str(len(self.items) + 2)}}}}

    def add_environment(self, runtime):
        for service in ('auth', 'rest'):
            self.add_container(inventory.container_name(runtime, service))
        for suffix in ('auth', 'rest', 'storage'):
            self.roles[runtime + '_' + suffix] = {'login': True, 'super': False}
        self.databases[runtime] = {'allow': True, 'template': False, 'size': 8192}
        self.secrets.add(runtime)
        self.published[runtime] = {'auth': 'http://172.20.0.2:9999', 'rest': 'http://172.20.0.3:3000',
                                   'storage': {'url': 'http://172.20.0.9:5000', 'tenantHost': runtime + '.storage.internal'}}

    # Crash injection ----------------------------------------------------------------------------
    def mutate(self, label, effect):
        self.count += 1
        hit = self.crash is not None and self.count == self.crash[0]
        if hit and self.crash[1] == 'before':
            raise Crash(label)
        result = effect()
        self.log.append(label)
        if hit:
            raise Crash(label)
        return result

    # Probe surface -------------------------------------------------------------------------------
    def containers(self):
        return copy.deepcopy(list(self.items.values()))

    def docker(self, *args):
        verb, target = args[0], args[-1]
        item = next(value for value in self.items.values() if value['Id'] == target)
        running = verb == 'start'
        return self.mutate(verb + ' ' + item['Name'].lstrip('/'), lambda: item['State'].__setitem__('Running', running))

    def sql(self, query, database='postgres'):
        if 'ALTER ROLE' in query:
            def alter():
                for name, state in re.findall(r'ALTER ROLE "([^"]+)" (NOLOGIN|LOGIN);', query):
                    self.roles[name]['login'] = state == 'LOGIN'
            return self.mutate('alter ' + ' '.join(re.findall(r'"([^"]+)" (?:NOLOGIN|LOGIN)', query)), alter)
        if 'pg_terminate_backend' in query:
            return self.mutate('terminate', lambda: '')
        match = re.search(r"FROM pg_database WHERE datname='([^']+)'", query)
        if match and 'datallowconn' in query:
            row = self.databases.get(match.group(1))
            return '' if row is None else str(row['allow']).lower() + '|' + str(row['template']).lower() + '\n'
        if match and 'pg_database_size' in query:
            row = self.databases.get(match.group(1))
            return '' if row is None else str(row['size']) + '\n'
        match = re.search(r"left\(rolname,\d+\)='([^']+)'", query)
        if match:
            return ''.join(name + '|' + str(self.roles[name]['super']).lower() + '\n' for name in sorted(self.roles) if name.startswith(match.group(1)))
        match = re.search(r'rolname IN \(([^)]*)\)', query)
        if match:
            names = re.findall(r"'([^']+)'", match.group(1))
            return ''.join(name + '|' + str(self.roles[name]['login']).lower() + '\n' for name in sorted(names) if name in self.roles)
        raise AssertionError('unexpected query: ' + query)

    def endpoints(self):
        return copy.deepcopy(self.published)

    def write_endpoints(self, value):
        self.mutate('endpoints', lambda: setattr(self, 'published', copy.deepcopy(value)))

    def secret_environments(self):
        return set(self.secrets)

    def read_journal(self, runtime):
        return copy.deepcopy(self.journals.get(runtime))

    def write_journal(self, runtime, value):
        self.mutate('journal', lambda: self.journals.__setitem__(runtime, copy.deepcopy(value)))

    def network_address(self, item, network):
        return item['NetworkSettings']['Networks'][network]['IPAddress']

    def http_ok(self, url):
        return self.healthy

    def snapshot(self, runtime):
        """Everything one environment owns, comparable before and after."""
        names = {name: copy.deepcopy(item['State']) for name, item in self.items.items() if name.startswith('sbarbase-durable-' + runtime + '-')}
        roles = {name: dict(value) for name, value in self.roles.items() if name.startswith(runtime + '_')}
        return {'containers': names, 'roles': roles, 'database': dict(self.databases[runtime]),
                'entry': copy.deepcopy(self.published.get(runtime)), 'secret': runtime in self.secrets}


class Derivation(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost((RUNTIME, OTHER))

    def derive(self, runtime=RUNTIME):
        return inventory.derive(runtime, INSTALLATION, self.host)

    def test_exact_inventory_of_one_environment(self):
        result = self.derive()
        self.assertEqual(result['coverage'], 'durable-environment')
        kinds = [(item['kind'], item.get('service')) for item in result['resources']]
        self.assertEqual(kinds, [('durable-container', 'auth'), ('durable-container', 'rest'), ('durable-tenant', None)])
        self.assertEqual([item['id'] for item in result['resources'][:2]],
                         ['sbarbase-durable-' + RUNTIME + '-auth', 'sbarbase-durable-' + RUNTIME + '-rest'])
        tenant = result['resources'][2]
        self.assertEqual(tenant['durable'], {'database': RUNTIME, 'roles': [RUNTIME + '_auth', RUNTIME + '_rest', RUNTIME + '_storage'],
                                             'storageTenant': RUNTIME})
        self.assertTrue(all(item['runtime'] == RUNTIME and item['installation'] == INSTALLATION for item in result['resources']))
        self.assertNotIn(OTHER, repr(result))

    def test_derivation_is_deterministic(self):
        self.assertEqual(self.derive(), self.derive())
        self.assertNotEqual({item['resource'] for item in self.derive()['resources']},
                            {item['resource'] for item in self.derive(OTHER)['resources']})

    def test_optional_stopped_containers_are_listed(self):
        for service in ('realtime', 'functions'):
            self.host.add_container(inventory.container_name(RUNTIME, service), running=False)
        services = [item.get('service') for item in self.derive()['resources']]
        self.assertEqual(services, ['auth', 'rest', 'realtime', 'functions', None])

    def refuses(self, reason, runtime=RUNTIME):
        with self.assertRaises(Refused) as caught:
            self.derive(runtime)
        self.assertEqual(str(caught.exception), reason)

    def test_foreign_owner_label_refuses(self):
        self.host.items[inventory.container_name(RUNTIME, 'rest')]['Config']['Labels']['io.sbarbase.owner'] = 'recovery-target'
        self.refuses('container_foreign:rest')

    def test_retained_or_recovery_label_refuses(self):
        for label in ('io.sbarbase.retained', 'io.sbarbase.recovery'):
            host = FakeHost()
            host.items[inventory.container_name(RUNTIME, 'auth')]['Config']['Labels'][label] = 'x'
            with self.assertRaises(Refused):
                inventory.derive(RUNTIME, INSTALLATION, host)

    def test_unknown_container_with_the_prefix_refuses(self):
        self.host.add_container('sbarbase-durable-' + RUNTIME + '-sidecar')
        self.refuses('container_unknown')

    def test_missing_required_container_refuses(self):
        del self.host.items[inventory.container_name(RUNTIME, 'rest')]
        self.refuses('container_missing:rest')

    def test_running_optional_service_contradicts_the_catalog(self):
        self.host.add_container(inventory.container_name(RUNTIME, 'realtime'), running=True)
        self.refuses('service_running:realtime')

    def test_missing_secret_entry_refuses(self):
        self.host.secrets.discard(RUNTIME)
        self.refuses('secret_entry_missing')

    def test_unpublished_environment_refuses(self):
        del self.host.published[RUNTIME]
        self.refuses('environment_not_published')

    def test_storage_tenant_must_be_this_one(self):
        self.host.published[RUNTIME]['storage']['tenantHost'] = OTHER + '.storage.internal'
        self.refuses('storage_tenant_unrecorded')

    def test_missing_or_closed_database_refuses(self):
        self.host.databases[RUNTIME]['allow'] = False
        self.refuses('database_unusable')
        del self.host.databases[RUNTIME]
        self.refuses('database_missing')

    def test_unknown_login_with_the_prefix_refuses(self):
        self.host.roles[RUNTIME + '_backdoor'] = {'login': True, 'super': False}
        self.refuses('role_unknown')

    def test_privileged_or_missing_login_refuses(self):
        self.host.roles[RUNTIME + '_rest']['super'] = True
        self.refuses('role_privileged:rest')
        del self.host.roles[RUNTIME + '_rest']
        self.refuses('role_missing:rest')

    def test_neighbor_objects_never_decide_the_outcome(self):
        # A defect in another environment does not change this one's inventory, and is refused for that one alone.
        self.host.roles[OTHER + '_backdoor'] = {'login': True, 'super': False}
        self.derive()
        self.refuses('role_unknown', OTHER)

    def test_invalid_identities_refuse(self):
        with self.assertRaises(Refused):
            inventory.derive('e_short', INSTALLATION, self.host)
        with self.assertRaises(Refused):
            inventory.derive(RUNTIME, 'not-a-uuid', self.host)


if __name__ == '__main__':
    unittest.main()
