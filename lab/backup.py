"""Per-environment backup, fenced replacement and explicit identity-bound recovery.

    backup.py create <environment|all> [--keep N] [--local-only] [--reason upgrade]
    backup.py list [<environment>]
    backup.py restore <environment> <backup> [--offsite]
    backup.py restore-storage <backup> [--offsite]
    backup.py restore-status
    backup.py recover-restore <environment|storage> <backup> <restore-stamp>
    backup.py complete-restore <environment|storage> <backup> <restore-stamp>
    backup.py discard-previous <environment|storage>
    backup.py offsite-list
    backup.py offsite-fetch <backup>
    backup.py offsite-key <path>

<environment> is the runtime id (``e_`` and 24 hex, the ``apiPath`` of the connection page)
or the environment's id from the console.

A backup is a directory under ``.lab/backups/<runtime>/<UTC time>/`` with the database
(``pg_dump`` custom format, taken inside one snapshot while the environment serves), the
environment's Storage files (a tar of its own tenant directory only) and ``manifest.json``,
written last, with sizes, SHA-256 digests and row counts. A directory without a manifest is
an interrupted backup and is never restored.

Restore stages the archive behind a PostgreSQL connection fence. Selected app services and
shared Storage stop before cutover, so neighboring Storage access also pauses. Old data and
files stay aside. An interruption requires guarded ``recover-restore``; no implicit rollback
runs. A hard kill may print nothing: ``restore-status`` reads private operation state and
shows exact recovery commands. After reopening, new writes survive recovery and readiness
retry. Use ``complete-restore`` for a printed readiness failure, without replaying data.

``create all`` gives every backup of the run one time, backs up Storage's shared metadata
database (``storage_metadata``: every environment's Storage registration, its signing keys and
the migration state Storage records per tenant) to ``.lab/backups/storage/<UTC time>/``, and
writes the installation manifest (``.lab/backups/installation/<UTC time>/``). Storage migrates
that database when a new image starts, so an upgrade that changes the Storage pin is undone
completely only with ``restore-storage`` from the same run as the environments' restores.
``restore-storage`` stops the shared Storage process for the length of the restore (every
environment's Storage pauses; nothing else does) and refuses a backup that does not register
an environment published now, since restoring it would drop that environment's registration.
``--reason upgrade`` (``lab/upgrade.py`` before it moves the checkout) marks every manifest of the run: the backups of the last ``UPGRADE_RUNS_KEPT`` upgrades
that moved the checkout (``lab/upgrade.py`` marks those, ``mark_moved``) are the way back to data a
newer version migrated, so count-based pruning never removes them and does not count them against
``--keep``. A try that stopped before it moved leaves an ordinary run. Two independent ways copy
backups off this host, each active only when configured: ``lab/offsite.py`` copies each new backup, encrypted, to S3-compatible
storage, and ``.lab/upstream/backup-offsite.json`` makes ``backup_offsite.py`` copy the daily run
as one encrypted set. A failed copy is reported and never changes a local backup. ``restore
--offsite`` fetches a ``backup_offsite.py`` set that is not on this host first.
``docs/guides/backup-and-restore.md`` is the operator's guide.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import image_identity

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.lab' / 'upstream'
BACKUPS = ROOT / '.lab' / 'backups'
PREFIX = 'sbarbase-durable'
DB = PREFIX + '-db'
DATABASE_OWNER = 'durable-upstream'
OBJECTS_VOLUME = PREFIX + '-objects'
# storage-files.cjs and the Storage file backend keep a tenant's files here in the volume.
TENANT_PARENT = 'sbarbase-lab'
# Storage's shared metadata database (lab/durable_runtime.py MULTITENANT_DATABASE_URL), backed up
# once per run under its own folder, and the one Storage process that holds it open.
STORAGE = 'storage'
STORAGE_DATABASE = 'storage_metadata'
STORAGE_CONTAINER = PREFIX + '-storage'
STORAGE_PORT = 5000
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
STAMP = re.compile(r'\d{8}T\d{6}Z')
DEFAULT_KEEP = 7
REASONS = ('upgrade',)
UPGRADE_RUNS_KEPT = 3


class BackupError(RuntimeError):
    pass


def run(argv, *, stdin=None, stdout=subprocess.PIPE, check=True, text=True, timeout=3600):
    """One external command. Errors name the step, never its output, which may hold data."""
    result = subprocess.run(argv, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=text, timeout=timeout)
    if check and result.returncode:
        raise BackupError(f'{argv[0]} {argv[1] if len(argv) > 1 else ""} failed with exit {result.returncode}')
    return result


def psql(database):
    container = admit_database()
    return ['docker', 'exec', '-i', container, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database]


def sql(query, database='postgres'):
    result = subprocess.run(
        psql(database),
        input=query, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise BackupError('database statement failed')
    return result.stdout.strip()


def image_pin(filename):
    """Read a strict logical pin without consulting the daemon or changing state."""
    try:
        pin = json.loads((ROOT / 'lab' / filename).read_text())
        image_identity.reference(pin)
        return pin
    except (OSError, ValueError, TypeError) as error:
        raise BackupError('Invalid backup image lock: ' + filename) from error


def storage_image():
    """The logical archived pin, never the daemon's configuration identity."""
    return image_pin('storage-image.lock.json')['id']


def resolve_image(pin):
    """Prove the repository-qualified pin and return its daemon-local identity."""
    try:
        reference = image_identity.reference(pin)
        inspected = run(['docker', 'image', 'inspect', reference], check=False)
        if inspected.returncode or inspected.stderr.strip():
            raise image_identity.inspection_failure(reference, inspected.stdout, inspected.stderr)
        native = image_identity.resolved_id(reference, image_identity.record(inspected.stdout))
        return reference, native
    except image_identity.IdentityError as error:
        raise BackupError(str(error)) from error


