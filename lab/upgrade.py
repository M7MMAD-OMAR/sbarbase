"""Move this installation to a newer Sbarbase version, with a way back.

Usage (with Docker, put `docker compose exec sbarbase` before each command):
  /usr/bin/python3 lab/upgrade.py check [--to REF]    what would change, and whether it can
  /usr/bin/python3 lab/upgrade.py start [--to REF]    back up, pull images, move the checkout
  /usr/bin/python3 lab/upgrade.py channel [--json]    the newest signed release, and what it takes
  /usr/bin/python3 lab/upgrade.py start --release vX.Y.Z [--allow-class rebuild]
                                                      start onto a verified release from the channel
  /usr/bin/python3 lab/upgrade.py status              the last upgrade and its outcome
  /usr/bin/python3 lab/upgrade.py rollback [--check]  move back to the version before it (--check: only
                                                      say whether it would refuse, and why)

REF defaults to origin/main, fetched first; it is an explicit operator choice, not the
release channel (lab/release_channel.py), and it is neither signature checked nor classified. After `start` or `rollback`, restart Sbarbase
(`docker compose up -d --build`, or `sudo systemctl restart sbarbase`). The next start
replaces Auth, REST and Storage containers whose pinned image or configuration changed,
and nothing else. If that start fails, the supervisor moves the checkout back by itself
and exits, and the restart policy starts the previous version again.

A version that changes the PostgreSQL image is refused: replacing the database container
is lab/migrate-generation.py's job, never an upgrade's. Every environment is backed up
before anything moves, and those backups stay after the upgrade.

The control state (every SQLite store a release may migrate: the control catalog and the key
store) is snapshotted before the checkout moves, and again when the new version starts, before
anything opens it. Until the new version passes its health checks (lab/upgrade_health.py) the
gateway holds application traffic, and the way back restores that snapshot, so nothing is
lost. After confirmation the fix is forward only: `rollback` then keeps the control state as it
is, and refuses when the previous version cannot read its catalog.
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
import time
from pathlib import Path

import durable_runtime as runtime
import hba_generation
import hba_journal
import install_server
import release_channel
import run as lab
import upgrade_guard

ROOT = lab.ROOT
UPGRADES = lab.STATE / 'upgrades'
STATE_FILE = UPGRADES / 'state.json'
# Held for the whole of a start or rollback. Not the installation operation lock: the running
# provisioning worker holds that one for its lifetime, and `start` runs while Sbarbase serves.
LOCK = UPGRADES / 'upgrade.lock'
SNAPSHOTS = UPGRADES / 'snapshots'
KEEP_SNAPSHOTS = 3
# Present while a new version waits for its health checks: the gateway holds application
# traffic (src/gateway/hold.ts). It counts only while the state below says a start is pending.
HOLD = UPGRADES / 'hold'
PENDING = upgrade_guard.PENDING
INTENT = runtime.UPGRADE_INTENT
UPSTREAM = runtime.STATE
# The key store lives with the secrets (lab/upstream-app.ts); it holds key digests only.
KEY_STORE = runtime.PRIVATE / 'managed-keys.sqlite'
SUPERVISOR_LOCK = UPSTREAM / 'supervisor.lock'
BACKUP_LOCK = UPSTREAM / 'backup.lock'
# Records that must be reconciled before anything moves (the same set hba_startup refuses).
UNSETTLED = ('worker-effect.json', hba_journal.NAME, hba_generation.MIGRATION)
DATABASE_LOCK = 'distro-image.lock.json'
# Which lock entry each replaceable service runs, as durable_runtime reads them.
SERVICES = upgrade_guard.SERVICES
DEFAULT_TARGET = 'origin/main'
# Who started an upgrade: an operator on the command line, the console's "update now" or the
# automatic updates (lab/updates.py). Recorded in the state as `trigger`; `automatic` keeps
# meaning that the way back happened by itself.
TRIGGERS = ('cli', 'console', 'automatic')
RESTART = 'docker compose up -d --build   (or: sudo systemctl restart sbarbase)'


class UpgradeError(Exception):
    pass


def git(*args, check=True):
    result = subprocess.run(['git', *args], cwd=ROOT, text=True, capture_output=True)
    if check and result.returncode:
        raise UpgradeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve(ref):
    if ref.startswith('origin/'):
        git('fetch', '-q', 'origin', ref.split('/', 1)[1])
    commit = git('rev-parse', '--verify', '-q', ref + '^{commit}', check=False)
    if not commit:
        raise UpgradeError(f'Unknown version {ref}')
    return commit


def lock_at(commit, name):
    """A lock file as it is at a commit; None when that version has no such file."""
    text = git('show', f'{commit}:lab/{name}', check=False)
    return json.loads(text) if text else None


def entries(lock):
    if not isinstance(lock, dict):
        return {}
    if isinstance(lock.get('id'), str):
        return {'default': lock}
    return {key: value for key, value in lock.items() if isinstance(value, dict) and isinstance(value.get('id'), str)}


def pins_at(commit):
    """The exact image each replaceable service runs at a commit."""
    pins = {}
    for service, (name, key) in SERVICES.items():
        lock = lock_at(commit, name)
        if lock is None:
            # That version does not run this service at all (Realtime before it existed).
            continue
        entry = lock if key is None else lock.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            raise UpgradeError(f'Version {commit[:12]} has no pin for {service}')
        pins[service] = entry['id']
    return pins


def images_at(commit):
    """Every pinned image of a version, as (label, id, pullable reference)."""
    found = []
    for name in install_server.LOCKS:
        for key, entry in entries(lock_at(commit, name)).items():
            digests = [item for item in entry.get('digests') or [] if isinstance(item, str) and '@sha256:' in item]
            found.append((f'{name}:{key}', entry['id'], digests[0] if digests else entry['id']))
    return found


def changes(current, target):
    """Pinned images that differ between two versions, as (label, before, after)."""
    rows = []
    for name in install_server.LOCKS:
        before, after = entries(lock_at(current, name)), entries(lock_at(target, name))
        for key in sorted(set(before) | set(after)):
            old, new = before.get(key, {}), after.get(key, {})
            if old.get('id') != new.get('id'):
                rows.append((f'{name}:{key}', old.get('tag', 'none'), new.get('tag', 'none')))
    return rows


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return None


def save_state(value):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lab.atomic(STATE_FILE, value)


def now():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')


@contextlib.contextmanager
def exclusive(path, refusal, wait=0):
    """Holds an exclusive flock on path, waiting up to `wait` seconds, or raises UpgradeError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        until = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise UpgradeError(refusal) from None
                time.sleep(.2)
        yield


