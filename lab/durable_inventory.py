"""Exact ownership inventory of one durable upstream environment, derived only from durable facts.

An environment of the durable runtime (lab/durable_runtime.py) owns, by construction:
  - its Auth and REST containers `sbarbase-durable-<runtime>-<service>` (and, while their services were
    ever on, Realtime, Functions and Studio containers under the same exact naming), all labelled
    `io.sbarbase.owner=durable-upstream`;
  - one database and the logins named by its runtime id inside the shared PostgreSQL cluster;
  - its entry in the runtime secret, its published endpoints entry and its Storage tenant (the tenant id
    is the runtime id; the Storage process and its objects volume are shared and are never owned).

Nothing here guesses. Every item is named by the runtime id, never found by a pattern across
environments, and every ambiguity or foreign object refuses with a closed, nonsecret reason. The
caller treats a refusal as "Exact ownership inventory unavailable". This module only reads.
"""
import argparse
import json
import re
import sys
import uuid

OWNER = 'durable-upstream'
PREFIX = 'sbarbase-durable'
NETWORK = PREFIX + '-net'
RUNTIME = re.compile(r'^e_[a-f0-9]{24}$')
UUID = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
COVERAGE = 'durable-environment'
# The services whose containers an environment can have, in the order the inventory lists them.
CONTAINERS = ('auth', 'rest', 'realtime', 'functions', 'studio', 'meta')
REQUIRED_CONTAINERS = ('auth', 'rest')
# Logins an environment can have. Anything else carrying its prefix is refused, not adopted.
ROLES = ('auth', 'rest', 'storage', 'realtime', 'studio', 'developer', 'cron')
REQUIRED_ROLES = ('auth', 'rest', 'storage')
FOREIGN_LABELS = ('io.sbarbase.retained', 'io.sbarbase.recovery')
NAMESPACE = uuid.UUID('8f0a1e52-5b0e-4d79-8a54-6c1f2f6f2f10')


class Refused(RuntimeError):
    """A closed nonsecret reason. Nothing was changed."""


def check_runtime(runtime):
    if not isinstance(runtime, str) or not RUNTIME.fullmatch(runtime):
        raise Refused('invalid_runtime')


def container_name(runtime, service):
    return PREFIX + '-' + runtime + '-' + service


def resource_uuid(installation, runtime, kind, identity):
    """Stable, so deriving twice from the same facts gives byte-identical resources."""
    return str(uuid.uuid5(NAMESPACE, '|'.join((installation, runtime, kind, identity))))


def name_of(item):
    return str(item.get('Name') or '').lstrip('/')


def container_items(probe, runtime):
    """The containers whose exact names carry this environment's prefix, by service. Unknown or
    foreign ones refuse: what cannot be named cannot be stopped, retained or restored exactly."""
    prefix = PREFIX + '-' + runtime + '-'
    found = {}
    for item in probe.containers():
        name = name_of(item)
        if not name.startswith(prefix):
            continue
        service = name[len(prefix):]
        if service not in CONTAINERS:
            raise Refused('container_unknown')
        labels = (item.get('Config') or {}).get('Labels') or item.get('Labels') or {}
        if labels.get('io.sbarbase.owner') != OWNER or any(key in labels for key in FOREIGN_LABELS):
            raise Refused('container_foreign:' + service)
        if not isinstance(item.get('Id'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['Id']):
            raise Refused('container_identity:' + service)
        if service in found:
            raise Refused('container_duplicate:' + service)
        found[service] = item
    return found


def derive(runtime, installation, probe):
    """The exact inventory, or Refused. `probe` supplies containers(), sql(), endpoints() and
    secret_environments(); tests stub it, lab/durable_probe.py reads the real host."""
    check_runtime(runtime)
    if not isinstance(installation, str) or not UUID.fullmatch(installation):
        raise Refused('invalid_installation')
    if runtime not in probe.secret_environments():
        raise Refused('secret_entry_missing')
    entry = probe.endpoints().get(runtime)
    if not isinstance(entry, dict):
        raise Refused('environment_not_published')
    storage = entry.get('storage')
    if not isinstance(storage, dict) or storage.get('tenantHost') != runtime + '.storage.internal':
        raise Refused('storage_tenant_unrecorded')
    items = container_items(probe, runtime)
    for service in REQUIRED_CONTAINERS:
        if service not in items:
            raise Refused('container_missing:' + service)
    for service, item in items.items():
        if service not in REQUIRED_CONTAINERS and (item.get('State') or {}).get('Running'):
            # The catalog says these services are off; a running one contradicts it.
            raise Refused('service_running:' + service)
    database = probe.sql("SELECT datallowconn::text||'|'||datistemplate::text FROM pg_database WHERE datname='" + runtime + "';").strip().splitlines()
    if database != ['true|false'] and database != ['t|f']:
        raise Refused('database_missing' if not database else 'database_unusable')
    marker = runtime + '_'
    rows = probe.sql("SELECT rolname||'|'||rolsuper::text FROM pg_roles WHERE left(rolname," + str(len(marker)) + ")='" + marker + "' ORDER BY rolname;").strip().splitlines()
    roles = []
    for row in rows:
        name, _, privileged = row.partition('|')
        suffix = name[len(marker):]
        if suffix not in ROLES:
            raise Refused('role_unknown')
        if privileged not in ('f', 'false'):
            raise Refused('role_privileged:' + suffix)
        roles.append(name)
    for suffix in REQUIRED_ROLES:
        if marker + suffix not in roles:
            raise Refused('role_missing:' + suffix)
    resources = [{'kind': 'durable-container', 'id': container_name(runtime, service),
                  'resource': resource_uuid(installation, runtime, 'container', service),
                  'installation': installation, 'runtime': runtime, 'service': service}
                 for service in CONTAINERS if service in items]
    resources.append({'kind': 'durable-tenant', 'id': runtime,
                      'resource': resource_uuid(installation, runtime, 'tenant', runtime),
                      'installation': installation, 'runtime': runtime,
                      'durable': {'database': runtime, 'roles': sorted(roles), 'storageTenant': runtime}})
    return {'coverage': COVERAGE, 'resources': resources}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('derive',))
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--installation', required=True)
    args = parser.parse_args(argv)
    try:
        from durable_probe import LiveProbe
        result = {'ok': True, **derive(args.runtime, args.installation, LiveProbe())}
        code = 0
    except Refused as error:
        result, code = {'ok': False, 'reason': str(error)}, 1
    except Exception:
        # Docker or SQL output can hold credentials: never echo it.
        result, code = {'ok': False, 'reason': 'observation_unavailable'}, 1
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
