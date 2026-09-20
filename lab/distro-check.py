"""Inspect the real installed Supabase PostgreSQL image in an isolated container.
No application volumes, network exposure or remote infrastructure are involved.
"""
import json
import secrets
import time
from pathlib import Path
import run as lab

TAG = 'public.ecr.aws/supabase/postgres:17.6.1.166'
LABEL = 'io.sbarbase.owner=postgres-distribution-probe'
checks = []


def check(name, passed):
    checks.append({'check': name, 'passed': bool(passed)})
    if not passed:
        raise RuntimeError(name)


def main():
    available = int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
    if available < 6 * 1024 * 1024:
        raise RuntimeError('Insufficient memory headroom for distribution probe')
    info = json.loads(lab.docker('image', 'inspect', TAG).stdout)[0]
    pin = {'tag': TAG, 'id': info['Id'], 'digests': info.get('RepoDigests', [])}
    (lab.ROOT/'lab/distro-image.lock.json').write_text(json.dumps(pin, indent=2))
    name = 'sbarbase-distro-' + secrets.token_hex(6)
    env_path = lab.PRIVATE/'distro-probe.env'
    password = secrets.token_hex(32)
    lab.secure_file(env_path, f'POSTGRES_PASSWORD={password}\nPOSTGRES_HOST=/var/run/postgresql\nPOSTGRES_DB=postgres\n')
    started = False
    auth_started = False
    auth_name = name + '-auth'
    auth_path = lab.PRIVATE / 'distro-auth-probe.env'
    evidence = {'image': pin, 'scope': 'Fresh upstream image initialization and database-boundary/bootstrap checks, no full platform or upgrade certification', 'checks': checks}
    def sql(query, database='postgres', check_result=True):
        return lab.docker('exec', '-i', name, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database, '-At', data=query, check=check_result)
    try:
        lab.docker('run', '-d', '--name', name, '--label', LABEL, '--network', 'none',
                   '--memory', '1024m', '--memory-swap', '1024m', '--cpus', '1', '--pids-limit', '128',
                   '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', '--env-file', str(env_path),
                   info['Id'], 'postgres', '-c', 'config_file=/etc/postgresql/postgresql.conf', '-c', 'log_statement=none')
        started = True
        for _ in range(120):
            result = sql("SELECT to_regrole('supabase_privileged_role') IS NOT NULL AND to_regprocedure('auth.uid()') IS NOT NULL;", check_result=False)
            ready = lab.docker('exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'supabase_admin', check=False)
            if result.returncode == 0 and result.stdout.strip() == 't' and ready.returncode == 0:
                break
            # Stop waiting early if initialization exited. Never print raw logs.
            state = lab.docker('inspect', '--format', '{{.State.Running}}', name).stdout.strip()
            if state != 'true':
                raise RuntimeError('Distribution initialization exited')
            time.sleep(.5)
        else:
            raise RuntimeError('Distribution initialization timed out')
        check('fresh upstream image initializes canonical schemas and roles', True)
        evidence['server_version'] = sql('SHOW server_version;').stdout.strip()
        evidence['extensions'] = sql("SELECT extname||':'||extversion FROM pg_extension ORDER BY extname;").stdout.splitlines()
        evidence['roles'] = [json.loads(line) for line in sql("SELECT row_to_json(r) FROM (SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolcanlogin,rolbypassrls FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname) r;").stdout.splitlines()]
        evidence['memberships'] = sql("SELECT member.rolname||' -> '||parent.rolname FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member JOIN pg_roles parent ON parent.oid=m.roleid ORDER BY 1;").stdout.splitlines()
        check('canonical API roles are NOLOGIN', sql("SELECT bool_and(NOT rolcanlogin) FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role');").stdout.strip() == 't')
        check('authenticator cannot assume supabase_admin after final migrations', sql("SELECT NOT pg_has_role('authenticator','supabase_admin','MEMBER');").stdout.strip() == 't')
        before = sql("SELECT pg_get_functiondef('auth.uid()'::regprocedure);").stdout
        evidence['uid_before_auth_reads_json_claims'] = 'request.jwt.claims' in before
        check('image bootstrap alone still needs Auth helper migrations', 'request.jwt.claims' not in before)
        auth_password = secrets.token_hex(32)
        sql(f"ALTER ROLE supabase_auth_admin PASSWORD '{auth_password}';")
        auth_pin = json.loads((lab.ROOT/'lab/images.lock.json').read_text())['auth']
        evidence['auth_image'] = auth_pin
        env = {'GOTRUE_API_HOST': '0.0.0.0', 'GOTRUE_API_PORT': '9999',
               'API_EXTERNAL_URL': 'http://localhost/auth/v1', 'GOTRUE_SITE_URL': 'http://localhost',
               'GOTRUE_DB_DRIVER': 'postgres', 'GOTRUE_DB_DATABASE_URL': f'postgres://supabase_auth_admin:{auth_password}@127.0.0.1:5432/postgres',
               'GOTRUE_JWT_SECRET': secrets.token_hex(32), 'GOTRUE_JWT_AUD': 'authenticated',
               'GOTRUE_DB_MAX_POOL_SIZE': '3', 'GOTRUE_EXTERNAL_EMAIL_ENABLED': 'true',
               'GOTRUE_MAILER_AUTOCONFIRM': 'true'}
        lab.secure_file(auth_path, ''.join(f'{k}={v}\n' for k,v in env.items()))
        lab.docker('run', '-d', '--name', auth_name, '--label', LABEL, '--network', f'container:{name}',
                   '--memory', '256m', '--memory-swap', '256m', '--cpus', '.25', '--pids-limit', '128',
                   '--log-opt', 'max-size=5m', '--log-opt', 'max-file=2', '--env-file', str(auth_path), auth_pin['id'])
        auth_started = True
        for _ in range(60):
            ready = lab.docker('exec', auth_name, 'wget', '-q', '-O', '/dev/null', 'http://127.0.0.1:9999/health', check=False)
            if ready.returncode == 0:
                break
            if lab.docker('inspect', '--format', '{{.State.Running}}', auth_name).stdout.strip() != 'true':
                raise RuntimeError('Auth migration startup exited')
            time.sleep(.5)
        else:
            raise RuntimeError('Auth readiness timed out')
        check('original Auth migrations complete on upstream database image', True)
        result = sql("BEGIN; SET LOCAL request.jwt.claims = '{\"sub\":\"11111111-1111-1111-1111-111111111111\"}'; SELECT auth.uid()='11111111-1111-1111-1111-111111111111'::uuid; ROLLBACK;")
        check('auth.uid reads modern JSON claims after Auth migrations', 't' in result.stdout.splitlines())
        check('Auth migration installs auth.jwt helper', sql("SELECT to_regprocedure('auth.jwt()') IS NOT NULL;").stdout.strip() == 't')
        sql('CREATE DATABASE distro_environment TEMPLATE template0;')
        check('new environment does not inherit Auth schema automatically', sql("SELECT to_regnamespace('auth') IS NULL;", 'distro_environment').stdout.strip() == 't')
        replay = lab.docker('exec', name, 'psql', '-X', '-1', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', 'distro_environment', '-f', '/docker-entrypoint-initdb.d/init-scripts/00000000000000-initial-schema.sql', check=False)
        check('raw upstream bootstrap cannot be replayed per database due to global roles', replay.returncode != 0 and 'already exists' in replay.stderr)
        check('failed transactional bootstrap leaves no partial publication', sql("SELECT count(*)=0 FROM pg_publication;", 'distro_environment').stdout.strip() == 't')
        evidence['raw_bootstrap_failure'] = 'Existing cluster-global role; raw replay is not an environment provisioning strategy.'
        print(f'{len(checks)} distribution checks passed, including expected bootstrap incompatibility.')
    finally:
        if auth_started:
            owner = lab.docker('inspect', '--format', '{{index .Config.Labels \"io.sbarbase.owner\"}}', auth_name, check=False)
            if owner.returncode == 0 and owner.stdout.strip() == 'postgres-distribution-probe':
                lab.docker('rm', '-f', auth_name)
        if started:
            owner = lab.docker('inspect', '--format', '{{index .Config.Labels "io.sbarbase.owner"}}', name, check=False)
            if owner.returncode == 0 and owner.stdout.strip() == 'postgres-distribution-probe':
                lab.docker('rm', '-f', name)
        env_path.unlink(missing_ok=True)
        auth_path.unlink(missing_ok=True)
        (lab.STATE/'distro-verification.json').write_text(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
