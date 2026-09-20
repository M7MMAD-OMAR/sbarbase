"""Fresh real worker/service lifecycle in a private namespaced source snapshot."""
import json
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import dev
import uuid
import run as lab


def main():
    checks=[]
    def check(name,value):
        if not value:raise RuntimeError(name)
        checks.append(name)
    memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    check('whole-stack host memory headroom exceeds 6 GiB',memory>=6*1024*1024)
    name='sbar-fresh-'+uuid.uuid4().hex[:16];owner=name
    root=Path(tempfile.mkdtemp(prefix='fresh-worker-',dir=lab.STATE));os.chmod(root,0o700)
    repo=root/'repo';repo.mkdir(mode=0o700)
    private=root/'diagnostics';private.mkdir(mode=0o700)
    def command(args,*,cwd=repo,timeout=180,input=None,label='command'):
        output=private/(label+'.stdout');error=private/(label+'.stderr')
        with output.open('w') as out,error.open('w') as err:
            child=subprocess.Popen(args,cwd=cwd,stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                                   stdout=out,stderr=err,text=True,start_new_session=True)
            try:
                if input is not None:
                    child.stdin.write(input);child.stdin.close()
                deadline=time.monotonic()+timeout
                while dev.child_status(child) is None:
                    if time.monotonic()>=deadline:raise RuntimeError('Fixture '+label+' timed out')
                    time.sleep(.02)
                status=dev.child_status(child)
            finally:dev.terminate_group(child,grace=5)
        if status:raise RuntimeError('Fixture '+label+' failed')
        return output.read_text()
    def docker(*args):return command(['docker',*args],label='docker')
    def resources(kind):
        args={'container':['ps','-aq'],'volume':['volume','ls','-q'],'network':['network','ls','-q']}[kind]
        return docker(*args,'--filter','label=io.sbarbase.owner='+owner).split()
    for kind in ('container','volume','network'):check('fresh '+kind+' namespace absent',not resources(kind))
    files=subprocess.check_output(['git','ls-files','-z'],cwd=lab.ROOT).decode().split('\0')
    selected=[item for item in files if item and (item.startswith(('lab/','src/')) or item in ('package.json','bun.lock','tsconfig.json','.gitignore'))]
    for item in selected:
        source=lab.ROOT/item
        if not source.is_file() or source.is_symlink():raise RuntimeError('Snapshot requires tracked regular source files')
        target=repo/item;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    (repo/'node_modules').symlink_to(lab.ROOT/'node_modules',target_is_directory=True)
    check('snapshot excludes retained runtime state',not (repo/'.lab').exists() and not (repo/'.secrets').exists())
    # Change only fixture resource identities, never execution or admission logic.
    replacements={'lab/durable_runtime.py':[("OWNER = 'durable-upstream'",f"OWNER = '{owner}'",1),("PREFIX = 'sbarbase-durable'",f"PREFIX = '{name}'",1)],
                  'lab/resource_admission.py':[("sbarbase-durable-db",name+'-db',2),("sbarbase-durable-storage",name+'-storage',2)],
                  'lab/pressure_admission.py':[("sbarbase-durable-db",name+'-db',1),("sbarbase-durable-storage",name+'-storage',1)]}
    for item,changes in replacements.items():
        path=repo/item;text=path.read_text()
        for before,after,count in changes:
            check('exact fixture identity replacement '+item+' '+before,text.count(before)==count)
            text=text.replace(before,after)
        check('retained durable identities absent from '+item,'sbarbase-durable' not in text and 'durable-upstream' not in text)
        path.write_text(text)
    # Fixed transitive worker helpers have no Docker target identities.
    for item in ('effect_receipt.py','guarded_sql_executor.py','sql_operation_fence.py','sql_operation_revoke.py','source_fence.py','worker.py','worker.ts','worker-effect.ts','worker-receipt.ts','worker_lock_exec.py','effect_lease.py'):
        text=(repo/'lab'/item).read_text()
        check('worker helper has no retained resource target '+item,'sbarbase-durable' not in text and 'durable-upstream' not in text)
    command(['git','-c','init.templateDir=','init','-q'],label='git-init')
    (repo/'.gitignore').write_text('.lab/\n.secrets/\nnode_modules/\n')
    completed=False
    try:
        command(['/usr/bin/python3','lab/durable_runtime.py','up'],timeout=180,label='infrastructure')
        seed="import {Catalog} from './src/control/catalog';const c=new Catalog('.lab/upstream/control.sqlite');try{const o=c.createOrganization('fresh-owner','Fresh');const p=c.createProject('fresh-owner',o,'Probe');const e=c.createEnvironment('fresh-owner',p,'production');await Bun.write('.lab/upstream/fresh-probe.json',JSON.stringify({environment:e}));}finally{c.close();}"
        command(['bun','-e',seed],label='seed')
        command(['/usr/bin/python3','lab/worker.py','--upstream'],timeout=220,label='worker')
        state=repo/'.lab/upstream'
        check('real worker consumed its pending receipt',not (state/'worker-effect.json').exists())
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(state/'control.sqlite')) as db:
            rows=db.execute('SELECT j.runtime,r.claim,j.attempt,j.state,j.environment,r.exit_code,j.claim FROM provision_jobs j JOIN provision_effect_results r ON r.environment=j.environment AND r.attempt=j.attempt').fetchall()
        check('one exact worker job succeeded',len(rows)==1 and rows[0][3]=='succeeded' and rows[0][2]==1 and rows[0][5]==0 and rows[0][6] is None)
        runtime,claim,attempt,status,environment,exit_code,cleared_claim=rows[0]
        witnesses=[json.loads(p.read_text()) for p in (state/'effect-outcomes').glob('*.json')]
        check('native success witness matches exact claim',len(witnesses)==1 and witnesses[0]['exitCode']==0 and witnesses[0]['job']=={'environment':environment,'runtime':runtime,'claim':claim,'attempt':attempt})
        stage=json.loads((state/'effect-stages'/(witnesses[0]['token']+'.json')).read_text())
        check('native success reached publication',stage['stage']=='publication')
        for database in ('postgres',runtime):
            result=command(['docker','exec','-i',name+'-db','psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database],input=f"SELECT state FROM sbarbase_provision_guard.operations WHERE token='{witnesses[0]['token']}';",label='sql-state')
            check('worker SQL authority retired in '+('control' if database=='postgres' else 'target'),result.strip()=='revoked')
        inventory=[json.loads(docker('inspect',identifier))[0] for identifier in resources('container')]
        expected={name+'-db',name+'-storage',name+'-management-auth',name+'-'+runtime+'-auth',name+'-'+runtime+'-rest'}
        check('fresh stack has only five planned containers',{item['Name'].lstrip('/') for item in inventory}==expected)
        check('fresh aggregate memory ceiling is 2304 MiB',sum(item['HostConfig']['Memory'] for item in inventory)==2304*1024*1024)
        check('fresh aggregate CPU ceiling is 2.25 CPUs',sum(item['HostConfig']['NanoCpus'] for item in inventory)==2250000000)
        check('fresh stack has no OOM or published container ports',all(not item['State']['OOMKilled'] and not item['HostConfig'].get('PortBindings') for item in inventory))
        network=json.loads(docker('network','inspect',name+'-net'))[0]
        check('fresh stack uses internal network',network['Internal'] is True)
        sdk=(lab.ROOT/'lab/fresh-worker-sdk.ts').read_text()
        (repo/'lab/fresh-worker-sdk.ts').write_text(sdk)
        result=command(['bun','lab/fresh-worker-sdk.ts',name+'-db'],timeout=90,label='sdk')
        check('real SDK signup RLS and Storage roundtrip',result.strip()=='Fresh worker SDK passed')
        completed=True
    finally:
        # Detached guardians hold these same locks. Do not tear down beneath one.
        leases=[]
        state=repo/'.lab/upstream'
        try:
            for lock_name in ('worker.lock','effect.lock','operation.lock'):
                path=state/lock_name
                if not path.exists():continue
                handle=path.open('r+')
                leases.append(handle)
                deadline=time.monotonic()+30
                while True:
                    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:
                        if time.monotonic()>=deadline:raise RuntimeError('Fixture cleanup ownership unavailable; private resources retained')
                        time.sleep(.1)
        except BaseException:
            for handle in leases:handle.close()
            raise
        # Exact captured Docker IDs only. No retained-runtime stop/cleanup helper.
        for identifier in resources('container'):
            item=json.loads(docker('inspect',identifier))[0]
            check('cleanup captured container ownership',item['Id']==identifier or item['Id'].startswith(identifier))
            check('cleanup container namespace',item['Name'].lstrip('/').startswith(name+'-') and item['Config']['Labels'].get('io.sbarbase.owner')==owner)
            check('fixture containers publish no host ports',not item['HostConfig'].get('PortBindings'))
            docker('rm','-f',item['Id'])
        for kind in ('volume','network'):
            for identifier in resources(kind):
                item=json.loads(docker(kind,'inspect',identifier))[0]
                check('cleanup '+kind+' namespace',item['Name'].startswith(name+'-') and item['Labels'].get('io.sbarbase.owner')==owner)
                docker(kind,'rm',item['Id'] if kind=='network' else item['Name'])
        check('all isolated Docker resources removed',all(not resources(kind) for kind in ('container','volume','network')))
        for handle in leases:handle.close()
    if completed:
        (lab.ROOT/'docs/evidence/fresh-worker-checks.json').write_text(json.dumps({'scope':'Fresh real worker receipts, leases and guarded SQL with original Auth/REST/Storage in a private source snapshot. Only Docker identity constants replaced. Single environment, not crash recovery or capacity.','count':len(checks),'checks':checks},indent=2)+'\n')
        print(str(len(checks))+' fresh worker lifecycle checks passed')


if __name__=='__main__':
    try:main()
    except Exception as error:
        # Our own fixed messages only; private diagnostics retain native output.
        if isinstance(error,RuntimeError):raise SystemExit(str(error)) from None
        raise SystemExit('Fresh worker fixture failed; inspect private diagnostics') from None
