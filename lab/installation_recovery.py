"""Collect original local installation material without claiming complete recovery.

Native databases, Storage signing material, resource admission, complete feature
inventory and authenticated portable packaging remain separate required stages.
The published collection is private plaintext and cannot authorize restore/purge.
"""
import contextlib
import hashlib
import runtime_files
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import subprocess
import stat
import secrets
import time

from fresh_host_recovery import open_path, private_root
from lifecycle_recovery import publish_exact
import sqlite_material


class Refused(ValueError):
    pass


MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
MISSING = ['native-database-and-object-snapshots', 'native-feature-inventory',
           'storage-signing-material-provenance', 'source-host-and-resource-admission',
           'complete-authenticated-package', 'distinct-host-lost-source-restoration']


def clock(deadline):
    if time.monotonic() >= deadline:
        raise Refused('Local collection deadline expired')


def witness(path):
    descriptor = open_path(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid() or value.st_nlink != 1:
            raise Refused('Owned regular local material required')
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                value.st_ctime_ns, stat.S_IMODE(value.st_mode))
    finally:
        os.close(descriptor)


def read(path, deadline, budget, *, private=False):
    clock(deadline)
    descriptor = open_path(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        value = os.fstat(descriptor)
        if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid() or value.st_nlink != 1
                or private and stat.S_IMODE(value.st_mode) != 0o600):
            raise Refused('Owned private local material required')
        before = (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns, stat.S_IMODE(value.st_mode))
        if before != witness(path):
            raise Refused('Local material replaced before reading')
        if value.st_size > budget[0]:
            raise Refused('Local collection byte limit exceeded')
        parts, remaining = [], value.st_size
        while remaining:
            clock(deadline)
            part = os.read(descriptor, min(65536, remaining))
            if not part:
                raise Refused('Local material truncated')
            parts.append(part); remaining -= len(part)
        if os.read(descriptor, 1) or before != witness(path):
            raise Refused('Local material changed')
        budget[0] -= value.st_size
        return b''.join(parts)
    finally:
        os.close(descriptor)


def tree(path, deadline):
    """Enumerate exactly the present tree, refusing links and special files."""
    clock(deadline)
    if not path.exists() and not path.is_symlink():
        return []
    descriptor = open_path(path, os.O_RDONLY | os.O_DIRECTORY)
    os.close(descriptor)
    result = []
    def failed(_error):
        raise Refused('Local material directory enumeration refused')
    for parent, directories, files in os.walk(path, followlinks=False, onerror=failed):
        clock(deadline)
        for name in directories:
            candidate = Path(parent) / name
            if candidate.is_symlink() or not candidate.is_dir():
                raise Refused('Linked local material directory refused')
        for name in files:
            candidate = Path(parent) / name
            witness(candidate)
            result.append(candidate)
            if len(result) > MAX_FILES:
                raise Refused('Local collection file limit exceeded')
    return sorted(result)


