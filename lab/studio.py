"""Supabase Studio for one environment, on demand.

    studio.py up <runtime>      start Studio and postgres-meta for one environment
    studio.py down <runtime>    stop them and close the Studio login
    studio.py reset             stop every Studio session (the supervisor runs this at start)

Studio and postgres-meta are the pinned upstream images (lab/studio-image.lock.json). They
connect to the environment's own database as ``<e>_studio``: a login that is LOGIN only while
Studio runs, with a password generated for that session and never stored, and that the HBA
file admits to that database only. It may read the Auth and Storage schemas, and change the
public schema, bypassing row security the way Studio's own admin role does on the platform.
It is never a superuser, never owns another service's schema, and never reaches another
environment's database.

Studio's server-side calls to Auth, REST and Storage go through the loopback server's
internal Studio route (``SUPABASE_URL``), which admits only this environment's service key.
The browser reaches Studio through the console, on ``<id>.studio.localhost``, behind the
management login (src/control/studio-proxy.ts).

Neither Studio nor postgres-meta checks who calls it, so neither may listen where another
environment's code runs: Edge Functions and the database itself (pg_net) sit on the shared
runtime network. Each session gets a private network of its own (``<e>-studio-net``, internal,
nothing else attached). Studio listens only on its address there, which the host reaches; its
attachment to the runtime network carries only its own outbound calls, to the database and to
the internal route. postgres-meta shares Studio's network namespace and listens on loopback.

State for the loopback server is ``.lab/upstream/studio.json``; the request and outcome of
each session live in the control catalog (``studio_sessions``).
"""
import argparse
import connection_budget
import datetime
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
from contextlib import closing
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import auth_settings
import durable_runtime as runtime
import image_identity
import resource_policy
import run as lab
import source_fence
import write_pause

PREFIX = runtime.PREFIX
STATE_FILE = runtime.STATE / 'studio.json'
CATALOG = runtime.STATE / 'control.sqlite'
RUNTIME_ID = re.compile(r'e_[a-f0-9]{24}')
# Below Linux's ephemeral range (32768 to 60999): the host's own outbound connections into the
# runtime network take source ports from that range on the same address, and one holding this
# port kept the route from opening for a whole run.
UPSTREAM_PORT = int(os.environ.get('SBARBASE_STUDIO_UPSTREAM_PORT', '25432'))
# Studio itself allows longer statements than the data plane: an operator's SQL editor
# query or a CSV import is not application traffic.
STATEMENT_TIMEOUT = '60s'
# Folders inside the Studio container, gone when the session stops. Saved SQL lives there for the
# session; the Edge Functions page reads a copy of the code each function runs (copy_functions).
SNIPPETS_FOLDER = '/tmp/snippets'
FUNCTIONS_FOLDER = '/tmp/functions'
FUNCTION_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
FUNCTION_VERSION = re.compile(r'v[0-9a-z]+-[0-9a-f]{8}')


class StudioError(RuntimeError):
    pass


def pins():
    return json.loads((lab.ROOT / 'lab' / 'studio-image.lock.json').read_text())


def names(e):
    return f'{PREFIX}-{e}-studio', f'{PREFIX}-{e}-meta'


def session_network(e):
    return f'{PREFIX}-{e}-studio-net'


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {'sessions': {}}


def save_state(value):
    runtime.atomic(STATE_FILE, value)


def network_gateway():
    """The host address on the runtime network: the loopback server's internal Studio route."""
    out = lab.docker('network', 'inspect', runtime.NETWORK, '--format', '{{(index .IPAM.Config 0).Gateway}}').stdout.strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', out):
        raise StudioError('Runtime network has no gateway address')
    return out


def role_sql(e, password):
    """Create or reopen the environment's Studio login with a fresh password and its grants."""
    role = f'{e}_studio'
    return f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
    CREATE ROLE {role} NOLOGIN NOINHERIT BYPASSRLS;
  END IF;
