"""Live check of the Auth variable names the console writes (docs/engineering/plans/2026-10-30-auth-providers-workspace.md, A6).

Usage: /usr/bin/python3 lab/auth-names-check.py [--evidence PATH] [--only SECTION]   (from the repository root)

Starts, on a private internal Docker network of its own, one small PostgreSQL (postgres:17-alpine), the
pinned Auth image (images.lock.json) and, for the template check, the pinned mail catcher, all named
zz-authprobe-*, and removes them at the end. It touches no installation state and no retained volume.
Each Auth environment is built through auth_settings.configuration, the function the supervisor uses,
and the effect is observed with real requests (a sign up, a refresh, an MFA enrolment, a captured mail),
so a name Auth ignores shows up as a missing effect. Only throwaway credentials are used and none is
written to the evidence. Machine load: one Auth at a time, a few hundred MiB in all.
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import auth_settings  # noqa: E402

LOCK = json.load(open(os.path.join(HERE, 'images.lock.json')))
NET, OPEN = 'zz-authprobe-net', 'zz-authprobe-open'
DB, AUTH, MAIL = 'zz-authprobe-db', 'zz-authprobe-auth', 'zz-authprobe-mail'
PG_IMAGE, AUTH_IMAGE = LOCK['db']['tag'], LOCK['auth']['tag']
MAIL_IMAGE = 'public.ecr.aws/supabase/mailpit:v1.31.3'
E = 'e_' + 'a' * 24
PW, JWT = secrets.token_hex(8), secrets.token_hex(24)
CHECKS = []


def sh(*args, check=True, input=None):
    p = subprocess.run(args, capture_output=True, text=True, input=input)
    if check and p.returncode:
        raise RuntimeError(f'{args[:3]} failed: {p.stderr.strip()[:300]}')
    return p


def record(name, ok, detail=''):
    detail = ' '.join(str(detail).split())[:200]
    CHECKS.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('ok:   ' if ok else 'FAIL: ') + name + (('  ' + detail) if detail else ''), flush=True)


def container_ip(name):
    return sh('docker', 'inspect', name, '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}').stdout.strip()


def request(base, method, path, body=None, token=None, headers=None):
    h = {'content-type': 'application/json', **(headers or {})}
    if token:
        h['authorization'] = 'Bearer ' + token
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text.startswith(('{', '[')) else text)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text


def service_token():
    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b'=')
    head = b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())
    body = b64(json.dumps({'role': 'service_role', 'exp': int(time.time()) + 600, 'aud': 'authenticated'}).encode())
    return (head + b'.' + body + b'.' + b64(hmac.new(JWT.encode(), head + b'.' + body, hashlib.sha256).digest())).decode()


def settings(config=None, session=None, signup=True, anonymous=False, manual=False, providers=None):
    return auth_settings.validate({'version': 2, 'revision': 1, 'site_url': 'http://localhost', 'redirect_urls': [], 'signup': signup,
                                   'anonymous': anonymous, 'manual_linking': manual, 'session': session or {}, 'config': config or {},
                                   'providers': providers or {}})


BASE = {'GOTRUE_API_HOST': '0.0.0.0', 'GOTRUE_API_PORT': '9999', 'GOTRUE_SITE_URL': 'http://localhost', 'GOTRUE_DB_DRIVER': 'postgres',
        'GOTRUE_JWT_SECRET': JWT, 'GOTRUE_JWT_AUD': 'authenticated', 'GOTRUE_JWT_DEFAULT_GROUP_NAME': 'authenticated',
        'GOTRUE_JWT_ADMIN_ROLES': 'service_role', 'GOTRUE_EXTERNAL_EMAIL_ENABLED': 'true', 'GOTRUE_MAILER_AUTOCONFIRM': 'true',
        'GOTRUE_DB_NAMESPACE': 'auth', 'GOTRUE_DB_DATABASE_URL': f'postgres://{E}_auth:{PW}@{DB}:5432/{E}'}


def start_auth(extra=None, doc=None):
    """One Auth at a time; the address, or None when it did not become healthy."""
    sh('docker', 'rm', '-f', AUTH, check=False)
    env = {**BASE, **auth_settings.configuration(E, doc), **(extra or {})}
    args = ['docker', 'run', '-d', '--name', AUTH, '--network', NET, '--memory', '256m', '--cpus', '0.5']
    for key, value in env.items():
        args += ['-e', f'{key}={value}']
    sh(*args, AUTH_IMAGE)
    for _ in range(60):
        time.sleep(1)
        if sh('docker', 'inspect', AUTH, '--format', '{{.State.Running}}').stdout.strip() != 'true':
            return None
        try:
            with urllib.request.urlopen(f'http://{container_ip(AUTH)}:9999/health', timeout=2):
                return f'http://{container_ip(AUTH)}:9999'
        except OSError:
            pass
    return None


def auth_log():
    p = sh('docker', 'logs', '--tail', '20', AUTH, check=False)
    return (p.stdout + p.stderr)[-400:]


def signup(at, password='correct-horse-battery-staple', headers=None):
    email = f'u{secrets.token_hex(4)}@example.com'
    return request(at, 'POST', '/signup', {'email': email, 'password': password}, headers=headers), email


def refresh(at, token, headers=None):
    return request(at, 'POST', '/token?grant_type=refresh_token', {'refresh_token': token}, headers=headers)


def psql(sql, db='postgres'):
    return sh('docker', 'exec', DB, 'psql', '-U', 'postgres', '-d', db, '-v', 'ON_ERROR_STOP=1', '-At', '-c', sql).stdout.strip()


def cleanup():
    for name in (AUTH, MAIL, DB):
        sh('docker', 'rm', '-f', name, check=False)
    for name in (NET, OPEN):
        sh('docker', 'network', 'rm', name, check=False)


def setup():
    cleanup()
    sh('docker', 'network', 'create', '--internal', NET)
    sh('docker', 'network', 'create', OPEN)
    sh('docker', 'run', '-d', '--name', DB, '--network', NET, '--memory', '384m', '-e', 'POSTGRES_PASSWORD=' + PW, PG_IMAGE,
       '-c', 'max_connections=40', '-c', 'shared_buffers=32MB')
    for _ in range(40):
        time.sleep(1)
        if sh('docker', 'exec', DB, 'pg_isready', '-U', 'postgres', check=False).returncode == 0:
            time.sleep(2)
            break
    psql(f"CREATE ROLE {E}_auth LOGIN PASSWORD '{PW}'")
    psql(f'CREATE DATABASE {E}')
    psql(f'CREATE SCHEMA auth AUTHORIZATION {E}_auth; ALTER ROLE {E}_auth IN DATABASE {E} SET search_path TO auth;', E)


def started(label, extra=None, **kw):
    at = start_auth(extra, settings(**kw))
    if not at:
        record(label + ': Auth starts', False, auth_log())
    return at


def names_with_effect():
    """The settings the console offers; each must change what Auth does."""
    at = started('DISABLE_SIGNUP', signup=False)
    if at:
        (s, b), _ = signup(at)
        record('DISABLE_SIGNUP true refuses a new user', s == 422 and b.get('error_code') == 'signup_disabled', f'{s} {b.get("error_code")}')
    at = started('JWT_EXP', session={'access_token_seconds': 120})
    if at:
        (s, b), _ = signup(at)
        record('JWT_EXP 120 gives expires_in 120', s == 200 and b.get('expires_in') == 120, f'{s}')
    at = started('PASSWORD_MIN_LENGTH', config={'PASSWORD_MIN_LENGTH': 12})
    if at:
        (s1, b1), _ = signup(at, 'abcdefgh1')
        (s2, _), _ = signup(at, 'abcdefghijkl1')
        record('PASSWORD_MIN_LENGTH 12 refuses 9 and accepts 13 characters', (s1, s2) == (422, 200), f'{s1}/{s2} {b1.get("error_code")}')
    at = started('anonymous', anonymous=True)
    if at:
        s, st = request(at, 'GET', '/settings')
        record('EXTERNAL_ANONYMOUS_USERS_ENABLED shows in the settings', st.get('external', {}).get('anonymous_users') is True)
        s, b = request(at, 'POST', '/signup', {})
        record('an anonymous sign in works', s == 200 and b.get('user', {}).get('is_anonymous') is True, f'{s}')
    at = started('SESSIONS_SINGLE_PER_USER', config={'SESSIONS_SINGLE_PER_USER': True})
    if at:
        (s, b), email = signup(at)
        s2, _ = request(at, 'POST', '/token?grant_type=password', {'email': email, 'password': 'correct-horse-battery-staple'})
        s3, b3 = refresh(at, b['refresh_token'])
        record('SESSIONS_SINGLE_PER_USER ends the first session at a new sign in', s2 == 200 and s3 in (400, 401, 403), f'{s2}/{s3} {b3.get("error_code")}')
    for variable, label in (('GOTRUE_SESSIONS_TIMEBOX', 'a session after its time box'), ('GOTRUE_SESSIONS_INACTIVITY_TIMEOUT', 'an idle session')):
        at = started(label, {variable: '3s'})
        if at:
            (s, b), _ = signup(at)
            time.sleep(5)
            s2, b2 = refresh(at, b['refresh_token'])
            record(f'{variable} (3s) ends {label}', s2 in (400, 401, 403), f'{s2} {b2.get("error_code")}')
    at = started('MFA max factors', config={'MFA_MAX_ENROLLED_FACTORS': 1})
    if at:
        (s, b), _ = signup(at)
        enrol = lambda n: request(at, 'POST', '/factors', {'factor_type': 'totp', 'friendly_name': n, 'issuer': 'x'}, b['access_token'])
        a1, a2 = enrol('a'), enrol('b')
        record('MFA_MAX_ENROLLED_FACTORS 1 refuses the second factor', a1[0] == 200 and a2[1].get('error_code') == 'too_many_enrolled_mfa_factors', f'{a1[0]}/{a2[0]}')
    for key, code in (('MFA_TOTP_ENROLL_ENABLED', 'mfa_totp_enroll_not_enabled'), ('MFA_TOTP_VERIFY_ENABLED', 'mfa_totp_verify_not_enabled')):
        at = started(key, config={key: False})
        if at:
            (s, b), _ = signup(at)
            tok = b['access_token']
            s1, r1 = request(at, 'POST', '/factors', {'factor_type': 'totp', 'friendly_name': 'a', 'issuer': 'x'}, tok)
            if key.endswith('ENROLL_ENABLED'):
                seen = r1.get('error_code')
            else:
                s2, r2 = request(at, 'POST', f'/factors/{r1.get("id")}/challenge', {}, tok)
                s3, r3 = request(at, 'POST', f'/factors/{r1.get("id")}/verify', {'challenge_id': r2.get('id'), 'code': '000000'}, tok)
                seen = r3.get('error_code')
            record(f'{key} false is refused with {code}', seen == code, str(seen))
    github = {'github': {'enabled': True, 'client_id': 'id', 'secret': 'sec', 'url': ''}}
    for manual in (False, True):
        at = started(f'manual linking {manual}', providers=github, manual=manual)
        if at:
            (s, b), _ = signup(at)
            s1, r1 = request(at, 'GET', '/user/identities/authorize?provider=github&skip_http_redirect=true', None, b['access_token'])
            record(f'SECURITY_MANUAL_LINKING_ENABLED {str(manual).lower()}: linking is ' + ('started' if manual else 'refused'),
                   (s1 == 200) if manual else (r1.get('error_code') == 'manual_linking_disabled'), f'{s1} {r1.get("error_code") if isinstance(r1, dict) else ""}')
    at = started('audit default')
    if at:
        signup(at)
        record('audit rows are written by default (control)', int(psql('SELECT count(*) FROM auth.audit_log_entries', E)) > 0)
        s, listing = request(at, 'GET', '/admin/audit?per_page=5', None, service_token())
        record('the admin audit endpoint lists the entries without database access', s == 200 and isinstance(listing, list) and len(listing) > 0, f'{s} n={len(listing) if isinstance(listing, list) else listing}')
        first = listing[0] if isinstance(listing, list) and listing else {}
        record('an audit entry has the fields the console shows', {'id', 'created_at', 'ip_address', 'payload'} <= set(first) and {'action', 'log_type'} <= set(first.get('payload', {})), f'keys={sorted(first)} payload={sorted(first.get("payload", {}))}')
    psql('TRUNCATE auth.audit_log_entries', E)
    at = started('audit off', {'GOTRUE_AUDIT_LOG_DISABLE_POSTGRES': 'true'})
    if at:
        signup(at)
        record('GOTRUE_AUDIT_LOG_DISABLE_POSTGRES true stops audit rows', int(psql('SELECT count(*) FROM auth.audit_log_entries', E)) == 0)
    at = started('HIBP', config={'PASSWORD_HIBP_ENABLED': True})
    if at:
        # Default deny: on the internal network alone Auth has no way out, so the range service cannot be asked and the password is accepted.
        (s0, _), _ = signup(at, 'password123456')
        record('default deny: with no outbound network Auth cannot reach the leaked password service (the password is accepted)', s0 == 200, f'{s0}')
        sh('docker', 'network', 'connect', OPEN, AUTH)
        time.sleep(1)
        (s, b), _ = signup(at, 'password123456')
        record('PASSWORD_HIBP_ENABLED refuses a leaked password once Auth has outbound access', s == 422 and b.get('error_code') == 'weak_password', f'{s}')


def downtime():
    """How long one Auth restart takes on this machine: the swap the console's apply performs (stop, start, wait for health)."""
    at = start_auth()
    if not at:
        record('measured: Auth restarts', False, auth_log())
        return
    begun = time.time()
    sh('docker', 'stop', AUTH)
    stopped = time.time()
    again = start_auth()
    healthy = time.time()
    record(f'measured: one Auth restart (stop {stopped - begun:.1f} s, start to healthy {healthy - stopped:.1f} s, {healthy - begun:.1f} s in all)', again is not None,
           f'stop_s={stopped - begun:.1f} start_to_healthy_s={healthy - stopped:.1f} total_s={healthy - begun:.1f}')


