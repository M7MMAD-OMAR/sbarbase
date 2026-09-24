"""Per-environment backup and in-place restore, while every other environment keeps serving.

    backup.py create <environment|all> [--keep N]
    backup.py list [<environment>]
    backup.py restore <environment> <backup> [--offsite]
    backup.py discard-previous <environment>
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

Restore replaces one environment with a backup of itself. Only that environment's Auth and
REST stop; Storage and every other environment keep serving. The current database is renamed
and the current files are moved aside, not deleted, and any failure puts them back. After a
successful restore they are kept until ``discard-previous``.

``create all`` gives every backup of the run one time and also writes the installation manifest
(``.lab/backups/installation/<UTC time>/``). When ``.lab/upstream/backup-offsite.json`` exists,
the run is then encrypted and copied to one S3-compatible bucket (``backup_offsite.py``); a failed
copy is reported and never changes a local backup. ``restore --offsite`` fetches a set that is
not on this host first. ``docs/guides/backup-and-restore.md`` is the operator's guide.
"""
import argparse
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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.lab' / 'upstream'
BACKUPS = ROOT / '.lab' / 'backups'
PREFIX = 'sbarbase-durable'
DB = PREFIX + '-db'
OBJECTS_VOLUME = PREFIX + '-objects'
# storage-files.cjs and the Storage file backend keep a tenant's files here in the volume.
TENANT_PARENT = 'sbarbase-lab'
RUNTIME = re.compile(r'e_[a-f0-9]{24}')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
STAMP = re.compile(r'\d{8}T\d{6}Z')
DEFAULT_KEEP = 7


class BackupError(RuntimeError):
    pass


def run(argv, *, stdin=None, stdout=subprocess.PIPE, check=True, text=True, timeout=3600):
    """One external command. Errors name the step, never its output, which may hold data."""
    result = subprocess.run(argv, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, text=text, timeout=timeout)
    if check and result.returncode:
        raise BackupError(f'{argv[0]} {argv[1] if len(argv) > 1 else ""} failed with exit {result.returncode}')
    return result


