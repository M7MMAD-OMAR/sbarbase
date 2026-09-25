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

This revision refuses the retained placement outright (see REFUSED_OWNERS). The
retained installation's recreation is a deliberate operator run with its own
evidence, not something this command performs while the runtime is supervising
the installation.

Read docs/engineering/CONTAINER-GENERATION-MIGRATION.md before running it. An interrupted run
leaves a private migration record in place; startup refuses until either this
command finishes the migration or an operator settles the HBA journal it names.
"""
import argparse
import json
import sys
from pathlib import Path
import hba_authority
import hba_migration
import hba_target
import run as lab

REFUSED_OWNERS=('durable-upstream','recovery-target')
REFUSED_PREFIXES=('sbarbase-durable','sbarbase-restore')
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


def plan_for(old,options):
    env_file=options.env_file or str(lab.PRIVATE/('upstream/'+old.name+'.env'))
    if not Path(env_file).is_file():
        raise RuntimeError('The retained environment file for this database is missing')
    return {'name':old.name,'owner':old.owner,'image':old.image,'tier':options.tier,
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
    options=parser.parse_args()
    state=Path(options.state)
    desired=desired_rules(options.inventory,options.inventory_runtime)
    if options.reconcile:
        intent=hba_migration.load(state)
        old=hba_target.Target(**intent['old'])
        refuse_retained(old.name,old.owner,attended=options.attended,confirmed=options.confirmed)
        completed=hba_migration.execute(docker,state,replacement=plan_for(old,options),desired=desired)
        print(json.dumps({'reconciled':True,'completed':completed,'migration':intent['migration'],
                          'retired_generation':intent['generation']}))
        return
    pin=hba_migration.pinned(state)
    old=hba_target.Target(**pin['target'])
    refuse_retained(old.name,old.owner,attended=options.attended,confirmed=options.confirmed)
    if options.assertion and options.retired!='stopped':
        raise RuntimeError('The retirement assertion applies to a stopped container only')
    if options.retired=='stopped' and not options.assertion:
        raise RuntimeError('Stopping the retired container requires --assert-retired-will-not-return')
    intent=hba_migration.publish_intent(state,target=old,generation=pin['generation'],volume=options.volume,
                                        inventory=hba_authority.digest(desired),
                                        retired_state='stopped-will-not-return' if options.retired=='stopped' else 'absent-verified')
    completed=hba_migration.execute(docker,state,replacement=plan_for(old,options),desired=desired)
    print(json.dumps({'migrated':True,'completed':completed,'migration':intent['migration'],
                      'retired_generation':intent['generation']}))


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Fixed sentences only; private diagnostics stay in the state directory.
        if isinstance(error,(RuntimeError,ValueError,FileNotFoundError)):raise SystemExit(str(error)) from None
        raise SystemExit('Generation migration failed; retained state is available for reconciliation')