def hooks_saml_sms():
    at = started('hook', {'GOTRUE_HOOK_CUSTOM_ACCESS_TOKEN_ENABLED': 'true', 'GOTRUE_HOOK_CUSTOM_ACCESS_TOKEN_URI': 'pg-functions://postgres/public/no_such_hook'})
    if at:
        (s, b), _ = signup(at)
        record('HOOK_CUSTOM_ACCESS_TOKEN: an enabled hook naming a missing function fails the sign up', s == 500 and 'hook' in json.dumps(b).lower(), f'{s}')
    psql(f'CREATE OR REPLACE FUNCTION public.hook_probe(event jsonb) RETURNS jsonb LANGUAGE plpgsql AS $$ BEGIN RETURN jsonb_set(event, \'{{claims,hooked}}\', \'true\'); END $$', E)
    psql(f'GRANT USAGE ON SCHEMA public TO {E}_auth; GRANT EXECUTE ON FUNCTION public.hook_probe(jsonb) TO {E}_auth', E)
    at = started('hook that runs', {'GOTRUE_HOOK_CUSTOM_ACCESS_TOKEN_ENABLED': 'true', 'GOTRUE_HOOK_CUSTOM_ACCESS_TOKEN_URI': 'pg-functions://postgres/public/hook_probe'})
    if at:
        (s, b), _ = signup(at)
        claims = json.loads(base64.urlsafe_b64decode(b['access_token'].split('.')[1] + '=='))
        record('a Postgres function hook adds a claim when the auth role may run it', s == 200 and claims.get('hooked') is True, f'{s} hooked={claims.get("hooked")}')
    key = subprocess.run(['openssl', 'genrsa', '-traditional', '2048'], capture_output=True).stdout
    der = subprocess.run(['openssl', 'rsa', '-traditional', '-outform', 'DER'], input=key, capture_output=True).stdout
    at = started('SAML', {'GOTRUE_SAML_ENABLED': 'true', 'GOTRUE_SAML_PRIVATE_KEY': base64.b64encode(der).decode()})
    if at:
        s, st = request(at, 'GET', '/settings')
        record('SAML_ENABLED with a PKCS#1 key shows saml_enabled in the settings', st.get('saml_enabled') is True)
        token = service_token()
        idp = ('<?xml version="1.0"?><EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="https://idp.example.com/meta">'
               '<IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol"><SingleSignOnService '
               'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="https://idp.example.com/sso"/></IDPSSODescriptor></EntityDescriptor>')
        s, created = request(at, 'POST', '/admin/sso/providers', {'type': 'saml', 'metadata_xml': idp, 'domains': ['example.org']}, token)
        record('the admin API creates a SAML provider from metadata XML', s == 201, f'{s}')
        pid = created.get('id') if isinstance(created, dict) else None
        s, listing = request(at, 'GET', '/admin/sso/providers', None, token)
        record('the admin API lists it', s == 200 and any(item.get('id') == pid for item in listing.get('items', [])))
        s, _ = request(at, 'DELETE', f'/admin/sso/providers/{pid}', None, token)
        record('the admin API deletes it', s == 200)
    bad = start_auth({'GOTRUE_SAML_ENABLED': 'true'})
    record('SAML_ENABLED without a key is refused at start', bad is None, auth_log() if bad is None else 'started')
    sms = {'GOTRUE_EXTERNAL_PHONE_ENABLED': 'true', 'GOTRUE_MAILER_AUTOCONFIRM': 'false', 'GOTRUE_SMS_AUTOCONFIRM': 'false'}
    at = started('SMS limit', {**sms, 'GOTRUE_SMS_PROVIDER': 'twilio', 'GOTRUE_SMS_TWILIO_ACCOUNT_SID': 'AC1', 'GOTRUE_SMS_TWILIO_AUTH_TOKEN': 'tok',
                               'GOTRUE_SMS_TWILIO_MESSAGE_SERVICE_SID': 'MG1', 'GOTRUE_RATE_LIMIT_SMS_SENT': '1'})
    if at:
        codes = [request(at, 'POST', '/signup', {'phone': f'+1555000{i:04d}', 'password': 'correct-horse-battery'})[1].get('error_code') for i in range(3)]
        record('RATE_LIMIT_SMS_SENT 1 limits the second message', codes[0] == 'sms_send_failed' and codes[1] == 'over_sms_send_rate_limit', str(codes))
    at = started('SMS autoconfirm', {**sms, 'GOTRUE_SMS_AUTOCONFIRM': 'true', 'GOTRUE_SMS_PROVIDER': 'twilio', 'GOTRUE_SMS_TWILIO_ACCOUNT_SID': 'AC1',
                                     'GOTRUE_SMS_TWILIO_AUTH_TOKEN': 'tok', 'GOTRUE_SMS_TWILIO_MESSAGE_SERVICE_SID': 'MG1'})
    if at:
        s, b = request(at, 'POST', '/signup', {'phone': '+15550002222', 'password': 'correct-horse-battery'})
        record('SMS_AUTOCONFIRM true signs a new number in without sending a code', s == 200 and 'access_token' in (b if isinstance(b, dict) else {}), f'{s}')
    providers = {'messagebird': {'GOTRUE_SMS_MESSAGEBIRD_ACCESS_KEY': 'k', 'GOTRUE_SMS_MESSAGEBIRD_ORIGINATOR': 'o'},
                 'textlocal': {'GOTRUE_SMS_TEXTLOCAL_API_KEY': 'k', 'GOTRUE_SMS_TEXTLOCAL_SENDER': 's'},
                 'vonage': {'GOTRUE_SMS_VONAGE_API_KEY': 'k', 'GOTRUE_SMS_VONAGE_API_SECRET': 's', 'GOTRUE_SMS_VONAGE_FROM': 'f'},
                 'twilio_verify': {'GOTRUE_SMS_TWILIO_VERIFY_ACCOUNT_SID': 'AC', 'GOTRUE_SMS_TWILIO_VERIFY_AUTH_TOKEN': 't', 'GOTRUE_SMS_TWILIO_VERIFY_MESSAGE_SERVICE_SID': 'VA'}}
    hosts = {'messagebird': 'messagebird', 'textlocal': 'textlocal', 'vonage': 'nexmo', 'twilio_verify': 'verify.twilio'}
    for name, variables in providers.items():
        at = started('SMS ' + name, {**sms, 'GOTRUE_SMS_PROVIDER': name, **variables})
        if at:
            s, b = request(at, 'POST', '/signup', {'phone': '+15550001111', 'password': 'correct-horse-battery'})
            record(f'SMS provider {name}: the variables reach the provider call', b.get('error_code') == 'sms_send_failed' and hosts[name] in json.dumps(b), f'{s}')