END $$;
ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT BYPASSRLS
  CONNECTION LIMIT {STUDIO_CONNECTIONS} PASSWORD '{password}';
ALTER ROLE {role} IN DATABASE {e} SET search_path TO public, extensions;
ALTER ROLE {role} IN DATABASE {e} SET statement_timeout = '{STATEMENT_TIMEOUT}';
GRANT CONNECT ON DATABASE {e} TO {role};
GRANT USAGE ON SCHEMA public, auth, storage, extensions TO {role};
GRANT CREATE ON SCHEMA public TO {role};
GRANT SELECT ON ALL TABLES IN SCHEMA auth, storage TO {role};
GRANT ALL ON ALL TABLES IN SCHEMA public TO {role};
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {role};
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role};
ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE {role} IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon, authenticated, service_role;
{runtime.studio_identity_sql(e)}
ALTER ROLE {role} LOGIN;
"""


STUDIO_CONNECTIONS = connection_budget.STUDIO_CONNECTIONS


def open_connections(e):
    """Room for the Studio login in the database limit, while the cluster can spare it."""
    limits = runtime_sql("SELECT current_setting('max_connections')::int - current_setting('superuser_reserved_connections')::int "
                         "- current_setting('reserved_connections')::int, coalesce(sum(greatest(datconnlimit, 0)), 0) "
                         "FROM pg_database WHERE datallowconn AND datname <> 'template1';").split('|')
    available, promised = int(limits[0]), int(limits[1])
    if promised + STUDIO_CONNECTIONS > available:
        raise StudioError('The database has no connections to spare for Studio')
    runtime_sql(f'ALTER DATABASE {e} CONNECTION LIMIT {runtime.current_database_limit(e, studio=True)};')


def close_login(e):
    role = f'{e}_studio'
    runtime_sql(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f"ALTER ROLE {role} NOLOGIN; END IF; END $$; "
                f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '{role}';")
    runtime_sql(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_database WHERE datname = '{e}') THEN "
                f"EXECUTE 'ALTER DATABASE {e} CONNECTION LIMIT {runtime.current_database_limit(e, studio=False)}'; END IF; END $$;")


def runtime_sql(query, database='postgres'):
    result = lab.docker('exec', '-i', runtime.DB, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin',
                        '-d', database, data=query, check=False)
    if result.returncode:
        raise StudioError('Database statement failed')
    return result.stdout.strip()


def require_rule(e):
    """The published HBA file must admit the Studio login to this database and nowhere else."""
    rows = runtime_sql("SELECT database[1] || ' ' || user_name[1] FROM pg_hba_file_rules "
                       f"WHERE '{e}_studio' = ANY(user_name) AND error IS NULL;").splitlines()
    if rows != [f'{e} {e}_studio']:
        raise StudioError('The Studio access rule is not published yet; restart Sbarbase once')


def ensure_images():
    locked = pins()
    if not isinstance(locked, dict) or set(locked) != {'studio', 'meta'}:
        raise StudioError('Studio lock must declare Studio and postgres-meta')
    images = {component: image_identity.reference(pin) for component, pin in locked.items()}
    missing = []
    for component, reference in images.items():
        result = lab.docker('image', 'inspect', reference, check=False)
        if result.returncode:
            if image_identity.is_missing(reference, result.stdout, result.stderr):
                missing.append((component, reference))
            else:
                raise image_identity.inspection_failure(reference, result.stdout, result.stderr)
        else:
            image_identity.resolved_id(reference, image_identity.record(result.stdout))
    for component, reference in missing:
        result = lab.docker('pull', reference, check=False)
        if result.returncode:
            raise StudioError(f'Pinned {component} image could not be pulled: {result.stderr.strip()}')
    for reference in images.values():
        result = lab.docker('image', 'inspect', reference, check=False)
        if result.returncode:
            raise image_identity.inspection_failure(reference, result.stdout, result.stderr)
        image_identity.resolved_id(reference, image_identity.record(result.stdout))
    return images


def headroom(needed_mib):
    available = runtime.available_memory_bytes() // 2**20
    if available < needed_mib + resource_policy.START_RESERVE_MIB // 2:
        raise StudioError('Not enough free memory for Studio on this server')


def remove(name):
    if runtime.inspect('container', name):
        lab.docker('rm', '-f', name)


def remove_network(e):
    if runtime.inspect('network', session_network(e)):
        lab.docker('network', 'rm', session_network(e))


def open_network(e):
    """A fresh private network for this session, and the address Studio takes on it. Docker
    picks a free subnet (one that clashes with no host route); the network is then made again
    with that subnet named, because Docker assigns a chosen address only on such a network."""
    name = session_network(e)
    remove_network(e)
    create = ('network', 'create', '--internal', '--label', 'io.sbarbase.owner=' + runtime.OWNER)
    lab.docker(*create, name)
    item = runtime.inspect('network', name)
    config = ((item or {}).get('IPAM') or {}).get('Config') or [{}]
    lab.docker('network', 'rm', name)
    try:
        subnet = ipaddress.ip_network(config[0].get('Subnet', ''))
        gateway = ipaddress.ip_address(config[0].get('Gateway', ''))
    except ValueError:
        raise StudioError('The Studio network has no usable subnet') from None
    address = next(str(host) for host in subnet.hosts() if host != gateway)
    lab.docker(*create, '--subnet', str(subnet), '--gateway', str(gateway), name)
    return address


def open_network_retrying(e, attempts=3):
    """open_network, again when another session took the subnet in between."""
    for attempt in range(attempts):
        try:
            return open_network(e)
        except RuntimeError:
            if attempt == attempts - 1:
                raise
            time.sleep(1)


# Studio listens only on its private address (HOSTNAME), so the image's own health check, which
# asks 127.0.0.1:3000, always failed and showed the container as unhealthy while it served.
STUDIO_HEALTH = ('--health-cmd', "node -e \"fetch('http://'+process.env.HOSTNAME+':3000/',{redirect:'manual'})"
                 ".then(r=>process.exit(r.status<500?0:1)).catch(()=>process.exit(1))\"",
                 '--health-interval', '30s', '--health-timeout', '10s', '--health-retries', '3')


def launch(name, tier, env, image, network, address=None, attach=(), health=()):
    """Create and start one container. ``network`` is the network it joins first, or
    ``container:<name>`` to share another container's namespace; ``attach`` lists the networks
    joined before it starts; ``health`` replaces the image's health check."""
    image = image_identity.immutable(image)
    result = lab.docker('image', 'inspect', image, check=False)
    if result.returncode:
        raise image_identity.inspection_failure(image, result.stdout, result.stderr)
    expected = image_identity.resolved_id(image, image_identity.record(result.stdout))
    flags = resource_policy.container_flags(tier)
    path = runtime.PRIVATE / (name + '.env')
    lab.secure_file(path, ''.join(f'{k}={v}\n' for k, v in env.items()))
    try:
        lab.docker('create', '--pull=never', '--name', name, '--label', 'io.sbarbase.owner=' + runtime.OWNER,
                   '--label', 'io.sbarbase.tier=' + flags['label'], '--network', network,
                   *(('--ip', address) if address else ()),
                   '--memory', flags['memory'], '--memory-swap', flags['memory'], '--cpus', str(flags['cpus']),
                   '--pids-limit', str(flags['pids']), '--cpu-shares', str(flags['shares']),
                   '--blkio-weight', str(flags['weight']), *resource_policy.io_flags(tier),
                   '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', *health, '--env-file', str(path), image)
    finally:
        # The container holds its environment now; nothing keeps the session password on disk.
        path.unlink(missing_ok=True)
    for extra in attach:
        lab.docker('network', 'connect', extra, name)
    lab.docker('start', name)
    item = runtime.inspect('container', name)
    if not item or item.get('Image') != expected or item.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != runtime.OWNER:
        raise StudioError('Studio container identity or ownership does not match')
    return item


