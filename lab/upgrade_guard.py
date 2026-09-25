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

Every move it makes is forced and verified (force_checkout): a stale .git/index.lock is removed
when no git process can be using the checkout, evidence written under docs/evidence/ is copied
into .lab/upgrades/evidence-<time>/ and other local changes to tracked files into
.lab/upgrades/aside-<time>/, and nothing is recorded until HEAD is the commit and the
tree is clean. A move that still fails is retried by the next MAX_ATTEMPTS starts; after that the
outcome is terminal (cannot_move): the start goes on only when the checkout holds the previous
version, or the confirmed one an operator's rollback tried to leave; otherwise the guard says so
in one line and waits STUCK_WAIT seconds before it exits, so the service manager's restarts try
again slowly instead of looping every few seconds, and never run the failed version ungated.

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
# Where the acceptance and the live checks write their evidence, over tracked files: never a
# local change (local_changes), and set aside before every move (set_aside_evidence).
EVIDENCE = 'docs/evidence/'
# guard() returns this when the checkout cannot be moved and neither version on disk may start.
STUCK = 3
# How long main() waits before it exits after STUCK. systemd restarts a failed ExecStartPre and
# Docker restarts any exit, and neither can be told to stay stopped by an ExecStartPre's exit
# status, so the wait is what keeps the restarts slow. It stays well under the unit's
# TimeoutStartSec (600 s), which every ExecStartPre shares. A terminal start (lab/dev.py
# run_guard) sets SBARBASE_GUARD_WAIT=0 and gets the line at once; an environment variable,
# because the copy that runs may be an older guard, which reads its first argument as the root.
STUCK_WAIT = 300
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


def status(layout, *args):
    """(code, path) pairs from `git status --porcelain=v1 -z`, run directly because git() strips
    the leading space of the first entry. A rename or copy is one entry followed by its origin in
    a field of its own; a rename's origin is reported as a second pair with the same code, since
    it changed too (it is gone), and a copy's is skipped (it did not). Raises Refused when git
    status fails, so a caller never takes a failure for a clean tree or for local changes."""
    result = subprocess.run(['git', 'status', '--porcelain=v1', '-z', *args], cwd=layout.root,
                            capture_output=True, text=True)
    if result.returncode:
        raise Refused(f'git status failed: {result.stderr.strip()}')
    fields = iter(result.stdout.split('\0'))
    entries = []
    for entry in fields:
        if len(entry) < 4:
            continue
        code = entry[:2]
        entries.append((code, entry[3:]))
        if 'R' in code or 'C' in code:
            origin = next(fields, '')
            if 'R' in code and origin:
                entries.append((code, origin))
    return entries


def changed_tracked(layout):
    """Every tracked path that differs from HEAD, staged or not. Raises Refused (status)."""
    return [path for _, path in status(layout, '--untracked-files=no')]


def local_changes(layout):
    """Tracked paths that differ from HEAD outside the evidence the live checks write. Raises Refused."""
    return [path for path in changed_tracked(layout) if not path.startswith(EVIDENCE)]


def clean(layout):
    """No tracked file differs from HEAD, evidence the live checks write aside. A git status that
    fails is not clean."""
    try:
        return not local_changes(layout)
    except Refused:
        return False


