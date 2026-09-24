"""Import check: a Supabase project moves into a new environment and its user signs in with the old password.

Usage: /usr/bin/python3 lab/import-check.py <operator.json> [--evidence PATH]

Needs a running installation with one provisioned environment (the first-project and database
checks leave one, with a sign-up trigger on auth.users). That environment is the source, as a
Supabase project would be: its developer login through the database listener and its API
through the gateway. The check seeds it with a user, a row-level-security table with that
user's row, a private bucket with that user's file, then creates a new environment, imports
into it with lab/import_project.py, and acts as the application against the new one: the user
signs in with the old password, reads only their own row, downloads their own file, and a new
sign-up runs the imported trigger. A second import into the now full environment is refused.
The operator password is read from the private file and never printed.
"""
import argparse
import datetime
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import durable_runtime as runtime

ROOT = Path(__file__).resolve().parent.parent
MANAGEMENT_KEY = 'sb_publishable_sbarbase_local_management'
checks = []
started = time.time()


def record(check, ok, detail=''):
    checks.append({'check': check, 'ok': bool(ok), 'detail': detail})
    print(('ok:   ' if ok else 'FAIL: ') + check + (f'  {detail}' if detail else ''), flush=True)
    return ok


def call(url, method='GET', body=None, headers=None, raw=False):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={**({'content-type': 'application/json'} if data is not None and not isinstance(body, bytes) else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = response.read()
            return response.status, payload if raw else (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, payload if raw else json.loads(payload)
        except ValueError:
            return error.code, payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operator')
    parser.add_argument('--evidence', default='docs/evidence/import-checks.json')
    args = parser.parse_args()
    if os.stat(args.operator).st_mode & 0o077:
        raise SystemExit('the operator file must be private (mode 600)')
    operator = json.loads(Path(args.operator).read_text())
    base = json.loads((ROOT / '.lab' / 'upstream' / 'server.json').read_text())['url']
    try:
        status, login = call(f'{base}/management/auth/v1/token?grant_type=password', 'POST',
                             {'email': operator['email'], 'password': operator['password']}, {'apikey': MANAGEMENT_KEY})
        token = (login or {}).get('access_token')
        if not record('operator logs in', status == 200 and token, f'status {status}'):
            return finish(args.evidence)
        manage = lambda path, method='GET', body=None: call(f'{base}/management/v1{path}', method, body, {'authorization': f'Bearer {token}'})
        organization = manage('/organizations')[1]['data'][0]
        project = manage(f"/organizations/{organization['id']}/projects")[1]['data'][0]
        environments = manage(f"/projects/{project['id']}/environments")[1]['data']
        source_env = next(item for item in environments if item.get('state') == 'succeeded')
        source = manage(f"/environments/{source_env['id']}/connection")[1]['apiPath'].lstrip('/')
        record('a provisioned source environment exists', bool(source), source)

        # The source project, seeded as an application would.
        source_key = manage(f"/environments/{source_env['id']}/keys", 'POST')[1]['token']
        api = f'{base}/{source}'
        email, password = f'imported-{secrets.token_hex(4)}@example.com', secrets.token_urlsafe(18) + 'Aa1!'
        status, signed = call(f'{api}/auth/v1/signup', 'POST', {'email': email, 'password': password}, {'apikey': source_key})
        user_token, user_id = (signed or {}).get('access_token'), ((signed or {}).get('user') or {}).get('id')
        seed = lab_sql(source, f"""
create table if not exists public.notes (id bigint generated always as identity primary key,
  owner uuid not null default auth.uid() references auth.users(id), body text not null);
alter table public.notes enable row level security;
drop policy if exists "own notes" on public.notes;
create policy "own notes" on public.notes for all to authenticated using (owner = auth.uid()) with check (owner = auth.uid());
grant select, insert on public.notes to authenticated;
insert into storage.buckets (id, name, public) values ('private-files', 'private-files', false) on conflict do nothing;
drop policy if exists "own files" on storage.objects;
create policy "own files" on storage.objects for all to authenticated using (bucket_id = 'private-files' and owner_id = auth.uid()::text)
  with check (bucket_id = 'private-files' and owner_id = auth.uid()::text);
notify pgrst, 'reload schema';""")
        time.sleep(1)
        headers = {'apikey': source_key, 'authorization': f'Bearer {user_token}'}
        note = call(f'{api}/rest/v1/notes', 'POST', {'body': 'imported note'}, headers)[0]
        content = b'file from the source ' + secrets.token_bytes(64)
        upload = call(f'{api}/storage/v1/object/private-files/{user_id}/note.bin', 'POST', content,
                      {**headers, 'content-type': 'application/octet-stream'})[0]
        record('the source has a user with a protected row and a private file', status == 200 and seed and note == 201 and upload == 200,
               f'signup {status}, row {note}, file {upload}')

        # A new, empty environment to import into.
        status, created = manage(f"/projects/{project['id']}/environments", 'POST', {'name': f'imported-{secrets.token_hex(3)}'})
        target_env = (created or {}).get('id')
        state, until = 'queued', time.time() + 300
        while time.time() < until and state not in ('succeeded', 'failed'):
            time.sleep(3)
            state = (manage(f'/environments/{target_env}/provision')[1] or {}).get('state')
        if not record('a new environment is provisioned for the import', status == 202 and state == 'succeeded', f'state {state}'):
            return finish(args.evidence)

        # The source's database, as a Supabase project's `postgres` user: the developer login.
        status, opened = manage(f"/environments/{source_env['id']}/database", 'PUT', {'enabled': True})
        state, until = 'pending', time.time() + 180
        while time.time() < until and state == 'pending':
            time.sleep(2)
            state = manage(f"/environments/{source_env['id']}/database")[1]['data']['state']
        database_url = opened['data']['url'] + '?sslmode=disable'
        secret = json.loads((ROOT / '.secrets' / 'upstream' / 'runtime.json').read_text())['environments'][source]['jwt']
        settings = {'database_url': database_url, 'api_url': api, 'service_role_key': runtime.token(secret, 'service_role'), 'apikey': source_key}
        report_path = ROOT / '.lab' / 'import-report.json'
        imported = subprocess.run(['/usr/bin/python3', 'lab/import_project.py', target_env, '--report', str(report_path)], cwd=ROOT,
                                  input=json.dumps(settings), capture_output=True, text=True, timeout=1800)
        report = json.loads(report_path.read_text()) if report_path.exists() else {}
        record('the import runs to the end and verifies its counts', imported.returncode == 0 and report.get('verified'),
               (imported.stdout + imported.stderr).strip().replace('\n', '; ')[-400:])
        manage(f"/environments/{source_env['id']}/database", 'PUT', {'enabled': False})

        # The application, pointed at the new environment.
        target = manage(f'/environments/{target_env}/connection')[1]['apiPath'].lstrip('/')
        target_key = manage(f'/environments/{target_env}/keys', 'POST')[1]['token']
        new_api = f'{base}/{target}'
        status, session = call(f'{new_api}/auth/v1/token?grant_type=password', 'POST', {'email': email, 'password': password}, {'apikey': target_key})
        new_token = (session or {}).get('access_token')
        record('the imported user signs in with the old password', status == 200 and new_token, f'status {status}')
        headers = {'apikey': target_key, 'authorization': f'Bearer {new_token}'}
        status, rows = call(f'{new_api}/rest/v1/notes?select=body', headers=headers)
        record('row level security came along: the user reads their own row', status == 200 and rows == [{'body': 'imported note'}], f'{status} {rows}')
        status, anonymous = call(f'{new_api}/rest/v1/notes?select=body', headers={'apikey': target_key})
        # The source gave visitors no access to this table; neither does the import.
        record('and a visitor reads none', (status == 200 and anonymous == []) or status in (401, 403), f'{status} {anonymous}')
        status, downloaded = call(f'{new_api}/storage/v1/object/private-files/{user_id}/note.bin', headers=headers, raw=True)
        record('the user downloads their own file, byte for byte', status == 200 and downloaded == content, f'status {status}')
        status, refused = call(f'{new_api}/storage/v1/object/private-files/{user_id}/note.bin', headers={'apikey': target_key}, raw=True)
        record('a visitor cannot download it', status in (400, 403, 404), f'status {status}')
        before = count(target, 'public.profiles')
        status, _ = call(f'{new_api}/auth/v1/signup', 'POST', {'email': f'new-{secrets.token_hex(4)}@example.com',
                         'password': secrets.token_urlsafe(18) + 'Aa1!'}, {'apikey': target_key})
        record('a new sign-up runs the imported trigger on auth.users', status == 200 and before is not None and count(target, 'public.profiles') == before + 1,
               f'profiles {before} → {count(target, "public.profiles")}')
        again = subprocess.run(['/usr/bin/python3', 'lab/import_project.py', target_env], cwd=ROOT, input=json.dumps(settings),
                               capture_output=True, text=True, timeout=600)
        record('a second import into the now full environment is refused', again.returncode == 1 and 'not empty' in again.stderr, again.stderr.strip()[-200:])
    except Exception as error:
        record('import check ran to the end', False, f'{type(error).__name__}: {error}')
    return finish(args.evidence)


def lab_sql(e, sql):
    result = subprocess.run(['docker', 'exec', '-i', runtime.DB, 'psql', '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', e],
                            input=sql, capture_output=True, text=True)
    if result.returncode:
        print(result.stderr.strip()[-400:], file=sys.stderr)
    return result.returncode == 0


def count(e, relation):
    result = subprocess.run(['docker', 'exec', runtime.DB, 'psql', '-X', '-qAt', '-U', 'supabase_admin', '-d', e, '-c',
                             f"SELECT CASE WHEN to_regclass('{relation}') IS NULL THEN -1 ELSE (SELECT count(*) FROM {relation}) END"],
                            capture_output=True, text=True)
    value = result.stdout.strip()
    return int(value) if value.lstrip('-').isdigit() and int(value) >= 0 else None


def finish(evidence):
    passed = bool(checks) and all(row['ok'] for row in checks)
    Path(evidence).write_text(json.dumps({
        'check': 'import', 'recorded': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'), 'passed': passed,
        'count': len(checks), 'seconds': round(time.time() - started),
        'scope': 'Import of a Supabase-shaped project into a new environment on a running installation: the source is another '
                 'environment read through its developer login and gateway, as a Supabase project is through its postgres user '
                 'and API; the imported user signs in with the old password, row level security, a private file and a sign-up '
                 'trigger carry over, and a second import is refused.',
        'checks': checks}, indent=2) + '\n')
    print(f'evidence: {evidence}\nimport check: {"passed" if passed else "failed"}')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