def held(path):
    """True while another process holds an flock on path; probes without keeping it."""
    try:
        with path.open('r') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    except PermissionError:
        # A lock this user cannot even open cannot be shown free.
        return True
    return False


def present(path):
    """Only a missing entry is absent; a denied or unreadable path counts as present."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def sync_directory(path):
    handle = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def stores():
    """(home, path) of every SQLite store a release may migrate: each one directly under
    .lab/upstream (the control catalog) and the key store."""
    found = [('upstream', path) for path in sorted(UPSTREAM.glob('*.sqlite')) if path.is_file()]
    if KEY_STORE.is_file():
        found.append(('keys', KEY_STORE))
    return found


def homes():
    return {'upstream': UPSTREAM, 'keys': KEY_STORE.parent}


def snapshot(commit):
    """A consistent copy of the control state, taken with SQLite's backup API (consistent while a
    running process has the stores open), into SNAPSHOTS/<commit>-<UTC time>/ with a manifest.
    The directory gets its name only once complete; returns that name."""
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    os.chmod(UPGRADES, 0o700)
    os.chmod(SNAPSHOTS, 0o700)
    name = f"{commit[:12]}-{datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
    partial = SNAPSHOTS / (name + '.partial')
    partial.mkdir(mode=0o700)
    files = []
    try:
        for home, path in stores():
            copy = partial / path.name
            os.close(os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            with contextlib.closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)) as source, \
                    contextlib.closing(sqlite3.connect(copy)) as target:
                source.backup(target)
                version = target.execute('PRAGMA user_version').fetchone()[0]
            with copy.open('rb') as handle:
                os.fsync(handle.fileno())
            files.append({'home': home, 'file': path.name, 'bytes': copy.stat().st_size,
                          'sha256': sha256(copy), 'user_version': version})
        lab.atomic(partial / 'manifest.json', {'from': commit, 'taken_at': now(), 'files': files})
        sync_directory(partial)
        os.replace(partial, SNAPSHOTS / name)
        sync_directory(SNAPSHOTS)
    except (OSError, sqlite3.Error) as error:
        shutil.rmtree(partial, ignore_errors=True)
        raise UpgradeError(f'The control state snapshot failed ({error.__class__.__name__}: {error})') from None
    return name


def prune_snapshots(keep=KEEP_SNAPSHOTS):
    """Keeps the newest few snapshots and always the one the upgrade state points at."""
    if not SNAPSHOTS.is_dir():
        return
    protected = (load_state() or {}).get('snapshot')
    complete, partial = [], []
    for path in SNAPSHOTS.iterdir():
        if path.name.endswith('.partial'):
            partial.append(path)
        elif (path / 'manifest.json').is_file():
            complete.append(path)
    complete.sort(key=lambda path: (path / 'manifest.json').stat().st_mtime_ns, reverse=True)
    for path in complete[keep:] + partial:
        if path.name != protected:
            shutil.rmtree(path, ignore_errors=True)


def layout():
    """This module's paths (which the tests redirect), in the form the guard takes."""
    return upgrade_guard.Layout(ROOT, upgrades=UPGRADES, state=STATE_FILE, lock=LOCK, snapshots=SNAPSHOTS,
                                supervisor_lock=SUPERVISOR_LOCK, intent=INTENT, homes=homes())


