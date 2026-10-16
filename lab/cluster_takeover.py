"""Take a rebuilt database cluster into use (upstream currency P3, part 4).

    /usr/bin/python3 lab/cluster_takeover.py

Design: docs/engineering/WHOLE-CLUSTER-RESTORE.md. lab/cluster_rebuild.py leaves a completed rebuild
in .lab/upstream/cluster-rebuild.json: a volume ``sbarbase-durable-pgdata-<12 hex>`` owned by the
runtime and the image it was built on. This command, with Sbarbase stopped (the upgrade,
supervisor, backup and worker locks held; the migration shares the worker lock through --worker-fd), replaces the installation's database container onto that
volume and image through the generation migration (lab/migrate-generation.py), so the runtime
mounts it from then on. The old volume is kept.

The checkout's PostgreSQL lock must name the rebuilt image, because the runtime checks the running
database against it. When it names another one and a database move is recorded, the checkout is
moved back to the move's from version (lab/upgrade.py move_back) only when that version's lock
names the rebuilt image and its lab/hba_migration.py carries this supersede protocol
(SUPERSEDE_PROTOCOL; the migration then runs from that checkout); anything else refuses.

What runs depends on the migration record:

- none: a generation migration onto the rebuilt volume and image;
- a stuck move's (its retired container captured, archived and removed, its replacement never
  captured): that migration is superseded (lab/hba_migration.py supersede) and a new one onto the
  rebuilt volume finishes from its archive;
- one already onto the rebuilt volume and image (an interrupted earlier run of this command): it is
  finished;
- any other: refused before the checkout moves.

Once the runtime names the rebuilt volume, a recorded database move that has begun is archived as
superseded by the rebuild (database-move-<to>-<its start>-superseded.json, beside the moves that
completed) and its record removed; its backups are kept from pruning first. Once everything that can
refuse is proven (the record, the way back, the rebuilt volume), the move record is marked as taken
over, so until it is archived a start and lab/upgrade.py database --continue point back here
(lab/database_move.py taken_over); --release unmarks it while no migration onto the rebuilt volume
is recorded or waiting. A prepared move refuses: abandon it first. Every step can be run again.
Unit tested only; nothing of it has run against a real daemon.
"""
import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys

import backup
import cluster_rebuild
import database_move
import database_volume
import durable_runtime as runtime
import hba_migration
import release_channel
import run as lab
import upgrade


class TakeoverError(RuntimeError):
    pass


def rebuilt():
    """The completed rebuild to take into use."""
    try:
        record = json.loads(cluster_rebuild.RECORD.read_text())
    except FileNotFoundError:
        raise TakeoverError('No cluster rebuild is recorded; run lab/cluster_rebuild.py first') from None
    except (OSError, ValueError):
        raise TakeoverError('The cluster rebuild record cannot be read; it needs review') from None
    if not isinstance(record, dict) or record.get('phase') != 'completed':
        raise TakeoverError('The last cluster rebuild did not complete; rebuild again first')
    if not database_volume.rebuilt(record.get('volume')) or not isinstance(record.get('image'), str) \
            or not record['image'].startswith('sha256:'):
        raise TakeoverError('The cluster rebuild record is malformed; it needs review')
    return record


def image_of(commit):
    """The daemon's identity of the PostgreSQL lock at ``commit``, or None when it cannot be proved."""
    try:
        pin = release_channel.lock_at(commit, release_channel.DATABASE_LOCK)
        return backup.resolve_image(pin)[1] if pin else None
    except (backup.BackupError, release_channel.ReleaseError, upgrade.UpgradeError):
        return None


def needs_move_back(record, move):
    """Whether the checkout must move back to the move's from version, proven before anything is
    marked: that version's lock names the rebuilt image and its migration can finish this one. A
    move back a kill interrupted is done again, since only its end shows in the lock file."""
    if move is not None and (move.get('taken_over') or {}).get('checkout') == 'moving':
        return True
    if database_move.lock_image() == record['image']:
        return False
    if move is None or image_of(move['from']) != record['image']:
        raise TakeoverError("This checkout's PostgreSQL lock names another image than the rebuilt cluster's, and no "
                            "recorded move leads back to a version whose lock names it; check out that version first")
    # The migration then runs from that version's checkout, so it must finish this one exactly as
    # this one would: its supersede protocol is this one's.
    source = upgrade.git('show', f"{move['from']}:lab/hba_migration.py", check=False)
    if hba_migration.PROTOCOL_LINE not in (source or '').splitlines():
        raise TakeoverError(f"The version {move['from'][:12]} cannot take a rebuilt cluster into use (its "
                            'lab/hba_migration.py does not have this supersede protocol); nothing was changed')
    return True


