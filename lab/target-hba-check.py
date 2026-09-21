"""Live fresh-target HBA creation on a disposable pinned PostgreSQL container.

Proves the recovery-target writer path end to end: creation evidence, one-shot
generation initialization in the per-target authority state, owned publication
with parser and reload acknowledgment, exact stopped cleanup. Not a full
recovery restore, not services, not power loss.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import hba_authority as authority
import hba_generation
import hba_runtime
import hba_startup
import hba_target

OWNER='recovery-target'
ADMIN='supabase_admin'
LAB=Path(__file__).resolve().parent
PREFIX='sbarbase-restore-'+secrets.token_hex(6)


def docker(*args,data=None,check=True,env=None):
    return subprocess.run(['docker',*args],input=data,text=True,capture_output=True,check=check,timeout=180,env=env)


def remove_exact(kind,identifier):
    info=json.loads(docker('inspect',identifier).stdout)[0]
    if kind=='container':
        if info['Id']!=identifier or info['Config']['Labels'].get('io.sbarbase.owner')!=OWNER:
            raise RuntimeError('Cleanup ownership mismatch')
        docker('rm','-f',identifier)
    else:
        if info['Name']!=identifier or (info.get('Labels') or {}).get('io.sbarbase.owner')!=OWNER:
            raise RuntimeError('Cleanup ownership mismatch')
        docker(kind,'rm',identifier)


def main():
    checks=[]
    def check(label,condition):
        if not condition:raise AssertionError(label)
        checks.append(label);print('ok:',label)
    info=json.loads(docker('info','--format','{{json .}}').stdout)
    check('native local Linux daemon',info['OSType']=='linux' and info['Name']==socket.gethostname())
    memory=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
    check('host headroom checked before bounded probe',memory>=3*1024**3)
    image=json.loads((LAB/'distro-image.lock.json').read_text())['id']
    docker('image','inspect',image)
    name=PREFIX+'-db';volume=PREFIX+'-pgdata'
    state_dir=Path(tempfile.mkdtemp(prefix='sbar-target-hba-'))
    cid=None
    try:
        # Caller-verified absence immediately before creation, as the restore path does.
        for kind,identifier in (('container',name),('volume',volume)):
            check('no preexisting '+kind+' before creation',docker(kind,'inspect',identifier,check=False).returncode!=0)
        docker('volume','create','--label','io.sbarbase.owner='+OWNER,volume)
        lease=hba_startup.acquire(hba_runtime.prepare_target_state(state_dir,PREFIX))
        with lease as startup:
            writer=hba_runtime.TargetHBA(docker,state_dir,PREFIX,name,OWNER,image,startup=startup)
            writer.before_create(preexisting_volume=False)
            check('private per-target authority state prepared',writer.state==state_dir/'targets'/PREFIX and writer.state.stat().st_mode&0o777==0o700)
            env={**os.environ,'POSTGRES_PASSWORD':secrets.token_hex(32)}
            cid=docker('run','-d','--pull=never','--restart=no','--name',name,'--label','io.sbarbase.owner='+OWNER,*resource_policy.labels('maintenance'),
                       '--network','none','--memory','1024m','--memory-swap','1024m','--cpus','1','--pids-limit','96',
                       '--log-opt','max-size=1m','--log-opt','max-file=1','-v',volume+':/var/lib/postgresql/data',
                       '-e','POSTGRES_PASSWORD','-e','POSTGRES_HOST=/var/run/postgresql','-e','POSTGRES_DB=postgres',
                       image,'postgres','-c','config_file=/etc/postgresql/postgresql.conf','-c','log_statement=none',
                       env=env).stdout.strip()
            deadline=time.monotonic()+120
            while docker('exec',cid,'pg_isready','-U',ADMIN,check=False).returncode:
                if time.monotonic()>deadline:raise RuntimeError('Disposable database readiness deadline')
                time.sleep(.5)
            check('fresh pinned cluster ready for the writer',True)
            before=(state_dir/'targets'/PREFIX/hba_generation.NAME).exists()
            check('no generation pin before initialization',not before)
            target=writer.ready(cid,created=True) or writer.target
            check('writer captured the created container identity',target is not None and target.container_id==cid)
            pin=hba_generation.load(writer.state)
            check('per-target generation pin written',pin['target']['container_id']==cid and pin['target']['owner']==OWNER)
            check('backend authority initialized once',authority.read(docker,cid,pin['generation']).generation==pin['generation'])
            environment='e_'+uuid.uuid4().hex[:24]
            rules='\n'.join(['local all supabase_admin trust']
                            +[f'host {environment} {environment}_{kind} 0.0.0.0/0 scram-sha-256' for kind in ('auth','rest','storage')]
                            +['host all all 0.0.0.0/0 reject','host all all ::/0 reject'])+'\n'
            outcome=writer.publish(rules)
            check('owned publication archived as publication-witnessed',outcome['application']=='publication-witnessed' and outcome['activation']=='unknown')
            live=docker('exec',cid,'cat','/etc/postgresql/pg_hba.conf').stdout
            check('published file equals the desired rules plus one revision marker',
                  live.count('# sbarbase-hba-revision:')==1 and ''.join(line for line in live.splitlines(keepends=True) if not line.startswith('# sbarbase-hba-revision:'))==rules)
            check('no parser errors after publication',
                  docker('exec','-i',cid,'psql','-X','-qAt','-U',ADMIN,'-d','postgres',data='SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL;').stdout.strip()=='0')
            try:writer.publish(rules)
            except RuntimeError as error:
                check('writer refuses a second publication',('already attempted' in str(error)) or ('unavailable' in str(error)))
            else:raise AssertionError('Second publication was allowed')
            check('no pending journal remains after settlement',not (writer.state/'hba-operation.json').exists())
        docker('stop',cid)
        stopped=json.loads(docker('inspect',cid).stdout)[0]
        check('created container stopped by exact identity',stopped['Id']==cid and stopped['State']['Running'] is False)
        evidence={'image':image,'scope':('Fresh recovery-target HBA creation on a disposable pinned PostgreSQL container: caller-verified creation '
                  'evidence, per-target authority state, one-shot generation initialization, owned publication with parser and reload '
                  'acknowledgment, refusal of a second publication and exact stopped cleanup. Not a full recovery restore and not services.'),
                  'count':len(checks),'checks':checks}
        out=LAB.parent/'docs'/'evidence'/'target-hba-creation-checks.json'
        out.write_text(json.dumps(evidence,indent=1)+'\n')
        print('evidence:',out)
    finally:
        if cid is not None and docker('inspect',cid,check=False).returncode==0:remove_exact('container',cid)
        if docker('volume','inspect',volume,check=False).returncode==0:remove_exact('volume',volume)


if __name__=='__main__':main()