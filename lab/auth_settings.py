"""Per environment sign-in settings: where Auth sends people, and which OAuth providers it offers.

The console writes one private file per environment,

    .secrets/upstream/<environment runtime id>-auth.json

mode 0600, and records a pending revision in the catalog. The supervisor then runs
`durable_runtime.py auth <runtime>`, which recreates that environment's Auth with the
settings below and records the outcome. Auth keeps all its state in the environment
database, so the recreate loses nothing.

A provider's client secret travels only in that file and in the Auth container's
environment, like every other credential this installation holds. The console never
reads it back: it shows only whether one is set.

The public address Auth uses for links and OAuth callbacks is installation wide,
`SBARBASE_PUBLIC_URL` (for example https://api.example.com). Each environment's Auth
answers under `<public address>/<runtime>/auth/v1`.
"""
import argparse
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / '.secrets' / 'upstream'
CATALOG = ROOT / '.lab' / 'upstream' / 'control.sqlite'
OPERATION_LOCK = ROOT / '.lab' / 'upstream' / 'operation.lock'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
# The providers the pinned Auth offers that need nothing beyond a client id and secret,
# and an address for the few that run on a server of their own choosing.
PROVIDERS = ('apple', 'azure', 'bitbucket', 'discord', 'facebook', 'figma', 'github', 'gitlab', 'google', 'kakao',
             'keycloak', 'linkedin_oidc', 'notion', 'slack_oidc', 'spotify', 'twitch', 'twitter', 'workos', 'zoom')
PROVIDER_URL = {'azure': False, 'gitlab': False, 'keycloak': True, 'workos': False}
MAX_REDIRECTS = 50
MAX_BYTES = 65536
DEFAULT_PUBLIC_URL = 'http://localhost'
# Configuration keys this module owns. A retained Auth container that differs from the
# desired configuration only in these may be recreated at start: they are the operator's
# own settings, never a sign of drift.
OWNED_PREFIXES = ('GOTRUE_EXTERNAL_', 'GOTRUE_SITE_URL', 'GOTRUE_URI_ALLOW_LIST', 'GOTRUE_DISABLE_SIGNUP',
                  'API_EXTERNAL_URL')


class SettingsError(ValueError):
    """Names the field that failed, never its value."""


def owned(key):
    return any(key == prefix or (prefix.endswith('_') and key.startswith(prefix)) for prefix in OWNED_PREFIXES)


def web_address(value, field, *, allow_path=True):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise SettingsError(field)
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise SettingsError(field)
    if not allow_path and (parts.path not in ('', '/') or parts.query or parts.fragment):
        raise SettingsError(field)
    return value


def redirect_address(value):
    """An allowed redirect may use Auth's own wildcards (`*`, `**`) and custom app schemes."""
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or ',' in value:
        raise SettingsError('redirect_urls')
    if not re.match(r'^[a-z][a-z0-9+.-]*://', value):
        raise SettingsError('redirect_urls')
    return value


def validate(settings):
    """The settings as the file holds them, or SettingsError naming the first bad field."""
    if not isinstance(settings, dict) or set(settings) - {'revision', 'site_url', 'redirect_urls', 'signup', 'anonymous', 'providers'}:
        raise SettingsError('settings')
    result = {'revision': settings.get('revision', 0), 'site_url': web_address(settings.get('site_url'), 'site_url'),
              'signup': settings.get('signup', True), 'anonymous': settings.get('anonymous', False)}
    if not isinstance(result['revision'], int) or result['revision'] < 0:
        raise SettingsError('revision')
    for field in ('signup', 'anonymous'):
        if not isinstance(result[field], bool):
            raise SettingsError(field)
    redirects = settings.get('redirect_urls', [])
    if not isinstance(redirects, list) or len(redirects) > MAX_REDIRECTS:
        raise SettingsError('redirect_urls')
    result['redirect_urls'] = [redirect_address(item) for item in redirects]
    providers = settings.get('providers', {})
    if not isinstance(providers, dict) or set(providers) - set(PROVIDERS):
        raise SettingsError('providers')
    result['providers'] = {}
    for name, entry in sorted(providers.items()):
        if not isinstance(entry, dict) or set(entry) - {'enabled', 'client_id', 'secret', 'url'}:
            raise SettingsError(name)
        enabled, client, secret, url = entry.get('enabled', False), entry.get('client_id', ''), entry.get('secret', ''), entry.get('url', '')
        if not isinstance(enabled, bool) or not isinstance(client, str) or not isinstance(secret, str) or not isinstance(url, str):
            raise SettingsError(name)
        if len(client) > 512 or len(secret) > 4096 or any(c.isspace() for c in client + secret):
            raise SettingsError(name)
        if enabled and (not client or not secret):
            raise SettingsError(name)
        if url:
            if name not in PROVIDER_URL:
                raise SettingsError(name)
            web_address(url, name)
        elif enabled and PROVIDER_URL.get(name):
            raise SettingsError(name)
        result['providers'][name] = {'enabled': enabled, 'client_id': client, 'secret': secret, 'url': url}
    return result