def mark(move, **changes):
    """Record in the move how far the takeover went; a start and database --continue point here."""
    move = {**move, 'taken_over': {**(move.get('taken_over') or {}), **changes}}
    database_move.save(move)
    return move


def move_back(record, move):
    """The guard's verified move back to the from version, recorded before and after, so a rerun
    after a kill inside it moves back again and installs that version's dependencies."""
    move = mark(move, checkout='moving')
    try:
        upgrade.move_back(move['from'], None, move)
    except upgrade.UpgradeError as error:
        raise TakeoverError(f"The checkout could not be moved back to {move['from'][:12]} ({error}); run this "
                            'command again') from None
    if database_move.lock_image() != record['image']:
        raise TakeoverError("The checkout's PostgreSQL lock still names another image")
    return mark(move, checkout='moved')


def prove_volume(record):
    """The rebuilt volume can take the database (owned by the runtime, holding a data directory of
    the image's major version), proven before anything is marked; the migration proves it again."""
    owner = hba_migration.pinned(runtime.STATE)['target']['owner']
    try:
        hba_migration.require_cluster_volume(lab.docker, record['volume'], owner, record['image'], runtime.STATE)
    except RuntimeError as error:
        raise TakeoverError(f"The rebuilt volume cannot take the database ({error}); nothing was changed") from None


def migration_command(record):
    """The generation migration to run, or None when the runtime already mounts the rebuilt volume."""
    state = runtime.STATE
    hba_migration.discard_unpublished(state)
    if hba_migration.present(state):
        intent = hba_migration.load(state)
        if hba_migration.target_volume(intent) == record['volume']:
            if hba_migration.target_image(intent) != record['image']:
                raise TakeoverError('A migration onto the rebuilt volume but another image is recorded; it needs review')
            return database_move.migration_command(True)
        if 'supersedes' in intent:
            raise TakeoverError('A superseding migration onto another volume is recorded; it needs review')
        # Checked here too, so a record that cannot be superseded refuses before the checkout moves.
        refusal = hba_migration.stuck_refusal(lab.docker, state, intent)
        if refusal:
            raise TakeoverError(refusal)
        return database_move.migration_command(False) + ['--to-volume', record['volume'], '--supersede-stuck']
    if hba_migration.pending_superseded(state) is not None:
        return database_move.migration_command(False) + ['--to-volume', record['volume'], '--supersede-stuck']
    if database_volume.current(state) == record['volume']:
        return None
    return database_move.migration_command(False) + ['--to-volume', record['volume']]


def retire_move(move, record):
    """Archive a begun move as superseded by the rebuild, then remove its record. Its backups are
    kept from pruning first, as the move itself would have kept them."""
    if move.get('backup') and not move.get('marked'):
        try:
            backup.mark_moved(move['backup'])
        except (OSError, backup.BackupError) as error:
            raise TakeoverError(f"The move's backup run {move['backup']} could not be kept from pruning ({error}); "
                                'run this command again') from None
        move = {**move, 'marked': True}
    retired = {**move, 'phase': 'superseded', 'superseded_by': {'run': record['run'], 'volume': record['volume'],
                                                                'image': record['image']},
               'finished_at': upgrade.now()}
    # Named by the move's own start, so another superseded move to the same version keeps its own.
    started = ''.join(character for character in str(move.get('started_at', '')) if character.isalnum()) or 'unknown'
    lab.atomic(database_move.STATE_FILE.with_name(f"database-move-{move['to'][:12]}-{started}-superseded.json"), retired)
    database_move.STATE_FILE.unlink()


