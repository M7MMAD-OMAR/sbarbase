"""Two-database barrier checks on a captured disposable upstream container."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import uuid
import sql_operation_fence as fence
import sql_operation_revoke as coordinator


def run(container,admin,check,docker,sql):
    def execute(database,script):
        return docker('exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',admin,'-d',database,data=script).stdout
    def denied(database,script):
        result=docker('exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',admin,'-d',database,data=script,check=False)
        return result.returncode!=0 and ('SQL operation is not active' in result.stderr or 'registration refused' in result.stderr)
    def identity():return ('e_'+uuid.uuid4().hex[:24],str(uuid.uuid4()),str(uuid.uuid4()),1)
    def create(args,closed=False):
        execute('postgres',fence.register(*args,initialize=True))
        execute('postgres',fence.guarded(*args,f"CREATE DATABASE {args[0]} ALLOW_CONNECTIONS {'false' if closed else 'true'};"))
    args=identity();runtime=args[0];create(args)
    metadata=coordinator.observe(execute,runtime)
    check('native database OIDs are normalized before identity validation',type(metadata['control_oid']) is int and type(metadata['target']['oid']) is int)
    execute(runtime,fence.register(*args,initialize=True))
    execute(runtime,fence.guarded(*args,'CREATE TABLE public.barrier_events(value text);'))
    with tempfile.TemporaryDirectory(prefix='sbarbase-pair-') as directory:
        marker=Path(directory)/'control-committed'
        payload={'container':container,'admin':admin,'identity':args,'marker':str(marker)}
        code='''import json,os,signal,subprocess,sys
from pathlib import Path
sys.path.insert(0,os.environ['SBARBASE_PROBE_LAB'])
import sql_operation_revoke as coordinator
p=json.loads(os.environ['SBARBASE_FENCE_FIXTURE'])
def execute(database,script):
 r=subprocess.run(['docker','exec','-i',p['container'],'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',p['admin'],'-d',database],input=script,text=True,capture_output=True,timeout=15)
 if r.returncode:raise RuntimeError('Fixture SQL failed')
 return r.stdout
def checkpoint(phase):
 if phase=='control_committed':
  Path(p['marker']).write_text('committed')
  os.kill(os.getpid(),signal.SIGSTOP)
coordinator.revoke_pair(execute,*p['identity'],checkpoint=checkpoint)
raise RuntimeError('Crash checkpoint skipped')
'''
        child=subprocess.Popen(['/usr/bin/python3','-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
            env={**os.environ,'SBARBASE_PROBE_LAB':str(Path(__file__).parent),'SBARBASE_FENCE_FIXTURE':json.dumps(payload)})
        try:
            deadline=time.monotonic()+10
            while not marker.exists() or 'State:\tT (stopped)' not in Path('/proc',str(child.pid),'status').read_text():
                if child.poll() is not None:
                    detail=child.stderr.read().decode(errors='replace')
                    category=next((text for text in ('Control database identity unavailable','Target database identity unavailable','Fixture SQL failed','Database identity changed','ModuleNotFoundError') if text in detail),'unclassified fixture error')
                    raise RuntimeError('Coordinator checkpoint unavailable: '+category)
                if time.monotonic()>deadline:raise RuntimeError('Coordinator checkpoint unavailable: deadline')
                time.sleep(.02)
            child.kill();check('coordinator killed after acknowledged control revocation',child.wait(timeout=5)==-signal.SIGKILL)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=5)
            child.stderr.close()
    check('control token is durably revoked after crash',execute('postgres',f"SELECT state FROM {fence.TABLE} WHERE token='{args[1]}';").strip()=='revoked')
    check('target remains active between the two revocation commits',execute(runtime,f"SELECT state FROM {fence.TABLE} WHERE token='{args[1]}';").strip()=='active')
    check('control SQL cannot continue after first commit',denied('postgres',fence.guarded(*args,'SELECT 1;')))
    execute(runtime,fence.guarded(*args,"INSERT INTO public.barrier_events VALUES ('during-gap');"))
    check('target may still write before target revocation',execute(runtime,'SELECT count(*) FROM public.barrier_events;').strip()=='1')
    result=coordinator.revoke_pair(execute,*args)
    check('retry acknowledges both phases with exact database identity',result['control']=='revoked' and result['target']=='revoked' and type(result['target_oid']) is int)
    mismatch=docker('exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',admin,'-d',runtime,data=fence.revoke(*args,initialize=True,expected_oid=result['target_oid'],expected_cluster='1'),check=False)
    check('same database OID cannot substitute a different cluster',mismatch.returncode!=0 and 'Cluster identity changed' in mismatch.stderr)
    check('retry result binds exact original identity',all(result[k]==v for k,v in zip(('runtime','token','claim','attempt'),args)))
    check('completed target barrier denies old SQL',denied(runtime,fence.guarded(*args,"INSERT INTO public.barrier_events VALUES ('late');")))
    check('delayed target registration cannot resurrect old token',denied(runtime,fence.register(*args,initialize=True)))
    check('repeated complete revocation preserves evidence identity',coordinator.revoke_pair(execute,*args)==result)
    check('only pre-barrier target data remains',execute(runtime,'SELECT value FROM public.barrier_events;').strip()=='during-gap')
    unregistered=identity();create(unregistered)
    result=coordinator.revoke_pair(execute,*unregistered)
    check('target revoke initializes a durable tombstone before registration',result['target']=='revoked' and denied(unregistered[0],fence.register(*unregistered,initialize=True)))
    missing=identity();execute('postgres',fence.register(*missing,initialize=True))
    result=coordinator.revoke_pair(execute,*missing)
    check('absent target is recorded only after control revoke',result['target']=='absent' and result['target_oid'] is None)
    check('late guarded creation cannot materialize absent target',denied('postgres',fence.guarded(*missing,f'CREATE DATABASE {missing[0]};')))
    check('absent target stays absent',coordinator.observe(execute,missing[0])['target'] is None)
    closed=identity();create(closed,True)
    refused=False
    try:coordinator.revoke_pair(execute,*closed)
    except RuntimeError as error:refused='Closed target' in str(error)
    check('closed target does not become completed barrier evidence',refused)
    check('closed target remains closed',coordinator.observe(execute,closed[0])['target']['allows_connections'] is False)
    check('closed target failure still retains control revocation',execute('postgres',f"SELECT state FROM {fence.TABLE} WHERE token='{closed[1]}';").strip()=='revoked')
    replaced=identity();create(replaced)
    def replace(phase):
        if phase=='target_committed':
            execute('postgres',f'DROP DATABASE {replaced[0]};\nCREATE DATABASE {replaced[0]};')
    refused=False
    try:coordinator.revoke_pair(execute,*replaced,checkpoint=replace)
    except RuntimeError as error:refused='identity changed' in str(error)
    check('target replacement prevents reuse of old database evidence',refused)