def restore_snapshot(name):
    """Puts every store back as the snapshot has it, all or nothing (lab/upgrade_guard.py, which
    the next start also uses to finish an interrupted way back)."""
    try:
        upgrade_guard.restore_snapshot(SNAPSHOTS, name, homes())
    except upgrade_guard.SnapshotError as error:
        raise UpgradeError(str(error)) from None


def guard_copy():
    """Where the guard every start runs first (lab/upgrade_guard.py) is copied: .lab/upgrades/guard.py,
    the path the systemd unit and the container's start script look for."""
    return UPGRADES / 'guard.py'


def install_guard():
    """Copies this version's guard to guard_copy() before anything moves. `start` runs in the
    checkout it leaves, whose tracked files plan() found unchanged, so this is the guard of the
    version the way back returns to, and the next starts run it rather than the new version's.
    A version without a guard leaves no copy, so no older copy outlives it."""
    target = guard_copy()
    UPGRADES.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'lab' / 'upgrade_guard.py'
    if not source.is_file():
        target.unlink(missing_ok=True)
        return
    partial = target.with_suffix('.pending')
    partial.unlink(missing_ok=True)
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(source.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, target)
    sync_directory(target.parent)


def catalog_version():
    """The control catalog's schema version now, or None without a catalog."""
    path = UPSTREAM / 'control.sqlite'
    if not path.is_file():
        return None
    try:
        with contextlib.closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=30)) as database:
            return database.execute('PRAGMA user_version').fetchone()[0]
    except sqlite3.Error as error:
        raise UpgradeError(f'The control catalog cannot be read ({error})') from None


def key_store_version():
    """The key store's schema version now, or None without a key store."""
    if not KEY_STORE.is_file():
        return None
    try:
        with contextlib.closing(sqlite3.connect(f'file:{KEY_STORE}?mode=ro', uri=True, timeout=30)) as database:
            return database.execute('PRAGMA user_version').fetchone()[0]
    except sqlite3.Error as error:
        raise UpgradeError(f'The key store cannot be read ({error})') from None


def schema_support(commit, path, constant):
    """The newest schema of one store a version opens, or None when that version cannot be read.

    A version that declares no such constant (every release before the catalog got its schema
    ladder, and every release so far for the key store) never wrote PRAGMA user_version, so the
    only schema it is known to open is the baseline, 0. That is not the same as "unknown": a
    commit git cannot read at all is unknown, and the caller refuses."""
    if subprocess.run(['git', 'cat-file', '-e', f'{commit}^{{commit}}'], cwd=ROOT, capture_output=True).returncode:
        return None
    found = re.search(constant + r'\s*=\s*(\d+)', git('show', f'{commit}:{path}', check=False))
    return int(found.group(1)) if found else 0


def catalog_support(commit):
    """The newest catalog schema a version opens (lab/upgrade.py schema_support)."""
    return schema_support(commit, 'src/control/catalog.ts', 'CATALOG_SCHEMA_VERSION')


