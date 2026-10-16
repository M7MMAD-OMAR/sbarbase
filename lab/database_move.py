"""The attended move to a Sbarbase version whose PostgreSQL image differs (upstream currency P1).

`lab/upgrade.py start` refuses such a version: the database container is replaced only by
lab/migrate-generation.py. This flow does both in one maintenance window, in two commands:

  /usr/bin/python3 lab/upgrade.py database --to REF     while Sbarbase runs, with every
                                                        environment's writes paused
  (stop Sbarbase)
  /usr/bin/python3 lab/upgrade.py database --continue   moves the checkout and replaces the
                                                        database onto the new lock's image
  (start Sbarbase)

`--to` changes nothing it cannot take back: it refuses unless the target's image pins differ from
this version's in the PostgreSQL image alone (only the pins are compared: other changes in the target
start with it, without the health checks and automatic way back `start` gives, and the snapshot is
not restored by this flow and may be pruned by later upgrades), pulls the target's images, backs up every environment and
the management database, snapshots the control state and records the move as `prepared`.
`--abandon` drops a prepared move. `--continue` refuses while Sbarbase or the old database runs, records `moving`
before the checkout moves and `migrating` once it has, then runs the generation migration with
--image-from-lock (or --reconcile, when an interrupted one is waiting). Each step can be run
again after an interruption, including one after the old container was removed and before the
replacement was ready, which the rerun adopts and starts again. Not covered: a kill while Git
writes the checkout can leave this very code half written, so the rerun (and the start refusal
below) may not run at all; the upgrades guide gives the manual `git checkout -f <to>`. Once the checkout has moved, the
backups of `--to` are marked as an upgrade that moved it, so pruning keeps them like an upgrade's,
before the database is changed; a mark that fails stops the move until a rerun succeeds. Once --continue has begun, Sbarbase refuses to start until the move
completes (before_start), and the generation migration record and the image check refuse too; a
prepared move leaves the running version alone.

Before --continue records the move as started it refuses, changing nothing, while an operation
record waits to settle, or when an environment was published or its write pause resumed since the
backups (the management console is not checked: operator changes made between the two steps are
not in the management backup); it also admits the target's images again. Once it has recorded the move as started there
is no way back: --abandon refuses from then on. After
that, a move that stays stuck (a generation change that keeps refusing, or a new image that never
becomes ready once the old container is removed) has no exit here: a restore into a fresh
cluster, upstream currency P3, is not built yet, so the move is rehearsed on a copy first.

The return to the previous image is the same flow towards that version. It is safe only when the
newer image did not change extension versions or rewrite catalogs on the volume; otherwise the
way back is a restore. Each command holds the upgrade lock while it runs, and while a move is
recorded `lab/upgrade.py start`, `rollback` and another move refuse.
"""
import contextlib
import fcntl
import json
import subprocess
import sys

import backup
import durable_runtime as runtime
import hba_migration
import image_identity
import release_channel
import run as lab
import upgrade
import write_pause

STATE_FILE = upgrade.DATABASE_MOVE
PHASES = ('prepared', 'moving', 'migrating')


class MoveError(upgrade.UpgradeError):
    pass


def load():
    try:
        record = json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise MoveError('The database move record cannot be read; it needs review') from None
    if not isinstance(record, dict) or record.get('phase') not in PHASES \
            or not all(isinstance(record.get(key), str) and len(record[key]) == 40 for key in ('from', 'to')):
        raise MoveError('The database move record is malformed; it needs review')
    return record


def save(record):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lab.atomic(STATE_FILE, record)


def published():
    """The published environments; an unreadable list refuses rather than reading as none."""
    path = runtime.STATE / 'endpoints.json'
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        raise MoveError('The published environments cannot be read; nothing was changed') from None
    if not isinstance(value, dict):
        raise MoveError('The published environments are malformed; nothing was changed')
    return value


def unpaused_environments(published=None):
    """Published environments whose writes are not held by a write pause (lab/write_pause.py)."""
    held = {item['runtime'] for item in write_pause.status() if item.get('phase') == 'held'}
    published = globals()['published']() if published is None else published
    return sorted(e for e in published if e not in held)


def held_pauses():
    """Each published environment and when its write pause took hold: the backups of --to are the
    way back only while this stays the same (no environment published since, no pause resumed)."""
    held = {item['runtime']: item.get('held_at') for item in write_pause.status() if item.get('phase') == 'held'}
    return {e: held.get(e) for e in sorted(published())}