def sql(query, database='postgres'):
    result = subprocess.run(
        ['docker', 'exec', '-i', DB, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'supabase_admin', '-d', database],
        input=query, capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise BackupError('database statement failed')
    return result.stdout.strip()


def storage_image():
    return json.loads((ROOT / 'lab' / 'storage-image.lock.json').read_text())['id']


def helper(script, *args, writable=False, stdin=None, stdout=subprocess.PIPE, text=True):
    """A short-lived container of the pinned Storage image with the objects volume mounted.

    No network, small limits, and an owner label so nothing else mistakes it for its own.
    """
    mode = '' if writable else ':ro'
    return run(['docker', 'run', '--rm', '-i', '--network', 'none', '--memory', '256m', '--cpus', '.5',
                '--label', 'io.sbarbase.owner=backup', '-v', f'{OBJECTS_VOLUME}:/data{mode}',
                '--entrypoint', 'sh', storage_image(), '-c', script, 'sh', *args],
               stdin=subprocess.DEVNULL if stdin is None else stdin, stdout=stdout, text=text)


def resolve(name, catalog=None):
    """A runtime id from a runtime id or a console environment id."""
    if RUNTIME.fullmatch(name):
        return name
    if not UUID.fullmatch(name):
        raise BackupError('Name an environment by its runtime id (e_...) or its environment id')
    path = Path(catalog or STATE / 'control.sqlite')
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as database:
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


def counts(e):
    """Rows a restore must bring back exactly: users, identities, buckets, objects."""
    out = sql("SELECT (SELECT count(*) FROM auth.users), (SELECT count(*) FROM auth.identities), "
              "(SELECT count(*) FROM storage.buckets), (SELECT count(*) FROM storage.objects);", e)
    users, identities, buckets, objects = (int(value) for value in out.split('|'))
    return {'auth.users': users, 'auth.identities': identities, 'storage.buckets': buckets, 'storage.objects': objects}


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


def create(e, keep=DEFAULT_KEEP, now=None):
    if e not in published():
        raise BackupError('Only a published environment can be backed up')
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime('%Y%m%dT%H%M%SZ')
    target = private_dir(BACKUPS / e) / stamp
    if target.exists():
        raise BackupError('A backup with this time already exists')
    private_dir(target)
    before = counts(e)
    # pg_dump reads one consistent snapshot while the environment keeps serving.
    fd = os.open(target / 'database.dump', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        run(['docker', 'exec', DB, 'pg_dump', '-U', 'supabase_admin', '-Fc', '-d', e], stdout=handle, text=False)
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
    write_private(target / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
    prune(e, keep)
    return target, manifest


def complete_backups(e):
    """Complete backups of one environment, oldest first."""
    folder = BACKUPS / e
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir()
                  if STAMP.fullmatch(path.name) and (path / 'manifest.json').is_file())


def prune(e, keep):
    """Keep the newest ``keep`` complete backups; interrupted ones older than the newest go too."""
    if keep < 1:
        raise BackupError('Keep at least one backup')
    complete = complete_backups(e)
    doomed = complete[:-keep]
    newest = complete[-1].name if complete else ''
    folder = BACKUPS / e
    if folder.is_dir():
        doomed += [path for path in folder.iterdir() if STAMP.fullmatch(path.name)
                   and not (path / 'manifest.json').is_file() and path.name < newest]
    for path in doomed:
        shutil.rmtree(path)
    return doomed


def verify(e, path):
    """The manifest of a complete backup of this environment whose files match their digests."""
    if not STAMP.fullmatch(path.name) or path.parent != BACKUPS / e:
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


def restore(e, name, now=None):
    if e not in published():
        raise BackupError('Only a published environment can be restored')
    path = BACKUPS / e / name
    manifest = verify(e, path)
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime('%Y%m%dt%H%M%Sz')
    previous = f'{e}_pre_{stamp}'
    aside = f'.pre-restore-{e}-{stamp}'
    moved_database = moved_files = False
    run(['docker', 'stop', *service_names(e)])
    try:
        # Nothing may hold the database while it is renamed; Storage reconnects by name afterwards.
        sql(f"ALTER DATABASE {e} ALLOW_CONNECTIONS false; "
            f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname='{e}';")
        sql(f'ALTER DATABASE {e} RENAME TO {previous};')
        moved_database = True
        with (path / 'database.dump').open('rb') as handle:
            run(['docker', 'exec', '-i', DB, 'pg_restore', '-U', 'supabase_admin', '--create', '--exit-on-error',
                 '-d', 'postgres'], stdin=handle, text=False)
        # The database properties the runtime set, copied from the database being replaced.
        limit, acl = sql(f"SELECT datconnlimit, coalesce(datacl::text,'') FROM pg_database WHERE datname='{previous}';").split('|')
        sql(f'ALTER DATABASE {e} CONNECTION LIMIT {int(limit)}; ALTER DATABASE {e} ALLOW_CONNECTIONS true; '
            f"ALTER DATABASE {previous} ALLOW_CONNECTIONS false;")
        restored_acl = sql(f"SELECT coalesce(datacl::text,'') FROM pg_database WHERE datname='{e}';")
        if restored_acl != acl:
            raise BackupError('Restored database access differs from the environment it replaces')
        if counts(e) != manifest['counts']:
            raise BackupError('Restored rows do not match the backup')
        helper(f'mkdir -p /data/{TENANT_PARENT} && cd /data/{TENANT_PARENT} && '
               'if [ -e "$1" ]; then mv "$1" "$2"; fi', e, aside, writable=True)
        moved_files = True
        with (path / 'objects.tar').open('rb') as handle:
            helper(f'cd /data/{TENANT_PARENT} && tar -xf -', writable=True, stdin=handle, text=False)
    except BaseException:
        if moved_files:
            helper(f'cd /data/{TENANT_PARENT} && rm -rf "$1" && if [ -e "$2" ]; then mv "$2" "$1"; fi',
                   e, aside, writable=True)
        if moved_database:
            sql(f"SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname='{e}';")
            sql(f'DROP DATABASE IF EXISTS {e} WITH (FORCE);')
            sql(f'ALTER DATABASE {previous} RENAME TO {e}; ALTER DATABASE {e} ALLOW_CONNECTIONS true;')
        else:
            sql(f'ALTER DATABASE {e} ALLOW_CONNECTIONS true;')
        start_services(e)
        raise
    start_services(e)
    wait_healthy(e)
    record = {'restored_at': stamp, 'backup': name, 'previous_database': previous, 'previous_files': aside,
              'counts': manifest['counts']}
    write_private(path / f'restore-{stamp}.json', json.dumps(record, indent=2) + '\n')
    return record


def discard_previous(e):
    """Drop what restores set aside for this environment, once the operator is satisfied."""
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
    listing = sub.add_parser('list')
    listing.add_argument('environment', nargs='?')
    back = sub.add_parser('restore')
    back.add_argument('environment')
    back.add_argument('backup')
    back.add_argument('--offsite', action='store_true', help='fetch the backup from the off-host target first')
    drop = sub.add_parser('discard-previous')
    drop.add_argument('environment')
    sub.add_parser('offsite-list')
    pull = sub.add_parser('offsite-fetch')
    pull.add_argument('backup')
    key = sub.add_parser('offsite-key')
    key.add_argument('path')
    args = parser.parse_args(argv)
    try:
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
                          f"files {manifest['objects']['files']}  users {manifest['counts']['auth.users']}")
            return 0
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
                failed, created = 0, []
                for e in targets:
                    try:
                        path, manifest = create(e, args.keep, now=now)
                        created.append(e)
                        print(f"backup {e} {path.name}: database {manifest['database']['bytes']} B, "
                              f"{manifest['objects']['files']} file(s), {manifest['counts']['auth.users']} user(s)")
                    except BackupError as error:
                        failed += 1
                        print(f'backup {e} failed: {error}', file=sys.stderr)
                copied = None
                if every:
                    offsite = None
                    try:
                        import backup_offsite as offsite
                        offsite.write_installation(stamp, created, args.keep)
                        print(f'installation manifest {stamp} written')
                    except Exception as error:
                        failed += 1
                        reason = str(error) if isinstance(error, BackupError) else type(error).__name__
                        print(f'installation manifest failed: {reason}', file=sys.stderr)
                    if offsite is not None:
                        # A failed copy is notified by after_run itself; exit 3 then tells the
                        # supervisor not to also report the run as completed.
                        copied = offsite.after_run(stamp, created, args.keep)
                return 1 if failed else OFFSITE_FAILED if copied is False else 0
            if args.command == 'offsite-fetch':
                import backup_offsite
                placed = backup_offsite.fetch(args.backup)
                print(f"fetched {args.backup}: {', '.join(placed) or 'nothing new, every backup is already here'}")
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