def key_store_support(commit):
    """The newest key store schema a version opens; 0 until a release declares one in keys.ts."""
    return schema_support(commit, 'src/control/keys.ts', 'KEY_STORE_SCHEMA_VERSION')


def plan(target_ref):
    current = git('rev-parse', 'HEAD')
    target = resolve(target_ref)
    refusals = []
    if [path for path in changed_tracked() if not path.startswith(EVIDENCE)]:
        refusals.append('The checkout has local changes to tracked files; commit or discard them first')
    if target == current:
        refusals.append('Already at this version')
    database = lock_at(current, DATABASE_LOCK), lock_at(target, DATABASE_LOCK)
    if (database[0] or {}).get('id') != (database[1] or {}).get('id'):
        refusals.append('This version changes the PostgreSQL image; that needs lab/migrate-generation.py, not an upgrade')
    state = load_state()
    if state and state.get('phase') in PENDING:
        refusals.append(f"The last {'upgrade' if state['phase'] == 'applied' else 'rollback'} has not started yet; restart Sbarbase first")
    for name in UNSETTLED:
        if present(UPSTREAM / name):
            refusals.append(f'A pending operation record ({name}) must be settled or reconciled first')
    if held(BACKUP_LOCK):
        refusals.append('A backup or restore is running; wait for it to finish')
    ahead = git('rev-list', '--count', f'{current}..{target}', check=False) or '?'
    behind = git('rev-list', '--count', f'{target}..{current}', check=False) or '?'
    return {'current': current, 'target': target, 'ahead': ahead, 'behind': behind,
            'changes': changes(current, target), 'refusals': refusals}


def report(details, target_ref):
    print(f"now      {details['current'][:12]}")
    print(f"target   {details['target'][:12]}  ({target_ref}: {details['ahead']} commit(s) ahead, {details['behind']} behind)")
    for label, before, after in details['changes']:
        print(f'image    {label}: {before} -> {after}')
    if not details['changes']:
        print('image    no pinned image changes')
    for refusal in details['refusals']:
        print('refused  ' + refusal)


def pull(commit):
    for number, (label, image, reference) in enumerate(images_at(commit), 1):
        if lab.docker('image', 'inspect', image, check=False).returncode:
            install_server.pull_image(label, reference, f'{number}')


def back_up():
    """Backs up every environment before anything moves; the backups stay afterwards."""
    # Local only: an unreachable off-site storage must not block an upgrade that has its backups.
    result = subprocess.run(['/usr/bin/python3', 'lab/backup.py', 'create', 'all', '--local-only'], cwd=ROOT, text=True)
    if result.returncode:
        raise UpgradeError('The backup before the upgrade failed; nothing was changed')


def owner_of_checkout(paths):
    """Keeps files git rewrote owned by the checkout's owner when this runs as root in Docker."""
    if os.geteuid() != 0:
        return
    stat = ROOT.stat()
    if stat.st_uid == 0:
        return
    for relative in paths:
        path = ROOT / relative
        if path.exists() or path.is_symlink():
            os.lchown(path, stat.st_uid, stat.st_gid)


# The acceptance and the live checks write their evidence into the checkout, over files the
# repository tracks. Those runs are this server's own record, not local edits, so they never
# block an upgrade: they are copied aside before the checkout moves. The first upgrade after
# an acceptance in the rehearsal VM was refused for exactly this.
EVIDENCE = 'docs/evidence/'


