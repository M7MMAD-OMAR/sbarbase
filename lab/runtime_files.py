"""The files two console processes read while a handover overlaps them (docs/engineering/HANDOVER.md, the format
rule): one home for their paths, their reads and their checked writes.

The files are the published endpoints, the shared database's address, the Studio state, the management
Auth address, the write pause records and the runtime secret. Nothing else in lab/ may name them (the scan
in lab/test_handover_formats.py): the paths are private names here, and every other module reads and writes
through the functions below. Their formats are declared in src/control/handover-formats.json.

The compatibility rule (the same in every writer): a writer checks only what it writes, the keys it sets and
their types, and that the value it patches is an object. It keeps every other key, in the file and inside the
entry it patches, whoever wrote it, and never rejects a key it does not know. What a writer puts on disk is
what the code it replaced put there: the same JSON through the same atomic write. Every function takes the
folder (`state`, `.lab/upstream`; `private`, `.secrets/upstream`) the caller already works in, so a test that
points a module at a temporary folder keeps working."""
import json
import os
import re
from pathlib import Path

import run as lab

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.lab/upstream'
PRIVATE = ROOT / '.secrets/upstream'

_ENDPOINTS = 'endpoints.json'
_DATABASE = 'database.json'
_STUDIO = 'studio.json'
_MANAGEMENT = 'management.json'
_PAUSES = 'write-pauses'
_RUNTIME_SECRET = 'runtime.json'

RUNTIME = re.compile(r'e_[a-f0-9]{24}')


class FormatError(ValueError):
    """A value a writer was asked to write does not have the declared type of a key it sets."""


def _state(state):
    return Path(state) if state is not None else STATE


def _private(private):
    return Path(private) if private is not None else PRIVATE


def _is_object(value):
    return isinstance(value, dict)


# Paths, for the callers that need a place and not a document -------------------------------------

def endpoints_path(state=None):
    return _state(state) / _ENDPOINTS


def studio_path(state=None):
    return _state(state) / _STUDIO


def management_path(state=None):
    return _state(state) / _MANAGEMENT


def pauses_folder(state=None):
    return _state(state) / _PAUSES


def pause_path(runtime, state=None):
    if not isinstance(runtime, str) or not RUNTIME.fullmatch(runtime):
        raise FormatError('Name an environment by its runtime id (e_ and 24 hex)')
    return pauses_folder(state) / (runtime + '.json')


def runtime_secret_path_for_tool(private=None):
    """The one place the secret's path leaves this module: for the callers that hand it to another program
    (lab/database_move.py, lab/migrate-generation.py) or to git. Never print what it names."""
    return _private(private) / _RUNTIME_SECRET


def runtime_secret_relative():
    """The secret's path under the checkout, as a backup payload names it."""
    return '.secrets/upstream/' + _RUNTIME_SECRET


# Endpoints ---------------------------------------------------------------------------------------

def read_endpoints(state=None, *, strict=False, required=False):
    """The published endpoints, one object per runtime id. A missing file is {} unless `required`.
    An unreadable or malformed one is {} too, unless `strict`, which lets the error through. Returned as
    the file has it: the caller judges what it reads."""
    path = endpoints_path(state)
    if required:
        return json.loads(path.read_text())
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        if strict:
            raise
        return {}


def _checked_entry(runtime, entry):
    if not _is_object(entry):
        raise FormatError(f'The published entry of {runtime} is not an object')
    for key in ('auth', 'rest'):
        if key in entry and not isinstance(entry[key], str):
            raise FormatError(f'The {key} address of {runtime} is not a string')
    for key in ('storage', 'functions', 'realtime'):
        if key in entry and not _is_object(entry[key]):
            raise FormatError(f'The {key} entry of {runtime} is not an object')


def write_endpoints(endpoints, state=None, writer=None):
    """The whole published document, through one rename. A writer checks only what it writes: the document
    must be an object of objects, and an entry that differs from what the file has is checked for the declared
    type of the keys it has. An entry the caller carried through unchanged is not judged, so an entry a later
    shape wrote cannot stop an address of another environment from being published. Every other key of every
    entry is written as it is. `writer` replaces the atomic write, for a caller that observes or wraps it."""
    if not _is_object(endpoints):
        raise FormatError('The published endpoints are not an object')
    current = read_endpoints(state)
    current = current if _is_object(current) else {}
    for runtime, entry in endpoints.items():
        if runtime not in current or current[runtime] != entry:
            _checked_entry(runtime, entry)
        elif not _is_object(entry):
            raise FormatError(f'The published entry of {runtime} is not an object')
    (writer or lab.atomic)(endpoints_path(state), endpoints)


