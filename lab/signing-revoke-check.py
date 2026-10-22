"""Signing rotation session revocation check, on a disposable database: who runs it and what bounds it.

Usage: /usr/bin/python3 lab/signing-revoke-check.py [--evidence PATH]

Starts a disposable pinned PostgreSQL container on its own network, provisions one environment with the
runtime's own SQL (lab/run.py provision_environment, Runtime.developer_sql), runs the pinned Auth's
migrations against it, and acts as the tenant: the developer login, a member of the Auth login, adds
triggers to auth.sessions and auth.refresh_tokens. Then:

- the statement a rotation ran before, as the superuser, lets the trigger make the developer login a
  superuser (in a transaction that is rolled back);
- `revoke_auth_sessions`, as the Auth login, ends every session while the trigger is refused;
- tenant code holding the session rows makes it give up within its lock bound;
- a trigger that lifts the bounds and sleeps at commit makes the client give up, and its server
  session is ended.

Needs about 1.5 GiB of host memory. Everything it creates carries its own label and is removed by
exact identity at the end. Touches no installation state and no retained container.
"""
import argparse
import datetime
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

import docker_profile
import durable_runtime
import run as lab

ROOT = Path(__file__).resolve().parent.parent
OWNER = 'signing-revoke-check'
ADMIN = 'supabase_admin'
checks = []
started = time.time()


def record(label, ok, detail=''):
    checks.append({'check': label, 'ok': bool(ok), **({'detail': detail} if detail else {})})
    print(('ok: ' if ok else 'FAILED: ') + label + (f' ({detail})' if detail else ''))
    return ok