def directories(root, deadline):
    result = {}
    def failed(_error):
        raise Refused('Local material directory enumeration refused')
    for start in [root / '.lab/upstream', root / '.secrets/upstream']:
        # State subtrees besides Functions are outside this local collection.
        for parent, children, _files in os.walk(start, followlinks=False, onerror=failed):
            clock(deadline)
            path = Path(parent)
            descriptor = open_path(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                value = os.fstat(descriptor)
                result[str(path.relative_to(root))] = (value.st_dev, value.st_ino, value.st_mtime_ns,
                                                      value.st_ctime_ns, value.st_mode, value.st_uid)
            finally:
                os.close(descriptor)
            if start.name == 'upstream' and start.parent.name == '.lab' and path == start:
                children[:] = [name for name in children if name == 'functions']
            for child in children:
                if (path / child).is_symlink():
                    raise Refused('Linked local material directory refused')
            if len(result) > MAX_FILES:
                raise Refused('Local collection directory limit exceeded')
    return result


def sources(root, deadline):
    state, private = root / '.lab/upstream', root / '.secrets/upstream'
    # Complete private files and Functions retained code are local data, even
    # when a service is disabled. No enabled-state assumption discards them.
    paths = tree(private, deadline) + tree(state / 'functions', deadline)
    paths += [p for p in state.iterdir() if p.name.endswith('.json')]
    paths += [state / 'control.sqlite']
    paths += [p for p in (root / 'lab').glob('*image*.lock.json')]
    paths = sorted(set(paths))
    if len(paths) > MAX_FILES:
        raise Refused('Local collection file limit exceeded')
    return paths


def preflight_parser():
    executable = shutil.which('node')
    if executable is None:
        raise Refused('Node.js 22.6 or newer is required for local Functions collection')
    try:
        version = subprocess.run([executable, '--version'], text=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=1, check=True).stdout.strip()
        match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', version)
        if not match or (int(match[1]), int(match[2])) < (22, 6):
            raise Refused('Node.js 22.6 or newer is required for local Functions collection')
        ready = subprocess.run([executable,
                                str(Path(__file__).with_name('functions-local-closure.mjs')), '--preflight'],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3, check=True)
        if json.loads(ready.stdout).get('status') != 'parser-ready':
            raise Refused('Pinned Functions parser toolchain unavailable')
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Refused('Node.js and pinned TypeScript parser toolchain unavailable') from None


PARSER_COMMAND = ['node', str(Path(__file__).with_name('functions-local-closure.mjs'))]


def parser(request, deadline):
    timeout = min(3, deadline - time.monotonic())
    if timeout <= 0:
        raise Refused('Local collection deadline expired')
    child = subprocess.Popen(PARSER_COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True,
                             cwd=Path(__file__).resolve().parent.parent)
    try:
        stdout, _stderr = child.communicate(json.dumps(request), timeout=timeout)
        observed = json.loads(stdout)
        if child.returncode or observed.get('status') != 'known-local-closure-checked':
            raise Refused('Known local Functions dependency missing or unsupported')
        return observed['external']
    except subprocess.TimeoutExpired:
        # A constructor failure may leave a compiler child holding stdout/stderr.
        # Terminate this owned process group, not only its parent helper.
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=0.5)
        raise Refused('Functions parser deadline expired') from None
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=0.5)


def functions(root, payloads, runtimes, deadline=None):
    """Validate every deployed immutable version and its shared import closure."""
    entries_for_parser = []
    for runtime in runtimes:
        prefix = '.lab/upstream/functions/' + runtime + '/'
        present = [name for name in payloads if name.startswith(prefix)]
        if not present:
            continue
        manifest_name = prefix + 'functions.json'
        if manifest_name not in payloads:
            raise Refused('Retained Functions code has no deployment manifest')
        try:
            value = json.loads(payloads[manifest_name])
            entries = value['functions']
            if type(entries) is not dict:
                raise ValueError()
            for name, entry in entries.items():
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
                    raise ValueError()
                version = entry['version']
                if not isinstance(version, str) or not re.fullmatch(r'v[a-z0-9]+-[a-f0-9]{8}', version):
                    raise ValueError()
                if type(entry['verify_jwt']) is not bool:
                    raise ValueError()
                entries_for_parser.append({'root': prefix.rstrip('/'), 'name': name, 'version': version})
            secret_name = '.secrets/upstream/functions/' + runtime + '/secrets.json'
            if secret_name not in payloads or type(json.loads(payloads[secret_name])) is not dict:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise Refused('Functions deployment closure or secrets incomplete') from None
    for name in payloads:
        for prefix in ('.lab/upstream/functions/', '.secrets/upstream/functions/'):
            if name.startswith(prefix) and name[len(prefix):].split('/')[0] not in runtimes:
                raise Refused('Functions material belongs to an unknown runtime')
    if entries_for_parser:
        try:
            code = {name: payload.decode('utf-8') for name, payload in payloads.items()
                    if name.startswith('.lab/upstream/functions/') and name != '.lab/upstream/functions/'}
            return parser({'files': code, 'entries': entries_for_parser},
                          deadline if deadline is not None else time.monotonic() + 3)
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
            raise Refused('Known local Functions dependency missing or unsupported') from None
    return []


