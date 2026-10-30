"""Per environment email templates: what the pinned Auth is told so it fetches them.

The pinned Auth does not take a template body in its environment: it takes the address of a page and
fetches it when it sends a message. The console writes one private file per environment,

    .secrets/upstream/<environment runtime id>-templates.json

mode 0600, with a random token and, for each of the six messages Auth sends, an optional subject and an
optional body. The console process serves the bodies on its internal route at

    http://<runtime network gateway>:<port>/<runtime id>/mail-templates/<token>/<kind>

on the host's address on the runtime network (the same internal route Studio uses). Containers on this host's
Docker networks can reach that address, so the random token is the protection. This
module validates the file the console wrote and turns it into the GOTRUE_MAILER_TEMPLATES_* and
GOTRUE_MAILER_SUBJECTS_* variables. A message with no custom body keeps Auth's own template; no variable is
added for it. A bad file raises, so the environment keeps its running Auth instead of silently sending
default messages.
"""
import json
import os
import re
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / '.secrets' / 'upstream'
STATE = ROOT / '.lab' / 'upstream'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
TOKEN = re.compile(r'[a-f0-9]{64}')
ADDRESS = re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}')
VERSION = 1
# The six messages the pinned Auth sends, in the order the cloud lists them.
KINDS = ('confirmation', 'invite', 'magic_link', 'email_change', 'recovery', 'reauthentication')
MAX_BODY = 32768
MAX_SUBJECT = 200
MAX_BYTES = 262144
# The port of the console's internal route; lab/studio.py UPSTREAM_PORT is the same setting.
ROUTE_PORT = int(os.environ.get('SBARBASE_STUDIO_UPSTREAM_PORT', '25432'))


# The variables this module sets.
OWNED_PREFIXES = ('GOTRUE_MAILER_TEMPLATES_', 'GOTRUE_MAILER_SUBJECTS_')


class TemplatesError(RuntimeError):
    """Names the field that failed, never its value."""


def path(e, directory=None):
    if not RUNTIME.fullmatch(e):
        raise TemplatesError('runtime')
    return Path(directory or DIRECTORY) / f'{e}-templates.json'


def _text(value, field, limit):
    if not isinstance(value, str) or len(value.encode()) > limit or '\x00' in value:
        raise TemplatesError(field)
    return value


def validate(document):
    """The normalized file, or an error naming the field."""
    if not isinstance(document, dict) or document.get('version') != VERSION:
        raise TemplatesError('version')
    token = document.get('token')
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise TemplatesError('token')
    templates = document.get('templates')
    if not isinstance(templates, dict) or set(templates) - set(KINDS):
        raise TemplatesError('templates')
    result = {}
    for kind, entry in templates.items():
        if not isinstance(entry, dict) or set(entry) - {'subject', 'body'}:
            raise TemplatesError(kind)
        subject = _text(entry.get('subject', ''), kind + '.subject', MAX_SUBJECT)
        body = _text(entry.get('body', ''), kind + '.body', MAX_BODY)
        if '\n' in subject or '\r' in subject:
            raise TemplatesError(kind + '.subject')
        if subject or body:
            result[kind] = {'subject': subject, 'body': body}
    return {'version': VERSION, 'token': token, 'templates': result}


def applied_path(e, directory=None):
    return path(e, directory).with_name(f'{e}-templates.applied.json')


def write_applied(e, document, directory=None):
    """Keeps the templates an Auth was just started with (private, never a link, replaced in one step), or removes the copy when
    there are none. Restarts and upgrade replacements use this copy, so a save that was never applied does not reach them."""
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
    """The environment's templates, or None when it has none. A file that exists and is not valid raises."""
    return _read(path(e, directory))


def _read(file):
    if not os.path.lexists(file):
        return None
    status = file.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_mode & 0o077 or status.st_size > MAX_BYTES:
        raise TemplatesError('file')
    try:
        return validate(json.loads(file.read_text()))
    except ValueError as error:
        if isinstance(error, TemplatesError):
            raise
        raise TemplatesError('file') from error


def route(state=None):
    """The address of the console's internal route on the runtime network, as the runtime published it."""
    import runtime_files
    try:
        published = runtime_files.read_studio(Path(state or STATE)).get('upstream') or {}
    except (OSError, ValueError, AttributeError):
        return None
    host, port = published.get('host'), published.get('port')
    if not isinstance(host, str) or not ADDRESS.fullmatch(host) or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    return host, port


def configuration(e, directory=None, state=None, applied=False):
    """Auth environment variables for this environment's templates; empty when it has none. `applied` reads the copy the running
    Auth was started with (restarts), not the saved file (an apply)."""
    document = load_applied(e, directory) if applied else load(e, directory)
    if document is None or not document['templates']:
        return {}
    at = route(state)
    if at is None:
        raise TemplatesError('route')
    config = {}
    for kind, entry in document['templates'].items():
        name = kind.upper()
        if entry['body']:
            config['GOTRUE_MAILER_TEMPLATES_' + name] = f'http://{at[0]}:{at[1]}/{e}/mail-templates/{document["token"]}/{kind}'
        if entry['subject']:
            config['GOTRUE_MAILER_SUBJECTS_' + name] = entry['subject']
    return config
