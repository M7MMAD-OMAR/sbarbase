"""Disposable PostgreSQL checks for interrupted database provisioning phases."""
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import effect_receipt
import run as lab

OWNER='partial-provision-probe'
ADMIN='postgres'


def docker(*args,data=None,check=True,env=None):
    result=subprocess.run(['docker',*args],input=data,text=True,capture_output=True,timeout=30,env=env)
    if check and result.returncode:raise RuntimeError('Disposable Docker operation failed')
    return result


def sql(container,query,database='postgres'):
    return docker('exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',ADMIN,'-d',database,data=query)


def child(container,state,phase):
    receipt=json.loads((state/'worker-effect.json').read_text());runtime=receipt['job']['runtime']
    with ExitStack() as stack:
        descriptors=[]
        for name in ('worker.lock','effect.lock','operation.lock'):
            file=stack.enter_context((state/name).open('a'))
            fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB);descriptors.append(file.fileno())
        # Match native provisioner ownership slots without aliasing open originals.
        copied=[fcntl.fcntl(fd,fcntl.F_DUPFD_CLOEXEC,10) for fd in descriptors[:2]]
        try:
            os.dup2(copied[0],3);os.dup2(copied[1],4)
            os.environ['SBARBASE_EFFECT_TOKEN']=receipt['token']
            effect_receipt.native_stage(state,runtime,'preflight')
            effect_receipt.native_stage(state,runtime,'database')
            def checkpoint(current):
                if current==phase:
                    (state/'ready').write_text(current)
                    os.kill(os.getpid(),signal.SIGSTOP)
            def execute(query,database='postgres'):
                if phase=='transaction_failure' and query.startswith('BEGIN;'):
                    try:sql(container,query.replace(' COMMIT;',' SELECT 1/0; COMMIT;'),database)
                    except RuntimeError:checkpoint('transaction_failure')
                    raise RuntimeError('Injected transaction failure did not stop provisioning')
                return sql(container,query,database)
            lab.provision_environment(runtime,{key:secrets.token_hex(32) for key in ('auth','rest')},
                                      checkpoint=checkpoint,executor=execute)
            raise RuntimeError('Requested phase was not reached')
        finally:
            for fd in copied:os.close(fd)


def bun(code,state):
    result=subprocess.run(['bun','-e',code],cwd=lab.ROOT,env={**os.environ,'SBARBASE_PROBE_STATE':str(state)},capture_output=True,text=True,timeout=15)
    if result.returncode:raise RuntimeError('Disposable catalog check failed')
    return result.stdout