def isolated(item, e, address):
    """Studio joined exactly its session network, at ``address``, and the runtime network.
    Returns its address on the runtime network."""
    networks = item.get('NetworkSettings', {}).get('Networks') or {}
    if set(networks) != {session_network(e), runtime.NETWORK} \
            or networks[session_network(e)].get('IPAddress') != address \
            or not networks[runtime.NETWORK].get('IPAddress'):
        raise StudioError('Studio is not confined to its own network')
    return networks[runtime.NETWORK]['IPAddress']


def unexposed(shared):
    """Nothing answers on Studio's runtime network address, on Studio's port or meta's. The
    confinement rests on how the pinned images read HOSTNAME and PG_META_HOST; an image update
    that changed either would show here, and the session is refused instead of opened."""
    for port in (3000, 8080):
        try:
            with socket.create_connection((shared, port), timeout=2):
                pass
        except OSError:
            continue
        raise StudioError(f'Studio answers on the shared runtime network (port {port}); it was stopped')


def meta_ready(meta, timeout=120):
    """postgres-meta answers its root without a database connection, so any reply means it
    listens. It listens on loopback inside Studio's namespace, so it is asked from there."""
    probe = ("fetch('http://127.0.0.1:8080/').then(()=>process.exit(0),()=>process.exit(1))")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lab.docker('exec', meta, 'node', '-e', probe, check=False).returncode == 0:
            return
        time.sleep(2)
    raise StudioError('postgres-meta did not become ready')