def docker(*args, data=None, check=True, env=None):
    result = subprocess.run(docker_profile.docker_command(*args), input=data, text=True, capture_output=True, timeout=300, env=env)
    if check and result.returncode:
        raise RuntimeError(f'Docker operation {args[0]} failed; exit {result.returncode}')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', default='docs/evidence/signing-revoke-checks.json')
    args = parser.parse_args()
    docker_profile.require_or_exit()
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))) * 1024
    if not record('host headroom before the disposable database', available >= 1536 * 1024 ** 2):
        return finish(args.evidence, {})
    images = {'db': json.loads((ROOT / 'lab' / 'distro-image.lock.json').read_text())['id'],
              'auth': json.loads((ROOT / 'lab' / 'images.lock.json').read_text())['auth']['id']}
    for image in images.values():
        docker('image', 'inspect', image)
    prefix = 'sbarbase-revoke-check-' + secrets.token_hex(4)
    network = container = holder = None
    try:
        network = docker('network', 'create', '--label', 'io.sbarbase.owner=' + OWNER, prefix + '-net').stdout.strip()
        container = docker('run', '-d', '--pull=never', '--restart=no', '--name', prefix + '-db', '--label', 'io.sbarbase.owner=' + OWNER,
                           '--network', network, '--network-alias', 'db', '--memory', '1024m', '--memory-swap', '1024m', '--cpus', '1',
                           '-e', 'POSTGRES_PASSWORD', '-e', 'POSTGRES_HOST=/var/run/postgresql', '-e', 'POSTGRES_DB=postgres',
                           images['db'], 'postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none',
                           env={**os.environ, 'POSTGRES_PASSWORD': secrets.token_hex(32)}).stdout.strip()
        deadline = time.monotonic() + 120
        while docker('exec', container, 'pg_isready', '-U', ADMIN, check=False).returncode:
            if time.monotonic() > deadline:
                raise RuntimeError('Disposable database readiness deadline')
            time.sleep(.5)
        time.sleep(3)

        def sql(query, database='postgres', check=True):
            return docker('exec', '-i', container, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', ADMIN, '-d', database, '-qAt', data=query, check=check)

        def as_developer(query, background=False):
            command = docker_profile.docker_command(
                'exec', '-i', container, 'sh', '-c',
                'read -r PGPASSWORD; export PGPASSWORD; exec psql -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$1" -d "$2" -qAt',
                'revoke-check', f'{e}_developer', e)
            if background:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
                process.stdin.write(developer + '\n' + query)
                process.stdin.close()
                return process
            return subprocess.run(command, input=developer + '\n' + query, text=True, capture_output=True, timeout=120)

        e = 'e_' + uuid.uuid4().hex[:24]
        v = {key: secrets.token_hex(32) for key in ('auth', 'rest', 'storage', 'jwt')}
        developer = secrets.token_hex(32)
        lab.provision_environment(e, v, executor=sql)
        # The Storage login, as Runtime.provision_database creates it; the developer login is a member of it too.
        sql(f"CREATE ROLE {e}_storage LOGIN NOINHERIT PASSWORD '{v['storage']}';")
        sql(durable_runtime.Runtime.developer_sql(None, e, developer))
        # Direct database access turned on, as Runtime.developer_on leaves it.
        sql(f'ALTER ROLE {e}_developer LOGIN;')
        config = lab.auth_configuration(e, v, 'db')
        migrated = docker('run', '--rm', '--pull=never', '--network', network, '--memory', '256m', '--label', 'io.sbarbase.owner=' + OWNER,
                          *[item for key in config for item in ('-e', key)], images['auth'], 'auth', 'migrate',
                          env={**os.environ, **config}, check=False)
        record('the pinned Auth migrated the environment as its own login', migrated.returncode == 0 and sql(
            f"SELECT bool_and(pg_get_userbyid(c.relowner) = '{e}_auth') FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'auth' AND c.relname IN ('sessions', 'refresh_tokens');", e).stdout.strip() == 't')

        seed = (f"INSERT INTO auth.users (id, aud, role, email) VALUES ('{uuid.uuid4()}', 'authenticated', 'authenticated', 'p' || pg_catalog.md5(pg_catalog.random()::text) || '@example.test') "
                "ON CONFLICT DO NOTHING; "
                "WITH u AS (SELECT id FROM auth.users LIMIT 1), s AS (INSERT INTO auth.sessions (id, user_id) SELECT gen_random_uuid(), id FROM u RETURNING id, user_id) "
                "INSERT INTO auth.refresh_tokens (token, user_id, session_id, revoked) SELECT pg_catalog.md5(pg_catalog.random()::text), user_id::text, id, false FROM s;")
        sql(seed, e)
        trigger = f"""SET ROLE {e}_auth;
CREATE TABLE auth.revoke_check_seen (who text, detail text);
CREATE FUNCTION auth.revoke_check() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  INSERT INTO auth.revoke_check_seen VALUES (current_user, session_user);
  BEGIN
    RESET ROLE;
    EXECUTE 'ALTER ROLE {e}_developer SUPERUSER';
  EXCEPTION WHEN others THEN INSERT INTO auth.revoke_check_seen VALUES ('refused', SQLERRM);
  END;
  RETURN NULL;
END $$;
CREATE TRIGGER revoke_check BEFORE DELETE ON auth.sessions FOR EACH STATEMENT EXECUTE FUNCTION auth.revoke_check();
CREATE TRIGGER revoke_check BEFORE UPDATE ON auth.refresh_tokens FOR EACH STATEMENT EXECUTE FUNCTION auth.revoke_check();"""
        added = as_developer(trigger)
        record('the developer login added triggers to auth.sessions and auth.refresh_tokens', added.returncode == 0, added.stderr.strip()[-300:])
        superuser = lambda: sql(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{e}_developer';").stdout.strip()

        before = sql('BEGIN; UPDATE auth.refresh_tokens SET revoked = true WHERE NOT revoked; DELETE FROM auth.sessions; '
                     f"SELECT rolsuper FROM pg_roles WHERE rolname = '{e}_developer'; ROLLBACK;", e).stdout.split()
        record('as the superuser, the previous statement let the trigger make the developer login a superuser',
               before[-1:] == ['t'] and superuser() == 'f', 'rolled back')

        record('as the Auth login, revoke_auth_sessions committed', durable_runtime.revoke_auth_sessions(e, v['auth'], container))
        state = sql("SELECT (SELECT count(*) FROM auth.sessions), (SELECT count(*) FROM auth.refresh_tokens WHERE NOT revoked);", e).stdout.strip()
        record('every session ended and every refresh token revoked', state == '0|0', state)
        seen = sql('SELECT who FROM auth.revoke_check_seen;', e).stdout.split()
        record('the trigger ran as the Auth login and was refused', set(seen) == {f'{e}_auth', 'refused'} and superuser() == 'f')

        sql(seed, e)
        holder = as_developer('BEGIN; SELECT id FROM auth.sessions FOR UPDATE; SELECT pg_sleep(60);', background=True)
        time.sleep(3)
        moment = time.monotonic()
        held = durable_runtime.revoke_auth_sessions(e, v['auth'], container)
        seconds = round(time.monotonic() - moment, 1)
        record('rows the tenant holds make it give up within the lock bound', not held and seconds < 15, f'{seconds} s')
        holder.kill()
        holder.wait()
        holder = None
        sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '{e}_developer';")

        lifted = f"""SET ROLE {e}_auth;
CREATE OR REPLACE FUNCTION auth.revoke_check() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  SET lock_timeout = 0; SET statement_timeout = 0; SET transaction_timeout = 0;
  RETURN NULL;
END $$;
CREATE FUNCTION auth.revoke_check_late() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(600); RETURN NULL; END $$;
CREATE CONSTRAINT TRIGGER revoke_check_late AFTER DELETE ON auth.sessions DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION auth.revoke_check_late();"""
        added = as_developer(lifted)
        record('the developer login replaced the trigger with one that lifts the bounds and sleeps at commit', added.returncode == 0, added.stderr.strip()[-300:])
        moment = time.monotonic()
        late = durable_runtime.revoke_auth_sessions(e, v['auth'], container)
        seconds = round(time.monotonic() - moment, 1)
        record('a trigger that lifts the bounds makes the client give up', not late and seconds < durable_runtime.SESSIONS_REVOKE_SECONDS + 15, f'{seconds} s')
        time.sleep(2)
        remaining = sql(f"SELECT count(*) FROM pg_stat_activity WHERE usename = '{e}_auth';").stdout.strip()
        record("and the Auth login's server session is ended", remaining == '0', remaining)
        state = sql('SELECT count(*) FROM auth.sessions;', e).stdout.strip()
        record('and nothing was committed', state == '1', state)
        record('the developer login is still not a superuser', superuser() == 'f')
    except Exception as error:
        record('revoke check ran to the end', False, f'{type(error).__name__}: {error}')
    finally:
        if holder:
            holder.kill()
        if container:
            docker('rm', '-f', container, check=False)
        if network:
            docker('network', 'rm', network, check=False)
    return finish(args.evidence, images)


def finish(evidence, images):
    passed = bool(checks) and all(row['ok'] for row in checks)
    Path(evidence).write_text(json.dumps({
        'check': 'signing-revoke', 'recorded': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'), 'passed': passed,
        'count': len(checks), 'seconds': round(time.time() - started), 'images': images,
        'scope': "The session revocation of a signing rotation, on a disposable pinned database with the pinned Auth's own schema: "
                 'tenant triggers on the Auth tables ran as the superuser before and escalated; as the Auth login they are refused, '
                 'held rows and lifted bounds make it give up, and nothing waits unbounded. Not a full rotation and not a running installation.',
        'checks': checks}, indent=2) + '\n')
    print(f'evidence: {evidence}\nsigning revoke check: {"passed" if passed else "failed"}')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