@contextlib.contextmanager
def held_locks():
    """Sbarbase stopped for the whole takeover: the upgrade, supervisor, backup and worker locks held.
    Yields the worker lock's descriptor: the generation migration this command runs shares it
    (--worker-fd, hba_startup.acquire) instead of opening its own, which the held lock would refuse."""
    with contextlib.ExitStack() as stack:
        for path, refusal in ((upgrade.LOCK, 'Another upgrade, rollback or database move is running'),
                              (upgrade.SUPERVISOR_LOCK, 'Sbarbase is running; stop it first'),
                              (upgrade.BACKUP_LOCK, 'A backup or restore is running')):
            stack.enter_context(upgrade.exclusive(path, refusal))
        descriptor = os.open(runtime.STATE / 'worker.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        stack.callback(os.close, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TakeoverError('The provisioning worker is running; stop Sbarbase first') from None
        yield descriptor


def takeover(run=subprocess.run):
    with held_locks() as worker:
        record = rebuilt()
        move = database_move.load()
        if move is not None and move['phase'] == 'prepared':
            raise TakeoverError('A database move is prepared; abandon it first (lab/upgrade.py database --abandon)')
        # Everything that can refuse is proven before the move is marked: the record classified,
        # the way back and the rebuilt volume. A refusal then leaves the move and the checkout as
        # they were.
        command = migration_command(record)
        back = needs_move_back(record, move)
        if command is not None and database_volume.current(runtime.STATE) != record['volume']:
            prove_volume(record)
        if move is not None and not move.get('taken_over'):
            move = mark(move, run=record['run'], volume=record['volume'])
        if back:
            move = move_back(record, move)
        if command is not None and run(command + ['--worker-fd', str(worker)], cwd=lab.ROOT,
                                       pass_fds=(worker,)).returncode:
            raise TakeoverError('The generation migration did not finish; run this command again '
                                '(it finishes the interrupted migration)')
        # Checked whether or not a migration ran: no record left, the rebuilt volume named, the pin
        # on the rebuilt image.
        if hba_migration.present(runtime.STATE) or database_volume.current(runtime.STATE) != record['volume'] \
                or hba_migration.pinned(runtime.STATE)['target']['image'] != record['image']:
            raise TakeoverError('The generation migration did not finish; run this command again '
                                '(it finishes the interrupted migration)' if command is not None else
                                'The runtime names the rebuilt volume but its database pin names another image; '
                                'it needs review')
        if move is not None:
            retire_move(move, record)
        return record, move, command is not None


def release():
    """Unmark a move whose takeover cannot go on, so the move's own commands apply again: only while
    no migration onto a rebuilt volume or superseding one is recorded or waiting. The checkout stays
    where the takeover left it."""
    with held_locks():
        move = database_move.load()
        if move is None or not move.get('taken_over'):
            raise TakeoverError('No database move is marked as taken over')
        state = runtime.STATE
        hba_migration.discard_unpublished(state)
        if hba_migration.pending_superseded(state) is not None or (
                hba_migration.present(state) and 'new_volume' in hba_migration.load(state)):
            raise TakeoverError('A migration onto a rebuilt volume is recorded or waiting; finish the takeover')
        try:
            rebuild = rebuilt()
        except TakeoverError:
            rebuild = None
        if rebuild is not None and database_volume.current(state) == rebuild['volume']:
            raise TakeoverError('The runtime already names the rebuilt volume; finish the takeover')
        database_move.save({key: value for key, value in move.items() if key != 'taken_over'})
        return move


def main(argv=None):
    parser = argparse.ArgumentParser(description='Take a rebuilt database cluster into use')
    parser.add_argument('--release', action='store_true',
                        help='unmark a move whose takeover cannot go on, before any migration onto the rebuilt volume')
    args = parser.parse_args(argv)
    if args.release:
        try:
            move = release()
        except (RuntimeError, ValueError, upgrade.UpgradeError) as error:
            print(f'refused: {error}', file=sys.stderr)
            return 1
        print(f"The database move to {move['to'][:12]} is no longer marked as taken over; "
              'lab/upgrade.py database applies to it again.')
        return 0
    try:
        record, move, migrated = takeover()
    except (RuntimeError, ValueError, upgrade.UpgradeError) as error:
        # Fixed sentences: the takeover's own, the locks', the records' and the migration record's.
        print(f'refused: {error}', file=sys.stderr)
        return 1
    if not migrated and move is None:
        print(f"The database already runs on {record['volume']}; nothing was changed.")
        return 0
    print(f"The database runs {record['image']} on {record['volume']}; the previous volume is kept.")
    if move is not None:
        print(f"The database move to {move['to'][:12]} was archived as superseded by the rebuild.")
    print('Start Sbarbase and run the checks; writes made after the backup run were not restored.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