def wait_ready(url, timeout=120, any_answer=False, what='Studio'):
    """Waits for a 200, or with any_answer for any HTTP reply: postgres-meta answers its root
    without a database connection, so a reply means it listens."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200 or any_answer:
                    return
        except urllib.error.HTTPError:
            if any_answer:
                return
        except Exception:
            pass
        time.sleep(2)
    raise StudioError(f'{what} did not become ready')


def route_ready(studio, url, timeout=30):
    """Studio reaches its route to Auth and Storage. Asked from inside Studio, since the host
    always reaches its own bridge address while a host firewall may drop the same connection from
    a container (docs/guides/server-deployment.md)."""
    probe = ("fetch(process.argv[1],{signal:AbortSignal.timeout(5000)})"
             ".then(()=>process.exit(0),()=>process.exit(1))")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lab.docker('exec', studio, 'node', '-e', probe, url, check=False).returncode == 0:
            return
        time.sleep(2)
    raise StudioError(f'Studio cannot reach its route to Auth and Storage ({url}); '
                      'the host firewall must accept TCP from Docker bridges to this port')


def copy_functions(studio, e):
    """Studio's Edge Functions page lists `<folder>/<name>/index.*`. Deployed code is kept as
    `<version>/<name>/` under a manifest (src/control/functions.ts), so each function's current
    version is copied in when the session opens; a later deploy shows on the next session."""
    root = runtime.functions_code(e)
    try:
        manifest = json.loads((root / 'functions.json').read_text())['functions']
    except (OSError, ValueError, KeyError, TypeError):
        manifest = {}
    lab.docker('exec', studio, 'mkdir', '-p', FUNCTIONS_FOLDER)
    if not isinstance(manifest, dict):
        return
    for name, entry in sorted(manifest.items()):
        version = entry.get('version') if isinstance(entry, dict) else None
        if not (FUNCTION_NAME.fullmatch(name) and isinstance(version, str) and FUNCTION_VERSION.fullmatch(version)):
            continue
        source = root / version / name
        if source.is_dir() and not source.is_symlink():
            lab.docker('cp', f'{source}/.', f'{studio}:{FUNCTIONS_FOLDER}/{name}')


def display_names(e):
    try:
        with closing(sqlite3.connect(f'file:{CATALOG}?mode=ro', uri=True)) as database, database:
            row = database.execute('SELECT o.name, e.name FROM provision_jobs j JOIN environments e ON e.id=j.environment '
                                   'JOIN projects p ON p.id=e.project JOIN organizations o ON o.id=p.organization '
                                   'WHERE j.runtime=?', (e,)).fetchone()
    except sqlite3.Error:
        row = None
    return row or ('Sbarbase', e)


def up(e):
    if not RUNTIME_ID.fullmatch(e):
        raise StudioError('Invalid environment')
    endpoints = json.loads((runtime.STATE / 'endpoints.json').read_text()) if (runtime.STATE / 'endpoints.json').exists() else {}
    if e not in endpoints:
        raise StudioError('Only a published environment can open Studio')
    if source_fence.is_fenced(lambda query: lab.docker('exec', '-i', runtime.DB, 'psql', '-X', '-qAt', '-U', 'supabase_admin',
                                                        '-d', 'postgres', data=query), e):
        raise StudioError('The environment database is fenced')
    try:
        if write_pause.is_paused(e):
            raise StudioError(f'{e} is paused for writes; open Studio after: write_pause.py resume {e}')
    except write_pause.PauseError as error:
        raise StudioError(str(error)) from None
    require_rule(e)
    image = ensure_images()
    headroom(resource_policy.memory_mib(resource_policy.TIERS['operator.studio'].memory)
             + resource_policy.memory_mib(resource_policy.TIERS['operator.meta'].memory))
    values = json.loads((runtime.PRIVATE / 'runtime.json').read_text())['environments'][e]
    password = secrets.token_hex(32)
    crypto = secrets.token_hex(32)
    service = runtime.token(values['jwt'], 'service_role')
    anon = runtime.token(values['jwt'], 'anon')
    studio, meta = names(e)
    remove(meta)
    remove(studio)
    # Held from the moment the Studio login opens until Studio serves: a write pause taken meanwhile
    # waits, then stops this Studio and closes the login (lab/write_pause.py).
    try:
        with write_pause.unpaused(e, 'open Studio'):
            open_connections(e)
            runtime_sql(role_sql(e, password), e)
            try:
                address = open_network_retrying(e)
                organization, environment = display_names(e)
                item = launch(studio, 'operator.studio', {
                    'HOSTNAME': address, 'STUDIO_PG_META_URL': 'http://127.0.0.1:8080',
                    'POSTGRES_HOST': runtime.DB, 'POSTGRES_PORT': '5432', 'POSTGRES_DB': e, 'POSTGRES_PASSWORD': password,
                    'POSTGRES_USER_READ_WRITE': f'{e}_studio', 'POSTGRES_USER_READ_ONLY': f'{e}_studio',
                    'PG_META_CRYPTO_KEY': crypto, 'PGRST_DB_SCHEMAS': 'public', 'PGRST_DB_EXTRA_SEARCH_PATH': 'public',
                    'DEFAULT_ORGANIZATION_NAME': organization, 'DEFAULT_PROJECT_NAME': environment,
                    'SUPABASE_URL': f'http://{network_gateway()}:{UPSTREAM_PORT}/{e}',
                    'SUPABASE_PUBLIC_URL': auth_settings.public_url(), 'SUPABASE_ANON_KEY': anon, 'SUPABASE_SERVICE_KEY': service,
                    'AUTH_JWT_SECRET': values['jwt'], 'ENABLED_FEATURES_LOGS_ALL': 'false', 'NEXT_TELEMETRY_DISABLED': '1',
                    'SNIPPETS_MANAGEMENT_FOLDER': SNIPPETS_FOLDER, 'EDGE_FUNCTIONS_MANAGEMENT_FOLDER': FUNCTIONS_FOLDER},
                    image['studio'], session_network(e), address=address, attach=(runtime.NETWORK,), health=STUDIO_HEALTH)
                shared = isolated(item, e, address)
                copy_functions(studio, e)
                launch(meta, 'operator.meta', {
                    'PG_META_HOST': '127.0.0.1', 'PG_META_PORT': '8080',
                    'PG_META_DB_HOST': runtime.DB, 'PG_META_DB_PORT': '5432', 'PG_META_DB_NAME': e,
                    'PG_META_DB_USER': f'{e}_studio', 'PG_META_DB_PASSWORD': password, 'PG_META_DB_SSL_MODE': 'disable',
                    'CRYPTO_KEY': crypto}, image['meta'], f'container:{studio}')
                meta_ready(meta)
                wait_ready(f'http://{address}:3000/api/platform/profile')
                unexposed(shared)
            except BaseException:
                remove(meta)
                remove(studio)
                remove_network(e)
                close_login(e)
                raise
            state = load_state()
            state['upstream'] = {'host': network_gateway(), 'port': UPSTREAM_PORT}
            state['sessions'][e] = {'url': f'http://{address}:3000',
                                    'started_at': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')}
            save_state(state)
            # The console opens Studio's internal route to Auth and Storage when it sees the state
            # above, within a couple of seconds. Studio counts as running only once that route
            # answers Studio itself, so its first user or bucket page never meets a closed port.
            try:
                route_ready(studio, f"http://{state['upstream']['host']}:{UPSTREAM_PORT}/")
            except StudioError:
                down(e)
                raise
            return state['sessions'][e]
    except write_pause.PauseError as error:
        raise StudioError(str(error)) from None


def down(e):
    if not RUNTIME_ID.fullmatch(e):
        raise StudioError('Invalid environment')
    state = load_state()
    state['sessions'].pop(e, None)
    save_state(state)
    studio, meta = names(e)
    remove(meta)
    remove(studio)
    remove_network(e)
    close_login(e)


def reset():
    """Stop every Studio session: after a restart none of them has a live browser session."""
    state = load_state()
    running = [line.split()[0] for line in lab.docker('ps', '-a', '--filter', 'label=io.sbarbase.owner=' + runtime.OWNER,
                                                     '--format', '{{.Names}}').stdout.split('\n') if line]
    targets = set(state.get('sessions', {}))
    targets |= {name[len(PREFIX) + 1:-len('-studio')] for name in running if name.endswith('-studio')}
    networks = lab.docker('network', 'ls', '--filter', 'label=io.sbarbase.owner=' + runtime.OWNER,
                          '--format', '{{.Name}}').stdout.split()
    targets |= {name[len(PREFIX) + 1:-len('-studio-net')] for name in networks if name.endswith('-studio-net')}
    for e in sorted(targets):
        if RUNTIME_ID.fullmatch(e):
            down(e)
    save_state({'sessions': {}})
    return sorted(targets)


def record(e, state, failure=None):
    """The outcome, in the catalog row the console reads."""
    try:
        with closing(sqlite3.connect(CATALOG, timeout=5)) as database, database:
            database.execute('UPDATE studio_sessions SET state=?, failure=?, updated_at=? WHERE runtime=?',
                             (state, failure, int(time.time() * 1000), e))
    except sqlite3.Error:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description='Supabase Studio for one environment, on demand')
    parser.add_argument('command', choices=('up', 'down', 'reset'))
    parser.add_argument('environment', nargs='?')
    args = parser.parse_args(argv)
    try:
        if args.command == 'reset':
            stopped = reset()
            print(f'stopped {len(stopped)} Studio session(s)')
            return 0
        if not args.environment:
            raise StudioError('Name the environment by its runtime id')
        if args.command == 'up':
            record(args.environment, 'starting')
            session = up(args.environment)
            record(args.environment, 'running')
            print(f'Studio for {args.environment} is running at {session["url"]}')
            return 0
        down(args.environment)
        record(args.environment, 'stopped')
        print(f'Studio for {args.environment} stopped')
        return 0
    except (StudioError, image_identity.IdentityError, resource_policy.ResourcePolicyError) as error:
        if args.environment:
            record(args.environment, 'failed', str(error)[:200])
        print(f'refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