def resolve_storage_image():
    return resolve_image(image_pin('storage-image.lock.json'))


def admit_database(pin=None):
    """A DB exec is authorized by its exact native image and runtime owner."""
    reference, native = resolve_image(pin if pin is not None else image_pin('distro-image.lock.json'))
    inspected = run(['docker', 'container', 'inspect', DB], check=False)
    if inspected.returncode or inspected.stderr.strip():
        raise BackupError('Database container inspection refused')
    try:
        item = image_identity.record(inspected.stdout)
        if not isinstance(item.get('Id'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['Id']) or item.get('Name') != '/' + DB:
            raise BackupError('Database container identity or name is not proved')
        if item.get('Image') != native or item.get('Config', {}).get('Labels', {}).get('io.sbarbase.owner') != DATABASE_OWNER:
            raise BackupError('Database container image or ownership differs from the configured runtime')
        if item.get('State', {}).get('Running') is not True:
            raise BackupError('Database container is not running')
    except (image_identity.IdentityError, AttributeError, TypeError) as error:
        raise BackupError('Invalid database container inspection') from error
    return item['Id']


def admit_objects_volume():
    """Refuse implicit creation or access to another owner's object volume."""
    inspected = run(['docker', 'volume', 'inspect', OBJECTS_VOLUME], check=False)
    if inspected.returncode or inspected.stderr.strip():
        raise BackupError('Objects volume inspection refused')
    try:
        item = image_identity.record(inspected.stdout)
        if item.get('Name') != OBJECTS_VOLUME or item.get('Labels', {}).get('io.sbarbase.owner') != DATABASE_OWNER:
            raise BackupError('Objects volume identity or ownership differs from the configured runtime')
    except (image_identity.IdentityError, AttributeError, TypeError) as error:
        raise BackupError('Invalid objects volume inspection') from error
    return OBJECTS_VOLUME


def preflight():
    """Prove all backup tools before writing artifacts or altering live data."""
    db = image_pin('distro-image.lock.json')
    storage = image_pin('storage-image.lock.json')
    database = admit_database(db)
    objects = resolve_image(storage)
    volume = admit_objects_volume()
    return {'db': database, 'storage': objects, 'objects_volume': volume}


def helper(script, *args, writable=False, stdin=None, stdout=subprocess.PIPE, text=True):
    """An admitted immutable Storage tool, isolated from network and resource bounded."""
    reference, _native = resolve_storage_image()
    volume = admit_objects_volume()
    mode = '' if writable else ':ro'
    return run(['docker', 'run', '--rm', '--pull=never', '-i', '--network', 'none', '--memory', '256m', '--cpus', '.5',
                '--label', 'io.sbarbase.owner=backup', '-v', f'{volume}:/data{mode}',
                '--entrypoint', 'sh', reference, '-c', script, 'sh', *args],
               stdin=subprocess.DEVNULL if stdin is None else stdin, stdout=stdout, text=text)


def resolve(name, catalog=None):
    """A runtime id from a runtime id or a console environment id."""
    if RUNTIME.fullmatch(name):
        return name
    if not UUID.fullmatch(name):
        raise BackupError('Name an environment by its runtime id (e_...) or its environment id')
    path = Path(catalog or STATE / 'control.sqlite')
    with contextlib.closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as database:
        row = database.execute("SELECT runtime FROM provision_jobs WHERE environment=? AND state='succeeded'",
                               (name,)).fetchone()
    if not row:
        raise BackupError('That environment is not provisioned')
    return row[0]


def published():
    path = STATE / 'endpoints.json'
    return json.loads(path.read_text()) if path.exists() else {}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


COUNTS = ("SELECT (SELECT count(*) FROM auth.users), (SELECT count(*) FROM auth.identities), "
          "(SELECT count(*) FROM storage.buckets), (SELECT count(*) FROM storage.objects);")


def parse_counts(out):
    users, identities, buckets, objects = (int(value) for value in out.split('|'))
    return {'auth.users': users, 'auth.identities': identities, 'storage.buckets': buckets, 'storage.objects': objects}


def counts(e):
    """Rows a restore must bring back exactly: users, identities, buckets, objects."""
    return parse_counts(sql(COUNTS, e))


# Counts and tenant ids only: the other columns hold encrypted credentials and signing keys.
STORAGE_COUNTS = "SELECT count(*), coalesce(string_agg(id, ',' ORDER BY id), '') FROM public.tenants;"


def parse_storage_counts(out):
    count, ids = out.split('|', 1)
    return {'tenants': int(count), 'ids': [item for item in ids.split(',') if item]}


def storage_counts():
    return parse_storage_counts(sql(STORAGE_COUNTS, STORAGE_DATABASE))


@contextlib.contextmanager
def snapshot(e, query=COUNTS, parse=parse_counts):
    """(snapshot id, counts) from one read-only snapshot, held open while pg_dump reads it.

    The counts come from the same snapshot the dump exports, so a sign-up or an upload that
    lands while the environment serves is either in both or in neither. Counting first and
    dumping afterwards let such a row into the dump only, and the restore then refused the
    backup because its rows did not match.
    """
    # A file avoids a filled stderr pipe blocking the held transaction. Diagnostics may
    # contain private data, so refuse them without copying their contents into errors.
    with tempfile.TemporaryFile() as diagnostics:
        process = subprocess.Popen(psql(e), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=diagnostics, text=True)
        session_failed = False
        try:
            process.stdin.write('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n'
                                'SELECT pg_export_snapshot();\n' + query + '\n')
            process.stdin.flush()
            exported = process.stdout.readline().strip()
            line = process.stdout.readline().strip()
            if not re.fullmatch(r'[0-9A-F]+-[0-9A-F]+-[0-9]+', exported) or not line:
                raise BackupError('database snapshot failed')
            if os.fstat(diagnostics.fileno()).st_size:
                raise BackupError('database snapshot emitted diagnostics')
            yield exported, parse(line)
        finally:
            try:
                if process.poll() is None:
                    try:
                        process.stdin.write('COMMIT;\n')
                        process.stdin.close()
                        session_failed = process.wait(timeout=60) != 0
                    except (OSError, subprocess.TimeoutExpired, ValueError):
                        process.kill()
                        process.wait()
                        session_failed = True
                else:
                    session_failed = process.poll() != 0
            finally:
                process.stdin.close()
                process.stdout.close()
            if session_failed or os.fstat(diagnostics.fileno()).st_size:
                raise BackupError('database snapshot failed or emitted diagnostics')


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def ownership(e):
    try:
        from recovery_bundle import catalog_ownership
    except ImportError:
        return None
    return catalog_ownership(STATE / 'control.sqlite', e)


def create(e, keep=DEFAULT_KEEP, now=None, reason=None, protected=None):
    no_pending_completion()
    if reason is not None and reason not in REASONS:
        raise BackupError('Unknown backup reason')
    if e not in published():
        raise BackupError('Only a published environment can be backed up')
    admitted = preflight()
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime('%Y%m%dT%H%M%SZ')
    target = private_dir(BACKUPS / e) / stamp
    if target.exists():
        raise BackupError('A backup with this time already exists')
    private_dir(target)
    # pg_dump reads the snapshot the counts were taken in, while the environment keeps serving.
    fd = os.open(target / 'database.dump', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle, snapshot(e) as (exported, before):
        run(['docker', 'exec', admitted['db'], 'pg_dump', '-U', 'supabase_admin', '-Fc', f'--snapshot={exported}', '-d', e],
            stdout=handle, text=False)
    # Files after the database: a file written in between is extra, never missing.
    fd = os.open(target / 'objects.tar', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        helper(f'cd /data/{TENANT_PARENT} 2>/dev/null && [ -d "$1" ] && exec tar -cf - "$1"; '
               'mkdir -p /tmp/empty/"$1" && cd /tmp/empty && exec tar -cf - "$1"', e, stdout=handle, text=False)
    with tarfile.open(target / 'objects.tar') as archive:
        files = sum(1 for member in archive.getmembers() if member.isfile())
    manifest = {
        'version': 1, 'runtime': e, 'created_at': stamp, 'ownership': ownership(e),
        'database': {'file': 'database.dump', 'bytes': (target / 'database.dump').stat().st_size,
                     'sha256': digest(target / 'database.dump')},
        'objects': {'file': 'objects.tar', 'bytes': (target / 'objects.tar').stat().st_size,
                    'sha256': digest(target / 'objects.tar'), 'files': files},
        'counts': before,
        'images': {'db': json.loads((ROOT / 'lab' / 'distro-image.lock.json').read_text())['id'],
                   'storage': storage_image()},
    }
    if reason is not None:
        manifest['reason'] = reason
    write_private(target / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    prune(e, keep, protected)
    return target, manifest


def create_storage(keep=DEFAULT_KEEP, now=None, reason=None, protected=None):
    """Back up ``storage_metadata`` the way an environment's database is backed up: pg_dump in
    custom format inside one snapshot while Storage keeps serving, and the manifest last. The
    manifest records the tenant count and ids from that snapshot (never another column), which
    a restore checks."""
    no_pending_completion()
    if reason is not None and reason not in REASONS:
        raise BackupError('Unknown backup reason')
    admitted = preflight()
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime('%Y%m%dT%H%M%SZ')
    target = private_dir(BACKUPS / STORAGE) / stamp
    if target.exists():
        raise BackupError('A Storage metadata backup with this time already exists')
    private_dir(target)
    fd = os.open(target / 'database.dump', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle, \
            snapshot(STORAGE_DATABASE, STORAGE_COUNTS, parse_storage_counts) as (exported, before):
        run(['docker', 'exec', admitted['db'], 'pg_dump', '-U', 'supabase_admin', '-Fc', f'--snapshot={exported}',
             '-d', STORAGE_DATABASE], stdout=handle, text=False)
    manifest = {
        'version': 1, 'kind': STORAGE_DATABASE, 'created_at': stamp,
        'database': {'file': 'database.dump', 'name': STORAGE_DATABASE,
                     'bytes': (target / 'database.dump').stat().st_size, 'sha256': digest(target / 'database.dump')},
        'counts': {'tenants': before['tenants']}, 'tenants': before['ids'],
        'images': {'db': json.loads((ROOT / 'lab' / 'distro-image.lock.json').read_text())['id'],
                   'storage': storage_image()},
    }
    if reason is not None:
        manifest['reason'] = reason
    write_private(target / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    prune(STORAGE, keep, protected)
    return target, manifest


def complete_backups(e):
    """Complete backups of one environment, oldest first."""
    folder = BACKUPS / e
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if STAMP.fullmatch(path.name) and (path / 'manifest.json').is_file())


def upgrade_run_times():
    """The times of every backup run taken for an upgrade, across every environment and the
    installation manifest: a run is one time, and any manifest of it marked ``reason: upgrade``
    names it."""
    runs = set()
    if not BACKUPS.is_dir():
        return runs
    for folder in BACKUPS.iterdir():
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not STAMP.fullmatch(path.name) or path.name in runs:
                continue
            try:
                if json.loads((path / 'manifest.json').read_text()).get('reason') == 'upgrade':
                    runs.add(path.name)
            except (OSError, ValueError, AttributeError):
                continue
    return runs


# Upgrade runs whose try moved the checkout (lab/upgrade.py marks them), kept to this many.
MOVED_KEPT = 20


def moved_record():
    return BACKUPS / 'upgrade-moved.json'


def moved_runs():
    """The upgrade runs whose try moved the checkout, or None before any was marked (the
    record did not exist before this rule, so the earlier rule then applies to every run)."""
    try:
        value = json.loads(moved_record().read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # Damaged: keeping more is the safe side, so every upgrade run counts.
        return None
    runs = value.get('runs') if isinstance(value, dict) else None
    return {run for run in runs if isinstance(run, str) and STAMP.fullmatch(run)} if isinstance(runs, list) else None


def mark_moved(stamp):
    """Records that the upgrade whose backup run is ``stamp`` moved the checkout, so its backups
    count among the last upgrades. A try that stopped before (its backup, snapshot or move
    failed) is never marked, and its run is pruned like any other."""
    if not STAMP.fullmatch(str(stamp)):
        raise BackupError('Not a backup run time')
    known = moved_runs()
    # The first record (or one replacing a damaged record) starts from every upgrade run there
    # is: until now each of them counted, and the first mark must not strip their protection.
    earlier = upgrade_run_times() if known is None else known
    runs = sorted(earlier | {stamp})[-MOVED_KEPT:]
    private_dir(BACKUPS)
    partial = moved_record().with_suffix('.pending')
    write_private(partial, json.dumps({'runs': runs}) + '\n')
    os.replace(partial, moved_record())


def upgrade_runs(limit=UPGRADE_RUNS_KEPT, current=None):
    """The times of the last ``limit`` backup runs taken for an upgrade that moved the checkout
    (mark_moved; every upgrade run while nothing was ever marked). ``current`` is the time of an
    upgrade run under way, whose manifests are not all written yet and whose try has not moved
    yet: it counts until the run ends."""
    runs = upgrade_run_times()
    moved = moved_runs()
    if moved is not None:
        runs &= moved
    if current:
        runs.add(current)
    return set(sorted(runs)[-limit:]) if limit > 0 else set()


def prune(e, keep, protected=None):
    """Keep the newest ``keep`` complete backups; interrupted ones older than the newest go too.

    Backups of the last UPGRADE_RUNS_KEPT upgrades are kept whatever their age and are not
    counted: ``keep`` applies to the others."""
    no_pending_completion()
    if keep < 1:
        raise BackupError('Keep at least one backup')
    complete = complete_backups(e)
    protected = upgrade_runs() if protected is None else protected
    completion = completion_path(e) if e == STORAGE or RUNTIME.fullmatch(e) else None
    completed_backup = json.loads(completion.read_text())['backup'] if completion and completion.exists() else None
    doomed = [path for path in complete if path.name not in protected and path.name != completed_backup][:-keep]
    newest = complete[-1].name if complete else ''
    folder = BACKUPS / e
    if folder.is_dir():
        doomed += [path for path in folder.iterdir() if STAMP.fullmatch(path.name)
                   and not (path / 'manifest.json').is_file() and path.name < newest]
    for path in doomed:
        shutil.rmtree(path)
    return doomed


def verify(e, path, *, staged_stamp=None):
    """The manifest of a complete backup of this environment whose files match their digests."""
    if staged_stamp is None:
        admitted = STAMP.fullmatch(path.name) and path.parent == BACKUPS / e
    else:
        admitted = (type(staged_stamp) is str and STAMP.fullmatch(staged_stamp)
                    and re.fullmatch(r'\.' + re.escape(staged_stamp) + r'\.fetching-[0-9a-f]{32}', path.name)
                    and path.parent == BACKUPS / e and not path.is_symlink())
    if not admitted:
        raise BackupError('Not a backup of this environment')
    manifest_path = path / 'manifest.json'
    if not manifest_path.is_file():
        raise BackupError('Backup is incomplete')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('version') != 1 or manifest.get('runtime') != e:
        raise BackupError('Backup belongs to another environment or format')
    for part in ('database', 'objects'):
        item = manifest[part]
        file = path / item['file']
        if item['file'] not in ('database.dump', 'objects.tar') or not file.is_file():
            raise BackupError(f'Backup {part} file is missing')
        if file.stat().st_size != item['bytes'] or digest(file) != item['sha256']:
            raise BackupError(f'Backup {part} file does not match its digest')
    return manifest


def verify_storage(path):
    """The manifest of a complete Storage metadata backup whose dump matches its digest."""
    if not STAMP.fullmatch(path.name) or path.parent != BACKUPS / STORAGE:
        raise BackupError('Not a Storage metadata backup')
    manifest_path = path / 'manifest.json'
    if not manifest_path.is_file():
        raise BackupError('Backup is incomplete')
    manifest = json.loads(manifest_path.read_text())
    item = manifest.get('database') if isinstance(manifest, dict) else None
    if not isinstance(item, dict) or manifest.get('version') != 1 or manifest.get('kind') != STORAGE_DATABASE \
            or not isinstance(manifest.get('tenants'), list):
        raise BackupError('Backup belongs to another format')
    file = path / 'database.dump'
    if item.get('file') != 'database.dump' or not file.is_file():
        raise BackupError('Backup database file is missing')
    if file.stat().st_size != item.get('bytes') or digest(file) != item.get('sha256'):
        raise BackupError('Backup database file does not match its digest')
    return manifest


def wait_healthy(e, timeout=180):
    endpoints = published().get(e, {})
    deadline = time.time() + timeout
    import urllib.request
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(endpoints['auth'] + '/health', timeout=5) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(2)
    raise BackupError('Auth did not come back after the restore')


def service_names(e):
    """The environment's own services; Realtime too when the environment runs it."""
    names = [f'{PREFIX}-{e}-auth', f'{PREFIX}-{e}-rest']
    if published().get(e, {}).get('realtime'):
        names.append(f'{PREFIX}-{e}-realtime')
    return names


def start_services(e):
    """Start Auth and REST again and publish their addresses, which a restart may change."""
    run(['docker', 'start', *service_names(e)])
    import durable_runtime
    path = STATE / 'endpoints.json'
    endpoints = json.loads(path.read_text())
    for service, port in (('auth', 9999), ('rest', 3000), ('realtime', durable_runtime.REALTIME_PORT)):
        if service == 'realtime' and not endpoints[e].get('realtime'):
            continue
        item = durable_runtime.inspect('container', f'{PREFIX}-{e}-{service}')
        address = item['NetworkSettings']['Networks'][durable_runtime.NETWORK]['IPAddress'] if item else ''
        if not address:
            raise BackupError(f'{service} has no address after the restore')
        if service == 'realtime':
            endpoints[e]['realtime']['url'] = f'http://{address}:{port}'
        else:
            endpoints[e][service] = f'http://{address}:{port}'
    durable_runtime.atomic(path, endpoints)



def atomic_private(path, value):
    """Persist private control state and its directory entry before returning."""
    if path.parent.is_symlink() or path.is_symlink():
        raise BackupError('Restore completion path is unverifiable')
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.completion-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def completion_path(scope):
    if not isinstance(scope, str) or (scope != STORAGE and not RUNTIME.fullmatch(scope)):
        raise BackupError('Invalid restore completion scope')
    return STATE / 'restore-completions' / (scope + '.json')


def no_pending_completion():
    import restore_operation
    for path in STATE.glob('restore-operation-*.json'):
        scope = path.name[len('restore-operation-'):-len('.json')]
        journal = restore_operation.read_journal(STATE, scope)
        if journal['phase'] not in ('completed', 'rolled-back'):
            raise BackupError('Restore operation is pending; use recover-restore before another backup, restore, prune or discard')
    folder = STATE / 'restore-completions'
    if not folder.exists():
        return
    if folder.is_symlink() or not folder.is_dir():
        raise BackupError('Unverifiable restore completion state')
    completed = []
    for path in folder.iterdir():
        if path.name.startswith('.completion-'):
            raise BackupError('Interrupted restore completion persistence requires review')
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            raise BackupError('Unverifiable restore completion state') from None
        if not isinstance(record, dict) or record.get('status') != 'completed':
            raise BackupError('Restore readiness is pending; use complete-restore before another restore, prune or discard')
        if path.is_symlink() or not isinstance(record.get('scope'), str) or path.name != record['scope'] + '.json':
            raise BackupError('Unverifiable restore completion state')
        completed.append(record)
    # Refuse all pending state before inspecting any completed identity.
    for record in completed:
        checkpoint, archive, success_record = load_completion(record.get('scope', ''), record.get('backup', ''), record.get('stamp', ''))
        verify_completion_success(archive, checkpoint['stamp'], success_record)


def database_oid(database):
    value = sql(f"SELECT oid FROM pg_database WHERE datname='{database}';")
    if not re.fullmatch(r'[1-9][0-9]*', value):
        raise BackupError('Restored database identity is unavailable')
    return value


def checkpoint_restore(scope, name, stamp, admitted, record, storage_cid=None):
    database = STORAGE_DATABASE if scope == STORAGE else scope
    path = BACKUPS / scope / name
    checkpoint = {'version': 1, 'status': 'readiness-pending', 'scope': scope,
                  'backup': name, 'stamp': stamp, 'database': database,
                  'container_id': admitted['db'], 'database_oid': database_oid(database),
                  'manifest_sha256': digest(path / 'manifest.json'),
                  'database_sha256': digest(path / 'database.dump'),
                  'objects_sha256': None if scope == STORAGE else digest(path / 'objects.tar'),
                  'record': record}
    if storage_cid is not None:
        checkpoint['version'] = 2
        checkpoint['storage_container_id'] = storage_cid
    atomic_private(completion_path(scope), checkpoint)
    return checkpoint


def load_completion(scope, name, stamp):
    """Validate durable archive and checkpoint identity, without touching live data."""
    path = completion_path(scope)
    if not isinstance(name, str) or not isinstance(stamp, str) or not STAMP.fullmatch(name) or not re.fullmatch(r'[0-9]{8}t[0-9]{6}z', stamp):
        raise BackupError('Invalid restore completion identity')
    try:
        if path.parent.is_symlink() or not path.parent.is_dir() or path.parent.stat().st_mode & 0o777 != 0o700 \
                or path.is_symlink() or path.stat().st_mode & 0o777 != 0o600:
            raise BackupError('Restore completion checkpoint is not private')
        checkpoint = json.loads(path.read_text())
    except (OSError, ValueError):
        raise BackupError('Restore completion checkpoint is missing or invalid') from None
    required = {'version', 'status', 'scope', 'backup', 'stamp', 'database', 'container_id',
                'database_oid', 'manifest_sha256', 'database_sha256', 'objects_sha256', 'record'}
    if isinstance(checkpoint, dict) and checkpoint.get('version') == 2:
        required.add('storage_container_id')
    database = STORAGE_DATABASE if scope == STORAGE else scope
    if not isinstance(checkpoint, dict) or set(checkpoint) != required or type(checkpoint['version']) is not int or checkpoint['version'] not in (1, 2) \
            or checkpoint['status'] not in ('readiness-pending', 'completed') \
            or (checkpoint['scope'], checkpoint['backup'], checkpoint['stamp'], checkpoint['database']) != (scope, name, stamp, database):
        raise BackupError('Restore completion checkpoint identity differs')
    if not isinstance(checkpoint['container_id'], str) or not re.fullmatch(r'[a-f0-9]{64}', checkpoint['container_id']) \
            or not isinstance(checkpoint['database_oid'], str) or not re.fullmatch(r'[1-9][0-9]*', checkpoint['database_oid']):
        raise BackupError('Restore completion native identity is malformed')
    if checkpoint['version'] == 2 and (not isinstance(checkpoint['storage_container_id'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', checkpoint['storage_container_id'])):
        raise BackupError('Restore completion Storage identity is malformed')
    archive = BACKUPS / scope / name
    manifest = verify_storage(archive) if scope == STORAGE else verify(scope, archive)
    record = checkpoint['record']
    expected = {'restored_at': stamp, 'backup': name, 'previous_database': database + '_pre_' + stamp,
                'counts': manifest['counts']}
    if scope != STORAGE:
        expected['previous_files'] = '.pre-restore-' + scope + '-' + stamp
    if record != expected or checkpoint['manifest_sha256'] != digest(archive / 'manifest.json') \
            or checkpoint['database_sha256'] != digest(archive / 'database.dump') \
            or checkpoint['objects_sha256'] != (None if scope == STORAGE else digest(archive / 'objects.tar')):
        raise BackupError('Restore completion archive identity differs')
    return checkpoint, archive, record


def verify_completion_success(archive, stamp, record):
    success = archive / ('restore-' + stamp + '.json')
    try:
        if success.is_symlink() or success.stat().st_mode & 0o777 != 0o600 or json.loads(success.read_text()) != record:
            raise BackupError('Restore completion success record differs')
    except (OSError, ValueError):
        raise BackupError('Restore completion success record is unavailable') from None


def complete_restore(scope, name, stamp):
    """Retry readiness only, never replay archive, SQL replacement or file extraction."""
    checkpoint, archive, record = load_completion(scope, name, stamp)
    admitted = preflight()
    if admitted['db'] != checkpoint['container_id'] or database_oid(checkpoint['database']) != checkpoint['database_oid']:
        raise BackupError('Restore completion database identity changed')
    if scope != STORAGE and scope not in published():
        raise BackupError('Restore completion environment is no longer published')
    if checkpoint['status'] == 'completed':
        verify_completion_success(archive, stamp, record)
        return record
    if checkpoint['version'] == 2:
        import restore_cutover
        import restore_operation
        journal = restore_operation.read_journal(STATE, scope)
        if (journal['backup'], journal['stamp'], journal['stage_oid'], journal['storage_cid']) != \
                (name, stamp, checkpoint['database_oid'], checkpoint['storage_container_id']) \
                or journal['phase'] not in ('opened', 'completed'):
            raise BackupError('Restore cutover requires guarded recover-restore before readiness')
        restore_cutover.storage(sys.modules[__name__], checkpoint['storage_container_id'])
    try:
        if scope == STORAGE:
            wait_storage(start_storage())
        else:
            if checkpoint['version'] == 2:
                wait_storage(start_storage())
            start_services(scope)
            wait_healthy(scope)
        atomic_private(archive / ('restore-' + stamp + '.json'), record)
        checkpoint['status'] = 'completed'
        atomic_private(completion_path(scope), checkpoint)
        if checkpoint['version'] == 2:
            journal['phase'] = 'completed'
            restore_operation.publish(STATE, journal, atomic_private)
    except Exception:
        raise BackupError(f'Data restored; readiness remains pending. Preserve current writes and retry: '
                          f'backup.py complete-restore {scope} {name} {stamp}') from None
    return record


def start_storage():
    """Start Storage again and publish its address for every environment; a restart may change it."""
    run(['docker', 'start', STORAGE_CONTAINER])
    import durable_runtime
    item = durable_runtime.inspect('container', STORAGE_CONTAINER)
    address = item['NetworkSettings']['Networks'][durable_runtime.NETWORK]['IPAddress'] if item else ''
    if not address:
        raise BackupError('Storage has no address after the restore')
    path = STATE / 'endpoints.json'
    endpoints = json.loads(path.read_text()) if path.exists() else {}
    for entry in endpoints.values():
        if isinstance(entry, dict) and isinstance(entry.get('storage'), dict):
            entry['storage']['url'] = f'http://{address}:{STORAGE_PORT}'
    durable_runtime.atomic(path, endpoints)
    return address


def wait_storage(address, timeout=180):
    """Storage listens once its start, its own migrations included, is done; seeing that needs no key."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((address, STORAGE_PORT), timeout=5):
                return True
        except OSError:
            time.sleep(2)
    raise BackupError('Storage did not come back after the restore')


def discard_previous_storage():
    """Drop the Storage metadata databases that restores set aside."""
    no_pending_completion()
    preflight()
    names = [line for line in sql(f"SELECT datname FROM pg_database WHERE datname LIKE "
                                  f"'{STORAGE_DATABASE}\\_pre\\_%' ESCAPE '\\';").splitlines() if line]
    for name in names:
        if not re.fullmatch(STORAGE_DATABASE + r'_pre_\d{8}t\d{6}z', name):
            raise BackupError('Unexpected database name')
        sql(f'DROP DATABASE {name} WITH (FORCE);')
    return names


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def restore(e, name, now=None):
    import restore_cutover
    return restore_cutover.restore(sys.modules[__name__], e, name, now or datetime.datetime.now(datetime.UTC))


def restore_storage(name, now=None):
    import restore_cutover
    return restore_cutover.restore(sys.modules[__name__], STORAGE, name, now or datetime.datetime.now(datetime.UTC))


def recover_restore(scope, name, stamp):
    import restore_cutover
    return restore_cutover.recover(sys.modules[__name__], scope, name, stamp)


def restore_status():
    """Read private state and return sanitized exact commands, without effects."""
    import restore_operation
    if STATE.is_symlink():
        raise BackupError('Restore status state is not private')
    if not STATE.exists():
        return []
    if STATE.is_symlink() or not STATE.is_dir() or STATE.stat().st_mode & 0o777 != 0o700:
        raise BackupError('Restore status state is not private')
    pending = {}
    try:
        for path in sorted(STATE.glob('restore-operation-*.json')):
            scope = path.name[len('restore-operation-'):-len('.json')]
            journal = restore_operation.read_journal(STATE, scope)
            if journal['phase'] not in ('completed', 'rolled-back'):
                pending[scope] = {'scope': scope, 'phase': journal['phase'], 'command':
                    'python3 lab/backup.py recover-restore ' + scope + ' ' + journal['backup'] + ' ' + journal['stamp']}
    except restore_operation.OperationError:
        raise BackupError('Restore status operation identity is unverifiable') from None
    folder = STATE / 'restore-completions'
    if folder.is_symlink():
        raise BackupError('Restore status completion folder is unverifiable')
    if folder.exists():
        if not folder.is_dir() or folder.stat().st_mode & 0o777 != 0o700:
            raise BackupError('Restore status completion folder is unverifiable')
        for path in sorted(folder.iterdir()):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
                raise BackupError('Restore status completion identity is unverifiable')
            try:
                reference = json.loads(path.read_text())
            except (OSError, ValueError):
                raise BackupError('Restore status completion identity is unverifiable') from None
            if not isinstance(reference, dict) or path.name != str(reference.get('scope')) + '.json':
                raise BackupError('Restore status completion identity is unverifiable')
            checkpoint, archive, record = load_completion(reference.get('scope'), reference.get('backup'), reference.get('stamp'))
            if checkpoint['status'] == 'completed':
                verify_completion_success(archive, checkpoint['stamp'], record)
            elif checkpoint['scope'] not in pending:
                scope = checkpoint['scope']
                pending[scope] = {'scope': scope, 'phase': 'readiness-pending', 'command':
                    'python3 lab/backup.py complete-restore ' + scope + ' ' + checkpoint['backup'] + ' ' + checkpoint['stamp']}
    return [pending[key] for key in sorted(pending)]


def discard_previous(e):
    """Drop what restores set aside for this environment, once the operator is satisfied."""
    no_pending_completion()
    preflight()
    names = [line for line in sql(f"SELECT datname FROM pg_database WHERE datname LIKE '{e}\\_pre\\_%' ESCAPE '\\';").splitlines() if line]
    for name in names:
        if not re.fullmatch(e + r'_pre_\d{8}t\d{6}z', name):
            raise BackupError('Unexpected database name')
        sql(f'DROP DATABASE {name} WITH (FORCE);')
    helper(f'cd /data/{TENANT_PARENT} 2>/dev/null || exit 0; for d in .pre-restore-"$1"-*; do [ -e "$d" ] && rm -rf "$d"; done; exit 0',
           e, writable=True)
    return names


def environments():
    return sorted(e for e in published() if RUNTIME.fullmatch(e))


# Exit code of `create all` when every local backup succeeded but the off-host copy failed
# and was already reported as backup.failed.
OFFSITE_FAILED = 3

def main(argv=None):
    parser = argparse.ArgumentParser(description='Per-environment backup and restore')
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('create')
    make.add_argument('environment')
    make.add_argument('--keep', type=int, default=DEFAULT_KEEP)
    make.add_argument('--local-only', action='store_true', help='do not copy the new backups off the server')
    make.add_argument('--reason', choices=REASONS,
                      help='why the backup is taken: upgrade keeps the backups of the last upgrades out of pruning')
    listing = sub.add_parser('list')
    listing.add_argument('environment', nargs='?')
    back = sub.add_parser('restore')
    back.add_argument('environment')
    back.add_argument('backup')
    back.add_argument('--offsite', action='store_true', help='fetch the backup from the off-host target first')
    shared = sub.add_parser('restore-storage', help="replace Storage's shared metadata database with a backup of it")
    shared.add_argument('backup')
    shared.add_argument('--offsite', action='store_true', help='fetch the backup from the off-host target first')
    completion = sub.add_parser('complete-restore', help='retry restore readiness without replacing data')
    completion.add_argument('environment')
    completion.add_argument('backup')
    completion.add_argument('stamp')
    recovery = sub.add_parser('recover-restore', help='recover a fenced cutover from native identities')
    recovery.add_argument('environment')
    recovery.add_argument('backup')
    recovery.add_argument('stamp')
    sub.add_parser('restore-status', help='show exact pending recovery commands without changing state')
    drop = sub.add_parser('discard-previous')
    drop.add_argument('environment')
    sub.add_parser('offsite-list')
    pull = sub.add_parser('offsite-fetch')
    pull.add_argument('backup')
    key = sub.add_parser('offsite-key')
    key.add_argument('path')
    args = parser.parse_args(argv)
    try:
        if args.command == 'restore-status':
            pending = restore_status()
            for item in pending:
                print(item['scope'] + ' ' + item['phase'] + ': ' + item['command'])
            if not pending:
                print('No pending restore operation')
            return 0
        if args.command in ('offsite-list', 'offsite-key'):
            import backup_offsite
            if args.command == 'offsite-key':
                path = backup_offsite.new_key(args.path)
                print(f'wrote a new off-host key to {path} (mode 0600); keep a copy away from this server')
                return 0
            for name in backup_offsite.list_remote():
                print(name)
            return 0
        if args.command == 'list':
            for e in ([resolve(args.environment)] if args.environment else environments()):
                for path in complete_backups(e):
                    manifest = json.loads((path / 'manifest.json').read_text())
                    print(f"{e}  {path.name}  database {manifest['database']['bytes']} B  "
                          f"files {manifest['objects']['files']}  users {manifest['counts']['auth.users']}"
                          + ('  before an upgrade' if manifest.get('reason') == 'upgrade' else ''))
            if not args.environment:
                for path in complete_backups(STORAGE):
                    manifest = json.loads((path / 'manifest.json').read_text())
                    print(f"{STORAGE_DATABASE}  {path.name}  database {manifest['database']['bytes']} B  "
                          f"tenants {manifest['counts']['tenants']}"
                          + ('  before an upgrade' if manifest.get('reason') == 'upgrade' else ''))
            return 0
        if args.command in ('create', 'restore', 'restore-storage', 'discard-previous'):
            preflight()
        private_dir(BACKUPS)
        with (STATE / 'backup.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BackupError('Another backup or restore is running')
            if args.command == 'create':
                every = args.environment == 'all'
                targets = environments() if every else [resolve(args.environment)]
                # One time for the whole run, so the run is one set here and off this host.
                now = datetime.datetime.now(datetime.UTC).replace(microsecond=0)
                stamp = now.strftime('%Y%m%dT%H%M%SZ')
                # Read once for the run, which counts among the last upgrades when it is one.
                protected = upgrade_runs(current=stamp if args.reason == 'upgrade' else None)
                failed, created = 0, []
                for e in targets:
                    try:
                        path, manifest = create(e, args.keep, now=now, reason=args.reason, protected=protected)
                        created.append(e)
                        print(f"backup {e} {path.name}: database {manifest['database']['bytes']} B, "
                              f"{manifest['objects']['files']} file(s), {manifest['counts']['auth.users']} user(s)")
                    except BackupError as error:
                        failed += 1
                        print(f'backup {e} failed: {error}', file=sys.stderr)
                copied = None
                if every:
                    # Storage's shared database, in the same run: a restore of the run brings the
                    # registrations back with the environments they belong to.
                    try:
                        path, manifest = create_storage(args.keep, now=now, reason=args.reason, protected=protected)
                        print(f"backup {STORAGE_DATABASE} {path.name}: database {manifest['database']['bytes']} B, "
                              f"{manifest['counts']['tenants']} tenant(s)")
                    except BackupError as error:
                        failed += 1
                        print(f'backup {STORAGE_DATABASE} failed: {error}', file=sys.stderr)
                    offsite = None
                    try:
                        import backup_offsite as offsite
                        offsite.write_installation(stamp, created, args.keep, reason=args.reason, protected=protected)
                        print(f'installation manifest {stamp} written')
                    except Exception as error:
                        failed += 1
                        reason = str(error) if isinstance(error, BackupError) else type(error).__name__
                        print(f'installation manifest failed: {reason}', file=sys.stderr)
                    if offsite is not None and not args.local_only:
                        # A failed copy is notified by after_run itself; exit 3 then tells the
                        # supervisor not to also report the run as completed.
                        copied = offsite.after_run(stamp, created, args.keep)
                # With off-site copies configured (lab/offsite.py), each new backup leaves the server too.
                import offsite
                if offsite.load_config() and not args.local_only:
                    try:
                        pushed = offsite.push(targets)
                        print(f'copied {len(pushed)} backup(s) off the server')
                    except Exception as error:
                        failed += 1
                        print(f'off-site copy failed: {error}', file=sys.stderr)
                return 1 if failed else OFFSITE_FAILED if copied is False else 0
            if args.command == 'offsite-fetch':
                import backup_offsite
                placed = backup_offsite.fetch(args.backup)
                print(f"fetched {args.backup}: {', '.join(placed) or 'nothing new, every backup is already here'}")
                return 0
            if args.command == 'complete-restore':
                scope = STORAGE if args.environment == STORAGE else resolve(args.environment)
                complete_restore(scope, args.backup, args.stamp)
                print(f'restore readiness completed for {scope}; current data was preserved')
                return 0
            if args.command == 'recover-restore':
                scope = STORAGE if args.environment == STORAGE else resolve(args.environment)
                result = recover_restore(scope, args.backup, args.stamp)
                print('restore recovery completed for ' + scope + ': ' + result.get('status', 'restored'))
                return 0
            if args.command == 'restore':
                e = resolve(args.environment)
                if args.offsite and not (BACKUPS / e / args.backup).exists():
                    import backup_offsite
                    backup_offsite.fetch(args.backup)
                record = restore(e, args.backup)
                print(f"restored {e} from {args.backup}; the previous state is kept as {record['previous_database']} "
                      f"until: backup.py discard-previous {e}")
                return 0
            if args.command == 'restore-storage':
                if args.offsite and not (BACKUPS / STORAGE / args.backup).exists():
                    import backup_offsite
                    backup_offsite.fetch(args.backup)
                record = restore_storage(args.backup)
                print(f"restored {STORAGE_DATABASE} from {args.backup}; the previous database is kept as "
                      f"{record['previous_database']} until: backup.py discard-previous {STORAGE}")
                return 0
            if args.environment == STORAGE:
                names = discard_previous_storage()
                print(f'discarded {len(names)} previous {STORAGE_DATABASE} database(s)')
                return 0
            e = resolve(args.environment)
            names = discard_previous(e)
            print(f'discarded {len(names)} previous database(s) and their files for {e}')
            return 0
    except BackupError as error:
        print(f'refused: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    # backup_offsite imports this module by name; one module keeps one BackupError class.
    sys.modules['backup'] = sys.modules[__name__]
    sys.exit(main())
