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

State for the loopback server is ``.lab/upstream/studio.json``; the request and outcome of
each session live in the control catalog (``studio_sessions``).
"""
import argparse
import connection_budget
import datetime
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import durable_runtime as runtime
import resource_policy
import run as lab
import source_fence

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


class StudioError(RuntimeError):
    pass


def pins():
    return json.loads((lab.ROOT / 'lab' / 'studio-image.lock.json').read_text())


def names(e):
    return f'{PREFIX}-{e}-studio', f'{PREFIX}-{e}-meta'


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
    runtime_sql(f'ALTER DATABASE {e} CONNECTION LIMIT {connection_budget.database_limit(studio=True, realtime=runtime.realtime_on(e))};')


def close_login(e):
    role = f'{e}_studio'
    runtime_sql(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f"ALTER ROLE {role} NOLOGIN; END IF; END $$; "
                f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE usename = '{role}';")
    runtime_sql(f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_database WHERE datname = '{e}') THEN "
                f"EXECUTE 'ALTER DATABASE {e} CONNECTION LIMIT {connection_budget.database_limit(realtime=runtime.realtime_on(e))}'; END IF; END $$;")


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
    for component, pin in pins().items():
        if lab.docker('image', 'inspect', pin['id'], check=False).returncode:
            reference = next((item for item in pin.get('digests', []) if '@sha256:' in item), pin['id'])
            if lab.docker('pull', reference, check=False).returncode:
                raise StudioError(f'Pinned {component} image could not be pulled')


def headroom(needed_mib):
    available = runtime.available_memory_bytes() // 2**20
    if available < needed_mib + resource_policy.START_RESERVE_MIB // 2:
        raise StudioError('Not enough free memory for Studio on this server')


def remove(name):
    if runtime.inspect('container', name):
        lab.docker('rm', '-f', name)


def launch(name, tier, env, image):
    flags = resource_policy.container_flags(tier)
    path = runtime.PRIVATE / (name + '.env')
    lab.secure_file(path, ''.join(f'{k}={v}\n' for k, v in env.items()))
    try:
        lab.docker('run', '-d', '--name', name, '--label', 'io.sbarbase.owner=' + runtime.OWNER,
                   '--label', 'io.sbarbase.tier=' + flags['label'], '--network', runtime.NETWORK,
                   '--memory', flags['memory'], '--memory-swap', flags['memory'], '--cpus', str(flags['cpus']),
                   '--pids-limit', str(flags['pids']), '--cpu-shares', str(flags['shares']),
                   '--blkio-weight', str(flags['weight']), *resource_policy.io_flags(tier),
                   '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', '--env-file', str(path), image)
    finally:
        # The container holds its environment now; nothing keeps the session password on disk.
        path.unlink(missing_ok=True)
    item = runtime.inspect('container', name)
    address = item['NetworkSettings']['Networks'][runtime.NETWORK]['IPAddress'] if item else ''
    if not address:
        raise StudioError('Studio container has no address')
    return address


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
    require_rule(e)
    ensure_images()
    headroom(resource_policy.memory_mib(resource_policy.TIERS['operator.studio'].memory)
             + resource_policy.memory_mib(resource_policy.TIERS['operator.meta'].memory))
    values = json.loads((runtime.PRIVATE / 'runtime.json').read_text())['environments'][e]
    password = secrets.token_hex(32)
    crypto = secrets.token_hex(32)
    service = runtime.token(values['jwt'], 'service_role')
    anon = runtime.token(values['jwt'], 'anon')
    studio, meta = names(e)
    remove(studio)
    remove(meta)
    open_connections(e)
    runtime_sql(role_sql(e, password), e)
    image = {component: pin['id'] for component, pin in pins().items()}
    try:
        meta_address = launch(meta, 'operator.meta', {
            'PG_META_PORT': '8080', 'PG_META_DB_HOST': runtime.DB, 'PG_META_DB_PORT': '5432', 'PG_META_DB_NAME': e,
            'PG_META_DB_USER': f'{e}_studio', 'PG_META_DB_PASSWORD': password, 'PG_META_DB_SSL_MODE': 'disable',
            'CRYPTO_KEY': crypto}, image['meta'])
        organization, environment = display_names(e)
        studio_address = launch(studio, 'operator.studio', {
            'HOSTNAME': '0.0.0.0', 'STUDIO_PG_META_URL': f'http://{meta_address}:8080',
            'POSTGRES_HOST': runtime.DB, 'POSTGRES_PORT': '5432', 'POSTGRES_DB': e, 'POSTGRES_PASSWORD': password,
            'POSTGRES_USER_READ_WRITE': f'{e}_studio', 'POSTGRES_USER_READ_ONLY': f'{e}_studio',
            'PG_META_CRYPTO_KEY': crypto, 'PGRST_DB_SCHEMAS': 'public', 'PGRST_DB_EXTRA_SEARCH_PATH': 'public',
            'DEFAULT_ORGANIZATION_NAME': organization, 'DEFAULT_PROJECT_NAME': environment,
            'SUPABASE_URL': f'http://{network_gateway()}:{UPSTREAM_PORT}/{e}',
            'SUPABASE_PUBLIC_URL': 'http://localhost', 'SUPABASE_ANON_KEY': anon, 'SUPABASE_SERVICE_KEY': service,
            'AUTH_JWT_SECRET': values['jwt'], 'ENABLED_FEATURES_LOGS_ALL': 'false', 'NEXT_TELEMETRY_DISABLED': '1'},
            image['studio'])
        wait_ready(f'http://{meta_address}:8080/', any_answer=True, what='postgres-meta')
        wait_ready(f'http://{studio_address}:3000/api/platform/profile')
    except BaseException:
        remove(studio)
        remove(meta)
        close_login(e)
        raise
    state = load_state()
    state['upstream'] = {'host': network_gateway(), 'port': UPSTREAM_PORT}
    state['sessions'][e] = {'url': f'http://{studio_address}:3000',
                            'started_at': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')}
    save_state(state)
    # The console opens Studio's internal route to Auth and Storage when it sees the state
    # above, within a couple of seconds. Studio counts as running only once that route
    # answers, so its first user or bucket page never meets a closed port.
    try:
        wait_ready(f"http://{state['upstream']['host']}:{UPSTREAM_PORT}/", timeout=30, any_answer=True,
                   what='The Studio route to Auth and Storage')
    except StudioError:
        down(e)
        raise
    return state['sessions'][e]


def down(e):
    if not RUNTIME_ID.fullmatch(e):
        raise StudioError('Invalid environment')
    state = load_state()
    state['sessions'].pop(e, None)
    save_state(state)
    for name in names(e):
        remove(name)
    close_login(e)


def reset():
    """Stop every Studio session: after a restart none of them has a live browser session."""
    state = load_state()
    running = [line.split()[0] for line in lab.docker('ps', '-a', '--filter', 'label=io.sbarbase.owner=' + runtime.OWNER,
                                                     '--format', '{{.Names}}').stdout.split('\n') if line]
    targets = set(state.get('sessions', {}))
    targets |= {name[len(PREFIX) + 1:-len('-studio')] for name in running if name.endswith('-studio')}
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
    except (StudioError, resource_policy.ResourcePolicyError) as error:
        if args.environment:
            record(args.environment, 'failed', str(error)[:200])
        print(f'refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
