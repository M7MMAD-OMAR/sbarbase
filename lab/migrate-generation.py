"""Explicit operator migration of one managed database container generation.

Usage:
  /usr/bin/python3 lab/migrate-generation.py --state .lab/upstream \
      --volume sbarbase-durable-pgdata --network sbarbase-durable-net \
      --tier system.db --inventory-runtime .secrets/upstream/runtime.json \
      --retired stopped --assert-retired-will-not-return

  /usr/bin/python3 lab/migrate-generation.py --state .lab/upstream ... --reconcile

The database identity (name, owner, pinned image, retired container) comes from
the established generation pin, never from a command line, so a migration cannot
be pointed at a different database. The retired container is stopped by this
operation only when --assert-retired-will-not-return is given, and it is removed
by its exact captured id.

The retained placement (see REFUSED_OWNERS) is refused unless the run is attended:
--attended-retained together with --confirm-retained naming the pinned container
exactly. That is a deliberate operator run with the runtime stopped, as
lab/upgrade.py database --continue performs it; nothing else recreates the
retained installation's database while the runtime supervises it.

A new migration also refuses while an upgrade or rollback is not confirmed yet
(no_pending_upgrade); finishing an interrupted one with --reconcile does not.

--image-from-lock replaces the database onto the image of this checkout's distribution lock
instead of the pinned one: same name, owner and volume, a new image. The image is proven on this
daemon first and, when it differs from the pinned one, recorded in the migration intent, so
--reconcile finishes onto that image even if
the lock has changed since.

--to-volume NAME replaces it onto another existing pgdata volume (a cluster rebuilt from a backup
run, upstream currency P3, named sbarbase-durable-pgdata-<12 hex>), with or without
--image-from-lock. Before the intent is written, and again before the retired container is
touched, the volume must exist, be owned by the runtime and hold a PostgreSQL data directory of the
major version the image runs (an empty one would start a new, empty cluster). The intent records it, and once the change reaches
its archive (after the rules are published and compared) lab/database_volume.py names it, so
the runtime mounts it from then on. The old volume is
kept.

Read docs/engineering/CONTAINER-GENERATION-MIGRATION.md before running it. An interrupted run
leaves a private migration record in place; startup refuses until either this
command finishes the migration or an operator settles the HBA journal it names.
"""
import argparse
import json
import sys
from pathlib import Path
import hba_authority
import database_volume
import hba_migration
import hba_target
import image_identity
import run as lab
import upgrade_guard

REFUSED_OWNERS=('durable-upstream','recovery-target')
REFUSED_PREFIXES=('sbarbase-durable','sbarbase-restore')
# The upgrade record lab/upgrade.py keeps (its STATE_FILE).
UPGRADE_STATE=lab.STATE/'upgrades'/'state.json'
DB_COMMAND=('postgres','-c','config_file=/etc/postgresql/postgresql.conf','-c','log_statement=none')


def docker(*args,data=None,check=True):
    import subprocess
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=check,timeout=300)


def refuse_retained(name,owner,*,attended=False,confirmed=None):
    """The retained placement is refused unless an attended run names it exactly."""
    if attended!=(confirmed is not None):
        raise RuntimeError('An attended retained migration needs both --attended-retained and --confirm-retained')
    if owner in REFUSED_OWNERS or name.startswith(REFUSED_PREFIXES):
        if not attended:
            raise RuntimeError('Retained placement migration is a deliberate operator run; this command refuses it')
        if confirmed!=name:
            raise RuntimeError('The confirmed name does not match the pinned retained container')
    return True


def no_pending_upgrade(path=None):
    """An upgrade or rollback that is not confirmed yet can move the installation back on its own,
    and that way back describes the database as it was: a new generation under it is refused."""
    path=UPGRADE_STATE if path is None else path
    try:
        record=json.loads(Path(path).read_text())
    except FileNotFoundError:
        return True
    except (OSError,ValueError):
        raise RuntimeError('The upgrade record cannot be read; review it before a generation migration') from None
    if not isinstance(record,dict):
        raise RuntimeError('The upgrade record is malformed; review it before a generation migration')
    if record.get('phase') in upgrade_guard.PENDING:
        raise RuntimeError('An upgrade or rollback is not confirmed yet; restart Sbarbase so it confirms, '
                           'or roll it back, before a generation migration')
    return True


def desired_rules(path,runtime_path):
    """The desired inventory: rules text, or the runtime's own environment inventory."""
    if path is not None:
        text=Path(path).read_text()
    elif runtime_path is not None:
        import durable_runtime
        text=durable_runtime.hba_content(json.loads(Path(runtime_path).read_text())['environments'])
    else:
        raise RuntimeError('A desired rule inventory is required')
    if not text.endswith('\n') or '\x00' in text:
        raise ValueError('The desired rule inventory must be complete text')
    return text


