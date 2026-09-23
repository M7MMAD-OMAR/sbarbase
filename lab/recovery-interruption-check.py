"""Terminate a real transactional pg_restore, then retry the same encrypted dump.
Uses a disposable database on the retained target, never the restored environment.
"""
import base64
import json
import secrets
import subprocess
import time
from pathlib import Path
import durable_runtime as runtime
import run as lab
from recovery_bundle import open_bundle


def main():
    d=json.loads((runtime.STATE/'recovery-target.json').read_text());db=d['database']
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner=recovery-target').stdout.strip() or lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip():raise RuntimeError('Runtime must be stopped')
    if int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))<6*1024*1024:raise RuntimeError('Memory headroom unavailable')
    state=json.loads(lab.docker('container','inspect',db).stdout)[0]
    if state['Config']['Labels'].get('io.sbarbase.owner')!='recovery-target':raise RuntimeError('Ownership mismatch')
    payload=open_bundle(json.loads(Path(d['archive']).read_text()),base64.b64decode(Path(d['key']).read_text(),validate=True))
    dump=base64.b64decode(payload['database']);temporary='recovery_interrupt_'+secrets.token_hex(6)
    checks=[];process=None;created=False
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    def sql(query,database='postgres'):
        return lab.docker('exec','-i',db,'psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database,data=query).stdout.strip()
    try:
        lab.docker('start',db)
        for _ in range(60):
            if lab.docker('exec',db,'pg_isready',check=False).returncode==0:break
            time.sleep(.5)
        if sql("SELECT count(*) FROM pg_database WHERE datname='"+temporary+"';")!='0':raise RuntimeError('Fixture database exists')
        sql('CREATE DATABASE '+temporary+' TEMPLATE template0;');created=True
        # This trigger blocks restored table creation inside pg_restore's transaction.
        sql("CREATE SCHEMA recovery_probe; CREATE FUNCTION recovery_probe.pause() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_TAG='CREATE TABLE' THEN PERFORM pg_sleep(60); END IF; END $$; CREATE EVENT TRIGGER recovery_pause ON ddl_command_start EXECUTE FUNCTION recovery_probe.pause();",temporary)
        args=['docker','exec','-i',db,'pg_restore','-U','supabase_admin','--exit-on-error','--single-transaction','-d',temporary]
        # Supply dump through a private temporary file to avoid blocking stdin writes.
        import tempfile
        with tempfile.TemporaryFile() as data:
            data.write(dump);data.seek(0)
            process=subprocess.Popen(args,stdin=data,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            pid=None
            for _ in range(100):
                found=sql("SELECT pid FROM pg_stat_activity WHERE datname='"+temporary+"' AND application_name='pg_restore' AND wait_event='PgSleep';")
                if found:pid=int(found);break
                if process.poll() is not None:break
                time.sleep(.1)
            check('real pg_restore reaches paused table creation',pid is not None)
            check('server terminates active restore backend',sql('SELECT pg_terminate_backend('+str(pid)+');')=='t')
            check('interrupted pg_restore reports failure',process.wait(timeout=15)!=0)
        check('partial application and Auth schemas rolled back',sql("SELECT count(*) FROM pg_namespace WHERE nspname IN ('auth','storage');",temporary)=='0' and sql("SELECT to_regclass('public.durable_items') IS NULL;",temporary)=='t')
        sql('DROP EVENT TRIGGER recovery_pause; DROP SCHEMA recovery_probe CASCADE;',temporary)
        result=subprocess.run(args,input=dump,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=120)
        check('same dump restores after interruption without partial-table collisions',result.returncode==0)
        for table in payload['table_snapshots']:
            relation='.'.join('"'+table[k].replace('"','""')+'"' for k in ('schema','name'))
            actual=sql("SELECT count(*)||'|'||encode(extensions.digest(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]'),'sha256'),'hex') FROM "+relation+' t;',temporary).split('|')
            check('retry data matches '+table['schema']+'.'+table['name'],int(actual[0])==table['rows'] and actual[1]==table['sha256'])
    finally:
        try:
            if process is not None and process.poll() is None:
                process.kill();process.wait(timeout=10)
            if created:
                sql('DROP DATABASE '+temporary+' WITH (FORCE);')
                check('disposable interruption database removed',sql("SELECT count(*) FROM pg_database WHERE datname='"+temporary+"';")=='0')
        finally:
            lab.docker('stop',db)
            if json.loads(lab.docker('container','inspect',db).stdout)[0]['State']['Running']:raise RuntimeError('Target stop failed')
    (lab.ROOT/'docs/evidence/recovery-interruption-checks.json').write_text(json.dumps({'scope':'Real pg_restore server-backend termination and transactional rollback/retry of the encrypted dump into a disposable database. Retained environment not modified. Not controller SIGKILL, power-loss recovery or production orchestration.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' restore interruption checks passed')


if __name__=='__main__':
    runtime.run_locked(main,'Interruption probe failed; sensitive output withheld')
