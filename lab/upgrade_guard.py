"""The first step of every Sbarbase start, before any of the version's own code runs.

Run with the checkout as the working directory (or passed as the only argument):

  /usr/bin/python3 .lab/upgrades/guard.py     when present: the copy `lab/upgrade.py start`
                                              took from the version the installation came from
  /usr/bin/python3 lab/upgrade_guard.py       otherwise

The systemd unit runs it as its first ExecStartPre and the container's start script runs it
before anything else; lab/dev.py runs it itself when neither did (a start from a terminal).

Why it exists: the way back after a failed upgrade used to run only from lab/dev.py, inside
the new version's own code, and only for the failures that code caught. A new version that
cannot even import, a preflight or `bun install` that fails before the supervisor runs, or a
process killed while its health checks run, restarted forever with application traffic held
and no way back. This script uses the standard library only and imports nothing from the rest
of lab/, so a broken release cannot break it, and the copy it normally runs from belongs to
the version it may have to go back to.

While an upgrade or a rollback waits for confirmation (state.json phase `applied` or
`rolling_back`), and never otherwise, it:

  - counts the start as an attempt, open until the start reaches its verdict or lab/dev.py
    closes it on a clean stop (a stop signal, Ctrl+C);
  - applied: goes back when the previous attempt is still open (the process died, or a step
    before the supervisor failed), when the attempts pass MAX_ATTEMPTS, when the checkout is
    not the version being confirmed, or when `start` stopped while it moved the checkout;
  - rolling_back: completes an interrupted way back (checkout, dependencies, then the control
    state snapshot), then counts the attempt; an open previous attempt or too many attempts
    record rollback_failed, always with the checkout back on the previous version.

A way back or a rollback_failed it records carries a notice in the state, so the next supervisor
start emits the notification it earns (lab/dev.py upgrade_notices).

Exit status 0 lets the start go on (on whichever version the checkout now holds); 1 means it
cannot, and the service manager tries again. Anything unreadable or not pending exits 0 at
once: a guard problem must never stop an installation that has no upgrade under way.
"""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PENDING = ('applied', 'rolling_back')
# Starts of one pending phase before the guard stops trying. Clean stops count too, so a
# service that is stopped and started over and over during a confirmation still ends.
MAX_ATTEMPTS = 3
# The state format this guard writes. States without it were written before the guard existed.
PROTOCOL = 2
EVIDENCE = 'docs/evidence/'
# Which lock entry each replaceable service runs, as durable_runtime reads them (lab/upgrade.py
# uses this same table). The guard needs it only for a state that predates `way_back`.
SERVICES = {'auth': ('images.lock.json', 'auth'), 'rest': ('images.lock.json', 'rest'),
            'storage': ('storage-image.lock.json', None), 'realtime': ('realtime-image.lock.json', None),
            'functions': ('functions-image.lock.json', None)}


class Refused(Exception):
    """The start cannot go on now; the service manager tries again."""


class SnapshotError(Exception):
    """The control state snapshot is missing or does not match its manifest: nothing was restored."""


class Layout:
    """Every path the guard touches, from the checkout. lab/upgrade.py passes its own."""

    def __init__(self, root, **paths):
        self.root = Path(root)
        lab = self.root / '.lab'
        self.upgrades = paths.get('upgrades') or lab / 'upgrades'
        self.state = paths.get('state') or self.upgrades / 'state.json'
        self.lock = paths.get('lock') or self.upgrades / 'upgrade.lock'
        self.snapshots = paths.get('snapshots') or self.upgrades / 'snapshots'
        upstream = paths.get('upstream') or lab / 'upstream'
        self.supervisor_lock = paths.get('supervisor_lock') or upstream / 'supervisor.lock'
        self.intent = paths.get('intent') or upstream / 'upgrade-intent.json'
        self.homes = paths.get('homes') or {'upstream': upstream, 'keys': self.root / '.secrets' / 'upstream'}


def now():
    return datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')


def say(text):
    print('upgrade guard: ' + text, file=sys.stderr, flush=True)


