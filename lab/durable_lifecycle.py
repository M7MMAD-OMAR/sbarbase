"""Effects of the retained lifecycle on a durable upstream environment (lab/durable_inventory.py).

Delete is soft. `quarantine` stops the environment's own containers, closes its own logins and
unpublishes its endpoints entry, so nothing resumes it on the next start. It destroys nothing:
the database, the Storage tenant and its objects, the secrets and the stopped containers stay for the
retention. `restore` reverses exactly what `quarantine` recorded. `purge` is refused here: a purge
would drop objects of the shared PostgreSQL cluster and of the shared Storage process, and nothing
proves yet that only this environment writes them.

Crash safety: every mutating step first writes its intent to a per environment journal (one atomic
file), then mutates through idempotent statements, then records the phase. A replay of the same
operation reads the journal and finishes; it never re-observes a state its own earlier attempt changed.
Each effect is also refused unless the catalog durably records the same operation, epoch, state and
exact resource (the caller persists its pending effect before calling).
"""
import argparse
import fcntl
import json
import re
import sqlite3
import sys
import time

import durable_inventory as inventory
from durable_inventory import Refused, RUNTIME, UUID, OWNER, PREFIX, NETWORK, CONTAINERS, ROLES, REQUIRED_ROLES

ACTIONS = ('inspect', 'quarantine', 'restore', 'purge', 'readiness')
STATES = {'quarantine': 'deleting', 'restore': 'restoring', 'purge': 'purging'}
PORTS = {'auth': (9999, '/health'), 'rest': (3000, '/')}
READY_SECONDS = 60


def valid_resource(resource, runtime):
    """The shape lab/durable_inventory.py derives and src/control/lifecycle-contract.ts validates."""
    if (not isinstance(resource, dict) or not isinstance(runtime, str) or not RUNTIME.fullmatch(runtime)
            or resource.get('runtime') != runtime or not UUID.fullmatch(str(resource.get('resource')))
            or not UUID.fullmatch(str(resource.get('installation')))):
        raise Refused('invalid_resource_contract')
    if resource.get('kind') == 'durable-container':
        if (set(resource) != {'kind', 'id', 'resource', 'installation', 'runtime', 'service'}
                or resource['service'] not in CONTAINERS or resource['id'] != inventory.container_name(runtime, resource['service'])):
            raise Refused('invalid_resource_contract')
    elif resource.get('kind') == 'durable-tenant':
        durable = resource.get('durable')
        if (set(resource) != {'kind', 'id', 'resource', 'installation', 'runtime', 'durable'} or resource['id'] != runtime
                or not isinstance(durable, dict) or set(durable) != {'database', 'roles', 'storageTenant'}
                or durable['database'] != runtime or durable['storageTenant'] != runtime
                or not isinstance(durable['roles'], list) or len(set(durable['roles'])) != len(durable['roles'])
                or any(not isinstance(name, str) or name[:len(runtime) + 1] != runtime + '_'
                       or name[len(runtime) + 1:] not in ROLES for name in durable['roles'])
                or any(runtime + '_' + suffix not in durable['roles'] for suffix in REQUIRED_ROLES)):
            raise Refused('invalid_resource_contract')
    else:
        raise Refused('invalid_resource_contract')
    return resource


def catalog_verifier(path):
    """Refuse an effect the catalog does not durably record: same operation, epoch, state, resource."""
    def verify(request):
        connection = sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)
        try:
            row = connection.execute('SELECT state, epoch, coverage, inventory FROM environment_lifecycle WHERE runtime=? AND operation=?',
                                     (request['runtime'], request['operation'])).fetchone()
        finally:
            connection.close()
        if (not row or row[0] != STATES[request['action']] or row[1] != request['epoch']
                or row[2] != inventory.COVERAGE or not row[3]
                or request['resource'] not in json.loads(row[3])):
            raise Refused('lifecycle_intent_unrecorded')
    return verify


