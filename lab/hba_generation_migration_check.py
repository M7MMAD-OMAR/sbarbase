"""Five real generation-migration crashes, only inside the private fresh fixture.

Each phase runs the actual operator command, kills it with SIGKILL at one exact
durable checkpoint, and then asserts that the fixture's database refuses startup,
refuses a repeated migration, reconciles exactly once, and only afterwards admits
startup again with the recreated container carrying its tier and its block IO
limits. The kill is an external SIGKILL from a profile hook (the same mechanism
the worker HBA crash check uses): production code carries no crash switch.
"""
import json
import os
import subprocess
from pathlib import Path
import hba_authority
import hba_generation
import hba_migration
import hba_target


PHASES=('after-intent','after-old-captured','after-recreated','after-generation','after-rules')
CHECKPOINTS={'after-old-captured':'old-captured','after-recreated':'new-captured',
             'after-generation':'generation-initialized','after-rules':'rules-published'}
PGDATA=hba_migration.PGDATA
HBA_PATH=hba_migration.HBA_PATH


HOOK = '''import json,os,signal,sys
from pathlib import Path
root=Path(__ROOT__)
phase=__PHASE__
checkpoint=__CHECKPOINT__
observed=Path(__OBSERVED__)
entry=Path(sys.argv[0]).resolve()
native=entry==root/'lab/migrate-generation.py'

def trace(frame,event,value):
    if event!='return':return
    if Path(frame.f_code.co_filename).resolve()!=root/'lab/hba_migration.py':return
    module=sys.modules.get('hba_migration')
    if module is None:return
    name=frame.f_code.co_name
    if phase=='after-intent':
        if name!='publish_intent' or frame.f_code is not module.publish_intent.__code__:return
        seen=None
    else:
        if name!='checkpoint' or frame.f_code is not module.checkpoint.__code__:return
        if frame.f_locals.get('phase')!=checkpoint:return
        seen=frame.f_locals.get('phase')
    sys.setprofile(None)
    with observed.open('x',encoding='utf-8') as output:
        json.dump({'phase':phase,'checkpoint':seen,'pid':os.getpid(),'record':str(value.get('migration')) if isinstance(value,dict) else None},output)
        output.flush();os.fsync(output.fileno())
    os.kill(os.getpid(),signal.SIGKILL)
    raise RuntimeError('SIGKILL did not terminate the migration process')

if native:sys.setprofile(trace)
'''


def docker(*args,data=None):
    return subprocess.run(['docker',*args],input=data,capture_output=True,text=True,timeout=300)


def inspect(reference):
    result=docker('inspect',reference)
    if result.returncode:raise RuntimeError('Fixture inspection unavailable')
    return json.loads(result.stdout)[0]


def sql(identifier,query,database='postgres'):
    result=docker('exec','-i',identifier,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database,data=query)
    if result.returncode:raise RuntimeError('Fixture SQL unavailable')
    return result.stdout.strip()


def inventory(repo):
    """The desired inventory from the runtime's own trusted values, one builder."""
    import durable_runtime
    values=json.loads((repo/'.secrets/upstream/runtime.json').read_text())
    return durable_runtime.hba_content(values['environments'])


def read_stderr(private,label):
    return (private/(label+'.stderr')).read_text()


def arguments(volume,network,rules):
    return ['--state','.lab/upstream','--volume',volume,'--network',network,'--tier','system.db',
            '--inventory',str(rules),'--retired','stopped','--assert-retired-will-not-return']