def update_endpoints(environment, change, state=None, writer=None):
    """Reads the file, applies `change` to one entry (an object, patched in place or returned whole) and writes
    it back under one rename. Only that entry is checked and changed: every other key of it, and every other
    entry, stays as it was, whoever wrote it. Returns the entry written."""
    endpoints = read_endpoints(state, strict=True)
    if not _is_object(endpoints) or environment not in endpoints:
        raise FormatError(f'{environment} is not published')
    entry = endpoints[environment]
    if not _is_object(entry):
        raise FormatError(f'The published entry of {environment} is not an object')
    changed = change(entry)
    endpoints[environment] = entry if changed is None else changed
    _checked_entry(environment, endpoints[environment])
    (writer or lab.atomic)(endpoints_path(state), endpoints)
    return endpoints[environment]


# The shared database, Studio, the management realm ----------------------------------------------

def _current(path):
    """What the file has now, to keep its other keys: nothing when it is missing, unreadable or not an object. The
    code these writers replaced wrote these two documents over whatever was there, so a truncated one healed at
    the next start; it still does."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return value if _is_object(value) else {}


def read_database(state=None):
    path = _state(state) / _DATABASE
    return json.loads(path.read_text()) if path.exists() else None


def write_database(host, port, state=None):
    """Where direct database access connects."""
    if not isinstance(host, str) or type(port) is not int:
        raise FormatError('The database address needs a host and a port')
    lab.atomic(_state(state) / _DATABASE, {**_current(_state(state) / _DATABASE), 'host': host, 'port': port})


def read_studio(state=None):
    """The Studio state; {'sessions': {}} while there is none."""
    path = studio_path(state)
    return json.loads(path.read_text()) if path.exists() else {'sessions': {}}


def write_studio(value, state=None):
    if not _is_object(value) or not _is_object(value.get('sessions')):
        raise FormatError('The Studio state needs its sessions as an object')
    if 'upstream' in value and not _is_object(value['upstream']):
        raise FormatError('The Studio state upstream is not an object')
    lab.atomic(studio_path(state), value)


def studio_sessions(state=None):
    """The runtime ids with a Studio session, or none when the state cannot be read."""
    path = studio_path(state)
    try:
        return set(json.loads(path.read_text()).get('sessions', {}) if path.exists() else {})
    except (OSError, ValueError):
        return set()


def read_management(state=None):
    """The management realm's address document, or None while there is none."""
    path = management_path(state)
    return json.loads(path.read_text()) if path.exists() else None


def management_present(state=None):
    return management_path(state).exists()


def update_management(auth, state=None, writer=None):
    """Publishes the management Auth address; any other key of the document stays."""
    if not isinstance(auth, str):
        raise FormatError('The management Auth address is not a string')
    (writer or lab.atomic)(management_path(state), {**_current(management_path(state)), 'auth': auth})


# Write pauses ------------------------------------------------------------------------------------

def paused_runtimes(state=None):
    """The runtime ids with a pause record (the file's existence is the pause)."""
    folder = pauses_folder(state)
    return {path.stem for path in folder.glob('e_*.json')} if folder.is_dir() else set()


def pause_exists(runtime, state=None):
    return pause_path(runtime, state).exists()


def read_pause(runtime, state=None):
    """The record as the file has it, None when there is none; an unreadable file raises."""
    path = pause_path(runtime, state)
    return json.loads(path.read_text()) if path.exists() else None


def write_pause(path, value):
    """One pause record at `path` (pause_path): private, durable, replaced in one rename. A pause is a
    hold, so the record must be an object."""
    if not _is_object(value):
        raise FormatError('A write pause record is not an object')
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    os.chmod(temporary, 0o600)
    with temporary.open('rb') as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


# The runtime secret ------------------------------------------------------------------------------

def read_runtime_secret(private=None):
    """The signing secrets document, read by its shape only: never print it."""
    return json.loads(runtime_secret_path_for_tool(private).read_text())


def runtime_secret_exists(private=None):
    return runtime_secret_path_for_tool(private).exists()


def write_runtime_secret(value, private=None):
    if not _is_object(value):
        raise FormatError('The runtime secret is not an object')
    for key in ('management', 'environments'):
        if key in value and not _is_object(value[key]):
            raise FormatError(f'The runtime secret {key} is not an object')
    lab.atomic(runtime_secret_path_for_tool(private), value)