class Effects:
    def __init__(self, probe, verify=None, sleep=time.sleep, clock=time.monotonic):
        self.probe = probe
        self.verify = verify
        self.sleep = sleep
        self.clock = clock

    def apply(self, request):
        if (not isinstance(request, dict) or set(request) != {'action', 'resource', 'runtime', 'epoch', 'operation'}
                or request['action'] not in ACTIONS or type(request['epoch']) is not int or request['epoch'] < 0
                or not isinstance(request['operation'], str) or not UUID.fullmatch(request['operation'])):
            raise Refused('invalid_effect_request')
        action = request['action']
        resource = valid_resource(request['resource'], request['runtime'])
        if action in STATES:
            if request['epoch'] < 1:
                raise Refused('invalid_effect_request')
            if self.verify is None:
                raise Refused('lifecycle_intent_required')
            self.verify(request)
        if action == 'purge':
            raise Refused('durable_purge_unavailable')
        if resource['kind'] == 'durable-container':
            return self.container(resource, action, request['operation'])
        return self.tenant(resource, action, request['operation'])

    # Journal -----------------------------------------------------------------------------------
    def journal(self, runtime, operation, create):
        value = self.probe.read_journal(runtime)
        if value is not None:
            if (not isinstance(value, dict) or value.get('version') != 1 or value.get('runtime') != runtime
                    or not isinstance(value.get('containers'), dict)):
                raise Refused('journal_invalid')
            if value.get('operation') == operation:
                return value
            if not create:
                return value
            parts = list(value['containers'].values()) + ([value['tenant']] if value.get('tenant') else [])
            if any(part.get('phase') != 'restored' for part in parts):
                raise Refused('journal_foreign_operation')
        elif not create:
            raise Refused('journal_missing')
        return {'version': 1, 'runtime': runtime, 'operation': operation, 'containers': {}, 'tenant': None}

    def save(self, journal):
        self.probe.write_journal(journal['runtime'], journal)

    # Containers --------------------------------------------------------------------------------
    def find(self, resource):
        """The one container of this exact name, owned by the durable runtime, or None."""
        items = [item for item in self.probe.containers() if inventory.name_of(item) == resource['id']]
        if not items:
            return None
        labels = (items[0].get('Config') or {}).get('Labels') or items[0].get('Labels') or {}
        if (len(items) != 1 or labels.get('io.sbarbase.owner') != OWNER
                or any(key in labels for key in inventory.FOREIGN_LABELS)):
            raise Refused('container_foreign')
        return items[0]

    def running(self, item):
        return bool((item.get('State') or {}).get('Running'))

    def container(self, resource, action, operation):
        item = self.find(resource)
        runtime, service = resource['runtime'], resource['service']
        if item is None:
            if action in ('quarantine', 'restore', 'readiness'):
                raise Refused('retained_resource_missing')
            return {'outcome': 'absent', 'reclaimedBytes': 0}
        if action == 'inspect':
            return {'outcome': 'present', 'reclaimedBytes': 0, 'observedBytes': 0}
        if action == 'quarantine':
            journal = self.journal(runtime, operation, True)
            part = journal['containers'].get(service)
            if part is None:
                part = journal['containers'][service] = {'id': item['Id'], 'running': self.running(item), 'phase': 'recorded'}
                self.save(journal)
            if part['id'] != item['Id']:
                raise Refused('container_identity_changed')
            if self.running(item):
                self.probe.docker('stop', '-t', '10', part['id'])
                item = self.find(resource)
                if item is None or self.running(item):
                    raise Refused('container_stop_unproven')
            part['phase'] = 'quarantined'
            self.save(journal)
            return {'outcome': 'quarantined', 'reclaimedBytes': 0}
        journal = self.journal(runtime, operation, False)
        part = journal['containers'].get(service)
        if part is None or part.get('id') != item['Id']:
            raise Refused('journal_incomplete')
        if action == 'readiness':
            if part['running'] and not self.ready(service, item):
                raise Refused('container_readiness_unproven')
            return {'outcome': 'restored', 'reclaimedBytes': 0}
        # restore
        tenant = journal.get('tenant')
        if not tenant or tenant.get('phase') != 'restored':
            raise Refused('tenant_not_restored')
        if journal.get('restoreOperation') not in (None, operation):
            raise Refused('journal_foreign_operation')
        if part['running']:
            self.probe.docker('start', part['id'])
            deadline = self.clock() + READY_SECONDS
            while True:
                item = self.find(resource)
                if item is not None and self.ready(service, item):
                    break
                if self.clock() >= deadline:
                    raise Refused('container_readiness_unproven')
                self.sleep(.5)
            self.publish_address(runtime, service, item)
        part['phase'] = 'restored'
        self.save(journal)
        return {'outcome': 'restored', 'reclaimedBytes': 0}

    def address(self, service, item):
        port, path = PORTS[service]
        host = self.probe.network_address(item, NETWORK)
        return ('http://' + host + ':' + str(port), path) if host else (None, path)

    def ready(self, service, item):
        if not self.running(item):
            return False
        if service not in PORTS:
            return True
        base, path = self.address(service, item)
        return bool(base) and self.probe.http_ok(base + path)

    def publish_address(self, runtime, service, item):
        """Restart gives a container a new address; the published entry, if restored, must say so."""
        if service not in PORTS:
            return
        endpoints = self.probe.endpoints()
        entry = endpoints.get(runtime)
        base, _ = self.address(service, item)
        if isinstance(entry, dict) and base and entry.get(service) != base:
            self.probe.write_endpoints({**endpoints, runtime: {**entry, service: base}})

    # The environment's database, logins and published entry -------------------------------------
    def role_states(self, roles):
        rows = self.probe.sql("SELECT rolname||'|'||rolcanlogin::text FROM pg_roles WHERE rolname IN (" +
                              ','.join("'" + name + "'" for name in roles) + ') ORDER BY rolname;').strip().splitlines()
        states = {}
        for row in rows:
            name, _, login = row.partition('|')
            states[name] = login in ('t', 'true')
        if set(states) != set(roles):
            raise Refused('tenant_incomplete')
        return states

    def tenant(self, resource, action, operation):
        runtime, roles = resource['runtime'], resource['durable']['roles']
        size = self.probe.sql("SELECT pg_database_size(datname) FROM pg_database WHERE datname='" + runtime + "';").strip()
        if not size:
            if action in ('quarantine', 'restore', 'readiness'):
                raise Refused('retained_resource_missing')
            return {'outcome': 'absent', 'reclaimedBytes': 0}
        if not size.isdigit():
            raise Refused('tenant_measurement_unavailable')
        states = self.role_states(roles)
        if action == 'inspect':
            return {'outcome': 'present', 'reclaimedBytes': 0, 'observedBytes': int(size)}
        if action == 'quarantine':
            for service in CONTAINERS:
                item = self.find({'id': inventory.container_name(runtime, service)})
                if item is not None and self.running(item):
                    raise Refused('container_still_running')
            journal = self.journal(runtime, operation, True)
            part = journal['tenant']
            if part is None:
                part = journal['tenant'] = {'roles': states, 'entry': self.probe.endpoints().get(runtime), 'phase': 'recorded'}
                self.save(journal)
            if set(part['roles']) != set(roles):
                raise Refused('journal_incomplete')
            closing = [name for name in roles if part['roles'][name]]
            if closing:
                self.probe.sql(self.bounds() + ' '.join('ALTER ROLE "' + name + '" NOLOGIN;' for name in closing))
            self.probe.sql(self.bounds() + "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename IN (" +
                           ','.join("'" + name + "'" for name in roles) + ');')
            endpoints = self.probe.endpoints()
            if runtime in endpoints:
                self.probe.write_endpoints({key: value for key, value in endpoints.items() if key != runtime})
            part['phase'] = 'quarantined'
            self.save(journal)
            return {'outcome': 'quarantined', 'reclaimedBytes': 0}
        journal = self.journal(runtime, operation, False)
        part = journal.get('tenant')
        if not part or set(part['roles']) != set(roles):
            raise Refused('journal_incomplete')
        if action == 'readiness':
            if states != part['roles'] or (part['entry'] is not None and runtime not in self.probe.endpoints()):
                raise Refused('tenant_readiness_unproven')
            return {'outcome': 'restored', 'reclaimedBytes': 0}
        # restore
        if journal.get('restoreOperation') not in (None, operation):
            raise Refused('journal_foreign_operation')
        if journal.get('restoreOperation') is None:
            journal['restoreOperation'] = operation
            self.save(journal)
        opening = [name for name in roles if part['roles'][name]]
        if opening:
            self.probe.sql(self.bounds() + ' '.join('ALTER ROLE "' + name + '" LOGIN;' for name in opening))
        endpoints = self.probe.endpoints()
        if part['entry'] is not None and runtime not in endpoints:
            self.probe.write_endpoints({**endpoints, runtime: part['entry']})
        part['phase'] = 'restored'
        self.save(journal)
        return {'outcome': 'restored', 'reclaimedBytes': 0}

    def bounds(self):
        return "SET lock_timeout = '5s'; SET statement_timeout = '30s'; "


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', required=True)
    args = parser.parse_args(argv)
    try:
        from durable_probe import LiveProbe
        probe = LiveProbe()
        # The installation operation lock, tried once: a start, a provision or an upgrade holds it.
        handle = (probe.state / 'operation.lock').open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused('operation_busy') from None
        try:
            data = sys.stdin.buffer.read(65537)
            if len(data) > 65536:
                raise Refused('effect_request_too_large')
            print(json.dumps(Effects(probe, catalog_verifier(args.catalog)).apply(json.loads(data)), sort_keys=True))
        finally:
            handle.close()
    except Exception as error:
        reason = str(error) if isinstance(error, Refused) else 'resource_effect_unavailable'
        print(json.dumps({'outcome': 'refused', 'reason': reason}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
