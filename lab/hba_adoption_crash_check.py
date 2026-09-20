"""Real legacy-adoption crash evidence on a disposable pinned PostgreSQL container.

Phases: healthy run, actual child SIGKILL after the durable database-started
checkpoint, and after the durable HBA completion checkpoint with pending journal
recovery. Not power loss, not daemon cancellation, not recovery-target writers.
"""
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
import atomic_hba
import hba_adoption as adoption
import hba_apply
import hba_authority as authority
import hba_generation
import hba_journal as journal
import hba_settlement
import hba_target

OWNER='hba-adoption-probe'
ADMIN='supabase_admin'
LAB=Path(__file__).resolve().parent


def docker(*args,data=None,check=True,env=None):
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=check,timeout=120,env=env)


def sql(cid,query):
    return docker('exec','-i',cid,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U',ADMIN,'-d','postgres',data=query).stdout.strip()


def wait_ready(cid):
    deadline=time.monotonic()+90
    while docker('exec',cid,'pg_isready','-U',ADMIN,check=False).returncode:
        if time.monotonic()>deadline:raise RuntimeError('Disposable database readiness deadline')
        time.sleep(.2)


def spawn_container(image,directory):
    name='sbarbase-adoption-'+uuid.uuid4().hex[:12]
    cidfile=Path(directory)/'container.id'
    env={**os.environ,'POSTGRES_PASSWORD':secrets.token_hex(32)}
    cid=docker('run','-d','--pull=never','--restart=no','--cidfile',str(cidfile),'--name',name,
               '--label','io.sbarbase.owner='+OWNER,'--network','none','--memory','1024m','--memory-swap','1024m',
               '--cpus','1','--pids-limit','96','--log-opt','max-size=1m','--log-opt','max-file=1',
               '--tmpfs','/var/lib/postgresql/data:rw,size=512m',
               '-e','POSTGRES_PASSWORD','-e','POSTGRES_HOST=/var/run/postgresql','-e','POSTGRES_DB=postgres',
               image,'postgres','-c','config_file=/etc/postgresql/postgresql.conf','-c','log_statement=none',
               env=env).stdout.strip()
    return name,cid


def remove_exact(name,cid,image):
    info=json.loads(docker('inspect',name).stdout)[0]
    if info['Id']!=cid or info['Image']!=image or info['Config']['Labels'].get('io.sbarbase.owner')!=OWNER:
        raise RuntimeError('Cleanup ownership mismatch')
    docker('rm','-f',cid)


def child(state):
    config=json.loads((state/'input.json').read_text())
    target=hba_target.Target(**config['target'])
    phase=config['phase']
    if phase in ('after-start','after-hba'):
        original=adoption.checkpoint
        def stopping_checkpoint(state_,phase_,payload):
            record=original(state_,phase_,payload)
            if phase_==('database-started' if phase=='after-start' else 'hba-completed'):
                os.kill(os.getpid(),signal.SIGSTOP)
            return record
        adoption.checkpoint=stopping_checkpoint
    if phase=='after-witness':
        # Kill with the durable completion witness on disk and the journal still pending.
        def stopping_settlement(*args,**kwargs):
            os.kill(os.getpid(),signal.SIGSTOP)
            return hba_settlement.complete_owned(*args,**kwargs)
        hba_settlement.complete_owned=stopping_settlement
    adoption.execute(docker,state,target=target)
    raise RuntimeError('Expected child to be killed at its checkpoint')


def run_phase(image,phase,check,docker):
    with tempfile.TemporaryDirectory(prefix='sbar-adoption-'+phase+'-') as directory:
        name,cid=spawn_container(image,directory)
        state=Path(directory)/'state';state.mkdir()
        try:
            wait_ready(cid)
            rules='local all all trust\nhost all all 0.0.0.0/0 reject\nhost all all ::/0 reject\n'
            docker('exec','-i',cid,'sh','-c','cat > /etc/postgresql/pg_hba.conf',data=rules)
            sql(cid,'SELECT pg_reload_conf();')
            docker('stop',cid)
            target=hba_target.Target(cid,name,OWNER,image)
            intent=adoption.publish_intent(docker,state,name=name,owner=OWNER,image=image)
            check(phase+': intent captured stopped inventory with pgdata volume',
                  intent['initial_state']=='stopped' and intent['target']['container_id']==cid
                  and any(m['destination']==adoption.PGDATA for m in intent['mounts']))
            config={'target':{'container_id':cid,'name':name,'owner':OWNER,'image':image},'phase':phase}
            (state/'input.json').write_text(json.dumps(config));(state/'input.json').chmod(0o600)
            inits=[]
            original_initialize=authority.initialize
            def counting_init(*args,**kwargs):
                inits.append(args);return original_initialize(*args,**kwargs)
            if phase=='healthy':
                with patch_initialize(counting_init):
                    completed=adoption.execute(docker,state,target=target)
                check(phase+': adoption completed and intent consumed',
                      completed['phase']=='completed' and not (state/adoption.NAME).exists())
                check(phase+': generation initialized exactly once',len(inits)==1)
            else:
                with tempfile.TemporaryDirectory() as logdir:
                    with (Path(logdir)/'child.log').open('wb') as log:
                        process=subprocess.Popen(['/usr/bin/python3',str(Path(__file__).resolve()),'--child',str(state)],
                                                 stdout=log,stderr=log,start_new_session=True,env={**os.environ,'PYTHONPATH':str(LAB)})
                        deadline=time.monotonic()+120
                        while time.monotonic()<deadline:
                            observed=os.waitid(os.P_PID,process.pid,os.WSTOPPED|os.WEXITED|os.WNOHANG|os.WNOWAIT)
                            if observed is not None:
                                if observed.si_code!=os.CLD_STOPPED:raise RuntimeError('Child exited before its durable checkpoint')
                                break
                            time.sleep(.05)
                        else:raise RuntimeError('Child checkpoint timed out')
                        os.kill(process.pid,signal.SIGKILL)
                        check(phase+': adopter actually killed at durable checkpoint',process.wait(timeout=10)==-signal.SIGKILL)
                with patch_initialize(counting_init):
                    if phase=='after-witness':
                        calls=[]
                        def traced(*args,**kwargs):
                            calls.append(args);return docker(*args,**kwargs)
                        outcome=hba_settlement.complete_applied(traced,state,target=target)
                        check(phase+': pending journal settled from durable witness without apply or SQL',
                              outcome['application']=='publication-witnessed'
                              and not any(authority.APPLY in args or 'psql' in args for args in calls))
                        check(phase+': settlement retained no pending journal',not (state/journal.NAME).exists())
                    completed=adoption.execute(docker,state,target=target)
                expected_inits=1 if phase=='after-start' else 0
                check(phase+': fresh recovery completes adoption from checkpoints',completed['phase']=='completed')
                check(phase+': recovery never repeats generation initialization',len(inits)==expected_inits)
            check(phase+': generation pin equals the intent generation',
                  hba_generation.load(state)['generation']==intent['generation'])
            check(phase+': checkpoints distinguish all durable phases',
                  all((state/adoption.CHECKPOINTS/(p+'.json')).exists() for p in adoption.PHASES))
            info=json.loads(docker('inspect',cid).stdout)[0]
            check(phase+': source database is stopped again by exact identity',
                  info['Id']==cid and info['State']['Running'] is False)
            with patch_initialize(counting_init):
                replay_inits=len(inits)
                try:adoption.execute(docker,state,target=target)
                except RuntimeError as error:
                    if 'already completed' not in str(error):raise
                else:raise AssertionError('Adoption replayed after completion')
            check(phase+': completed adoption refuses replay without new INIT',len(inits)==replay_inits)
        finally:
            if docker('inspect',name,check=False).returncode==0:remove_exact(name,cid,image)


import contextlib
@contextlib.contextmanager
def patch_initialize(wrapper):
    original=authority.initialize
    authority.initialize=wrapper
    try:yield
    finally:authority.initialize=original


def main():
    checks=[]
    def check(label,condition):
        if not condition:raise AssertionError(label)
        checks.append(label);print('ok:',label)
    info=json.loads(docker('info','--format','{{json .}}').stdout)
    check('native local Linux daemon',info['OSType']=='linux' and info['Name']==socket.gethostname())
    memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
    check('host headroom checked before bounded probe',memory>=4*1024**3)
    image=json.loads((LAB/'distro-image.lock.json').read_text())['id']
    docker('image','inspect',image)
    for phase in ('healthy','after-start','after-hba','after-witness'):
        run_phase(image,phase,check,docker)
    evidence={'image':image,'scope':'Durable legacy adoption on a disposable pinned Supabase PostgreSQL container: healthy run, adopter SIGKILL after database-started and after HBA-completion checkpoints, pending-journal recovery, exact stop and replay refusal. Quiescence of legacy host clients remains an explicit operational assumption; raw legacy writers are not fenced. Retained installation untouched.','count':len(checks),'checks':checks}
    out=LAB.parent/'docs'/'evidence'/'hba-adoption-crash-checks.json'
    out.write_text(json.dumps(evidence,indent=1)+'\n')
    print('evidence:',out)


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--child':child(Path(sys.argv[2]))
    elif len(sys.argv)==1:main()
    else:raise SystemExit('usage: hba_adoption_crash_check.py [--child STATE]')