def run(repo,private,name,phase,command,check):
    """One crash test: the migration dies at `phase` and must refuse until reconciled."""
    if phase not in PHASES:raise ValueError('Unknown generation migration crash phase')
    state=repo/'.lab/upstream'
    pin=hba_generation.load(state)
    retired=hba_target.Target(**pin['target'])
    check('the fixture database is pinned before '+phase,retired.name==name+'-db' and retired.owner==name)
    before=inspect(retired.container_id)
    check('the retired container is running before '+phase,before['State']['Running'] is True)
    volume=next((mount['Name'] for mount in before['Mounts'] if mount['Destination']==PGDATA),None)
    network=before['HostConfig']['NetworkMode']
    check('the retired pgdata volume is the fixture volume',volume==name+'-pgdata')
    baseline={'pin':(state/hba_generation.NAME).read_bytes(),
              'rules':docker('exec',retired.container_id,'sha256sum',HBA_PATH).stdout.split()[0],
              'databases':sql(retired.container_id,"SELECT string_agg(datname,',' ORDER BY datname) FROM pg_database;"),
              'rows':sql(retired.container_id,'SELECT count(*) FROM sbarbase_provision_guard.operations;')}
    rules=private/('migration-rules-'+phase)
    rules.write_text(inventory(repo));rules.chmod(0o600)
    observed=private/('migration-crash-'+phase+'.json')
    hook=private/('migration-hook-'+phase)
    hook.mkdir(mode=0o700,exist_ok=True)
    script=hook/'sitecustomize.py'
    script.write_text(HOOK.replace('__ROOT__',repr(str(repo.resolve())))
                          .replace('__PHASE__',repr(phase))
                          .replace('__CHECKPOINT__',repr(CHECKPOINTS.get(phase)))
                          .replace('__OBSERVED__',repr(str(observed))))
    script.chmod(0o600)
    argv=arguments(volume,network,rules)
    command(['/usr/bin/python3','lab/migrate-generation.py',*argv],label='migration-crash',timeout=600,
            env=dict(os.environ,PYTHONPATH=str(hook)),expect_failure=True)
    check('the migration process died at the exact '+phase+' checkpoint',json.loads(observed.read_text())['phase']==phase)
    record=hba_migration.load(state)
    check('the crash left the migration record in place',hba_migration.present(state) and record['generation']==pin['generation'])
    if phase=='after-intent':
        check('the intent is durable before any retired capture',not hba_migration.done(state,'old-captured'))
    else:
        check('the checkpoint the crash follows is durable',hba_migration.done(state,CHECKPOINTS[phase]))

    crash_pin=(state/hba_generation.NAME).read_bytes()
    command(['/usr/bin/python3','lab/durable_runtime.py','up'],label='migration-startup-blocked',timeout=180,expect_failure=True)
    check('ordinary startup refuses while the record is present',
          'Durable runtime operation failed' in read_stderr(private,'migration-startup-blocked'))
    check('the refused startup changed neither the pin nor the record',
          (state/hba_generation.NAME).read_bytes()==crash_pin and hba_migration.present(state))

    command(['/usr/bin/python3','lab/migrate-generation.py',*argv],label='migration-repeat',timeout=120,expect_failure=True)
    check('a repeated migration refuses while the record is present',
          'A generation migration record already exists' in read_stderr(private,'migration-repeat'))

    migrated=command(['/usr/bin/python3','lab/migrate-generation.py',*argv,'--reconcile'],label='migration-reconcile',timeout=900)
    check('the interrupted migration reconciles once',json.loads(migrated)['reconciled'] is True)
    command(['/usr/bin/python3','lab/migrate-generation.py',*argv,'--reconcile'],label='migration-reconcile-twice',timeout=120,expect_failure=True)
    check('a second reconciliation refuses',
          'No generation migration record to reconcile' in read_stderr(private,'migration-reconcile-twice'))

    after=hba_generation.load(state)
    check('the pin names the recreated container with one new generation',
          after['generation']!=pin['generation'] and after['target']['container_id']!=retired.container_id
          and after['target']['name']==retired.name and after['target']['image']==retired.image
          and after['target']['owner']==retired.owner)
    check('the migration record is gone after completion',not hba_migration.present(state))
    replacement=inspect(after['target']['container_id'])
    host=replacement['HostConfig']
    check('the recreated container carries its resource tier',
          replacement['Config']['Labels'].get('io.sbarbase.tier')=='system' and host['Memory']==1024*1024*1024
          and host['NanoCpus']==1000000000 and host['PidsLimit']==128 and host['CpuShares']==2048 and host['BlkioWeight']==800)
    import resource_policy
    bounds=resource_policy.IO_LIMITS['system.db']
    rates=tuple((entry[0]['Rate'] if entry else None) for entry in
                (host.get('BlkioDeviceReadBps'),host.get('BlkioDeviceWriteBps'),
                 host.get('BlkioDeviceReadIOps'),host.get('BlkioDeviceWriteIOps')))
    check('the recreated container carries its per device block IO limits',
          rates==(256*1024*1024,128*1024*1024,6000,3000) and bounds==('256mb','128mb',6000,3000))
    check('the recreated container mounts the same pgdata volume',
          next((mount['Name'] for mount in replacement['Mounts'] if mount['Destination']==PGDATA),None)==volume)
    check('the recreated container publishes no host port',not host.get('PortBindings'))

    registry=json.loads(docker('exec',after['target']['container_id'],'cat',hba_authority.PATH).stdout)['record']
    check('the new registry holds one retired token of the new generation',
          registry['generation']==after['generation'] and all(item['state']=='revoked' for item in registry['operations'].values())
          and len(registry['operations'])==1)
    evidence=hba_migration.read_evidence(state,record['migration'])
    check('the evidence binds the retired generation and its last observed digest',
          evidence['retired_generation']==pin['generation'] and evidence['retired_container']==retired.container_id
          and evidence['rules']['retired_hba_digest']==baseline['rules']
          and evidence['new_generation']==after['generation'] and evidence['hba_activation']=='unknown')
    check('the evidence states the rule comparison instead of implying byte equality',
          evidence['rules']['difference'] in ('identical','re-derived-from-inventory')
          and evidence['rules']['inventory_matches_observed'] is True
          and evidence['rules']['retired_matches_observed'] is True)
    archive=hba_migration.archive_directory(state,record['migration'])
    check('the retired pin bytes are archived, never rewritten',
          (archive/hba_generation.NAME).read_bytes()==baseline['pin'])

    command(['/usr/bin/python3','lab/durable_runtime.py','up'],label='migration-startup-admitted',timeout=600)
    check('startup is admitted again after reconciliation and the recreated database is running',
          inspect(after['target']['container_id'])['State']['Running'] is True
          and hba_generation.load(state)['target']==after['target'])
    identifier=after['target']['container_id']
    check('the recreated database kept every environment database',
          sql(identifier,"SELECT string_agg(datname,',' ORDER BY datname) FROM pg_database;")==baseline['databases'])
    check('the recreated database kept its guard rows',
          sql(identifier,'SELECT count(*) FROM sbarbase_provision_guard.operations;')==baseline['rows'])
    return evidence


def run_all(repo,private,name,selection,command,check):
    """Run one phase or all five, each on the fixture, each with its own evidence."""
    import run as lab
    phases=PHASES if selection in (None,'all') else (selection,)
    for phase in phases:
        checks=[]
        def collect(label,value):
            check(label,value)
            checks.append(label)
        evidence=run(repo,private,name,phase,command,collect)
        path=lab.ROOT/'docs/evidence'/('generation-migration-'+phase+'.json')
        path.write_text(json.dumps({'crash_phase':phase,
            'scope':('Real container generation migration on the disposable fresh fixture: one SIGKILL at the named '
                     'durable checkpoint, the resulting startup refusal, the repeated-migration refusal, exactly one '
                     'reconciliation, and the recreated container with its resource tier and per device block IO '
                     'limits on the same pgdata volume. No retained placement resource was touched.'),
            'checks':len(checks),'evidence':checks,'retired':evidence['retired_container'],
            'replacement':evidence['new_container'],'difference':evidence['rules']['difference']},indent=2)+'\n')
        print(phase+': '+str(len(checks))+' generation migration crash checks passed')