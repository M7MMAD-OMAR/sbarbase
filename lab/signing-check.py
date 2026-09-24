"""Signing key check: an environment's JWT signing secret is rotated and every service follows.

Usage: /usr/bin/python3 lab/signing-check.py <operator.json> [--evidence PATH]

Needs a running installation with one provisioned environment. The check turns Realtime on, signs
a person in, and keeps their session and a service-role token signed with the current secret.
It rotates the key through the management API and waits for the supervisor, then acts as the
application: the old session and the old service-role token are refused by Auth, REST and
Storage, the person signs in again and everything answers, Realtime's broadcast API still
answers through the gateway, and the publishable key never changed. The secret is read from the
private runtime file to mint the old and new service-role tokens, and never printed.
"""
import argparse
import datetime
import json
import os
import secrets
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


def call(url, method='GET', body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={**({'content-type': 'application/json'} if data is not None else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            return error.code, json.loads(payload)
        except ValueError:
            return error.code, None


def secret(e):
    return json.loads((ROOT / '.secrets' / 'upstream' / 'runtime.json').read_text())['environments'][e]['jwt']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operator')
    parser.add_argument('--evidence', default='docs/evidence/signing-checks.json')
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
        environment = next(item for item in manage(f"/projects/{project['id']}/environments")[1]['data'] if item.get('state') == 'succeeded')
        env_id = environment['id']
        e = manage(f'/environments/{env_id}/connection')[1]['apiPath'].lstrip('/')
        record('a provisioned environment exists', bool(e))
        api, key = f'{base}/{e}', manage(f'/environments/{env_id}/keys', 'POST')[1]['token']

        status, _ = manage(f'/environments/{env_id}/realtime', 'PUT', {'enabled': True})
        state = wait(lambda: manage(f'/environments/{env_id}/realtime')[1]['data']['state'], 'pending', 240)
        record('Realtime is on before the rotation', state == 'on', f'state {state}')
        broadcast = lambda: call(f'{api}/realtime/v1/api/broadcast', 'POST',
                                 {'messages': [{'topic': 'signing', 'event': 'probe', 'payload': {}}]}, {'apikey': key})[0]
        record('the broadcast API answers before the rotation', broadcast() in (200, 202))

        email, password = f'signing-{secrets.token_hex(4)}@example.com', secrets.token_urlsafe(18) + 'Aa1!'
        status, session = call(f'{api}/auth/v1/signup', 'POST', {'email': email, 'password': password}, {'apikey': key})
        old_access, old_refresh = (session or {}).get('access_token'), (session or {}).get('refresh_token')
        old_secret = secret(e)
        old_service = runtime.token(old_secret, 'service_role')
        as_user = lambda access: {'apikey': key, 'authorization': f'Bearer {access}'}
        record('a person is signed in, and Auth, REST and Storage accept their session',
               status == 200 and accepted(answers(api, as_user(old_access))), f'{answers(api, as_user(old_access))}')
        record('Storage accepts a service-role token signed with the current key',
               call(f'{api}/storage/v1/bucket', headers=as_user(old_service))[0] == 200)

        status, rotation = manage(f'/environments/{env_id}/signing-key/rotate', 'POST')
        again = manage(f'/environments/{env_id}/signing-key/rotate', 'POST')[0]
        record('an owner asks for a new signing key, and a second request waits for the first', status == 202 and again == 409,
               f'status {status}, then {again}')
        state = wait(lambda: manage(f'/environments/{env_id}/signing-key')[1]['data']['state'], 'pending', 300)
        record('the supervisor rotates it', state == 'done', f'state {state}')
        view = manage(f'/environments/{env_id}/signing-key')[1]['data']
        record('the console shows when, never the key', bool(view.get('rotatedAt')) and old_secret not in json.dumps(view)
               and secret(e) not in json.dumps(view))
        record('the environment has a new secret', secret(e) != old_secret and len(secret(e)) == 64)

        record('the old session is refused by Auth, REST and Storage', refused(answers(api, as_user(old_access))),
               f'{answers(api, as_user(old_access))}')
        record('a service-role token signed with the old key is refused', call(f'{api}/storage/v1/bucket', headers=as_user(old_service))[0] in (400, 401, 403))
        status, refreshed = call(f'{api}/auth/v1/token?grant_type=refresh_token', 'POST', {'refresh_token': old_refresh}, {'apikey': key})
        record('the old refresh token no longer gives a session', status in (400, 401, 403) or not (refreshed or {}).get('access_token'),
               f'status {status}')
        status, session = call(f'{api}/auth/v1/token?grant_type=password', 'POST', {'email': email, 'password': password}, {'apikey': key})
        new_access = (session or {}).get('access_token')
        record('the person signs in again with the same publishable key and password', status == 200 and bool(new_access), f'status {status}')
        record('Auth, REST and Storage accept the new session', accepted(answers(api, as_user(new_access))),
               f'{answers(api, as_user(new_access))}')
        record('Storage accepts a service-role token signed with the new key',
               call(f'{api}/storage/v1/bucket', headers=as_user(runtime.token(secret(e), 'service_role')))[0] == 200)
        record('the broadcast API answers after the rotation', broadcast() in (200, 202))
        manage(f'/environments/{env_id}/realtime', 'PUT', {'enabled': False})
        state = wait(lambda: manage(f'/environments/{env_id}/realtime')[1]['data']['state'], 'pending', 120)
        record('Realtime is turned off again', state == 'off', f'state {state}')
    except Exception as error:
        record('signing check ran to the end', False, f'{type(error).__name__}: {error}')
    return finish(args.evidence)


def wait(read, busy, seconds):
    state, until = busy, time.time() + seconds
    while time.time() < until and state == busy:
        time.sleep(2)
        state = read()
    return state


def answers(api, headers):
    """What Auth, REST and Storage answer to one session: its user, a table that does not exist
    (404 for a valid token, refused before that for an invalid one) and the bucket list."""
    return (call(f'{api}/auth/v1/user', headers=headers)[0], call(f'{api}/rest/v1/signing_probe_missing', headers=headers)[0],
            call(f'{api}/storage/v1/bucket', headers=headers)[0])


# Storage answers a bad token with 400; Auth and REST with 401 or 403.
refused = lambda codes: all(code in (400, 401, 403) for code in codes)
accepted = lambda codes: all(code < 500 and code not in (400, 401, 403) for code in codes)


def finish(evidence):
    passed = bool(checks) and all(row['ok'] for row in checks)
    Path(evidence).write_text(json.dumps({
        'check': 'signing', 'recorded': datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds'), 'passed': passed,
        'count': len(checks), 'seconds': round(time.time() - started),
        'scope': "Rotation of one environment's JWT signing secret on a running installation, through the management API and the "
                 'supervisor: the old session, refresh token and service-role token refused by Auth, REST and Storage, a new sign-in '
                 'with the same publishable key accepted everywhere, and Realtime still answering through the gateway.',
        'checks': checks}, indent=2) + '\n')
    print(f'evidence: {evidence}\nsigning check: {"passed" if passed else "failed"}')
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
