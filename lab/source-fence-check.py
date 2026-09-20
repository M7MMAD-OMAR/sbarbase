"""Live database fence on a disposable environment, retaining recovery data."""
import fcntl
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
    created=False;client=None;checks=[]
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
        before=sql('SELECT count(*) FROM public.durable_items;',d['environment']).stdout.strip()
        client=subprocess.Popen(['docker','exec',db,'psql','-X','-At','-U','supabase_admin','-d',e,'-c','SELECT pg_sleep(60);'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        active=False
        for _ in range(50):
            if sql("SELECT count(*) FROM pg_stat_activity WHERE datname='"+e+"' AND wait_event='PgSleep';").stdout.strip()=='1':active=True;break
            time.sleep(.1)
        check('existing fixture session is active before fence',active)
        source_fence.fence(sql,e)
        check('existing database session terminated',client.wait(timeout=10)!=0)
        check('new privileged database connection denied',sql('SELECT 1;',e,check=False).returncode!=0)
        check('neighbor retained environment remains readable',sql('SELECT count(*) FROM public.durable_items;',d['environment']).stdout.strip()==before)
        lab.docker('restart',db);ready()
        check('database fence survives server restart',source_fence.is_fenced(sql,e))
        check('new connection still denied after restart',sql('SELECT 1;',e,check=False).returncode!=0)
        source_fence.unfence(sql,e)
        check('explicit operator rollback restores connectivity',sql('SELECT 1;',e).stdout.strip()=='1')
    finally:
        try:
            if client is not None and client.poll() is None:client.kill();client.wait(timeout=10)
            if created:sql('DROP DATABASE '+e+' WITH (FORCE);')
        finally:lab.docker('stop',db)
    check('target stopped after fixture removal',not json.loads(lab.docker('inspect',db).stdout)[0]['State']['Running'])
    (lab.ROOT/'docs/evidence/source-fence-checks.json').write_text(json.dumps({'scope':'Database connection fence and session termination on disposable database, persistence across restart and explicit rollback. Retained neighbor read checked. Does not fence in-flight Storage filesystem/S3 writes or prove full cutover.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' database fence checks passed')


if __name__=='__main__':
    try:
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);main()
    except Exception:raise SystemExit('Fence probe failed; sensitive output withheld') from None
