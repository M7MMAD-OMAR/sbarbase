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

A file may also carry a top level `session` object with three members:
`access_token_seconds`, `timebox_seconds` and `inactivity_seconds`. They become
GOTRUE_JWT_EXP, GOTRUE_SESSIONS_TIMEBOX and GOTRUE_SESSIONS_INACTIVITY_TIMEOUT. A member
left null adds nothing to the container's environment, and an unset cap means a session
does not expire: the pinned Auth reads an unset timebox and inactivity timeout that way.
"""
import argparse
import fcntl
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
# The map of settings the console shows, shared with src/control/auth-config.ts. Keys with store `config` are validated
# and written from it, so a new one is one registry entry.
REGISTRY = json.loads((ROOT / 'src' / 'control' / 'auth-config-registry.json').read_text())
CONFIG_KEYS = {entry['key']: entry for entry in REGISTRY['core'] if entry.get('store') == 'config'}
# Settings the console once offered that a live check showed to do nothing (see the registry's `retired`).
RETIRED_KEYS = frozenset(REGISTRY.get('retired', {}))
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
# The file format this version reads. A file from a newer version is refused, never half read.
VERSION = 2
# The only providers the nonce check applies to, as the cloud offers it.
NONCE_PROVIDERS = ('apple', 'google')
MAX_BYTES = 65536
DEFAULT_PUBLIC_URL = 'http://localhost'
# The session members a file may set, with their inclusive ranges in seconds.
SESSION_LIMITS = {'access_token_seconds': (60, 86400), 'timebox_seconds': (3600, 315360000),
                  'inactivity_seconds': (300, 315360000)}
# Configuration keys this module owns. A retained Auth container that differs from the
# desired configuration only in these may be recreated at start: they are the operator's
# own settings, never a sign of drift.
# GOTRUE_JWT_EXP and GOTRUE_SESSIONS_ are the session settings below: operator settings of that
# environment, never drift.
OWNED_PREFIXES = ('GOTRUE_EXTERNAL_', 'GOTRUE_PASSWORD_', 'GOTRUE_MFA_', 'GOTRUE_SECURITY_MANUAL_LINKING_', 'GOTRUE_SITE_URL', 'GOTRUE_URI_ALLOW_LIST', 'GOTRUE_DISABLE_SIGNUP',
                  'API_EXTERNAL_URL', 'GOTRUE_JWT_EXP', 'GOTRUE_SESSIONS_')


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
    if not isinstance(settings, dict) or set(settings) - {'version', 'revision', 'site_url', 'redirect_urls', 'signup', 'anonymous', 'manual_linking', 'config',
                                                          'providers', 'session'}:
        raise SettingsError('settings')
    version = settings.get('version', 1)
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= VERSION:
        raise SettingsError('version')
    result = {'revision': settings.get('revision', 0), 'site_url': web_address(settings.get('site_url'), 'site_url'),
              'signup': settings.get('signup', True), 'anonymous': settings.get('anonymous', False),
              'manual_linking': settings.get('manual_linking', False)}
    if not isinstance(result['revision'], int) or result['revision'] < 0:
        raise SettingsError('revision')
    for field in ('signup', 'anonymous', 'manual_linking'):
        if not isinstance(result[field], bool):
            raise SettingsError(field)
    redirects = settings.get('redirect_urls', [])
    if not isinstance(redirects, list) or len(redirects) > MAX_REDIRECTS:
        raise SettingsError('redirect_urls')
    result['redirect_urls'] = [redirect_address(item) for item in redirects]
    config = settings.get('config', {})
    if not isinstance(config, dict):
        raise SettingsError('config')
    # A key the registry retired is dropped, so a file saved before it was retired still loads.
    config = {key: value for key, value in config.items() if key not in RETIRED_KEYS}
    if set(config) - set(CONFIG_KEYS):
        raise SettingsError('config')
    result['config'] = {}
    for key, value in config.items():
        entry = CONFIG_KEYS[key]
        if value is None:
            continue
        if entry['type'] == 'bool':
            if not isinstance(value, bool):
                raise SettingsError(key)
        elif isinstance(value, bool) or not isinstance(value, int) or not entry['min'] <= value <= entry['max']:
            raise SettingsError(key)
        result['config'][key] = value
    providers = settings.get('providers', {})
    if not isinstance(providers, dict) or set(providers) - set(PROVIDERS):
        raise SettingsError('providers')
    result['providers'] = {}
    for name, entry in sorted(providers.items()):
        if not isinstance(entry, dict) or set(entry) - {'enabled', 'client_id', 'secret', 'url', 'skip_nonce_check', 'email_optional'}:
            raise SettingsError(name)
        enabled, client, secret, url = entry.get('enabled', False), entry.get('client_id', ''), entry.get('secret', ''), entry.get('url', '')
        nonce, optional = entry.get('skip_nonce_check', False), entry.get('email_optional', False)
        if not isinstance(nonce, bool) or not isinstance(optional, bool):
            raise SettingsError(name)
        if not isinstance(enabled, bool) or not isinstance(client, str) or not isinstance(secret, str) or not isinstance(url, str):
            raise SettingsError(name)
        if name == 'google':
            # A list of client IDs: spaces around the commas are tidied, an empty entry is not allowed.
            client = ','.join(part.strip() for part in client.split(',')) if client else client
            if client and '' in client.split(','):
                raise SettingsError(name)
        if len(client) > (2048 if name == 'google' else 512) or len(secret) > 4096 or any(c.isspace() for c in client + secret):
            raise SettingsError(name)
        if nonce and name not in NONCE_PROVIDERS:
            raise SettingsError(name)
        if enabled and (not client or not secret):
            raise SettingsError(name)
        if url:
            if name not in PROVIDER_URL:
                raise SettingsError(name)
            web_address(url, name)
        elif enabled and PROVIDER_URL.get(name):
            raise SettingsError(name)
        result['providers'][name] = {'enabled': enabled, 'client_id': client, 'secret': secret, 'url': url,
                                     'skip_nonce_check': nonce, 'email_optional': optional}
    # A file written before sessions existed, and a session object missing a member, both mean
    # every member is null: no such variable reaches the container.
    session = settings['session'] if 'session' in settings else {}
    if not isinstance(session, dict) or set(session) - set(SESSION_LIMITS):
        raise SettingsError('session')
    result['session'] = {}
    for name, (low, high) in SESSION_LIMITS.items():
        value = session.get(name)
        if value is None:
            result['session'][name] = None
        elif isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise SettingsError(name)
        else:
            result['session'][name] = value
    return result


def path(e):
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    return DIRECTORY / f'{e}-auth.json'


def applied_path(e):
    """The last settings Auth was successfully recreated with: what every start uses, so a saved
    change that failed, or has not been applied yet, never reaches a restart."""
    if not RUNTIME.fullmatch(e):
        raise SettingsError('environment')
    return DIRECTORY / f'{e}-auth.applied.json'


def read_private(file):
    """Text of a settings file, refused when it is a link or readable by anyone but the owner."""
    status = file.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_mode & 0o077 or status.st_size > MAX_BYTES:
        raise SettingsError('settings')
    return file.read_text()


def load_applied(e):
    """The last applied settings, or None before the first apply. An unreadable copy raises."""
    file = applied_path(e)
    if not os.path.lexists(file):
        return None
    return validate(json.loads(read_private(file)))


def file_form(settings):
    """The settings as a file holds them. While none of the switches added with version 2 is on, the file
    keeps the version 1 shape, so a build from before them still reads it (a rollback is not an outage)."""
    plain = not settings.get('manual_linking') and not settings.get('config') and not any(
        entry.get('skip_nonce_check') or entry.get('email_optional') for entry in settings['providers'].values())
    if not plain:
        # An empty config is left out, so a build from before the simple settings still reads a file that only uses version 2 switches.
        shaped = {key: value for key, value in settings.items() if key != 'config' or value}
        return {**shaped, 'version': VERSION}
    stripped = {key: value for key, value in settings.items() if key not in ('manual_linking', 'config')}
    stripped['providers'] = {name: {key: value for key, value in entry.items() if key not in ('skip_nonce_check', 'email_optional')}
                             for name, entry in settings['providers'].items()}
    return {**stripped, 'version': 1}


def write_applied(e, settings):
    """Keeps the settings just applied, created private (mode 0600, never a link) and replaced in one step."""
    file = applied_path(e)
    pending = file.with_name(file.name + '.pending')
    pending.unlink(missing_ok=True)
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(json.dumps(file_form(settings)))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, file)


def load(e):
    """The environment's settings, or None when it has none. A file that fails validation raises."""
    file = path(e)
    # lexists: a link that points nowhere is a refusal, not "no settings".
    if not os.path.lexists(file):
        return None
    return validate(json.loads(read_private(file)))


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
    # Off is Auth's own default, so only an enabled switch adds a variable: an environment saved before
    # this setting existed keeps the very environment it had.
    if settings.get('manual_linking'):
        config['GOTRUE_SECURITY_MANUAL_LINKING_ENABLED'] = 'true'
    # A value the person set, and only that: an unset key adds nothing, so Auth keeps its own default.
    for key, value in (settings.get('config') or {}).items():
        config['GOTRUE_' + key] = ('true' if value else 'false') if isinstance(value, bool) else str(value)
    # A null member adds nothing, so a file without session settings yields the same dict as before.
    session = settings.get('session') or {}
    for name, variable in (('access_token_seconds', 'GOTRUE_JWT_EXP'), ('timebox_seconds', 'GOTRUE_SESSIONS_TIMEBOX'),
                           ('inactivity_seconds', 'GOTRUE_SESSIONS_INACTIVITY_TIMEOUT')):
        if session.get(name) is not None:
            # JWT_EXP is a count of seconds; the two caps are Go duration strings.
            config[variable] = str(session[name]) if name == 'access_token_seconds' else f'{session[name]}s'
    for name, entry in settings['providers'].items():
        if not entry['enabled']:
            continue
        prefix = 'GOTRUE_EXTERNAL_' + name.upper() + '_'
        config.update({prefix + 'ENABLED': 'true', prefix + 'CLIENT_ID': entry['client_id'],
                       prefix + 'SECRET': entry['secret'], prefix + 'REDIRECT_URI': external + '/callback'})
        if entry['url']:
            config[prefix + 'URL'] = entry['url']
        if entry.get('skip_nonce_check'):
            config[prefix + 'SKIP_NONCE_CHECK'] = 'true'
        if entry.get('email_optional'):
            config[prefix + 'EMAIL_OPTIONAL'] = 'true'
    return config


