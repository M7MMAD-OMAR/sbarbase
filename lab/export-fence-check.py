"""Live database fence on a disposable environment, retaining recovery data."""
import json
import secrets
import subprocess
import time
from pathlib import Path
import durable_runtime as runtime
import run as lab
import source_fence


def main():
    d=json.loads((runtime.STATE/'recovery-target.json').read_text());db=d['database'];e='e_'+secrets.token_hex(12)
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner=recovery-target').stdout.strip() or lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip():raise RuntimeError('Owned runtimes must be stopped')
    if int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))<6*1024*1024:raise RuntimeError('Insufficient headroom')
    state=json.loads(lab.docker('inspect',db).stdout)[0]
    if state['Config']['Labels'].get('io.sbarbase.owner')!='recovery-target':raise RuntimeError('Ownership mismatch')
    created=False;client=None;checks=[];roles=[];original_hba=None
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    def sql(query,database='postgres',check=True):return lab.docker('exec','-i',db,'psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database,data=query,check=check)
    def ready():
        for _ in range(60):
            if lab.docker('exec',db,'pg_isready',check=False).returncode==0:return
            time.sleep(.5)
        raise RuntimeError('Database not ready')
    try:
        lab.docker('start',db);ready()
        sql('CREATE DATABASE '+e+';');created=True
        original_hba=lab.docker('exec',db,'cat','/etc/postgresql/pg_hba.conf').stdout
        for kind in ('auth','rest','storage'):
            name=e+'_'+kind;sql('CREATE ROLE '+name+' LOGIN;');roles.append(name)
        lab.docker('exec','-i',db,'sh','-c','cat > /etc/postgresql/pg_hba.conf',data='host '+e+' '+e+'_auth 127.0.0.1/32 trust\n'+original_hba)
        sql('SELECT pg_reload_conf();')
        def service_connect():return lab.docker('exec',db,'psql','-X','-At','-h','127.0.0.1','-U',e+'_auth','-d',e,'-c','SELECT 1;',check=False)
        check('fixture service login connects before fencing',service_connect().returncode==0)
        journal=[]
        source_fence.prepare_export(sql,e,lambda value:journal.append(json.loads(json.dumps(value))))
        check('service login rejected after export fence',service_connect().returncode!=0)
        check('operator connection remains available for export',sql('SELECT 1;',e).stdout.strip()=='1')
        dump=subprocess.run(['docker','exec',db,'pg_dump','-U','supabase_admin','-Fc',e],capture_output=True,timeout=30)
        check('pg_dump completes with service logins disabled',dump.returncode==0 and dump.stdout.startswith(b'PGDMP'))
        check('original login intent retained in journal',journal[0]['phase']=='preparing' and all(r['login'] for r in journal[0]['original_logins']) and journal[-1]['phase']=='services-fenced')
        check('installer sees export-fenced environment as fenced',source_fence.is_fenced(sql,e))
        source_fence.fence(sql,e)
        check('final database fence also closes operator connections',sql('SELECT 1;',e,check=False).returncode!=0)
    finally:
        try:
            if client is not None and client.poll() is None:client.kill();client.wait(timeout=10)
            if created:sql('DROP DATABASE '+e+' WITH (FORCE);')
            for role in roles:sql('DROP ROLE '+role+';')
            if original_hba is not None:
                lab.docker('exec','-i',db,'sh','-c','cat > /etc/postgresql/pg_hba.conf',data=original_hba);sql('SELECT pg_reload_conf();')
        finally:lab.docker('stop',db)
    check('target stopped after fixture removal',not json.loads(lab.docker('inspect',db).stdout)[0]['State']['Running'])
    (lab.ROOT/'docs/evidence/export-fence-checks.json').write_text(json.dumps({'scope':'Disposable export fence: actual service login denied, local operator pg_dump succeeds, final database fence denies operator. Temporary roles/database/HBA rule removed. Not a full cutover export rehearsal.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' export fence checks passed')


if __name__=='__main__':
    runtime.run_locked(main,'Fence probe failed; sensitive output withheld')