def set_aside_evidence(layout, commit):
    """The acceptance and the live checks write their evidence into the checkout, over files the
    repository tracks. Those runs are this server's own record, not local edits, so no move of the
    checkout refuses on them: the changed tracked evidence and the untracked evidence `commit`
    would overwrite are copied to <upgrades>/evidence-<time>/ (0700), then the tracked paths are
    restored from HEAD (index and tree) and the clashing ones removed. A copy already there is
    kept, as set_aside keeps one: a move retried within the same second must not replace this
    server's evidence with a half-written tree. Returns (folder, the paths git rewrote or removed),
    folder None when nothing was set aside; raises Refused or OSError, and a copy that fails stops
    before anything is restored."""
    changed = [path for path in changed_tracked(layout) if path.startswith(EVIDENCE)]
    untracked = [path for code, path in status(layout, '--untracked-files=all', '--', EVIDENCE) if code == '??']
    arriving = set(names(git(layout, 'ls-tree', '-r', '--name-only', '-z', commit, '--', EVIDENCE, check=False)))
    clashing = [path for path in untracked if path in arriving]
    if not changed and not clashing:
        return None, []
    aside = layout.upgrades / ('evidence-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    for relative in changed + clashing:
        source, target = layout.root / relative, aside / relative
        # A deleted or renamed-away file has nothing to copy; the restore brings it back.
        if not (source.is_file() or source.is_symlink()) or target.exists() or target.is_symlink():
            continue
        aside.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(aside, 0o700)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    if changed:
        git(layout, 'restore', '--source=HEAD', '--staged', '--worktree', '--', *changed)
    for relative in clashing:
        (layout.root / relative).unlink(missing_ok=True)
    return aside, changed + clashing


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


def own(layout, paths):
    """Files git rewrote keep the checkout's owner when this runs as root in the container."""
    if os.geteuid() != 0:
        return
    owner = layout.root.stat()
    if owner.st_uid == 0:
        return
    for relative in paths:
        path = layout.root / relative
        if path.exists() or path.is_symlink():
            os.lchown(path, owner.st_uid, owner.st_gid)


def names(text):
    return [name for name in text.split('\0') if name]


def git_running(root):
    """Whether a git process may be working in this checkout: one whose working directory is in
    it, or one whose working directory this user cannot read (another user's git, as root on the
    host, cannot be shown to be elsewhere)."""
    root = Path(root).resolve()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if not (entry / 'comm').read_text().strip().startswith('git'):
                continue
        except OSError:
            continue
        try:
            directory = Path(os.readlink(entry / 'cwd'))
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if directory == root or root in directory.parents:
            return True
    return False


def clear_stale_index_lock(layout):
    """Removes .git/index.lock left by a git process that was killed (SIGKILL, power loss), which
    otherwise refuses every checkout for good. Callers hold the upgrade lock, so no upgrade,
    rollback or guard runs git here; the lock is removed only when no git process can be using
    the checkout either. Returns whether it removed one."""
    lock = layout.root / '.git' / 'index.lock'
    if not lock.exists() or git_running(layout.root):
        return False
    lock.unlink(missing_ok=True)
    say('removed a stale .git/index.lock that no git process holds')
    return True


def aside_folder(layout, state):
    """One folder per upgrade or way back, so the copies a retried move makes land together."""
    moment = str(state.get('rollback_at') or state.get('started_at') or now())
    return layout.upgrades / ('aside-' + ''.join(char for char in moment if char.isalnum()))


def set_aside(layout, commit, folder):
    """Copies every tracked file that differs from HEAD (staged or not, evidence included, since
    a forced checkout overwrites all of them) and every untracked file the commit would
    overwrite into `folder`, 0700. A copy already there is kept: the first copy is the operator's
    own edit, a later one may be a half-written tree from a move that stopped. Returns the paths
    that differ; raises OSError when a copy fails, and then nothing is overwritten."""
    changed = names(git(layout, 'diff', '--name-only', '-z', 'HEAD', '--'))
    untracked = set(names(git(layout, 'ls-files', '--others', '--exclude-standard', '-z')))
    arriving = set(names(git(layout, 'ls-tree', '-r', '--name-only', '-z', commit)))
    copied = []
    for relative in changed + sorted(untracked & arriving):
        source = layout.root / relative
        target = folder / relative
        if not (source.is_file() or source.is_symlink()) or target.exists() or target.is_symlink():
            continue
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
        copied.append(relative)
    if copied:
        sync_directory(folder)
        say(f'{len(copied)} changed file(s) of the checkout were copied to {folder} before the move')
    return changed + sorted(untracked & arriving)


def force_checkout(layout, commit, folder):
    """Moves the checkout to `commit` whatever the tree holds, and proves it did: local changes go
    aside first (set_aside; a copy that fails stops here, before anything is overwritten), a
    stale index lock is cleared, `git checkout -f --detach` runs, and HEAD and a clean tree are
    checked before the caller records anything. Raises Refused or OSError."""
    clear_stale_index_lock(layout)
    previous = head(layout)
    # Evidence first, after the stale lock is gone: it lands where an upgrade puts it
    # (evidence-<time>), and set_aside then copies only the operator's own changes.
    evidence, restored = set_aside_evidence(layout, commit)
    if evidence is not None:
        say(f'evidence written on this server was copied to {evidence} before the move')
    rewritten = restored + set_aside(layout, commit, folder)
    git(layout, 'checkout', '-q', '-f', '--detach', commit)
    if head(layout) != commit or not clean(layout):
        raise Refused(f'git checkout did not leave a clean checkout at {commit[:12]}')
    diff = git(layout, 'diff', '--name-only', previous, commit, check=False).splitlines() if previous else []
    own(layout, sorted(set(diff) | set(rewritten)))


def move_checkout(layout, commit, intent, install=bun_install, folder=None):
    """The guard's own move: the intent the next start reads, the checkout, the dependencies."""
    if intent is not None:
        write_json(layout.intent, intent)
    else:
        layout.intent.unlink(missing_ok=True)
    force_checkout(layout, commit, folder or aside_folder(layout, {}))
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
    """The record that makes a way back resumable, saved before anything moves, with the phase
    it leaves (`back_from`): a way back from `confirmed` that cannot move leaves a version on disk that
    passed its health checks, which the guard may then start (cannot_move)."""
    state.update({'phase': 'rolling_back', 'automatic': automatic, 'rollback_at': now(), 'protocol': PROTOCOL,
                  'back_from': state['phase'], 'move_failures': 0,
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


def cannot_move(layout, state, save, error):
    """A move of the checkout (or the restore after it) failed. The next MAX_ATTEMPTS starts try
    again, since most causes pass (a git process that had the index locked, a full disk someone
    clears). After that the outcome is terminal, and the least harmful start is chosen:

      - the checkout holds the previous version, clean: record `failed` (the upgrade's own move
        back) or `rollback_failed`, and let the previous version start without the gate;
      - an operator's rollback of a confirmed upgrade could not leave it, and the confirmed
        version is intact: record `confirmed` again with `rollback_failure`, and start it;
      - otherwise the failed or a half-written version is on disk: record `stuck`, keep the phase
        pending so nothing ever starts it ungated, and return STUCK (main waits, then exits).

    A state that cannot even be saved raises, and the service manager's restarts keep trying."""
    text = f'{error.__class__.__name__}: {error}' if isinstance(error, OSError) else str(error)
    count = state.get('move_failures')
    state['move_failures'] = (count if isinstance(count, int) and not isinstance(count, bool) else 0) + 1
    source = state['from'][:12]
    if state['move_failures'] < MAX_ATTEMPTS:
        save(state)
        raise Refused(f'The checkout could not be moved back to {source} ({text}); the next start tries again')
    here, tidy = head(layout), clean(layout)
    state.pop('stuck', None)
    if here == state['from'] and tidy:
        if state['phase'] == 'applied':
            state.update({'phase': 'failed', 'finished_at': now(),
                          'failure': f'The upgrade stopped while it moved the checkout. The checkout is back at {source}, '
                                     f'but the move back did not finish ({text})'})
            save(state)
            say(state['failure'] + '; it starts without the health checks now')
            return 0
        failure = f'The checkout is back at {source}, but the way back did not finish ({text})'
        if state.get('restore_pending') and state.get('restored') != state.get('snapshot'):
            try:
                restore_snapshot(layout.snapshots, state.get('snapshot'), layout.homes)
                state['restored'] = state['snapshot']
            except (SnapshotError, OSError) as problem:
                failure += (f'; the control state was not put back ({problem}): restore from the backups taken '
                            'before the upgrade (lab/backup.py list)')
        state.update({'phase': 'rollback_failed', 'finished_at': now(), 'failure': failure})
        save(notice(state, 'rolling_back'))
        say(failure + '; it starts without the health checks now')
        return 0
    if state['phase'] == 'rolling_back' and state.get('back_from') == 'confirmed' and here == state['to'] and tidy:
        # The way back wrote the previous version's pins before it failed to move: the version
        # that starts now is the confirmed one, which must not replace its services with them.
        layout.intent.unlink(missing_ok=True)
        state.update({'phase': 'confirmed', 'guard': None,
                      'rollback_failure': f'The rollback could not move the checkout back to {source} ({text}); '
                                          f"Sbarbase stays on the confirmed version {state['to'][:12]}"})
        save(state)
        say(state['rollback_failure'])
        return 0
    state['stuck'] = {'at': now(), 'reason': text}
    save(state)
    say(f'the checkout cannot be moved back to {source} ({text}), and the version it holds must not start '
        'without its health checks, so Sbarbase stays stopped. Fix the cause (lab/upgrade.py status), then '
        f'restart Sbarbase; until then this guard tries again every {STUCK_WAIT // 60} minutes')
    return STUCK


def guard(layout, install=bun_install):
    """One run; returns the exit status, or STUCK. See the module docstring."""
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
            # The folder is named after the way back, so every retry of it shares one.
            move_checkout(layout, state['from'], intent_back(layout, state), install, aside_folder(layout, state))

        if state['phase'] == 'applied' and not moved(layout, state):
            # `start` stopped while it moved the checkout: the new version never ran, so putting
            # the checkout back is all there is to undo. HEAD may not have moved at all while the
            # tree is half written, so the move is forced and verified, never assumed.
            if not state.get('stuck'):
                # A stuck retry keeps to its one line (cannot_move).
                say('the upgrade stopped while it moved the checkout; moving back to ' + state['from'][:12])
            try:
                move_checkout(layout, state['from'], None, install, aside_folder(layout, state))
            except (Refused, OSError) as error:
                return cannot_move(layout, state, save, error)
            for key in ('stuck', 'move_failures'):
                state.pop(key, None)
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
        except (Refused, OSError) as error:
            return cannot_move(layout, state, save, error)
        for key in ('stuck', 'move_failures'):
            state.pop(key, None)
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
        status = guard(layout)
    except Refused as error:
        say(str(error))
        return 1
    except OSError as error:
        # The state itself could not be written: nothing is known to be settled.
        say(f'the upgrade state could not be recorded ({error.__class__.__name__}: {error}); the next start tries again')
        return 1
    if status == STUCK:
        # Outside the locks, so the operator's own commands can run meanwhile.
        if os.environ.get('SBARBASE_GUARD_WAIT') != '0':
            time.sleep(STUCK_WAIT)
        return 1
    return status


if __name__ == '__main__':
    sys.exit(main())