def lock_image(root=None):
    """The image of this checkout's distribution lock, as the daemon proves it: the identity a
    running container reports, the same one the runtime checks at start. Never pulled here."""
    pin=json.loads(((root or lab.ROOT)/'lab'/'distro-image.lock.json').read_text())
    reference=image_identity.reference(pin)
    result=docker('image','inspect',reference,check=False)
    if result.returncode:
        raise image_identity.inspection_failure(reference,result.stdout,result.stderr)
    return image_identity.resolved_id(reference,image_identity.record(result.stdout))


def plan_for(old,options,image=None):
    env_file=options.env_file or str(lab.PRIVATE/('upstream/'+old.name+'.env'))
    if not Path(env_file).is_file():
        raise RuntimeError('The retained environment file for this database is missing')
    return {'name':old.name,'owner':old.owner,'image':image or old.image,'tier':options.tier,
            'memory':options.memory or _tier_value(options.tier,'memory'),'cpus':options.cpus or _tier_value(options.tier,'cpus'),
            'network':options.network,'env_file':env_file,'command':DB_COMMAND}


def _tier_value(tier,field):
    import resource_policy
    entry=resource_policy.container_flags(tier)[field]
    if entry is None:raise RuntimeError('The resource tier does not define '+field)
    return entry


def main():
    parser=argparse.ArgumentParser(description='container generation migration')
    parser.add_argument('--state',required=True)
    parser.add_argument('--volume',required=True)
    parser.add_argument('--network',required=True)
    parser.add_argument('--tier',default='system.db')
    parser.add_argument('--memory')
    parser.add_argument('--cpus')
    parser.add_argument('--env-file')
    parser.add_argument('--inventory')
    parser.add_argument('--inventory-runtime',dest='inventory_runtime')
    parser.add_argument('--retired',choices=('stopped','absent'),default='stopped')
    parser.add_argument('--assert-retired-will-not-return',action='store_true',dest='assertion')
    parser.add_argument('--reconcile',action='store_true')
    parser.add_argument('--attended-retained',action='store_true',dest='attended')
    parser.add_argument('--confirm-retained',dest='confirmed')
    parser.add_argument('--image-from-lock',action='store_true',dest='from_lock',
                        help="replace the database onto the image of this checkout's distribution lock")
    parser.add_argument('--to-volume',dest='to_volume',
                        help='replace the database onto this existing pgdata volume (a rebuilt cluster)')
    options=parser.parse_args()
    state=Path(options.state)
    desired=desired_rules(options.inventory,options.inventory_runtime)
    # A record directory a kill left empty, before its intent, never started: it is removed here,
    # where every run comes in, so neither a reconcile nor a new migration is stuck on it.
    discarded=hba_migration.discard_unpublished(state)
    if options.reconcile:
        if discarded:
            raise RuntimeError('The migration record was left before its intent; nothing had started, so it was '
                               'removed. Start the migration again without --reconcile')
        intent=hba_migration.load(state)
        old=hba_target.Target(**intent['old'])
        refuse_retained(old.name,old.owner,attended=options.attended,confirmed=options.confirmed)
        if options.from_lock:
            raise RuntimeError('--reconcile finishes onto the image its intent recorded; drop --image-from-lock')
        if options.to_volume:
            raise RuntimeError('--reconcile finishes onto the volume its intent recorded; drop --to-volume')
        replacement=plan_for(old,options,hba_migration.target_image(intent))
        completed=hba_migration.execute(docker,state,replacement=replacement,desired=desired)
        print(json.dumps({'reconciled':True,'completed':completed,'migration':intent['migration'],
                          'retired_generation':intent['generation']}))
        return
    pin=hba_migration.pinned(state)
    old=hba_target.Target(**pin['target'])
    refuse_retained(old.name,old.owner,attended=options.attended,confirmed=options.confirmed)
    no_pending_upgrade()
    image=lock_image() if options.from_lock else None
    if options.assertion and options.retired!='stopped':
        raise RuntimeError('The retirement assertion applies to a stopped container only')
    if options.retired=='stopped' and not options.assertion:
        raise RuntimeError('Stopping the retired container requires --assert-retired-will-not-return')
    if options.to_volume:
        # Before the intent: a volume that cannot take the database leaves no record to reconcile.
        if not database_volume.rebuilt(options.to_volume) or options.to_volume==options.volume:
            raise RuntimeError('--to-volume names a rebuilt database volume (sbarbase-durable-pgdata-<12 hex>) other than the current one')
        hba_migration.require_cluster_volume(docker,options.to_volume,old.owner,image or old.image,state)
    intent=hba_migration.publish_intent(state,target=old,generation=pin['generation'],volume=options.volume,
                                        inventory=hba_authority.digest(desired),
                                        retired_state='stopped-will-not-return' if options.retired=='stopped' else 'absent-verified',
                                        image=image,new_volume=options.to_volume)
    completed=hba_migration.execute(docker,state,replacement=plan_for(old,options,image),desired=desired)
    print(json.dumps({'migrated':True,'completed':completed,'migration':intent['migration'],
                      'retired_generation':intent['generation']}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Fixed sentences only; private diagnostics stay in the state directory.
        if isinstance(error,(RuntimeError,ValueError,FileNotFoundError)):raise SystemExit(str(error)) from None
        raise SystemExit('Generation migration failed; retained state is available for reconciliation')