def record(e, state, failure=None, revision=None):
    """The outcome, in the catalog row the console reads. `revision` is the one this outcome is for.
    A newer revision saved while it ran is still pending: its row keeps that state, and only
    `applied` moves, so a save made during an apply is never marked done or failed unseen."""
    try:
        with closing(sqlite3.connect(CATALOG, timeout=5)) as database, database:
            if revision is None:
                database.execute('UPDATE auth_settings SET state=?, failure=?, updated_at=? WHERE runtime=?',
                                 (state, failure, int(time.time() * 1000), e))
            else:
                database.execute('UPDATE auth_settings SET state=CASE WHEN revision<=? THEN ? ELSE state END, '
                                 'failure=CASE WHEN revision<=? THEN ? ELSE failure END, '
                                 'applied=CASE WHEN ?=\'applied\' THEN ? ELSE applied END, updated_at=? WHERE runtime=?',
                                 (revision, state, revision, failure, state, revision, int(time.time() * 1000), e))
    except sqlite3.Error:
        pass


# durable_runtime.BUSY, the exit status of a console change that found a lock held.
BUSY = 76


def busy():
    """True while another runtime operation (a provisioning, a mail change) holds the lock."""
    try:
        with OPERATION_LOCK.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
    except BlockingIOError:
        return True


