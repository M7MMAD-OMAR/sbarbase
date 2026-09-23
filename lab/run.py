"""Owned, bounded local component lab. Never manages unrelated containers."""
import argparse
import effect_receipt
import fcntl
import re
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.lab'
PRIVATE = ROOT / '.secrets'
LABEL = 'io.sbarbase.owner=component-lab'
NETWORK = 'sbarbase-lab'
DB = 'sbarbase-lab-db'
ENVS = ('a_prod', 'a_stage', 'b_prod')
IMAGES = {'db': 'postgres:17-alpine', 'auth': 'public.ecr.aws/supabase/gotrue:v2.196.0',
          'rest': 'public.ecr.aws/supabase/postgrest:v14.15'}


def docker(*args, data=None, check=True):
    result = subprocess.run(['docker', *args], input=data, text=True, capture_output=True)
    if check and result.returncode:
        # Docker/SQL errors may contain generated credentials. Do not echo them.
        raise RuntimeError(f'Docker operation {args[0]} failed; exit {result.returncode}')
    return result


def owned(name):
    r = docker('inspect', '--format', '{{index .Config.Labels "io.sbarbase.owner"}}', name, check=False)
    if r.returncode == 0 and r.stdout.strip() != 'component-lab':
        raise RuntimeError(f'Resource name collision: {name}')
    return r.returncode == 0


