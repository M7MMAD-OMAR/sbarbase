"""Move this installation to a newer Sbarbase version, with a way back.

Usage (with Docker, put `docker compose exec sbarbase` before each command):
  /usr/bin/python3 lab/upgrade.py check [--to REF]    what would change, and whether it can
  /usr/bin/python3 lab/upgrade.py start [--to REF]    back up, pull images, move the checkout
  /usr/bin/python3 lab/upgrade.py status              the last upgrade and its outcome
  /usr/bin/python3 lab/upgrade.py rollback            move back to the version before it

REF defaults to origin/main, fetched first. After `start` or `rollback`, restart Sbarbase
(`docker compose up -d --build`, or `sudo systemctl restart sbarbase`). The next start
replaces Auth, REST and Storage containers whose pinned image or configuration changed,
and nothing else. If that start fails, the supervisor moves the checkout back by itself
and exits, and the restart policy starts the previous version again.

A version that changes the PostgreSQL image is refused: replacing the database container
is lab/migrate-generation.py's job, never an upgrade's. Every environment is backed up
before anything moves, and those backups stay after the upgrade.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import durable_runtime as runtime
import install_server
import run as lab

ROOT = lab.ROOT
STATE_FILE = lab.STATE / 'upgrades' / 'state.json'
INTENT = runtime.UPGRADE_INTENT
DATABASE_LOCK = 'distro-image.lock.json'
# Which lock entry each replaceable service runs, as durable_runtime reads them.
SERVICES = {'auth': ('images.lock.json', 'auth'), 'rest': ('images.lock.json', 'rest'),
            'storage': ('storage-image.lock.json', None), 'realtime': ('realtime-image.lock.json', None),
            'functions': ('functions-image.lock.json', None)}
DEFAULT_TARGET = 'origin/main'
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


def plan(target_ref):
    current = git('rev-parse', 'HEAD')
    target = resolve(target_ref)
    refusals = []
    if git('status', '--porcelain', '--untracked-files=no'):
        refusals.append('The checkout has local changes to tracked files; commit or discard them first')
    if target == current:
        refusals.append('Already at this version')
    database = lock_at(current, DATABASE_LOCK), lock_at(target, DATABASE_LOCK)
    if (database[0] or {}).get('id') != (database[1] or {}).get('id'):
        refusals.append('This version changes the PostgreSQL image; that needs lab/migrate-generation.py, not an upgrade')
    state = load_state()
    if state and state.get('phase') in ('applied', 'rolling_back'):
        refusals.append(f"The last {'upgrade' if state['phase'] == 'applied' else 'rollback'} has not started yet; restart Sbarbase first")
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
    result = subprocess.run(['/usr/bin/python3', 'lab/backup.py', 'create', 'all'], cwd=ROOT, text=True)
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


def checkout(commit, intent):
    """Records which images the next start may replace, then moves the checkout."""
    previous = git('rev-parse', 'HEAD')
    INTENT.parent.mkdir(parents=True, exist_ok=True)
    lab.atomic(INTENT, intent)
    try:
        git('checkout', '-q', '--detach', commit)
    except UpgradeError:
        INTENT.unlink(missing_ok=True)
        raise
    owner_of_checkout(git('diff', '--name-only', previous, commit).splitlines())
    install_dependencies()


def install_dependencies():
    if subprocess.run(['bun', 'install', '--frozen-lockfile'], cwd=ROOT).returncode:
        raise UpgradeError('bun install failed for this version')


def start(target_ref):
    details = plan(target_ref)
    report(details, target_ref)
    if details['refusals']:
        raise UpgradeError('Nothing was changed')
    target = details['target']
    pins = pins_at(target)
    pull(target)
    back_up()
    record = {'phase': 'applied', 'from': details['current'], 'to': target, 'started_at': now(),
              'changes': details['changes'], 'automatic': False}
    save_state(record)
    try:
        checkout(target, {'pins': pins, 'from': details['current'], 'to': target})
    except UpgradeError as error:
        # The checkout did not move, or moved without its dependencies: go back to where it was.
        git('checkout', '-q', '--detach', details['current'], check=False)
        INTENT.unlink(missing_ok=True)
        save_state({**record, 'phase': 'failed', 'failure': str(error), 'finished_at': now()})
        raise
    print(f'The checkout is at {target[:12]}. Restart Sbarbase now:\n  {RESTART}')
    print('If the new version does not start, Sbarbase moves back by itself.')


def rollback(automatic=False):
    state = load_state()
    if not state or state.get('phase') not in ('applied', 'confirmed'):
        raise UpgradeError('There is no upgrade to roll back')
    source = state['from']
    pins = pins_at(source)
    pull(source)
    state.update({'phase': 'rolling_back', 'automatic': automatic, 'rollback_at': now()})
    save_state(state)
    checkout(source, {'pins': pins, 'from': state['to'], 'to': source})
    print(f'The checkout is back at {source[:12]}.' + ('' if automatic else f' Restart Sbarbase now:\n  {RESTART}'))


def after_start(started):
    """Called by the supervisor once its start has succeeded or failed.

    Returns True when a failed start moved the checkout back, so the caller exits and
    the restart policy brings up the previous version.
    """
    state = load_state()
    if not state or state.get('phase') not in ('applied', 'rolling_back'):
        return False
    if started:
        state.update({'phase': 'confirmed' if state['phase'] == 'applied' else 'rolled_back', 'finished_at': now()})
        save_state(state)
        INTENT.unlink(missing_ok=True)
        return False
    if state['phase'] == 'applied':
        try:
            rollback(automatic=True)
        except (UpgradeError, SystemExit) as error:
            state.update({'phase': 'rollback_failed', 'failure': str(error), 'finished_at': now()})
            save_state(state)
            return False
        return True
    state.update({'phase': 'rollback_failed', 'failure': 'The previous version did not start either', 'finished_at': now()})
    save_state(state)
    return False


def status():
    state = load_state()
    if not state:
        print('No upgrade has run on this installation.')
        return
    words = {'applied': 'waiting for a restart onto the new version',
             'confirmed': 'the new version started',
             'rolling_back': 'waiting for a restart onto the previous version',
             'rolled_back': 'back on the previous version' + (' (automatic, the new version did not start)' if state.get('automatic') else ''),
             'rollback_failed': 'the previous version did not start; restore from the backups taken before the upgrade',
             'failed': 'the upgrade stopped before the checkout moved; nothing changed'}
    print(f"upgrade  {state['from'][:12]} -> {state['to'][:12]}, started {state['started_at']}")
    print(f"result   {state['phase']}: {words.get(state['phase'], '')}")
    if state.get('failure'):
        print('reason   ' + state['failure'])
    print(f"now at   {git('rev-parse', 'HEAD')[:12]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description='Upgrade Sbarbase, with a way back')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'start'):
        sub.add_parser(name).add_argument('--to', default=DEFAULT_TARGET)
    sub.add_parser('status')
    sub.add_parser('rollback')
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    try:
        if args.command == 'check':
            details = plan(args.to)
            report(details, args.to)
            return 1 if details['refusals'] else 0
        if args.command == 'start':
            start(args.to)
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