def sync_directory(path):
    handle = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def write_json(path, value):
    """Private pending file, fsync, rename, fsync the directory (as lab/run.py atomic does)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    pending.unlink(missing_ok=True)
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(json.dumps(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, path)
    sync_directory(path.parent)


def read_state(layout):
    try:
        state = json.loads(layout.state.read_text())
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def pending(state):
    return isinstance(state, dict) and state.get('phase') in PENDING and all(
        isinstance(state.get(key), str) and state[key] for key in ('from', 'to'))


@contextlib.contextmanager
def locked(path, refusal, wait=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        until = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise Refused(refusal) from None
                time.sleep(.2)
        yield


def git(layout, *args, check=True):
    result = subprocess.run(['git', *args], cwd=layout.root, text=True, capture_output=True)
    if check and result.returncode:
        raise Refused(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def head(layout):
    return git(layout, 'rev-parse', 'HEAD', check=False)


def clean(layout):
    """No tracked file differs from HEAD, evidence the live checks write aside."""
    result = subprocess.run(['git', 'status', '--porcelain=v1', '-z', '--untracked-files=no'], cwd=layout.root,
                            capture_output=True, text=True)
    if result.returncode:
        return False
    return not [entry[3:] for entry in result.stdout.split('\0') if len(entry) > 3 and not entry[3:].startswith(EVIDENCE)]


def moved(layout, state):
    """Whether `start` finished moving the checkout (and installing its dependencies). A state
    written before the guard existed has no such record, so the checkout itself says."""
    if state.get('protocol') is None:
        return head(layout) == state['to']
    return state.get('moved') is True


def moved_back(layout, state):
    """Whether the way back finished moving the checkout; derived from HEAD for an older state."""
    if state.get('protocol') is None:
        return head(layout) == state['from']
    return state.get('moved_back') is True and head(layout) == state['from']


def intent_back(layout, state):
    """The intent the way back writes (which images the previous version's start may put back):
    recorded by `start`, or read from the previous version's lock files for an older state. None
    when that cannot be read; the next start then replaces nothing and refuses a changed service."""
    if isinstance(state.get('way_back'), dict):
        return state['way_back']
    pins = {}
    for service, (name, key) in SERVICES.items():
        text = git(layout, 'show', f"{state['from']}:lab/{name}", check=False)
        if not text:
            continue
        try:
            lock = json.loads(text)
        except ValueError:
            return None
        entry = lock.get(key) if key is not None and isinstance(lock, dict) else lock
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            return None
        pins[service] = entry['id']
    return {'pins': pins, 'from': state['to'], 'to': state['from']}


def way_back_done(layout, state):
    restored = not state.get('restore_pending') or state.get('restored') == state.get('snapshot')
    return moved_back(layout, state) and restored


def bun_install(layout):
    if subprocess.run(['bun', 'install', '--frozen-lockfile'], cwd=layout.root).returncode:
        raise Refused('bun install failed for the previous version')


def own(layout, previous, commit):
    """Files git rewrote keep the checkout's owner when this runs as root in the container."""
    if os.geteuid() != 0:
        return
    owner = layout.root.stat()
    if owner.st_uid == 0:
        return
    for relative in git(layout, 'diff', '--name-only', previous, commit, check=False).splitlines():
        path = layout.root / relative
        if path.exists() or path.is_symlink():
            os.lchown(path, owner.st_uid, owner.st_gid)


def move_checkout(layout, commit, intent, install=bun_install):
    """The guard's own move: the intent the next start reads, the checkout, the dependencies."""
    previous = head(layout)
    if intent is not None:
        write_json(layout.intent, intent)
    else:
        layout.intent.unlink(missing_ok=True)
    git(layout, 'checkout', '-q', '--detach', commit)
    own(layout, previous, commit)
    install(layout)


def sha256(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def restore_snapshot(snapshots, name, places):
    """Puts every store back as the snapshot has it. Every file is verified first, all or nothing;
    each store is then replaced atomically (private temporary file, fsync, rename). Callers make
    sure nothing has the stores open: the supervisor is not running."""
    folder = snapshots / name if isinstance(name, str) and name else None
    try:
        manifest = json.loads((folder / 'manifest.json').read_text())
    except (TypeError, OSError, ValueError):
        raise SnapshotError('The control state snapshot of this upgrade is missing; nothing was restored') from None
    for item in manifest['files']:
        copy = folder / item['file']
        if item.get('home') not in places or '/' in item['file'] or not copy.is_file() \
                or copy.stat().st_size != item['bytes'] or sha256(copy) != item['sha256']:
            raise SnapshotError(f"The control state snapshot does not match its manifest ({item['file']}); nothing was restored")
    for item in manifest['files']:
        target = places[item['home']] / item['file']
        partial = target.with_name(target.name + '.upgrade-restore')
        partial.unlink(missing_ok=True)
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle, (folder / item['file']).open('rb') as source:
            shutil.copyfileobj(source, handle)
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            # Keep the owner and mode of the store it replaces (root in Docker, another owner outside).
            current = target.stat()
            if os.geteuid() == 0:
                os.chown(partial, current.st_uid, current.st_gid)
            os.chmod(partial, current.st_mode & 0o777)
        # A leftover rollback journal would be played back into the restored file: remove it first.
        for suffix in ('-journal', '-wal', '-shm'):
            target.with_name(target.name + suffix).unlink(missing_ok=True)
        os.replace(partial, target)
        sync_directory(target.parent)


def begin_way_back(state, automatic, reason):
    """The record that makes a way back resumable, saved before anything moves."""
    state.update({'phase': 'rolling_back', 'automatic': automatic, 'rollback_at': now(), 'protocol': PROTOCOL,
                  'restore_pending': state['phase'] == 'applied' and bool(state.get('attempted_at')),
                  'moved_back': False, 'guard': None})
    if reason:
        state['reason'] = reason
    return state


def complete_way_back(layout, state, save, move):
    """Moves the checkout back first, then restores the control state, recording each step once
    done, so whatever stops it halfway leaves `rolling_back` that the next run completes. The
    checkout goes first: a restore that cannot happen must never leave the failed version as
    what the next start runs. Raises Refused, SnapshotError or OSError."""
    if not moved_back(layout, state):
        move()
        state['moved_back'] = True
        save(state)
    if state.get('restore_pending') and state.get('restored') != state.get('snapshot'):
        restore_snapshot(layout.snapshots, state.get('snapshot'), layout.homes)
        state['restored'] = state['snapshot']
        save(state)


def notice(state, was):
    """Records that this change of phase (from `was` to the state's phase now) earns an outcome
    notification. The guard imports nothing from lab/, so it cannot emit one itself: the next
    supervisor start does (lab/dev.py upgrade_notices, through lab/updates.py announce_outcome),
    into the catalog of the version that runs then, and removes the notice."""
    earlier = state.get('notices') if isinstance(state.get('notices'), list) else []
    state['notices'] = [*earlier, {'was': was, 'phase': state['phase']}]
    return state


def snapshot_failed(state, error):
    state.update({'phase': 'rollback_failed', 'finished_at': now(),
                  'failure': f"{error}. The checkout is back at {state['from'][:12]}, but the control state was not put "
                             'back: restore from the backups taken before the upgrade (lab/backup.py list)'})
    return state


def mismatch(layout, state):
    """Why the checkout is not what this pending phase confirms, or None."""
    if state['phase'] == 'applied':
        if not moved(layout, state):
            return 'the upgrade stopped while it moved the checkout'
        if head(layout) != state['to'] or not clean(layout):
            return 'the checkout is not the version being confirmed'
        return None
    if not way_back_done(layout, state):
        return 'the way back to the previous version did not finish'
    if not clean(layout):
        return 'the checkout has local changes to tracked files'
    return None


def guard(layout, install=bun_install):
    """One run; returns the exit status. See the module docstring."""
    if not pending(read_state(layout)):
        return 0
    with locked(layout.lock, 'Another upgrade or rollback is running', wait=30), \
            locked(layout.supervisor_lock, 'Sbarbase is already running'):
        state = read_state(layout)
        if not pending(state):
            return 0

        def save(value):
            write_json(layout.state, value)

        def back():
            move_checkout(layout, state['from'], intent_back(layout, state), install)

        if state['phase'] == 'applied' and not moved(layout, state):
            # `start` stopped while it moved the checkout: the new version never ran, so putting
            # the checkout back is all there is to undo.
            say('the upgrade stopped while it moved the checkout; moving back to ' + state['from'][:12])
            move_checkout(layout, state['from'], None, install)
            state.update({'phase': 'failed', 'finished_at': now(),
                          'failure': 'The upgrade stopped while it moved the checkout; the checkout went back'})
            save(state)
            return 0
        record = state.get('guard')
        if not isinstance(record, dict) or record.get('phase') != state['phase'] \
                or not isinstance(record.get('attempts'), int):
            record = {'phase': state['phase'], 'attempts': 0, 'open': False}
        crashed = record.get('open') is True
        record = {**record, 'attempts': record['attempts'] + 1}
        if state['phase'] == 'applied':
            reason = None
            if crashed:
                reason = 'the previous start of the new version ended before its health checks passed'
            elif record['attempts'] > MAX_ATTEMPTS:
                reason = f'the new version did not pass its health checks in {MAX_ATTEMPTS} starts'
            elif mismatch(layout, state):
                reason = mismatch(layout, state)
            if reason is None:
                state['guard'] = {**record, 'open': True}
                save(state)
                return 0
            say(reason + '; moving back to ' + state['from'][:12])
            begin_way_back(state, True, reason[0].upper() + reason[1:])
            save(notice(state, 'applied'))
            record, crashed = {'phase': 'rolling_back', 'attempts': 1}, False
        try:
            complete_way_back(layout, state, save, back)
        except SnapshotError as error:
            save(notice(snapshot_failed(state, error), 'rolling_back'))
            say(state['failure'])
            return 0
        except OSError as error:
            raise Refused(f'The way back did not finish ({error.__class__.__name__}: {error}); '
                          'the next start tries again') from None
        if crashed or record['attempts'] > MAX_ATTEMPTS:
            # An open attempt only says that start never finished: it may have ended in the
            # preflight, before the previous version's supervisor ran at all.
            failure = ('The previous version did not finish a start either' if crashed else
                       f'The previous version did not pass its health checks in {MAX_ATTEMPTS} starts either')
            state.update({'phase': 'rollback_failed', 'finished_at': now(), 'failure': failure})
            save(notice(state, 'rolling_back'))
            say(failure + '; it starts without the health checks now')
            return 0
        state['guard'] = {**record, 'open': True}
        save(state)
        return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    layout = Layout(Path(argv[0] if argv else os.getcwd()).resolve())
    try:
        return guard(layout)
    except Refused as error:
        say(str(error))
        return 1


if __name__ == '__main__':
    sys.exit(main())