def porcelain(*args):
    """(status, path) pairs from git status -z; git() strips the leading space of the first entry."""
    result = subprocess.run(['git', 'status', '--porcelain=v1', '-z', *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise UpgradeError(f'git status failed: {result.stderr.strip()}')
    return [(entry[:2], entry[3:]) for entry in result.stdout.split('\0') if len(entry) > 3]


def changed_tracked():
    return [path for _, path in porcelain('--untracked-files=no')]


def set_aside_evidence(commit):
    """Copy evidence written here to .lab/upgrades/evidence-<time>/, then clear the paths the move would touch."""
    changed = [path for path in changed_tracked() if path.startswith(EVIDENCE)]
    untracked = [path for code, path in porcelain('--untracked-files=all', '--', EVIDENCE) if code == '??']
    arriving = set(git('ls-tree', '-r', '--name-only', commit, '--', EVIDENCE, check=False).splitlines())
    clashing = [path for path in untracked if path in arriving]
    if not changed and not clashing:
        return None
    aside = UPGRADES / ('evidence-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    for path in changed + clashing:
        target = aside / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target)
    if changed:
        git('checkout', '-q', '--', *changed)
    for path in clashing:
        (ROOT / path).unlink()
    print(f'Evidence written on this server was copied to {aside} before the move')
    return aside


def checkout(commit, intent):
    """Records which images the next start may replace, then moves the checkout."""
    previous = git('rev-parse', 'HEAD')
    INTENT.parent.mkdir(parents=True, exist_ok=True)
    lab.atomic(INTENT, intent)
    try:
        set_aside_evidence(commit)
        git('checkout', '-q', '--detach', commit)
    except UpgradeError:
        INTENT.unlink(missing_ok=True)
        raise
    owner_of_checkout(git('diff', '--name-only', previous, commit).splitlines())
    install_dependencies()


def install_dependencies():
    if subprocess.run(['bun', 'install', '--frozen-lockfile'], cwd=ROOT).returncode:
        raise UpgradeError('bun install failed for this version')


def start(target_ref, trigger='cli'):
    with exclusive(LOCK, 'Another upgrade or rollback is running'):
        apply(target_ref, trigger)


def apply(target_ref, trigger='cli'):
    details = plan(target_ref)
    report(details, target_ref)
    if details['refusals']:
        raise UpgradeError('Nothing was changed')
    target = details['target']
    pins, back = pins_at(target), pins_at(details['current'])
    pull(target)
    back_up()
    # Proves the control state can be copied before anything moves. The new version takes a
    # fresh one when it starts (before_start), which is the one the way back restores.
    try:
        taken = snapshot(details['current'])
        install_guard()
    except (UpgradeError, OSError) as error:
        raise UpgradeError(f'{error}; nothing was changed') from None
    # `moved` becomes true once the checkout and its dependencies are in place; until then the
    # guard takes a crash for a move that never finished and undoes it. `way_back` is the intent
    # the way back writes, known now, so the guard needs nothing from the new version.
    record = {'phase': 'applied', 'from': details['current'], 'to': target, 'started_at': now(),
              'changes': details['changes'], 'automatic': False, 'snapshot': taken, 'trigger': trigger,
              'protocol': upgrade_guard.PROTOCOL, 'moved': False,
              'way_back': {'pins': back, 'from': target, 'to': details['current']}}
    save_state(record)
    prune_snapshots()
    try:
        checkout(target, {'pins': pins, 'from': details['current'], 'to': target})
    except UpgradeError as error:
        # The checkout did not move, or moved without its dependencies: go back to where it was.
        git('checkout', '-q', '--detach', details['current'], check=False)
        INTENT.unlink(missing_ok=True)
        save_state({**record, 'phase': 'failed', 'failure': str(error), 'finished_at': now()})
        raise
    save_state({**record, 'moved': True})
    print(f'The checkout is at {target[:12]}. Restart Sbarbase now:\n  {RESTART}')
    print('If the new version does not start, Sbarbase moves back by itself.')
    if trigger == 'cli' and held(SUPERVISOR_LOCK):
        # From the command line while Sbarbase runs (the only way inside the container, where
        # the supervisor is the container's own process): until the restart, the running
        # supervisor is the previous version, and the scripts it starts (Studio, sign-in and
        # toggle applies, the daily backup, a restarted worker, the final runtime stop) are read
        # from the moved checkout. The console's "update now" avoids that window by draining the
        # supervisor first (lab/dev.py begin_drain); here the restart must simply follow at once.
        print('Sbarbase is still running the previous version from the moved checkout: restart it now, '
              'before it starts anything else.')


def rollback(automatic=False, reason=None):
    # The automatic way back runs inside the supervisor, which already holds its own lock; it
    # waits a little for the upgrade lock rather than failing on a moment's overlap.
    with exclusive(LOCK, 'Another upgrade or rollback is running', wait=30 if automatic else 0):
        go_back(automatic, reason)


def rollback_refusal(state=None):
    """Why `rollback` would refuse before moving anything, or None. The console's "roll back"
    (lab/updates.py) asks this same question, so the page and the command cannot disagree."""
    state = load_state() if state is None else state
    if not state or state.get('phase') not in ('applied', 'confirmed'):
        return 'There is no upgrade to roll back'
    if state['phase'] == 'confirmed':
        # After confirmation the fix is forward only: later writes stay, so every store must be
        # one the previous version can still open. Unknown support refuses; it never allows.
        source = state['from']
        for label, current, supported in (('control catalog', catalog_version(), catalog_support(source)),
                                          ('key store', key_store_version(), key_store_support(source))):
            if current is None:
                continue
            if supported is None:
                return (f'Version {source[:12]} cannot be read from this checkout, so whether it opens the {label} '
                        'is unknown. Nothing was changed')
            if current > supported:
                return (f'The {label} is at schema {current}, and {source[:12]} opens only up to {supported}. '
                        'After a confirmed upgrade the fix is forward only: move to a newer version, or restore from '
                        'the backups taken before the upgrade (lab/backup.py list). Nothing was changed')
    return None


def short_reason(reason):
    """The first line of why the automatic way back ran, bounded (the supervisor's own error
    text, which never carries a secret)."""
    lines = str(reason or '').strip().splitlines()
    return lines[0][:300] if lines else None


def way_back(state):
    """The intent the way back writes: recorded by `start`, or worked out for an older record."""
    return state.get('way_back') or {'pins': pins_at(state['from']), 'from': state['to'], 'to': state['from']}


def go_back(automatic, reason=None):
    state = load_state()
    refusal = rollback_refusal(state)
    if refusal:
        raise UpgradeError(refusal)
    source = state['from']
    # Before confirmation the new version may have migrated the control state, and nothing it
    # did is trusted: the snapshot it took on start goes back. A version that never started
    # (no `attempted_at`) touched nothing, and restoring would drop what the running version
    # wrote since `start`.
    restore = state['phase'] == 'applied' and bool(state.get('attempted_at'))
    intent = way_back(state)
    pull(source)
    guard = contextlib.nullcontext() if automatic or not restore else \
        exclusive(SUPERVISOR_LOCK, 'Sbarbase is running; stop it first (sudo systemctl stop sbarbase, or docker compose stop), '
                                   'then roll back, so the control state can be put back safely')
    with guard:
        # A start that failed and one that failed its health checks read differently in `status`.
        upgrade_guard.begin_way_back(state, automatic, short_reason(reason) if automatic else None)
        state['way_back'] = intent
        save_state(state)
        # From here the way back is resumable: the checkout moves first, then the control state
        # is restored, each step recorded once done. Whatever stops it in between leaves
        # rolling_back, which the next start completes (lab/upgrade_guard.py) before anything
        # else runs, so the failed version is never started again without its gate.
        try:
            upgrade_guard.complete_way_back(layout(), state, save_state, lambda: checkout(source, intent))
        except upgrade_guard.SnapshotError as error:
            save_state(upgrade_guard.snapshot_failed(state, error))
            raise UpgradeError(state['failure']) from None
    print(f'The checkout is back at {source[:12]}.' + ('' if automatic else f' Restart Sbarbase now:\n  {RESTART}'))


def before_start():
    """Called by the supervisor after taking its locks and before anything opens the control
    state. Returns True when this start confirms a pending upgrade or rollback, which the
    supervisor then does only after its health checks pass (lab/upgrade_health.py).

    The checkout must be the version the phase confirms (`to` while applied, `from` while
    rolling back, with no tracked file changed): anything else, such as a crash in the middle
    of a checkout or of `bun install`, raises UpgradeError, and the caller's failed start moves
    back or completes the way back. A mismatched tree is never confirmed.

    On the first start of a new version it snapshots the control state again, so the way back
    keeps everything the previous version wrote until the restart, and records that the new
    version was attempted. Later starts of the same version keep that snapshot: the catalog may
    already be migrated. A snapshot that fails raises UpgradeError, and the caller moves back
    before the new version touched anything.
    """
    state = load_state()
    if not state or state.get('phase') not in PENDING:
        # A marker without a pending start is stale (a crash, or an older release that never
        # removes it): clear it so it can never hold traffic.
        HOLD.unlink(missing_ok=True)
        return False
    problem = upgrade_guard.mismatch(layout(), state)
    if problem:
        raise UpgradeError(problem[0].upper() + problem[1:])
    if state['phase'] == 'applied' and not state.get('attempted_at'):
        # A record the previous version left unsettled (a provisioning receipt, an HBA journal
        # or migration: after `start` from the command line the old supervisor kept working
        # until the restart) is settled by that version, never by this one: the snapshot below
        # would hold the catalog without the matching record, and a restore after this version
        # settled it would leave the two disagreeing. So this start fails before it touched
        # anything, the way back restores nothing, and the previous version settles it.
        for name in UNSETTLED:
            if present(UPSTREAM / name):
                raise UpgradeError(f'The previous version left a pending operation record ({name}); it settles it first. '
                                   'Start the upgrade again once it has')
        with exclusive(LOCK, 'Another upgrade or rollback is running', wait=30):
            taken = snapshot(state['from'])
            state.update({'snapshot': taken, 'attempted_at': now()})
            save_state(state)
        prune_snapshots()
    UPGRADES.mkdir(parents=True, exist_ok=True)
    lab.atomic(HOLD, {'phase': state['phase'], 'since': now()})
    return True


def close_attempt():
    """A start that stopped cleanly before its verdict (a stop signal, Ctrl+C, a reboot) closes
    the attempt the guard opened, so the next start does not take it for a crash. Returns
    whether an open attempt was closed."""
    state = load_state()
    if not state or state.get('phase') not in PENDING:
        return False
    record = state.get('guard')
    if not isinstance(record, dict) or record.get('phase') != state['phase'] or record.get('open') is not True:
        return False
    save_state({**state, 'guard': {**record, 'open': False}})
    return True


def after_start(started, reason=None):
    """Called by the supervisor once its start has succeeded or failed.

    Returns True when a failed start moved the checkout back, so the caller exits and
    the restart policy brings up the previous version.

    A confirmation is saved before the hold is lifted: when the state cannot be saved this
    raises, the hold stays, and the supervisor tries again (lab/upgrade_health.Confirmation).

    rollback_failed is recorded only with the checkout back on the previous version. A way back
    that could not finish leaves `applied` (nothing recorded yet: the next start's guard goes
    back itself) or `rolling_back` (which the next start completes), so the failed version is
    never what an ungated start runs.
    """
    state = load_state()
    if not state or state.get('phase') not in PENDING:
        HOLD.unlink(missing_ok=True)
        return False
    if started:
        state.update({'phase': 'confirmed' if state['phase'] == 'applied' else 'rolled_back', 'finished_at': now()})
        save_state(state)
        INTENT.unlink(missing_ok=True)
        HOLD.unlink(missing_ok=True)
        return False
    try:
        if state['phase'] == 'applied':
            try:
                rollback(automatic=True, reason=reason)
            except (UpgradeError, SystemExit, OSError) as error:
                # Nothing is marked failed from this (possibly stale) copy of the state: a
                # snapshot that cannot be restored recorded rollback_failed itself, with the
                # checkout already back, and every other failure stays resumable (see above).
                print(f'The way back did not finish: {error}', file=sys.stderr, flush=True)
                return False
            return True
        if not upgrade_guard.way_back_done(layout(), state):
            # The way back stopped halfway: finish it, and the previous version starts next.
            with exclusive(LOCK, 'Another upgrade or rollback is running', wait=30):
                try:
                    upgrade_guard.complete_way_back(layout(), state, save_state,
                                                    lambda: checkout(state['from'], way_back(state)))
                except upgrade_guard.SnapshotError as error:
                    save_state(upgrade_guard.snapshot_failed(state, error))
                    return False
            return True
        state.update({'phase': 'rollback_failed', 'failure': 'The previous version did not start either', 'finished_at': now()})
        save_state(state)
        return False
    finally:
        # The version that starts next writes its own marker if it has a start to confirm.
        HOLD.unlink(missing_ok=True)


def status():
    state = load_state()
    if not state:
        print('No upgrade has run on this installation.')
        return
    words = {'applied': 'waiting for the new version to start and pass its health checks',
             'confirmed': 'the new version started and passed its health checks',
             'rolling_back': 'waiting for the previous version to start and pass its health checks',
             'rolled_back': 'back on the previous version' + (' (automatic)' if state.get('automatic') else ''),
             'rollback_failed': 'the previous version did not start; restore from the backups taken before the upgrade',
             'failed': 'the upgrade stopped before the checkout moved; nothing changed'}
    print(f"upgrade  {state['from'][:12]} -> {state['to'][:12]}, started {state['started_at']}")
    print(f"result   {state['phase']}: {words.get(state['phase'], '')}")
    if state.get('automatic') and state.get('reason'):
        # Recorded by the automatic way back: a start that failed, or one that failed its health checks.
        print('why back ' + state['reason'])
    if state.get('failure'):
        print('reason   ' + state['failure'])
    if state.get('snapshot'):
        restored = ' (restored on the way back)' if state.get('restored') == state['snapshot'] else ''
        print(f"control  snapshot {SNAPSHOTS.relative_to(ROOT) if SNAPSHOTS.is_relative_to(ROOT) else SNAPSHOTS}/{state['snapshot']}{restored}")
    print(f"now at  {git('rev-parse', 'HEAD')[:12]}")


# The release channel: lab/release_channel.py decides, these only wire it in.

def show_channel(result):
    current, available = result['current'], result['available']
    print(f"now        {current['version']} ({current['commit'][:12]})")
    if available:
        print(f"available  {available['version']} ({available['tag']}, {available['commit'][:12]}), class {available['class']}, "
              + ('signed' if available['signed'] else 'NOT signed'))
        for reason in available['reasons']:
            print('reason     ' + reason)
        for row in available['changes']:
            print(f"image      {row['image']}: {row['from']} -> {row['to']}")
        print('notes      ' + available['notes']['en'])
    else:
        print('available  nothing newer')
    for refusal in result['refusals']:
        print('refused    ' + refusal)


def channel(as_json=False, preview=False):
    try:
        result = release_channel.check(channel='preview' if preview else 'stable')
    except release_channel.ReleaseError as error:
        raise UpgradeError(str(error))
    release_channel.write_check(result)
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        show_channel(result)


def start_release(tag, allow=(), trigger='cli'):
    """Starts an upgrade to a signed release: verified, classified, then the usual start."""
    try:
        release = release_channel.prepare(tag, allow)
    except release_channel.ReleaseError as error:
        raise UpgradeError(f'{error}\nNothing was changed')
    print(f"release  {tag}, class {release['class']}, signed")
    began = now()
    try:
        # The verified commit, never the ref: nothing can move between the check and the checkout.
        start(release['commit'], trigger)
    finally:
        state = load_state()
        if state and state.get('to') == release['commit'] and state.get('started_at', '') >= began and 'release' not in state:
            save_state({**state, 'release': {'version': release['version'], 'tag': tag, 'class': release['class'], 'signed': True}})
    if release['class'] == 'rebuild':
        print('This release changes what a plain restart does not pick up:')
        for reason in release['reasons']:
            print('  ' + reason)
        print('With Docker, restart with: docker compose up -d --build')
        print('With systemd, reinstall the unit first: sudo /usr/bin/python3 lab/install_server.py supervise --apply')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Upgrade Sbarbase, with a way back')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check').add_argument('--to', default=DEFAULT_TARGET)
    starting = sub.add_parser('start')
    target = starting.add_mutually_exclusive_group()
    target.add_argument('--to', default=DEFAULT_TARGET)
    target.add_argument('--release', help='a signed release tag from the channel, vX.Y.Z')
    starting.add_argument('--allow-class', action='append', choices=['rebuild'], default=[],
                          help='apply a release that needs a rebuild (a manual release is always refused)')
    starting.add_argument('--trigger', choices=TRIGGERS, default='cli', help=argparse.SUPPRESS)
    listing = sub.add_parser('channel')
    listing.add_argument('--json', action='store_true')
    listing.add_argument('--preview', action='store_true', help='include pre-releases')
    sub.add_parser('status')
    sub.add_parser('rollback').add_argument('--check', action='store_true',
                                            help='only say whether a rollback would refuse, and why')
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    try:
        if args.command == 'channel':
            channel(args.json, args.preview)
            return 0
        if args.command == 'start' and args.release:
            start_release(args.release, args.allow_class, args.trigger)
            return 0
        if args.command == 'rollback' and args.check:
            refusal = rollback_refusal()
            print(refusal or 'A rollback would go ahead.')
            return 1 if refusal else 0
        if args.command == 'check':
            details = plan(args.to)
            report(details, args.to)
            return 1 if details['refusals'] else 0
        if args.command == 'start':
            start(args.to, args.trigger)
        elif args.command == 'rollback':
            rollback()
        else:
            status()
        return 0
    except UpgradeError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