def path(e):
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    return DIRECTORY / f'{e}-auth.json'


def load(e):
    """The environment's settings, or None when it has none. A file that fails validation raises."""
    file = path(e)
    if not file.exists():
        return None
    if file.stat().st_size > MAX_BYTES:
        raise SettingsError('settings')
    return validate(json.loads(file.read_text()))


def public_url():
    value = os.environ.get('SBARBASE_PUBLIC_URL', '').strip().rstrip('/') or DEFAULT_PUBLIC_URL
    return web_address(value, 'SBARBASE_PUBLIC_URL', allow_path=False).rstrip('/')


def configuration(e, settings):
    """Auth environment variables for these settings; the caller adds them to the base configuration."""
    external = f'{public_url()}/{e}/auth/v1'
    config = {'API_EXTERNAL_URL': external}
    if settings is None:
        return config
    config.update({'GOTRUE_SITE_URL': settings['site_url'],
                   'GOTRUE_URI_ALLOW_LIST': ','.join(settings['redirect_urls']),
                   'GOTRUE_DISABLE_SIGNUP': 'false' if settings['signup'] else 'true',
                   'GOTRUE_EXTERNAL_ANONYMOUS_USERS_ENABLED': 'true' if settings['anonymous'] else 'false'})
    for name, entry in settings['providers'].items():
        if not entry['enabled']:
            continue
        prefix = 'GOTRUE_EXTERNAL_' + name.upper() + '_'
        config.update({prefix + 'ENABLED': 'true', prefix + 'CLIENT_ID': entry['client_id'],
                       prefix + 'SECRET': entry['secret'], prefix + 'REDIRECT_URI': external + '/callback'})
        if entry['url']:
            config[prefix + 'URL'] = entry['url']
    return config


def record(e, state, failure=None, revision=None):
    """The outcome, in the catalog row the console reads."""
    try:
        with closing(sqlite3.connect(CATALOG, timeout=5)) as database, database:
            database.execute('UPDATE auth_settings SET state=?, failure=?, applied=COALESCE(?, applied), updated_at=? WHERE runtime=?',
                             (state, failure, revision, int(time.time() * 1000), e))
    except sqlite3.Error:
        pass


def busy():
    """True while another runtime operation (a provisioning, a mail change) holds the lock."""
    try:
        with OPERATION_LOCK.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
    except BlockingIOError:
        return True


def apply(e):
    """Recreates the environment's Auth with its saved settings and records the outcome."""
    try:
        settings = load(e)
    except (SettingsError, ValueError, OSError):
        record(e, 'failed', 'The saved settings are not valid. Save them again.')
        return 1
    if busy():
        # Another operation is running; the supervisor asks again shortly.
        return 75
    result = subprocess.run(['/usr/bin/python3', 'lab/durable_runtime.py', 'auth', e], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        record(e, 'failed', 'Auth did not start with these settings, so the previous settings stay in use.')
        return 1
    record(e, 'applied', None, settings['revision'] if settings else 0)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Apply one environment\'s sign-in settings')
    parser.add_argument('command', choices=('apply',))
    parser.add_argument('environment')
    args = parser.parse_args(argv)
    if not RUNTIME.fullmatch(args.environment):
        print('Invalid environment', file=sys.stderr)
        return 2
    return apply(args.environment)


if __name__ == '__main__':
    sys.exit(main())