def collect(root, output, *, timeout=30, max_bytes=MAX_BYTES):
    """Publish a private, explicitly incomplete collection of original stores."""
    if type(timeout) not in (int, float) or not 0 < timeout <= 60 or type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
        raise Refused('Finite local collection limits required')
    root, output = Path(root), Path(output)
    if not root.is_absolute() or not output.is_absolute() or '..' in root.parts or '..' in output.parts or output == root or root in output.parents:
        raise Refused('Collection destination must be outside source installation')
    deadline, budget = time.monotonic() + timeout, [max_bytes]
    preflight_parser()
    # Holding both original read transactions prevents SQLite commits while
    # their individual complete snapshots and surrounding local files are read.
    # This does not establish native writer quiescence or cross-store authority.
    with private_root(output.parent) as parent, contextlib.ExitStack() as stack:
        if output.exists() or output.is_symlink():
            raise Refused('Collection destination already exists')
        paths = sources(root, deadline)
        before = {str(p.relative_to(root)): witness(p) for p in paths}
        before_directories = directories(root, deadline)
        payloads = {}
        databases = {'.lab/upstream/control.sqlite': 'catalog',
                     '.secrets/upstream/managed-keys.sqlite': 'managed-keys'}
        handles = {}
        for name, kind in databases.items():
            path = root / name
            if name not in before or before[name][5] != 0o600:
                raise Refused('Complete private Catalog and KeyStore required')
            connection = stack.enter_context(contextlib.closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)))
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            connection.execute('SELECT count(*) FROM sqlite_master').fetchone()
            handles[name] = connection
        for name, connection in handles.items():
            clock(deadline)
            if before[name][2] > budget[0]:
                raise Refused('Local collection byte limit exceeded')
            payload = connection.serialize()
            if len(payload) > budget[0]:
                raise Refused('Local collection byte limit exceeded')
            material = sqlite_material.decode(databases[name], payload, deadline, max_bytes=max_bytes)
            payloads[name] = material.payload
            budget[0] -= len(payload)
        rows = handles['.lab/upstream/control.sqlite'].execute('SELECT environment,runtime,state FROM provision_jobs ORDER BY environment').fetchall()
        runtimes = {runtime for _, runtime, _ in rows}
        if not rows or any(not isinstance(runtime, str) or not RUNTIME.fullmatch(runtime) for runtime in runtimes):
            raise Refused('Original application runtime inventory required')
        for path in paths:
            name = str(path.relative_to(root))
            if name not in databases:
                payloads[name] = read(path, deadline, budget, private=name.startswith('.secrets/'))
        if runtime_files.runtime_secret_relative() not in payloads:
            raise Refused('Original private runtime configuration required')
        external_modules = functions(root, payloads, runtimes, deadline)
        # Verify enumeration and identities again before any publication.
        if (paths != sources(root, deadline) or before != {str(p.relative_to(root)): witness(p) for p in paths}
                or before_directories != directories(root, deadline)):
            raise Refused('Source installation changed during collection')
        temporary_name = '.installation-collection-' + secrets.token_hex(16)
        os.mkdir(temporary_name, 0o700, dir_fd=parent)
        temporary = Path('/proc/self/fd') / str(parent) / temporary_name
        original = temporary.stat()
        try:
            materials = []
            for name, payload in sorted(payloads.items()):
                clock(deadline)
                file = temporary / name
                file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                directory = file.parent
                while directory != temporary:
                    directory.chmod(0o700)
                    directory = directory.parent
                descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                materials.append({'name': name, 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
            manifest = {'schema': 1, 'format': 'sbarbase-local-installation-collection-v1',
                        'complete': False, 'encrypted': False, 'restore_admitted': False,
                        'missing_suppliers': MISSING, 'materials': materials,
                        'runtimes': sorted(runtimes), 'sqlite_scope': 'individual-read-transactions',
                        'external_function_modules_unverified': external_modules}
            file = temporary / 'collection.json'
            with file.open('x', encoding='utf-8') as handle:
                os.chmod(file, 0o600)
                json.dump(manifest, handle, separators=(',', ':')); handle.flush(); os.fsync(handle.fileno())
            if (paths != sources(root, deadline) or before != {str(p.relative_to(root)): witness(p) for p in paths}
                or before_directories != directories(root, deadline)):
                raise Refused('Source installation changed before publication')
            clock(deadline)
            publish_exact(parent, temporary_name, output.name)
            os.fsync(parent)
            return {'status': 'local-collection-only', 'complete': False, 'encrypted': False,
                    'restore_admitted': False, 'files': len(materials), 'missing_suppliers': MISSING}
        finally:
            if temporary.exists():
                current = temporary.stat()
                if (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino):
                    shutil.rmtree(temporary)


def capture(*_args, **_kwargs):
    raise Refused('Complete installation packaging requires genuine native suppliers; local collection cannot replace them')