def main(upstream=False,sql_fence=False,cross_fence=False):
    global ADMIN
    ADMIN="supabase_admin" if upstream else "postgres"
    checks=[];container=None;native=None
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    info=json.loads(docker('info','--format','{{json .}}').stdout)
    check('native local Linux daemon',info['OSType']=='linux' and info['Name']==socket.gethostname())
    memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
    reserve=4 if upstream else 3
    check('host headroom checked before bounded probe',memory>=reserve*1024**3)
    pins=json.loads((lab.ROOT/('lab/distro-image.lock.json' if upstream else 'lab/images.lock.json')).read_text())
    image=pins['id'] if upstream else pins['db']['id']
    memory_limit='1024m' if upstream else '512m'
    tmpfs_size=512*1024**2 if upstream else 384*1024**2
    docker('image','inspect',image)
    name='sbarbase-partial-'+uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix='sbarbase-partial-') as directory:
        cidfile=Path(directory)/'container.id'
        try:
            # Trust applies only inside an isolated, unpublished disposable container.
            settings=['-e','POSTGRES_PASSWORD','-e','POSTGRES_HOST=/var/run/postgresql','-e','POSTGRES_DB=postgres'] if upstream else ['-e','POSTGRES_HOST_AUTH_METHOD=trust']
            command=['postgres','-c','config_file=/etc/postgresql/postgresql.conf','-c','log_statement=none'] if upstream else []
            child_env={**os.environ,'POSTGRES_PASSWORD':secrets.token_hex(32)} if upstream else None
            container=docker('run','-d','--pull=never','--restart=no','--cidfile',str(cidfile),'--name',name,'--label','io.sbarbase.owner='+OWNER,
                '--network','none','--memory',memory_limit,'--memory-swap',memory_limit,'--cpus','1' if upstream else '0.5','--pids-limit','96',
                '--log-opt','max-size=1m','--log-opt','max-file=1',
                '--tmpfs','/var/lib/postgresql/data:rw,size='+str(tmpfs_size),
                *settings,image,*command,env=child_env).stdout.strip()
            observed=json.loads(docker('inspect',container).stdout)[0]
            check('owned exact container is isolated and memory bounded',observed['Id']==container and observed['Image']==image and observed['Config']['Labels']['io.sbarbase.owner']==OWNER and observed['HostConfig']['NetworkMode']=='none' and not observed['HostConfig']['PortBindings'] and observed['HostConfig']['Memory']==(1024 if upstream else 512)*1024**2)
            deadline=time.monotonic()+60
            while docker('exec',container,'pg_isready','-h','127.0.0.1','-U',ADMIN,check=False).returncode:
                if time.monotonic()>deadline:raise RuntimeError('Disposable database readiness deadline')
                time.sleep(.2)
            if upstream:
                check('upstream bootstrap roles present',sql(container,"SELECT count(*) FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role','supabase_privileged_role');").stdout.strip()=='4')
                docker('exec','-i',container,'sh','-c','cat > /etc/postgresql/pg_hba.conf',data='local all all trust\nhost all all 0.0.0.0/0 reject\nhost all all ::/0 reject\n')
                sql(container,'SELECT pg_reload_conf();')
            else:
                sql(container,'CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS;')
            sql(container,'CREATE ROLE neighbor LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;')
            check('neighbor role is unprivileged without inherited memberships',sql(container,"SELECT NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls) AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid) FROM pg_roles WHERE rolname='neighbor';").stdout.strip()=='t')
            sql(container,'CREATE DATABASE neighbor;')
            sql(container,"CREATE TABLE sentinel(value text); INSERT INTO sentinel VALUES ('preserve-neighbor');",'neighbor')
            setup="""import {Catalog} from './src/control/catalog';
const state=process.env.SBARBASE_PROBE_STATE!;
const c=new Catalog(state+'/control.sqlite');
try{const o=c.createOrganization('probe','O'),p=c.createProject('probe',o,'P');c.createEnvironment('probe',p,'E');console.log(JSON.stringify(c.claimProvision()));}finally{c.close();}"""
            if sql_fence:
                from sql_operation_fence_check import run as run_fence
                run_fence(container,ADMIN,check,docker,sql)
            if cross_fence:
                from sql_operation_revoke_check import run as run_pair
                run_pair(container,ADMIN,check,docker,sql)
            for phase in (() if sql_fence or cross_fence else ('roles','database','permissions','transaction_failure')):
                state=Path(directory)/phase;state.mkdir()
                job=json.loads(bun(setup,state));runtime=job['runtime']
                check(phase+': fresh runtime has no prior database or roles',sql(container,f"SELECT NOT EXISTS(SELECT 1 FROM pg_database WHERE datname='{runtime}') AND NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname IN ('{runtime}_auth','{runtime}_rest'));").stdout.strip()=='t')
                receipt={'version':1,'phase':'pending','token':str(uuid.uuid4()),'native':'durable-provision-v1','stageProtocol':1,
                         'job':{key:job[key] for key in ('environment','runtime','claim','attempt')}}
                effect_receipt.publish(state/'worker-effect.json',receipt)
                native=subprocess.Popen(['/usr/bin/python3',str(Path(__file__).resolve()),'--child',container,str(state),phase,'upstream' if upstream else 'component'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                deadline=time.monotonic()+30
                while not (state/'ready').exists() or 'State:\tT (stopped)' not in Path('/proc',str(native.pid),'status').read_text():
                    if native.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Native checkpoint unavailable')
                    time.sleep(.02)
                before_sql=sql(container,f"SELECT (SELECT count(*) FROM pg_roles WHERE rolname IN ('{runtime}_auth','{runtime}_rest')),EXISTS(SELECT 1 FROM pg_database WHERE datname='{runtime}');").stdout.strip()
                check(phase+': checkpoint SQL committed before kill',before_sql==('2|f' if phase=='roles' else '2|t'))
                receipt_before=(state/'worker-effect.json').read_bytes()
                # Popen owns this unreaped stopped child; its PID cannot be recycled.
                native.kill();check(phase+': native process killed after SQL checkpoint',native.wait(timeout=5)==-9);native=None
                with ExitStack() as locks:
                    for filename in ('worker.lock','effect.lock','operation.lock'):
                        file=locks.enter_context((state/filename).open('r'));fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    stage=json.loads((state/'effect-stages'/(receipt['token']+'.json')).read_text())
                    check(phase+': database mutation stage survives',stage['stage']=='database' and stage['job']==receipt['job'])
                    check(phase+': created roles persist',sql(container,f"SELECT count(*) FROM pg_roles WHERE rolname IN ('{runtime}_auth','{runtime}_rest');").stdout.strip()=='2')
                    exists=sql(container,f"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='{runtime}');").stdout.strip()=='t'
                    check(phase+': database existence matches committed boundary',exists==(phase!='roles'))
                    if exists:
                        permitted=sql(container,f"SELECT has_database_privilege('neighbor','{runtime}','CONNECT');").stdout.strip()=='t'
                        check(phase+': ACL reflects interruption before or after grants',permitted==(phase!='permissions'))
                        connect=docker('exec',container,'psql','-X','-qAt','-U','neighbor','-d',runtime,'-c','SELECT 1',check=False)
                        check(phase+': unrelated role cannot connect to partial environment',connect.returncode!=0 and ('not currently accepting connections' if phase!='permissions' else 'permission denied for database') in connect.stderr)
                        allowed=sql(container,f"SELECT datallowconn FROM pg_database WHERE datname='{runtime}';").stdout.strip()
                        check(phase+': database opens only after permission transaction',allowed==('t' if phase=='permissions' else 'f'))
                        if phase!='permissions':
                            refused=False
                            try:lab.provision_environment(runtime,{key:secrets.token_hex(32) for key in ('auth','rest')},executor=lambda query,database='postgres':sql(container,query,database))
                            except RuntimeError as error:refused='closed' in str(error)
                            check(phase+': existing closed database refuses direct reentry',refused)
                        if phase=='transaction_failure':
                            check('failed transaction rolls back role membership',sql(container,f"SELECT pg_has_role('{runtime}_rest','anon','member');").stdout.strip()=='f')
                        if phase=='permissions':
                            schema=sql(container,"SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='auth');",runtime).stdout.strip()=='t'
                            check(phase+': auth schema created',schema)
                            scoped=docker('exec',container,'psql','-X','-qAt','-U',runtime+'_auth','-d',runtime,'-c','SELECT current_database()')
                            check(phase+': intended role connects to exact database',scoped.stdout.strip()==runtime)
                    verify="""import {Catalog} from './src/control/catalog';import {settleWorkerReceipt} from './lab/worker-receipt';
const s=process.env.SBARBASE_PROBE_STATE!,r=await Bun.file(s+'/worker-effect.json').json(),c=new Catalog(s+'/control.sqlite');
try{let blocked=false;try{settleWorkerReceipt(c,s+'/worker.lock',true);}catch(e){blocked=String(e).includes('external effects');}
const j=c.getProvision('probe',r.job.environment);
if(!blocked||j.state!=='running'||j.claim!==r.job.claim||j.attempt!==r.job.attempt||j.runtime!==r.job.runtime||c.claimProvision()!==null||!await Bun.file(s+'/worker-effect.json').exists())throw Error('Unsafe recovery');
console.log('blocked');}finally{c.close();}"""
                    check(phase+': fresh settlement blocks replay and preserves pending claim',bun(verify,state).strip()=='blocked')
                    check(phase+': receipt bytes unchanged after refused recovery',(state/'worker-effect.json').read_bytes()==receipt_before)
                    check(phase+': SQL boundary unchanged after refused recovery',sql(container,f"SELECT (SELECT count(*) FROM pg_roles WHERE rolname IN ('{runtime}_auth','{runtime}_rest')),EXISTS(SELECT 1 FROM pg_database WHERE datname='{runtime}');").stdout.strip()==before_sql)
                    check(phase+': no completion witness inferred',not (state/'effect-outcomes').exists())
                    check(phase+': neighboring database unchanged',sql(container,'SELECT value FROM sentinel;','neighbor').stdout.strip()=='preserve-neighbor')
            check('disposable database did not OOM',not json.loads(docker('inspect',container).stdout)[0]['State']['OOMKilled'])
        finally:
            try:
                if native is not None and native.poll() is None:native.kill();native.wait(timeout=5)
            finally:
                if container is None and cidfile.exists():container=cidfile.read_text().strip()
                if container:
                    current=json.loads(docker('inspect',container).stdout)[0]
                    if current['Id']!=container or current['Image']!=image or current['Name']!='/'+name or current['Config']['Labels'].get('io.sbarbase.owner')!=OWNER:raise RuntimeError('Cleanup ownership mismatch')
                    docker('rm','-f','-v',container)
                    check('exact disposable container absent from successful inventory',container not in docker('ps','-aq','--no-trunc').stdout.split())
    evidence={'image':image,'profile':'upstream' if upstream else 'component','scope':('Pinned Supabase PostgreSQL distribution, ' if upstream else 'Pinned stock PostgreSQL 17 component, ')+ ' actual provision_environment SQL interrupted after roles, database and permissions checkpoints plus injected permission transaction failure. Process kill follows synchronous SQL completion. Confirms durable partial state and blocked replay, not full Supabase recovery or in-flight daemon cancellation. Retained installation untouched.','count':len(checks),'checks':checks}
    if sql_fence:evidence['scope']='Experimental same-database operation revocation on pinned PostgreSQL. Concurrent queued batches, cancellation, tombstones and CREATE DATABASE tested. Explicitly proves identical advisory keys do not fence another database. Not wired into runtime recovery.'
    if cross_fence:evidence['scope']='Experimental sequential control and target SQL revocation on pinned PostgreSQL. Actual coordinator SIGKILL between commits, retry, target tombstones, absent/closed targets and OID replacement tested. Not an atomic cutoff, production integration or replay authorization.'
    output=('upstream-' if upstream else '')+('sql-pair-fence-checks.json' if cross_fence else 'sql-fence-checks.json' if sql_fence else 'partial-database-crash-checks.json')
    (lab.ROOT/'docs/evidence'/output).write_text(json.dumps(evidence,indent=2)+'\n')
    print(str(len(checks))+(' SQL operation fence checks passed' if sql_fence or cross_fence else ' partial database crash checks passed'))


if __name__=='__main__':
    try:
        if len(sys.argv)==6 and sys.argv[1]=='--child' and sys.argv[5] in ('component','upstream'):
            ADMIN='supabase_admin' if sys.argv[5]=='upstream' else 'postgres'
            child(sys.argv[2],Path(sys.argv[3]),sys.argv[4])
        elif len(sys.argv)==1:main()
        elif sys.argv[1:]==['--upstream']:main(upstream=True)
        elif sys.argv[1:] in (['--sql-fence'],['--upstream','--sql-fence']):main(upstream='--upstream' in sys.argv,sql_fence=True)
        elif sys.argv[1:] in (['--cross-fence'],['--upstream','--cross-fence']):main(upstream='--upstream' in sys.argv,cross_fence=True)
        else:raise RuntimeError('Invalid probe arguments')
    except AssertionError as error:raise SystemExit('Probe assertion failed: '+str(error)) from None
    except RuntimeError as error:raise SystemExit('Partial database probe failed: '+str(error)) from None
    except Exception:raise SystemExit('Partial database probe failed; inspect only its uniquely labelled disposable container') from None