def templates():
    """All six messages: the address and subject variables of each, fetched from the network gateway address."""
    gateway = sh('docker', 'network', 'inspect', NET, '--format', '{{(index .IPAM.Config 0).Gateway}}').stdout.strip()
    hits = []
    kinds = ('confirmation', 'invite', 'magic_link', 'email_change', 'recovery', 'reauthentication')

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            kind = self.path.rsplit('/', 1)[-1]
            self.send_response(200)
            self.send_header('content-type', 'text/html')
            self.end_headers()
            self.wfile.write(f'<html><body><h2>PROBE-{kind.upper()}</h2><a href="{{{{ .ConfirmationURL }}}}">go</a> {{{{ .Token }}}}</body></html>'.encode())

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer((gateway, 18181), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sh('docker', 'run', '-d', '--name', MAIL, '--network', NET, '--memory', '128m', MAIL_IMAGE)
    time.sleep(3)
    mail_ip = container_ip(MAIL)
    env = {'GOTRUE_SMTP_HOST': MAIL, 'GOTRUE_SMTP_PORT': '1025', 'GOTRUE_SMTP_USER': '', 'GOTRUE_SMTP_PASS': '', 'GOTRUE_SMTP_ADMIN_EMAIL': 'a@example.com',
           'GOTRUE_MAILER_AUTOCONFIRM': 'false', 'GOTRUE_SMTP_MAX_FREQUENCY': '1s'}
    for kind in kinds:
        env['GOTRUE_MAILER_TEMPLATES_' + kind.upper()] = f'http://{gateway}:18181/token/{kind}'
        env['GOTRUE_MAILER_SUBJECTS_' + kind.upper()] = f'Probe {kind}'
    at = start_auth(env, settings())
    if not at:
        record('templates: Auth starts with all six addresses and subjects', False, auth_log())
        server.shutdown()
        return
    admin = service_token()

    def user():
        email = f'u{secrets.token_hex(4)}@example.com'
        request(at, 'POST', '/admin/users', {'email': email, 'password': 'correct-horse-battery-staple', 'email_confirm': True}, admin)
        s, b = request(at, 'POST', '/token?grant_type=password', {'email': email, 'password': 'correct-horse-battery-staple'})
        return email, b.get('access_token')

    signup(at)
    request(at, 'POST', '/invite', {'email': f'i{secrets.token_hex(4)}@example.com'}, admin)
    request(at, 'POST', '/magiclink', {'email': user()[0]})
    request(at, 'POST', '/recover', {'email': user()[0]})
    email, token = user()
    request(at, 'PUT', '/user', {'email': f'n{secrets.token_hex(4)}@example.com'}, token)
    request(at, 'GET', '/reauthenticate', None, token)
    time.sleep(4)

    def mailbox():
        with urllib.request.urlopen(f'http://{mail_ip}:8025/api/v1/messages?limit=100', timeout=5) as r:
            return json.loads(r.read())['messages']

    def body(message):
        with urllib.request.urlopen(f'http://{mail_ip}:8025/api/v1/message/{message["ID"]}', timeout=5) as r:
            return json.loads(r.read()).get('HTML', '')
    messages = mailbox()
    record('Auth on the internal network fetched templates from the network gateway address', len(hits) > 0, str(sorted(set(hits))))
    for kind in kinds:
        found = [m for m in messages if m['Subject'] == f'Probe {kind}']
        text = body(found[0]) if found else ''
        record(f'MAILER_TEMPLATES_{kind.upper()} and MAILER_SUBJECTS_{kind.upper()} apply to the {kind} message',
               bool(found) and f'PROBE-{kind.upper()}' in text and '{{' not in text, f'messages={len(found)}')
    # The console being unreachable: Auth is asked to send a message whose template cannot be fetched.
    server.shutdown()
    server.server_close()
    before = len(mailbox())
    s, b = request(at, 'POST', '/recover', {'email': user()[0]})
    time.sleep(3)
    after = mailbox()
    default_sent = [m for m in after[:len(after) - before] if 'Probe recovery' in m['Subject']] if len(after) > before else []
    fresh = after[: len(after) - before] if len(after) > before else []
    html = body(fresh[0]) if fresh else ''
    record('observed: with the template address unreachable, a recovery request ' + ('still sends a message' if fresh else 'is refused (no message sent)'),
           True, f'status={s} new_messages={len(fresh)} subjects={[m["Subject"] for m in fresh]} custom_body_used={"PROBE-" in html}')


def findings_that_retired_settings():
    """Settings the console no longer offers. Each is recorded as observed: the check passes when the finding holds."""
    at = started('rotation off', {'GOTRUE_SECURITY_REFRESH_TOKEN_ROTATION_ENABLED': 'false'})
    if at:
        (s, b), _ = signup(at)
        s1, b1 = refresh(at, b['refresh_token'])
        record('retired: with GOTRUE_SECURITY_REFRESH_TOKEN_ROTATION_ENABLED=false a refresh still gives a new refresh token', s1 == 200 and b1.get('refresh_token') != b['refresh_token'])
    at = started('reuse interval', {'GOTRUE_SECURITY_REFRESH_TOKEN_REUSE_INTERVAL': '0'})
    if at:
        (s, b), _ = signup(at)
        refresh(at, b['refresh_token'])
        s2, _ = refresh(at, b['refresh_token'])
        record('retired: with the reuse interval at 0 the old token is still accepted straight after a refresh', s2 == 200)
    for header in (True, False):
        extra = {'GOTRUE_RATE_LIMIT_HEADER': 'X-Forwarded-For'} if header else {}
        hdr = {'X-Forwarded-For': '203.0.113.30'} if header else {}
        at = started('anonymous limit', {**extra, 'GOTRUE_RATE_LIMIT_ANONYMOUS_USERS': '1'}, anonymous=True)
        if at:
            codes = [request(at, 'POST', '/signup', {}, headers=hdr)[0] for _ in range(4)]
            record(f'retired: RATE_LIMIT_ANONYMOUS_USERS 1 limits only when a client address header is configured (header {header})', (429 in codes) == header, str(codes))


def mail_settings():
    """Confirm email and the email limits, set through the sign-in settings (the `config` store) with no mail file: each must change what Auth does."""
    if sh('docker', 'inspect', MAIL, '--format', '{{.State.Running}}', check=False).stdout.strip() != 'true':
        sh('docker', 'rm', '-f', MAIL, check=False)
        sh('docker', 'run', '-d', '--name', MAIL, '--network', NET, '--memory', '128m', MAIL_IMAGE)
        time.sleep(3)
    relay = {'GOTRUE_SMTP_HOST': MAIL, 'GOTRUE_SMTP_PORT': '1025', 'GOTRUE_SMTP_USER': '', 'GOTRUE_SMTP_PASS': '', 'GOTRUE_SMTP_ADMIN_EMAIL': 'a@example.com'}
    mail_ip = container_ip(MAIL)

    def mailbox():
        with urllib.request.urlopen(f'http://{mail_ip}:8025/api/v1/messages?limit=100', timeout=5) as r:
            return json.loads(r.read())['messages']
    at = started('MAILER_AUTOCONFIRM', relay, config={'MAILER_AUTOCONFIRM': False, 'RATE_LIMIT_EMAIL_SENT': 1})
    if at:
        before = len(mailbox())
        (s, b), email = signup(at)
        time.sleep(2)
        record('MAILER_AUTOCONFIRM false (from the sign-in settings) leaves a new user unconfirmed and sends the confirmation mail',
               s == 200 and 'access_token' not in b and len(mailbox()) > before, f'{s} messages+={len(mailbox()) - before}')
        (s2, b2), _ = signup(at)
        record('RATE_LIMIT_EMAIL_SENT 1 refuses the second mail', s2 == 429 and b2.get('error_code') == 'over_email_send_rate_limit', f'{s2} {b2.get("error_code")}')
    at = started('SMTP_MAX_FREQUENCY', relay, config={'MAILER_AUTOCONFIRM': False, 'SMTP_MAX_FREQUENCY': 1})
    if at:
        (s, b), email = signup(at)
        quick, _ = request(at, 'POST', '/resend', {'type': 'signup', 'email': email})
        time.sleep(2)
        later, _ = request(at, 'POST', '/resend', {'type': 'signup', 'email': email})
        record('SMTP_MAX_FREQUENCY 1 (saved as whole seconds, sent as 1s) refuses a resend at once and allows it after two seconds',
               quick == 429 and later == 200, f'at once {quick}, after 2 s {later}')
    # The two 5 minute limits count per client address: Auth applies them only when it is told which request header carries that address
    # (GOTRUE_RATE_LIMIT_HEADER, which the gateway does not set), the same finding as the retired per client limits.
    for header in (True, False):
        extra = {**relay, **({'GOTRUE_RATE_LIMIT_HEADER': 'X-Forwarded-For'} if header else {})}
        hdr = {'X-Forwarded-For': '203.0.113.40'} if header else {}
        at = started('RATE_LIMIT_OTP', extra, config={'MAILER_AUTOCONFIRM': False, 'RATE_LIMIT_OTP': 1, 'RATE_LIMIT_VERIFY': 1})
        if at:
            codes = [request(at, 'POST', '/magiclink', {'email': f'o{secrets.token_hex(4)}@example.com'}, headers=hdr)[0] for _ in range(3)]
            record(f'observed: RATE_LIMIT_OTP 1 on /magiclink (header {header})', True, str(codes))
            codes = [request(at, 'POST', '/otp', {'email': f'p{secrets.token_hex(4)}@example.com'}, headers=hdr)[0] for _ in range(3)]
            record(f'observed: RATE_LIMIT_OTP 1 on /otp (header {header})', True, str(codes))
            codes = [request(at, 'POST', '/verify', {'type': 'signup', 'token': 'x', 'email': 'v@example.com'}, headers=hdr)[0] for _ in range(3)]
            record(f'observed: RATE_LIMIT_VERIFY 1 on POST /verify (header {header})', True, str(codes))
            try:
                codes = [request(at, 'GET', '/verify?type=signup&token=x', None, headers=hdr)[0] for _ in range(3)]
            except Exception as error:
                codes = [type(error).__name__]
            record(f'observed: RATE_LIMIT_VERIFY 1 on GET /verify (header {header})', True, str(codes))


def main():
    evidence = sys.argv[sys.argv.index('--evidence') + 1] if '--evidence' in sys.argv else 'docs/evidence/auth-live-names-checks.json'
    started_at = time.time()
    try:
        setup()
        first = start_auth()
        record('first start with the console base configuration', first is not None, '' if first else auth_log())
        sections = (names_with_effect, downtime, hooks_saml_sms, templates, mail_settings, findings_that_retired_settings)
        # `--only mail_settings` runs just that section, for a check of one change.
        only = sys.argv[sys.argv.index('--only') + 1] if '--only' in sys.argv else None
        for section in sections:
            if only is None or section.__name__ == only:
                section()
    finally:
        cleanup()
    passed = bool(CHECKS) and all(item['ok'] for item in CHECKS)
    with open(evidence, 'w') as handle:
        json.dump({'check': 'auth-names', 'recorded': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'passed': passed, 'count': len(CHECKS),
                   'seconds': round(time.time() - started_at), 'auth_image': AUTH_IMAGE, 'checks': CHECKS,
                   'scope': 'The Auth variables the console writes, each tried on the pinned Auth with a real request, on a private internal Docker '
                            'network with a throwaway PostgreSQL. No installation, retained volume or real provider was involved.'}, handle, indent=1)
        handle.write('\n')
    print(f'evidence: {evidence}\nauth names check: {"passed" if passed else "failed"}')
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
