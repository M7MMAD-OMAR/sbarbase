"""Live check of Log In With Telegram (docs/engineering/plans/2026-11-21-telegram-login.md).

Usage: /usr/bin/python3 lab/telegram-oidc-check.py [--evidence PATH]   (from the repository root)

Starts on throwaway Docker networks one PostgreSQL and the pinned Auth (images.lock.json), named zz-tgprobe-*, and removes
them at the end. It touches no installation state and no retained volume. Auth is configured through auth_settings.configuration
and the Telegram row is written by durable_runtime.apply_custom_providers, the two functions the supervisor uses. Auth needs the
internet for this (it fetches https://oauth.telegram.org/.well-known/openid-configuration when the row is created), so its network
is not internal; this probe has no secret of value: the bot id and secret are made up. A real login is not tried (it needs a bot).
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import auth_settings  # noqa: E402
import durable_runtime  # noqa: E402

LOCK = json.load(open(os.path.join(HERE, 'images.lock.json')))
NET, DB, AUTH = 'zz-tgprobe-net', 'zz-tgprobe-db', 'zz-tgprobe-auth'
E = 'e_' + 'b' * 24
PW, JWT = secrets.token_hex(8), secrets.token_hex(24)
PUBLIC = 'https://api.example.test'
BOT, SECRET = '123456789', 'throwaway-' + secrets.token_hex(6)
CHECKS = []


def sh(*args, check=True):
    p = subprocess.run(args, capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f'{args[:3]} failed: {p.stderr.strip()[:300]}')
    return p


def record(name, ok, detail=''):
    detail = ' '.join(str(detail).split())[:240]
    CHECKS.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('ok:   ' if ok else 'FAIL: ') + name + (('  ' + detail) if detail else ''), flush=True)


def ip(name):
    return sh('docker', 'inspect', name, '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}').stdout.strip()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get(url):
    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=20) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def cleanup():
    for name in (AUTH, DB):
        sh('docker', 'rm', '-f', name, check=False)
    sh('docker', 'network', 'rm', NET, check=False)


def start(settings):
    sh('docker', 'rm', '-f', AUTH, check=False)
    base = {'GOTRUE_API_HOST': '0.0.0.0', 'GOTRUE_API_PORT': '9999', 'GOTRUE_SITE_URL': 'http://localhost', 'GOTRUE_DB_DRIVER': 'postgres',
            'GOTRUE_JWT_SECRET': JWT, 'GOTRUE_JWT_AUD': 'authenticated', 'GOTRUE_JWT_DEFAULT_GROUP_NAME': 'authenticated',
            'GOTRUE_JWT_ADMIN_ROLES': 'service_role', 'GOTRUE_EXTERNAL_EMAIL_ENABLED': 'true', 'GOTRUE_MAILER_AUTOCONFIRM': 'true',
            'GOTRUE_DB_NAMESPACE': 'auth', 'GOTRUE_DB_DATABASE_URL': f'postgres://{E}_auth:{PW}@{DB}:5432/{E}'}
    env = {**base, **auth_settings.configuration(E, settings)}
    args = ['docker', 'run', '-d', '--name', AUTH, '--network', NET, '--memory', '256m', '--cpus', '0.5']
    for key, value in env.items():
        args += ['-e', f'{key}={value}']
    sh(*args, LOCK['auth']['tag'])
    for _ in range(60):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f'http://{ip(AUTH)}:9999/health', timeout=2):
                return f'http://{ip(AUTH)}:9999'
        except OSError:
            pass
    return None


def settings(enabled=True):
    return auth_settings.validate({'revision': 1, 'site_url': 'http://localhost', 'redirect_urls': [],
                                   'providers': {'telegram': {'enabled': enabled, 'client_id': BOT, 'secret': SECRET}}})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence')
    args = parser.parse_args()
    os.environ['SBARBASE_PUBLIC_URL'] = PUBLIC
    cleanup()
    try:
        sh('docker', 'network', 'create', NET)
        sh('docker', 'run', '-d', '--name', DB, '--network', NET, '--memory', '384m', '-e', 'POSTGRES_PASSWORD=' + PW, LOCK['db']['tag'],
           '-c', 'max_connections=40', '-c', 'shared_buffers=32MB')
        for _ in range(40):
            time.sleep(1)
            if sh('docker', 'exec', DB, 'pg_isready', '-U', 'postgres', check=False).returncode == 0:
                time.sleep(2)
                break
        psql = lambda sql, db='postgres': sh('docker', 'exec', DB, 'psql', '-U', 'postgres', '-d', db, '-v', 'ON_ERROR_STOP=1', '-At', '-c', sql).stdout.strip()
        psql(f"CREATE ROLE {E}_auth LOGIN PASSWORD '{PW}'")
        psql(f'CREATE DATABASE {E}')
        psql(f'CREATE SCHEMA auth AUTHORIZATION {E}_auth; ALTER ROLE {E}_auth IN DATABASE {E} SET search_path TO auth;', E)
        on = settings()
        at = start(on)
        record('Auth v2.197.0 starts with a Telegram entry and no Telegram variable', at is not None and not [k for k in auth_settings.configuration(E, on) if 'TELEGRAM' in k])
        if not at:
            return finish(args.evidence)
        # Before the row exists, the provider is unknown.
        status, _, _ = get(at + '/authorize?provider=custom:telegram')
        record('before the apply, custom:telegram is refused', status in (400, 422), status)
        wanted = auth_settings.custom_providers(on)
        try:
            durable_runtime.apply_custom_providers(at, JWT, wanted)
            record('apply creates the row (Auth fetched Telegram discovery)', True)
        except RuntimeError as error:
            record('apply creates the row (Auth fetched Telegram discovery)', False, error)
            return finish(args.evidence)
        status, headers, _ = get(at + '/authorize?provider=custom:telegram')
        location = headers.get('Location', '')
        parts = urllib.parse.urlsplit(location)
        query = urllib.parse.parse_qs(parts.query)
        record('/authorize answers a redirect', status in (301, 302, 303, 307), status)
        record('the redirect targets https://oauth.telegram.org/auth', (parts.scheme, parts.netloc, parts.path) == ('https', 'oauth.telegram.org', '/auth'), location[:80])
        record('client_id is the bot id', query.get('client_id') == [BOT], query.get('client_id'))
        record('scope is openid profile', query.get('scope') == ['openid profile'], query.get('scope'))
        record('response_type is code', query.get('response_type') == ['code'])
        record('PKCE S256 challenge is present', query.get('code_challenge_method') == ['S256'] and len(query.get('code_challenge', [''])[0]) >= 43)
        record('redirect_uri is the environment callback', query.get('redirect_uri') == [f'{PUBLIC}/{E}/auth/v1/callback'], query.get('redirect_uri'))
        record('state is present', bool(query.get('state')))
        # The stored row: no secret in any answer, the user id claim is allowed, email optional.
        req = urllib.request.Request(at + '/admin/custom-providers/custom:telegram', headers={'authorization': 'Bearer ' + durable_runtime.token(JWT, 'service_role')})
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
        row = json.loads(body)
        record('the admin answer never carries the client secret', SECRET not in body and 'client_secret' not in row)
        record('the row has the allowlist, email_optional and PKCE', row.get('custom_claims_allowlist') == ['id'] and row.get('email_optional') is True and row.get('pkce_enabled') is True,
               {k: row.get(k) for k in ('custom_claims_allowlist', 'email_optional', 'pkce_enabled', 'issuer')})
        # A second apply updates in place (no issuer, no fetch); a changed bot id shows in the redirect.
        changed = auth_settings.validate({'revision': 2, 'site_url': 'http://localhost', 'redirect_urls': [],
                                          'providers': {'telegram': {'enabled': True, 'client_id': '987654321', 'secret': SECRET}}})
        durable_runtime.apply_custom_providers(at, JWT, auth_settings.custom_providers(changed))
        _, headers, _ = get(at + '/authorize?provider=custom:telegram')
        record('a second apply updates the same row', urllib.parse.parse_qs(urllib.parse.urlsplit(headers.get('Location', '')).query).get('client_id') == ['987654321'])
        # Disabled deletes it.
        durable_runtime.apply_custom_providers(at, JWT, auth_settings.custom_providers(settings(False)))
        status, _, _ = get(at + '/authorize?provider=custom:telegram')
        record('a disabled entry removes the row: custom:telegram is refused again', status in (400, 422), status)
        finish(args.evidence)
    finally:
        cleanup()


def finish(path):
    failed = [c for c in CHECKS if not c['ok']]
    print(f'{len(CHECKS) - len(failed)} of {len(CHECKS)} checks passed')
    if path:
        with open(path, 'w') as handle:
            json.dump({'script': 'lab/telegram-oidc-check.py', 'auth': LOCK['auth']['tag'], 'checks': CHECKS}, handle, indent=1)
    sys.exit(1 if failed or not CHECKS else 0)


if __name__ == '__main__':
    main()