def plan(target_ref):
    current = upgrade.git('rev-parse', 'HEAD')
    target = upgrade.resolve(target_ref)
    refusals = []
    if load() is not None:
        refusals.append('A database move is already recorded; continue or abandon it first')
    if upgrade.local_changes():
        refusals.append('The checkout has local changes to tracked files; commit or discard them first')
    if target == current:
        refusals.append('Already at this version')
    diffs = list(release_channel.pin_diffs(current, target))
    if not release_channel.database_changes(diffs):
        refusals.append('This version keeps the PostgreSQL image; move to it with lab/upgrade.py start')
    others = [f"{name} {entry}" for name, entry, _, _ in diffs if name != release_channel.DATABASE_LOCK]
    if others:
        refusals.append('This version also changes other images (' + ', '.join(others) + '); move to a version '
                        'that changes only the PostgreSQL image, after taking the others with lab/upgrade.py start')
    script = upgrade.git('show', f'{target}:lab/migrate-generation.py', check=False)
    if '--image-from-lock' not in (script or ''):
        refusals.append('That version cannot replace the database onto its own lock image '
                        '(its lab/migrate-generation.py has no --image-from-lock); the way there is a restore')
    try:
        unpaused = unpaused_environments()
    except MoveError as error:
        unpaused = []
        refusals.append(str(error))
    if unpaused:
        refusals.append('Pause writes first, every environment (lab/write_pause.py pause): ' + ', '.join(unpaused))
    # The recorded move is named above; the others are an upgrade's own transient refusals.
    refusals += [item for item in upgrade.transient_refusals(lock=False) if 'database move' not in item]
    return {'current': current, 'target': target, 'diffs': diffs, 'refusals': refusals}


def prepare(target_ref):
    with upgrade.exclusive(upgrade.LOCK, 'Another upgrade, rollback or database move is running'):
        details = plan(target_ref)
        for refusal in details['refusals']:
            print('refused  ' + refusal)
        if details['refusals']:
            raise MoveError('Nothing was changed', details['refusals'])
        current, target = details['current'], details['target']
        upgrade.pull(target)
        run = upgrade.back_up()
        try:
            taken = upgrade.snapshot(current)
        except (upgrade.UpgradeError, OSError) as error:
            raise MoveError(f'{error}; nothing was changed') from None
        images = {side: (release_channel.lock_at(commit, release_channel.DATABASE_LOCK) or {}).get('tag')
                  for side, commit in (('from', current), ('to', target))}
        save({'version': 1, 'phase': 'prepared', 'from': current, 'to': target, 'started_at': upgrade.now(),
              'images': images, 'snapshot': taken, 'pauses': held_pauses(), **({'backup': run} if run else {})})
        print(f"Prepared: {images['from']} -> {images['to']}. Every environment was backed up first.")
        print('Stop Sbarbase now, then run: /usr/bin/python3 lab/upgrade.py database --continue')


def abandon():
    with upgrade.exclusive(upgrade.LOCK, 'Another upgrade, rollback or database move is running'):
        record = load()
        if record is None:
            raise MoveError('No database move is recorded')
        if record['phase'] != 'prepared':
            raise MoveError('The move has started; finish it with: lab/upgrade.py database --continue')
        STATE_FILE.unlink()
        print('The prepared database move was dropped; nothing had moved.')


def database_stopped():
    item = runtime.inspect('container', runtime.DB)
    return not (item and item.get('State', {}).get('Running'))


def migration_command(reconcile):
    command = ['/usr/bin/python3', 'lab/migrate-generation.py', '--state', str(runtime.STATE),
               '--volume', runtime.PREFIX + '-pgdata', '--network', runtime.NETWORK, '--tier', 'system.db',
               '--inventory-runtime', str(runtime.PRIVATE / 'runtime.json'),
               '--retired', 'stopped', '--assert-retired-will-not-return',
               '--attended-retained', '--confirm-retained', runtime.DB]
    return command + (['--reconcile'] if reconcile else ['--image-from-lock'])


def lock_image():
    """The daemon's identity for this checkout's PostgreSQL lock, as the runtime checks it."""
    reference = image_identity.reference(json.loads((lab.ROOT / 'lab' / release_channel.DATABASE_LOCK).read_text()))
    try:
        result = lab.docker('image', 'inspect', reference, check=False)
        if result.returncode:
            raise image_identity.inspection_failure(reference, result.stdout, result.stderr)
        return image_identity.resolved_id(reference, image_identity.record(result.stdout))
    except image_identity.IdentityError as error:
        raise MoveError(f'The PostgreSQL image {reference} is not proved on this host ({error}); pull it '
                        f'(docker pull {reference}), then continue the move') from None


def already_moved():
    """True when the generation pin already names the lock's image: the change finished and only
    the record was left, so it must not run a second time."""
    return not hba_migration.present(runtime.STATE) and hba_migration.pinned(runtime.STATE)['target']['image'] == lock_image()


