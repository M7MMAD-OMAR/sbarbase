"""Per environment phone sign-in and SMS provider settings: what the pinned Auth is told.

The console writes one private file per environment,

    .secrets/upstream/<environment runtime id>-sms.json

mode 0600, and records a pending revision in the catalog; the supervisor's apply then recreates that
environment's Auth with the variables below (lab/auth_settings.py `apply`, the path every Authentication
change takes). The provider's credentials travel only in that file and in the Auth container's
environment. The console never reads them back: it shows only whether one is set.

Only settings tried against the pinned Auth are offered (lab/auth-names-check.py): the provider and its
credentials, the phone confirmation switch (the opposite of SMS_AUTOCONFIRM) and the messages per hour limit. The
minimum interval per number is not offered: the live check could not observe it without a real provider. An environment with no file adds no variable. A bad
file raises, so the environment keeps its running Auth.
"""
import json
import os
import re
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / '.secrets' / 'upstream'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
VERSION = 1
MAX_BYTES = 8192
MAX_VALUE = 256
# provider -> (field, variable suffix, secret) in the order the console shows them. The variable is GOTRUE_SMS_<suffix>.
PROVIDERS = {
    'twilio': (('account_sid', 'TWILIO_ACCOUNT_SID', False), ('auth_token', 'TWILIO_AUTH_TOKEN', True),
               ('message_service_sid', 'TWILIO_MESSAGE_SERVICE_SID', False)),
    'twilio_verify': (('account_sid', 'TWILIO_VERIFY_ACCOUNT_SID', False), ('auth_token', 'TWILIO_VERIFY_AUTH_TOKEN', True),
                      ('message_service_sid', 'TWILIO_VERIFY_MESSAGE_SERVICE_SID', False)),
    'messagebird': (('access_key', 'MESSAGEBIRD_ACCESS_KEY', True), ('originator', 'MESSAGEBIRD_ORIGINATOR', False)),
    'textlocal': (('api_key', 'TEXTLOCAL_API_KEY', True), ('sender', 'TEXTLOCAL_SENDER', False)),
    'vonage': (('api_key', 'VONAGE_API_KEY', False), ('api_secret', 'VONAGE_API_SECRET', True), ('from', 'VONAGE_FROM', False)),
}


class SmsError(RuntimeError):
    """Names the field that failed, never its value."""


def path(e, directory=None):
    if not RUNTIME.fullmatch(e):
        raise SmsError('runtime')
    return Path(directory or DIRECTORY) / f'{e}-sms.json'


def _value(value, field):
    if not isinstance(value, str) or not value or len(value) > MAX_VALUE or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise SmsError(field)
    try:
        value.encode()  # a lone surrogate cannot be written to the container's environment file
    except UnicodeEncodeError:
        raise SmsError(field) from None
    return value


def validate(document):
    """The normalized file, or an error naming the field."""
    if not isinstance(document, dict) or document.get('version') != VERSION:
        raise SmsError('version')
    allowed = {'version', 'enabled', 'provider', 'autoconfirm', 'rate_limit_sms_sent', *PROVIDERS}
    if set(document) - allowed:
        raise SmsError('settings')
    if not isinstance(document.get('enabled'), bool) or not isinstance(document.get('autoconfirm'), bool):
        raise SmsError('enabled')
    provider = document.get('provider')
    if provider not in PROVIDERS:
        raise SmsError('provider')
    limit = document.get('rate_limit_sms_sent', 30)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000000:
        raise SmsError('rate_limit_sms_sent')
    result = {'version': VERSION, 'enabled': document['enabled'], 'provider': provider, 'autoconfirm': document['autoconfirm'],
              'rate_limit_sms_sent': limit}
    # Only the chosen provider's block is kept: another provider's credentials are not carried along.
    block = document.get(provider)
    if not isinstance(block, dict) or set(block) - {field for field, _, _ in PROVIDERS[provider]}:
        raise SmsError(provider)
    result[provider] = {field: _value(block.get(field), f'{provider}.{field}') for field, _, _ in PROVIDERS[provider]}
    return result


def applied_path(e, directory=None):
    return path(e, directory).with_name(f'{e}-sms.applied.json')


def write_applied(e, document, directory=None):
    """Keeps the phone settings an Auth was just started with (private, never a link, replaced in one step), or removes the copy when
    there are none. Restarts, upgrade replacements and the outbound network decision use this copy, so a save that was never applied
    does not reach them."""
    file = applied_path(e, directory)
    if document is None:
        file.unlink(missing_ok=True)
        return
    pending = file.with_name(file.name + '.pending')
    pending.unlink(missing_ok=True)
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(json.dumps(document))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, file)


def load_applied(e, directory=None):
    return _read(applied_path(e, directory))


def load(e, directory=None):
    """The environment's SMS settings, or None when it has none. A file that exists and is not valid raises."""
    return _read(path(e, directory))


def _read(file):
    if not os.path.lexists(file):
        return None
    status = file.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_mode & 0o077 or status.st_size > MAX_BYTES:
        raise SmsError('file')
    try:
        return validate(json.loads(file.read_text()))
    except SmsError:
        raise
    except ValueError as error:
        raise SmsError('file') from error


def wanted(e, directory=None, document=None):
    """Whether this environment's Auth needs a way out for SMS: phone sign-in is on, and a provider answers over HTTPS. Without a
    `document` it asks the applied copy: what the running Auth was started with."""
    if document is not None:
        return bool(document['enabled'])
    if not RUNTIME.fullmatch(e):
        return False
    try:
        document = load_applied(e, directory)
    except SmsError:
        return False
    return bool(document and document['enabled'])


def configuration(e, directory=None, applied=False):
    """Auth environment variables for this environment's phone settings; empty when it has none or has it off. `applied` reads the copy
    the running Auth was started with (restarts), not the saved file (an apply)."""
    if not RUNTIME.fullmatch(e):
        return {}
    document = load_applied(e, directory) if applied else load(e, directory)
    if document is None or not document['enabled']:
        return {}
    config = {'GOTRUE_EXTERNAL_PHONE_ENABLED': 'true', 'GOTRUE_SMS_PROVIDER': document['provider'],
              'GOTRUE_SMS_AUTOCONFIRM': 'true' if document['autoconfirm'] else 'false',
              'GOTRUE_RATE_LIMIT_SMS_SENT': str(document['rate_limit_sms_sent'])}
    for field, suffix, _ in PROVIDERS[document['provider']]:
        config['GOTRUE_SMS_' + suffix] = document[document['provider']][field]
    return config
