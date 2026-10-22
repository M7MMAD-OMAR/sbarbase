"""Live check that opening or closing an environment's developer login cannot be held up by a tenant.

Turning direct database access on opens the login under the installation's operation lock
(durable_runtime.database_command). A session acting as the login can change its own password in a
transaction it leaves open, and the role row then holds that statement. developer_login_sql ends the
environment's sessions that hold role rows first, in the same transaction. This starts a disposable
Supabase Postgres container and, for a transaction left open by the login's own session and by a request
running a function the login owns, checks that a plain bounded ALTER ROLE waits and fails (the
control), that developer_login_sql succeeds at once and ends the holder, and that closing the login
works the same way. Removes the container at the end. Needs Docker and about 1 GiB.

  /usr/bin/python3 lab/developer-login-lock-check.py [image] [evidence.json]
"""
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import durable_runtime  # noqa: E402

IMAGE = sys.argv[1] if len(sys.argv) > 1 else 'public.ecr.aws/supabase/postgres:17.6.1.166'
EVIDENCE = sys.argv[2] if len(sys.argv) > 2 else None
NAME = 'sbarbase-developer-login-lock-check-' + secrets.token_hex(3)
E = 'e_' + secrets.token_hex(12)
results = []


def docker(*args, **options):
    return subprocess.run(['docker', *args], capture_output=True, text=True, **options)


def psql(sql, database='postgres', user='supabase_admin'):
    return docker('exec', '-i', NAME, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', user, '-h', '127.0.0.1',
                  '-d', database, '-qAt', input=sql)


def superuser(sql, database='postgres'):
    result = psql(sql, database)
    if result.returncode:
        raise RuntimeError(result.stderr[:500])
    return result.stdout.strip()


def check(label, passed, detail=''):
    results.append({'check': label, 'result': 'pass' if passed else 'fail', **({'detail': detail[:300]} if detail and not passed else {})})


def held(label, user, sql, statement):
    """Leave `sql` open as `user`, then run the control and `statement`; the holder must end."""
    holder = subprocess.Popen(['docker', 'exec', '-i', NAME, 'psql', '-X', '-U', user, '-h', '127.0.0.1', '-d', E, '-c', sql],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(2)
    control = psql(f"SET lock_timeout = '2s'; ALTER ROLE {E}_developer CONNECTION LIMIT 10;")
    check(f'{label}: a bounded ALTER ROLE without ending sessions waits and fails', 'lock timeout' in control.stderr, control.stderr)
    started = time.monotonic()
    result = psql(statement)
    took = time.monotonic() - started
    check(f'{label}: developer_login_sql succeeds within a second', result.returncode == 0 and took < 1, f'{result.stderr} {took:.2f}s')
    try:
        _, error = holder.communicate(timeout=10)
        check(f'{label}: the holding session was ended', holder.returncode != 0 and any(word in error for word in ('terminat', 'connection to server was lost')), error)
    except subprocess.TimeoutExpired:
        holder.kill()
        check(f'{label}: the holding session was ended', False, 'still running')


def main():
    docker('run', '-d', '--rm', '--name', NAME, '-e', 'POSTGRES_PASSWORD=' + secrets.token_hex(16),
           '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', IMAGE)
    try:
        for _ in range(120):
            if psql('SELECT 1').returncode == 0:
                break
            time.sleep(1)
        time.sleep(3)
        superuser(f"CREATE ROLE {E}_developer LOGIN; CREATE ROLE {E}_rest LOGIN; CREATE DATABASE {E};")
        superuser(f"GRANT CONNECT ON DATABASE {E} TO {E}_developer, {E}_rest; GRANT CREATE ON SCHEMA public TO {E}_developer;", E)
        made = psql(f"""CREATE FUNCTION public.hold() RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN
  EXECUTE 'ALTER ROLE {E}_developer PASSWORD ''{secrets.token_hex(8)}'''; PERFORM pg_sleep(60); END $$;
GRANT EXECUTE ON FUNCTION public.hold() TO {E}_rest;""", E, f'{E}_developer')
        check('the login can write a function that changes its own password', made.returncode == 0, made.stderr)
        held('own session', f'{E}_developer', f"BEGIN; ALTER ROLE {E}_developer PASSWORD '{secrets.token_hex(8)}'; SELECT pg_sleep(60);",
             durable_runtime.developer_login_sql(E, True))
        held('request through a function the login owns', f'{E}_rest', 'SELECT public.hold();', durable_runtime.developer_login_sql(E, True))
        check('the login is open', superuser(f"SELECT rolcanlogin FROM pg_roles WHERE rolname = '{E}_developer'") == 't')
        held('closing, own session', f'{E}_developer', f"BEGIN; ALTER ROLE {E}_developer PASSWORD '{secrets.token_hex(8)}'; SELECT pg_sleep(60);",
             durable_runtime.developer_login_sql(E, False))
        check('the login is closed', superuser(f"SELECT rolcanlogin FROM pg_roles WHERE rolname = '{E}_developer'") == 'f')
    finally:
        docker('rm', '-f', NAME)
    passed = sum(item['result'] == 'pass' for item in results)
    print(f'{passed} of {len(results)} checks passed')
    for item in results:
        print(item['result'], item['check'], item.get('detail', ''))
    if EVIDENCE:
        Path(EVIDENCE).write_text(json.dumps({'image': IMAGE, 'date': time.strftime('%Y-%m-%d'),
                                              'script': 'lab/developer-login-lock-check.py', 'checks': results}, indent=1) + '\n')
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