def secure_file(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(content)


def atomic(path, value):
    """Replace path with JSON value: private pending file, fsync, rename, fsync the directory."""
    pending = path.with_suffix('.pending')
    secure_file(pending, json.dumps(value))
    with pending.open('rb') as handle:
        os.fsync(handle.fileno())
    os.replace(pending, path)
    effect_receipt.sync_directory(path.parent)


def sql(query, database='postgres', check=True):
    return docker('exec', '-i', DB, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', database, '-At', data=query, check=check)


def launch(name, image, env, memory, cpus, port=None, extra=()):
    if owned(name):
        validate_existing(name, image, env)
        docker('start', name)
        return
    path = PRIVATE / (name + '.env')
    secure_file(path, ''.join(f'{k}={v}\n' for k, v in env.items()))
    args = ['run', '-d', '--name', name, '--label', LABEL, '--network', NETWORK,
            '--memory', memory, '--memory-swap', memory, '--cpus', str(cpus),
            '--pids-limit', '128', '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2',
            '--env-file', str(path)]
    # Internal bridge endpoints are reachable by this Linux host, not published.
    args += list(extra) + [image]
    docker(*args)


def validate_existing(name, image, env):
    """Reject stale runtime configuration before starting a retained container.

    Resolve the pin through Docker because manifest digests and image config
    digests can differ. Keep inspection output private: it contains credentials.
    Replacing containers, especially database images, requires a separate
    migration procedure, never an implicit delete/recreate on startup.
    """
    actual = json.loads(docker('inspect', name).stdout)[0]
    expected = json.loads(docker('image', 'inspect', image).stdout)[0]
    if actual.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != 'component-lab':
        raise RuntimeError('Retained container ownership mismatch')
    if actual.get('Image') != expected.get('Id') or not expected.get('Id'):
        raise RuntimeError('Retained container image differs from pin; explicit migration required')
    configured = dict(entry.split('=', 1) for entry in actual['Config'].get('Env', []) if '=' in entry)
    if any(configured.get(key) != str(value) for key, value in env.items()):
        raise RuntimeError('Retained container configuration differs; explicit reconciliation required')


def port(name, inside):
    info = json.loads(docker('inspect', name).stdout)[0]
    address = info['NetworkSettings']['Networks'][NETWORK]['IPAddress']
    if not address:
        raise RuntimeError(f'{name} has no active network endpoint')
    return f'http://{address}:{inside}'


def provision_environment(e, credentials, checkpoint=lambda phase: None, executor=None):
    """Provision SQL phases; closed databases require explicit reconciliation."""
    execute = executor or sql
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,30}', e):
        raise ValueError('Invalid environment identifier')
    database_state = execute(f"SELECT datallowconn FROM pg_database WHERE datname='{e}';").stdout.strip()
    if database_state == 'f':
        raise RuntimeError('Existing environment database is closed; explicit reconciliation required')
    if database_state not in ('', 't'):
        raise RuntimeError('Database connection state unavailable')
    created_here = database_state == ''
    for role in ('auth', 'rest'):
        name = f'{e}_{role}'
        password = credentials[role]
        if not re.fullmatch(r'[a-f0-9]{64}', password):
            raise ValueError('Invalid generated credential')
        if execute(f"SELECT 1 FROM pg_roles WHERE rolname='{name}'").stdout.strip() != '1':
            execute(f"CREATE ROLE {name} LOGIN NOINHERIT PASSWORD '{password}';")
    checkpoint('roles')
    if created_here:
        execute(f'CREATE DATABASE {e} ALLOW_CONNECTIONS false;')
    checkpoint('database')
    reopen = f' ALTER DATABASE {e} ALLOW_CONNECTIONS true;' if created_here else ''
    execute(f"BEGIN; REVOKE ALL ON DATABASE {e} FROM PUBLIC; GRANT CONNECT ON DATABASE {e} TO {e}_auth, {e}_rest; GRANT anon, authenticated, service_role TO {e}_rest;{reopen} COMMIT;")
    execute(f"REVOKE CREATE ON SCHEMA public FROM PUBLIC; CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION {e}_auth; GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role; GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;", e)
    execute(f'ALTER ROLE {e}_auth IN DATABASE {e} SET search_path TO auth;')
    checkpoint('permissions')


def auth_configuration(e, v, database_host, mail=None):
    """The environment's Auth environment. Without mail the dict is unchanged, byte for byte.

    `mail` is the validated per environment mail configuration, or None. It is
    read once, at process start: the pinned Auth has no reload of the mailer, so a
    mail change is a container reconcile (docs/ENVIRONMENT-EMAIL.md sections 2.1
    and 2.3). The management realm calls this with three arguments on purpose: it
    is the operator's own identity realm and never gains SMTP.
    """
    config = {
        'GOTRUE_API_HOST': '0.0.0.0', 'GOTRUE_API_PORT': '9999',
        'API_EXTERNAL_URL': f'http://localhost/{e}/auth/v1',
        'GOTRUE_SITE_URL': 'http://localhost', 'GOTRUE_DB_DRIVER': 'postgres',
        'GOTRUE_DB_DATABASE_URL': f'postgres://{e}_auth:{v["auth"]}@{database_host}:5432/{e}',
        'GOTRUE_JWT_SECRET': v['jwt'], 'GOTRUE_JWT_AUD': 'authenticated',
        'GOTRUE_JWT_DEFAULT_GROUP_NAME': 'authenticated', 'GOTRUE_JWT_ADMIN_ROLES': 'service_role',
        'GOTRUE_EXTERNAL_EMAIL_ENABLED': 'true', 'GOTRUE_MAILER_AUTOCONFIRM': 'true',
        'GOTRUE_DB_MAX_POOL_SIZE': '3', 'GOTRUE_DB_NAMESPACE': 'auth'}
    if mail is None:
        return config
    config.update({
        'GOTRUE_SMTP_HOST': mail['host'], 'GOTRUE_SMTP_PORT': str(mail['port']),
        'GOTRUE_SMTP_USER': mail['user'], 'GOTRUE_SMTP_PASS': mail['pass'],
        'GOTRUE_SMTP_ADMIN_EMAIL': mail['admin_email'], 'GOTRUE_SMTP_SENDER_NAME': mail['sender_name'],
        'GOTRUE_SMTP_MAX_FREQUENCY': mail['max_frequency'],
        # The pinned mailer logs one record per message, including the recipient
        # address, when this is true. It is fixed here so no configuration can
        # turn recipient address logging on by accident.
        'GOTRUE_SMTP_LOGGING_ENABLED': 'false',
        'GOTRUE_MAILER_AUTOCONFIRM': 'true' if mail['autoconfirm'] else 'false',
        'GOTRUE_MAILER_OTP_EXP': str(mail['otp_exp']), 'GOTRUE_MAILER_OTP_LENGTH': str(mail['otp_length']),
        'GOTRUE_MAILER_SECURE_EMAIL_CHANGE_ENABLED': 'true' if mail['secure_email_change'] else 'false',
        'GOTRUE_RATE_LIMIT_EMAIL_SENT': str(mail['rate_limit_email_sent']),
        'GOTRUE_RATE_LIMIT_OTP': str(mail['rate_limit_otp']),
        'GOTRUE_RATE_LIMIT_VERIFY': str(mail['rate_limit_verify']),
        'GOTRUE_RATE_LIMIT_HEADER': mail['rate_limit_header']})
    if mail['reply_to']:
        # Reply-To is not a first class variable in this pin: it travels as an
        # SMTP header, which upstream parses as JSON into map[string][]string.
        # The key is omitted when empty so no empty header is ever sent.
        config['GOTRUE_SMTP_HEADERS'] = json.dumps({'Reply-To': [mail['reply_to']]})
    return config


def rest_configuration(e, v, database_host):
    return {
        'PGRST_DB_URI': f'postgres://{e}_rest:{v["rest"]}@{database_host}:5432/{e}',
        'PGRST_DB_SCHEMAS': 'public', 'PGRST_DB_ANON_ROLE': 'anon',
        'PGRST_JWT_SECRET': v['jwt'], 'PGRST_DB_POOL': '3'}


def launch_services(e, v, pins):
    auth = f'sbarbase-lab-{e}-auth'
    rest = f'sbarbase-lab-{e}-rest'
    launch(auth, pins['auth']['id'], auth_configuration(e, v, DB), '256m', .25, 9999)
    launch(rest, pins['rest']['id'], rest_configuration(e, v, DB), '256m', .25, 3000)
    return {'auth': port(auth,9999), 'rest': port(rest,3000)}


def up():
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
    if available < 6 * 1024 * 1024:
        raise RuntimeError('Less than 6 GiB available; do not start the lab now')
    STATE.mkdir(exist_ok=True)
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    lock = ROOT / 'lab' / 'images.lock.json'
    if not lock.exists():
        pins = {}
        for key, tag in IMAGES.items():
            item = json.loads(docker('image', 'inspect', tag).stdout)[0]
            pins[key] = {'tag': tag, 'id': item['Id'], 'digests': item['RepoDigests']}
        lock.write_text(json.dumps(pins, indent=2))
    pins = json.loads(lock.read_text())
    # Program-only secret loading; never log or expose this state to tool output.
    secret_path = PRIVATE / 'lab.json'
    if not secret_path.exists():
        values = {'admin': secrets.token_hex(24), 'environments': {e: {k: secrets.token_hex(32) for k in ('auth', 'rest', 'jwt')} for e in ENVS}}
        secure_file(secret_path, json.dumps(values))
    values = json.loads(secret_path.read_text())
    environments = tuple(values['environments'])
    if len(environments) > 5:
        raise RuntimeError('Lab environment admission limit exceeded')
    net = docker('network', 'inspect', NETWORK, check=False)
    if net.returncode:
        docker('network', 'create', '--internal', '--label', LABEL, NETWORK)
    elif json.loads(net.stdout)[0].get('Labels', {}).get('io.sbarbase.owner') != 'component-lab':
        raise RuntimeError('Network ownership collision')
    vol = docker('volume', 'inspect', 'sbarbase-lab-pgdata', check=False)
    if vol.returncode:
        docker('volume', 'create', '--label', LABEL, 'sbarbase-lab-pgdata')
    elif json.loads(vol.stdout)[0].get('Labels', {}).get('io.sbarbase.owner') != 'component-lab':
        raise RuntimeError('Volume ownership collision')
    launch(DB, pins['db']['id'], {'POSTGRES_PASSWORD': values['admin']}, '1024m', 1,
           extra=('-v', 'sbarbase-lab-pgdata:/var/lib/postgresql/data'))
    for _ in range(60):
        if docker('exec', DB, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', check=False).returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError('Database readiness timed out')
    sql("DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF; END $$;")
    hba = ['local all all trust', 'host all postgres 0.0.0.0/0 reject']
    for e in environments:
        v = values['environments'][e]
        provision_environment(e, v)
        for role in ('auth', 'rest'):
            hba.append(f'host {e} {e}_{role} 0.0.0.0/0 scram-sha-256')
    hba += ['host all all 0.0.0.0/0 reject', 'host all all ::/0 reject']
    docker('exec', '-i', DB, 'sh', '-c', 'cat > "$PGDATA/pg_hba.conf"', data='\n'.join(hba)+'\n')
    sql('SELECT pg_reload_conf();')
    endpoints = {}
    for e in environments:
        v = values['environments'][e]
        endpoints[e] = launch_services(e, v, pins)
    (STATE / 'endpoints.json').write_text(json.dumps(endpoints, indent=2))
    for e in environments:
        for service, suffix in (('auth', '/health'), ('rest', '/')):
            for attempt in range(30):
                try:
                    with urllib.request.urlopen(endpoints[e][service] + suffix, timeout=2) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(.5)
            else:
                raise RuntimeError(f'{e} {service} readiness failed')
    print(f'Lab started: {1+2*len(environments)} containers, aggregate limits {1024+512*len(environments)} MiB and {1+.5*len(environments)} logical CPUs.')
    status()


def status():
    print(docker('ps', '-a', '--filter', f'label={LABEL}', '--format', '{{.Names}}\t{{.Status}}').stdout.strip())


def stop():
    ids = docker('ps', '-q', '--filter', f'label={LABEL}').stdout.split()
    if ids:
        docker('stop', *ids)
    print('Owned lab containers stopped; volumes preserved.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['up', 'status', 'stop'])
    args = parser.parse_args()
    STATE.mkdir(exist_ok=True)
    operation_lock = (STATE / 'operation.lock').open('w')
    try:
        fcntl.flock(operation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Another lab operation is active')
    try:
        globals()[args.command]()
    except Exception as exc:
        print(f'Lab operation failed: {exc}')
        if args.command == 'up':
            stop()
        raise SystemExit(1)