def prior_path(e):
    """The mail file as it was before an unapplied console save (src/control/smtp.ts `keepPrior`)."""
    return DIRECTORY / f'{e}-mail.prior'


def settle_prior_mail(e, applied):
    """After an apply: drop the saved prior mail file when it worked, put it back when it did not, so the file on disk
    never stays ahead of the Auth that runs."""
    prior = prior_path(e)
    if not os.path.lexists(prior):
        return
    try:
        if not applied:
            target = DIRECTORY / f'{e}-mail.json'
            if prior.read_text() == 'none':
                target.unlink(missing_ok=True)
            else:
                os.replace(prior, target)
                return
        prior.unlink(missing_ok=True)
    except OSError:
        pass


def row_revision(e):
    """The revision the catalog holds for this environment, or None."""
    try:
        with closing(sqlite3.connect(CATALOG, timeout=5)) as database:
            row = database.execute('SELECT revision FROM auth_settings WHERE runtime=?', (e,)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def apply(e):
    """Recreates the environment's Auth with its saved settings and records the outcome."""
    try:
        # A crash between writing and renaming leaves the pending copy, which holds secrets.
        stale = path(e).with_name(path(e).name + '.pending')
        # The console writes this copy and renames it at once; only one that has sat for a minute is a crash's.
        if os.path.lexists(stale) and time.time() - stale.lstat().st_mtime > 60:
            stale.unlink(missing_ok=True)
        applied_path(e).with_name(applied_path(e).name + '.pending').unlink(missing_ok=True)
        settings = load(e)
    except (SettingsError, ValueError, OSError):
        record(e, 'failed', 'The saved settings are not valid. Save them again.')
        return 1
    if busy():
        # Another operation is running; the supervisor asks again shortly.
        return 75
    row_revision_at_start = row_revision(e) if settings is None else None
    result = subprocess.run(['/usr/bin/python3', 'lab/durable_runtime.py', 'auth', e], cwd=ROOT, capture_output=True, text=True)
    if result.returncode == BUSY:
        # The operation lock was taken after busy() looked: nothing changed, ask again.
        return 75
    # Without a settings file the row's own revision stands for it, so a save made while this ran stays pending.
    revision = settings['revision'] if settings else (row_revision_at_start if row_revision_at_start is not None else None)
    if result.returncode:
        settle_prior_mail(e, False)
        record(e, 'failed', 'Auth did not start with these settings, so the previous settings stay in use.', revision)
        return 1
    # The copy restarts use was kept by the recreate itself, from the settings Auth started with.
    settle_prior_mail(e, True)
    record(e, 'applied', None, revision)
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
