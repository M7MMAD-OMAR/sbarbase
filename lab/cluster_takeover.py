"""Take a rebuilt database cluster into use (upstream currency P3, part 4).

    /usr/bin/python3 lab/cluster_takeover.py

Design: docs/engineering/WHOLE-CLUSTER-RESTORE.md. lab/cluster_rebuild.py leaves a completed rebuild
in .lab/upstream/cluster-rebuild.json: a volume ``sbarbase-durable-pgdata-<12 hex>`` owned by the
runtime and the image it was built on. This command, with Sbarbase stopped (the upgrade,
supervisor, worker and backup locks held), replaces the installation's database container onto that
volume and image through the generation migration (lab/migrate-generation.py), so the runtime
mounts it from then on. The old volume is kept.

The checkout's PostgreSQL lock must name the rebuilt image, because the runtime checks the running
database against it. When it names another one and a database move is recorded, the checkout is
moved back to the move's from version (lab/upgrade.py move_back) only when that version's lock
names the rebuilt image and its generation migration knows --supersede-stuck (the migration then
runs from that checkout); anything else refuses.

What runs depends on the migration record:

- none: a generation migration onto the rebuilt volume and image;
- a stuck move's (its retired container captured, archived and removed, its replacement never
  captured): that migration is superseded (lab/hba_migration.py supersede) and a new one onto the
  rebuilt volume finishes from its archive;
- one already onto the rebuilt volume and image (an interrupted earlier run of this command): it is
  finished;
- any other: refused, unchanged.

Once the runtime names the rebuilt volume, a recorded database move that has begun is archived as
superseded by the rebuild, beside the moves that completed, and its record removed; its backups
are kept from pruning first. A prepared move refuses: abandon it first. Every step can be run again.
Unit tested only; nothing of it has run against a real daemon.
"""
import argparse
import json
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


def align_checkout(record, move):
    """The checkout's lock names the rebuilt image, moving back to the move's from version if needed."""
    if database_move.lock_image() == record['image']:
        return False
    if move is None or image_of(move['from']) != record['image']:
        raise TakeoverError("This checkout's PostgreSQL lock names another image than the rebuilt cluster's, and no "
                            "recorded move leads back to a version whose lock names it; check out that version first")
    # The migration then runs from that version's checkout, so it must know how to finish this one.
    script = upgrade.git('show', f"{move['from']}:lab/migrate-generation.py", check=False)
    if '--supersede-stuck' not in (script or ''):
        raise TakeoverError(f"The version {move['from'][:12]} cannot take a rebuilt cluster into use (its "
                            'lab/migrate-generation.py has no --supersede-stuck); nothing was changed')
    try:
        upgrade.move_back(move['from'], None, move)
    except upgrade.UpgradeError as error:
        raise TakeoverError(f"The checkout could not be moved back to {move['from'][:12]} ({error})") from None
    if database_move.lock_image() != record['image']:
        raise TakeoverError("The checkout's PostgreSQL lock still names another image")
    return True


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
    lab.atomic(database_move.STATE_FILE.with_name(f"database-move-{move['to'][:12]}-superseded.json"), retired)
    database_move.STATE_FILE.unlink()


def takeover(run=subprocess.run):
    with upgrade.exclusive(upgrade.LOCK, 'Another upgrade, rollback or database move is running'), \
            cluster_rebuild.held_locks():
        record = rebuilt()
        move = database_move.load()
        if move is not None and move['phase'] == 'prepared':
            raise TakeoverError('A database move is prepared; abandon it first (lab/upgrade.py database --abandon)')
        # The record is classified first, so a refusal leaves the checkout where it was.
        command = migration_command(record)
        align_checkout(record, move)
        if command is not None:
            result = run(command, cwd=lab.ROOT)
            if result.returncode or hba_migration.present(runtime.STATE) \
                    or database_volume.current(runtime.STATE) != record['volume'] \
                    or hba_migration.pinned(runtime.STATE)['target']['image'] != record['image']:
                raise TakeoverError('The generation migration did not finish; run this command again '
                                    '(it finishes the interrupted migration)')
        if move is not None:
            retire_move(move, record)
        return record, move


def main(argv=None):
    argparse.ArgumentParser(description='Take a rebuilt database cluster into use').parse_args(argv)
    try:
        record, move = takeover()
    except (RuntimeError, ValueError, upgrade.UpgradeError) as error:
        # Fixed sentences: the takeover's own, the locks', the records' and the migration record's.
        print(f'refused: {error}', file=sys.stderr)
        return 1
    print(f"The database runs {record['image']} on {record['volume']}; the previous volume is kept.")
    if move is not None:
        print(f"The database move to {move['to'][:12]} was archived as superseded by the rebuild.")
    print('Start Sbarbase and run the checks; writes made after the backup run were not restored.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