@contextlib.contextmanager
def supervisor_lock():
    """Held for the whole move, so Sbarbase cannot start in the middle of it."""
    path = upgrade.SUPERVISOR_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MoveError('Sbarbase is running; stop it first, then continue the move') from None
        yield


def resume(run=subprocess.run):
    """Runs the move from wherever it stopped. Every step can be repeated."""
    with upgrade.exclusive(upgrade.LOCK, 'Another upgrade, rollback or database move is running'), supervisor_lock():
        record = load()
        if record is None:
            raise MoveError('No database move is recorded; start one with: lab/upgrade.py database --to REF')
        # The old database must be down until the generation change begins; after that the
        # container that runs is the replacement it created (its record waits, or the pin already
        # names the lock image), and the rerun finishes it.
        if not database_stopped() and (record['phase'] == 'prepared' or not (
                hba_migration.present(runtime.STATE) or (record['phase'] == 'migrating' and already_moved()))):
            raise MoveError('The old database container still runs; stop Sbarbase so it stops, then continue the move')
        if record['phase'] == 'prepared':
            # Sbarbase ran between --to and the stop, so an operation record may have appeared
            # since: the generation change would refuse on it after the checkout moved, with no
            # way back. While the move is only prepared, Sbarbase can start and settle it.
            unsettled = [item for item in upgrade.transient_refusals(lock=False) if 'database move' not in item]
            if unsettled:
                raise MoveError('Nothing was moved; settle these first (start Sbarbase so it settles them, then stop '
                                'it and continue the move): ' + '; '.join(unsettled))
            # The backups of --to hold every write only while every environment then published is
            # still paused by the same pause, and none was published since.
            if 'pauses' in record and held_pauses() != record['pauses']:
                raise MoveError('Nothing was moved; an environment was published, or its write pause resumed, since '
                                'the backups were taken: abandon the move (--abandon) and prepare it again')
            # The target's images may have been removed since --to; they are admitted again first.
            upgrade.pull(record['to'])
            record = {**record, 'phase': 'moving'}
            save(record)
        if record['phase'] == 'moving':
            # The guard's verified move: local changes aside, a clean tree at `to`, its dependencies.
            upgrade.move_back(record['to'], None, record)
            record = {**record, 'phase': 'migrating'}
            save(record)
        if upgrade.git('rev-parse', 'HEAD') != record['to'] or upgrade.local_changes():
            raise MoveError(f"The checkout is not a clean {record['to'][:12]}: as the service account, run "
                            f"git checkout -f {record['to']}, then continue the move")
        if record.get('backup') and not record.get('marked'):
            # As an upgrade that moved the checkout: these backups are the way back, so they count
            # among the last upgrades' and are kept from pruning (lab/backup.py mark_moved). Until
            # that is recorded the database is not changed; a rerun marks them again.
            try:
                backup.mark_moved(record['backup'])
            except (OSError, backup.BackupError) as error:
                raise MoveError(f"The backup run {record['backup']} could not be kept from pruning ({error}); "
                                'nothing was changed in the database, run the move again') from None
            record = {**record, 'marked': True}
            save(record)
        # An empty record a kill left before its intent never started; it does not need --reconcile.
        hba_migration.discard_unpublished(runtime.STATE)
        if not already_moved():
            result = run(migration_command(hba_migration.present(runtime.STATE)), cwd=lab.ROOT)
            if result.returncode or not already_moved():
                raise MoveError('The database generation change did not finish; run the move again '
                                '(it reconciles the interrupted change)')
        done = {**record, 'phase': 'completed', 'finished_at': upgrade.now()}
        lab.atomic(STATE_FILE.with_name(f"database-move-{record['to'][:12]}.json"), done)
        STATE_FILE.unlink()
        print(f"The database runs {record['images']['to']} and the checkout is at {record['to'][:12]}.")
        print('Start Sbarbase, run the checks after the change, then resume writes: lab/write_pause.py resume <environment>')


def start_refusal():
    """Why Sbarbase must not start now: a move that has begun is finished first, and a record that
    cannot be read is reviewed first."""
    try:
        record = load()
    except MoveError as error:
        return f'{error}; Sbarbase does not start until it is reviewed'
    if record is not None and record['phase'] != 'prepared':
        return 'A database move is under way; finish it with: /usr/bin/python3 lab/upgrade.py database --continue'
    return None


def status():
    record = load()
    if record is None:
        print('No database move is recorded')
        return
    print(f"{record['phase']}  {record['images']['from']} -> {record['images']['to']}  "
          f"({record['from'][:12]} -> {record['to'][:12]})")


def main(args):
    if args.abandon:
        abandon()
    elif args.resume:
        resume()
    elif args.to:
        prepare(args.to)
    else:
        status()


if __name__ == '__main__':
    print('Run it through: /usr/bin/python3 lab/upgrade.py database', file=sys.stderr)
    sys.exit(2)
