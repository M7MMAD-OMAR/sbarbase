"""Capture one durable environment into a private, portable recovery fixture.
Stops the owned source stack. No target restore or off-host backup is claimed.
"""
import base64
import fcntl
import hashlib
import json
import os
import select
import time
import re
import secrets
import sqlite3
import subprocess
from pathlib import Path
import durable_runtime as runtime
import run as lab
from recovery_bundle import seal, open_bundle, MAX_PAYLOAD


def binary(args, data=None):
    if data is not None and len(data)>MAX_PAYLOAD:raise RuntimeError('Recovery input too large')
    process=subprocess.Popen(args,stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        if data is not None:process.stdin.write(data);process.stdin.close()
        parts=[];size=0;end=time.monotonic()+120
        while True:
            remaining=end-time.monotonic()
            if remaining<=0 or not select.select([process.stdout],[],[],remaining)[0]:
                raise RuntimeError('Recovery export command timed out')
            chunk=os.read(process.stdout.fileno(),min(65536,MAX_PAYLOAD+1-size))
            if not chunk:break
            parts.append(chunk);size+=len(chunk)
            if size>MAX_PAYLOAD:raise RuntimeError('Recovery fixture exceeds payload budget')
        if process.wait(timeout=max(.1,end-time.monotonic())):raise RuntimeError('Recovery export command failed')
        return b''.join(parts)
    finally:
        if process.poll() is None:process.kill();process.wait()
        if process.stdin and not process.stdin.closed:process.stdin.close()
        process.stdout.close()


def main():
    checks=[]
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    target=runtime.Runtime()
    if not runtime.inspect('container',runtime.DB)['State']['Running']:
        raise RuntimeError('Owned source runtime must be running')
    probe=json.loads((runtime.STATE/'probe.json').read_text())
    with sqlite3.connect('file:'+str(runtime.STATE/'control.sqlite')+'?mode=ro',uri=True) as catalog:
        job=catalog.execute("SELECT runtime FROM provision_jobs WHERE environment=? AND state='succeeded'",(probe['environments'][0],)).fetchone()
    if not job or job[0] not in target.values['environments']:raise RuntimeError('Recovery fixture unavailable')
    e=job[0]
    if not re.fullmatch(r'e_[a-f0-9]{24}',e):raise RuntimeError('Invalid source environment')
    def rows(query,database='postgres'):
        return json.loads(target.sql(f"SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]'::jsonb) FROM ({query}) t;",database).stdout)
    storage=runtime.PREFIX+'-storage'
    admin=target.endpoint(storage,5001)
    status,raw=runtime.http(admin+'/tenants/'+e,headers={'apikey':target.values['storage_admin']})
    check('selected tenant configuration readable',status==200)
    tenant=json.loads(raw)
    raw_tenant=rows(f"SELECT * FROM tenants WHERE id='{e}'",'storage_metadata')
    raw_keys=rows(f"SELECT id,tenant_id,kind,content,active,created_at FROM tenants_jwks WHERE tenant_id='{e}' ORDER BY id",'storage_metadata')
    # Decrypt only selected tenant keys inside the pinned Storage process. Its
    # shared encryption key remains in that process and is not put in the bundle.
    code="const fs=require('fs'),{decrypt}=require('/app/dist/internal/auth/crypto');const rows=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(rows.map(r=>({...r,content:JSON.parse(decrypt(r.content))}))));"
    jwks=json.loads(binary(['docker','exec','-i',storage,'node','-e',code],json.dumps(raw_keys).encode()))
    for table in ('tenants_s3_credentials','iceberg_catalogs','iceberg_namespaces','iceberg_tables','shard_reservation','shard_slots'):
        check('unsupported '+table+' state absent',not rows(f"SELECT 1 FROM {table} WHERE tenant_id='{e}' LIMIT 1",'storage_metadata'))
    running=lab.docker('ps','--format','{{.Names}}','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.split()
    for name in running:runtime.inspect('container',name)
    services=[name for name in running if name!=runtime.DB]
    try:
        if services:lab.docker('stop',*services)
        check('only source database remains running',lab.docker('ps','--format','{{.Names}}','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.split()==[runtime.DB])
        check('selected database has no remaining client sessions',not rows(f"SELECT 1 FROM pg_stat_activity WHERE datname='{e}' AND backend_type='client backend' LIMIT 1"))
        check('selected database has no prepared transactions',not rows(f"SELECT 1 FROM pg_prepared_xacts WHERE database='{e}' LIMIT 1"))
        check('selected database has no replication slots',not rows(f"SELECT 1 FROM pg_replication_slots WHERE database='{e}' LIMIT 1"))
        check('selected database has no enabled subscriptions',not rows(f"SELECT 1 FROM pg_subscription WHERE subdbid=(SELECT oid FROM pg_database WHERE datname='{e}') AND subenabled LIMIT 1"))
        cron_exists=target.sql("SELECT to_regclass('cron.job') IS NOT NULL;").stdout.strip()=='t'
        check('selected database has no active cron jobs',not cron_exists or not rows(f"SELECT 1 FROM cron.job WHERE database='{e}' AND active LIMIT 1"))
        for table in ('tenants_s3_credentials','iceberg_catalogs','iceberg_namespaces','iceberg_tables','shard_reservation','shard_slots'):
            check('quiesced '+table+' state absent',not rows(f"SELECT 1 FROM {table} WHERE tenant_id='{e}' LIMIT 1",'storage_metadata'))
        after_keys=rows(f"SELECT id,tenant_id,kind,content,active,created_at FROM tenants_jwks WHERE tenant_id='{e}' ORDER BY id",'storage_metadata')
        check('signing state unchanged through quiescence',after_keys==raw_keys)
        check('tenant configuration unchanged through quiescence',rows(f"SELECT * FROM tenants WHERE id='{e}'",'storage_metadata')==raw_tenant)
        role_names=[e+'_'+kind for kind in ('auth','rest','storage')]
        selected=','.join("'"+name+"'" for name in role_names)
        roles=rows(f"SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconnlimit,rolvaliduntil,rolconfig FROM pg_roles WHERE rolname IN ({selected}) ORDER BY rolname")
        check('exact scoped logins exported',len(roles)==3 and all(r['rolcanlogin'] and not any(r[k] for k in ('rolsuper','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')) for r in roles))
        memberships=rows(f"SELECT parent.rolname AS parent,member.rolname AS member,m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles member ON member.oid=m.member WHERE member.rolname IN ({selected}) ORDER BY 1,2")
        check('memberships stay inside canonical API roles',all(m['parent'] in ('anon','authenticated','service_role') and not m['admin_option'] for m in memberships))
        database=rows(f"SELECT datname,pg_get_userbyid(datdba) AS owner,pg_encoding_to_char(encoding) AS encoding,datcollate,datctype,datlocprovider,datlocale,daticurules,datcollversion,datconnlimit FROM pg_database WHERE datname='{e}'")
        acl=rows(f"SELECT coalesce(grantee.rolname,'PUBLIC') AS grantee,grantor.rolname AS grantor,a.privilege_type,a.is_grantable FROM pg_database d CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a LEFT JOIN pg_roles grantee ON grantee.oid=a.grantee JOIN pg_roles grantor ON grantor.oid=a.grantor WHERE d.datname='{e}' ORDER BY 1,3")
        settings=rows(f"SELECT coalesce(r.rolname,'ALL') AS role,coalesce(d.datname,'ALL') AS database,s.setconfig FROM pg_db_role_setting s LEFT JOIN pg_roles r ON r.oid=s.setrole LEFT JOIN pg_database d ON d.oid=s.setdatabase WHERE d.datname='{e}' OR (s.setdatabase=0 AND r.rolname IN ({selected})) ORDER BY 1,2")
        defaults=rows("SELECT rolname,rolconfig FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role') ORDER BY rolname")
        tables=rows("SELECT n.nspname AS schema,c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('auth','storage','public') AND c.relkind='r' ORDER BY 1,2",e)
        snapshots=[]
        for table in tables:
            identifier='.'.join('"'+table[k].replace('"','""')+'"' for k in ('schema','name'))
            digest=target.sql(f"SELECT count(*)||'|'||encode(extensions.digest(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]'),'sha256'),'hex') FROM {identifier} t;",e).stdout.strip().split('|')
            snapshots.append({**table,'rows':int(digest[0]),'sha256':digest[1]})
        dump=binary(['docker','exec',runtime.DB,'pg_dump','-U','supabase_admin','-Fc',e])
        volume=runtime.PREFIX+'-objects';runtime.inspect('volume',volume)
        helper=(lab.ROOT/'lab/storage-files.cjs').read_text()
        helper_name='sbarbase-recovery-files-'+secrets.token_hex(8)
        if lab.docker('inspect',helper_name,check=False).returncode==0:raise RuntimeError('Snapshot helper collision')
        try:
            files=json.loads(binary(['docker','run','--rm','-i','--name',helper_name,'--label','io.sbarbase.owner=recovery-export','--network','none','--memory','128m','--memory-swap','128m','--cpus','.25','--pids-limit','64','--mount','type=volume,source='+volume+',target=/tmp/storage-data,readonly','--entrypoint','node',target.pins['storage']['id'],'-e',helper],json.dumps({'operation':'snapshot','tenant':e}).encode()))
        finally:
            remaining=lab.docker('inspect',helper_name,check=False)
            if remaining.returncode==0:
                if json.loads(remaining.stdout)[0]['Config']['Labels'].get('io.sbarbase.owner')!='recovery-export':raise RuntimeError('Snapshot helper ownership changed')
                lab.docker('rm','-f',helper_name)
        check('objects and metadata captured',len(files)>0)
        payload={'format':2,'environment':e,'images':target.pins,'database':base64.b64encode(dump).decode(),'database_sha256':hashlib.sha256(dump).hexdigest(),'database_metadata':database,'table_snapshots':snapshots,'database_acl':acl,'roles':roles,'memberships':memberships,'settings':settings,'canonical_defaults':defaults,'credentials':target.values['environments'][e],'storage_tenant':tenant,'storage_jwks':jwks,'files':files,'scope':'Quiescent local file-backed environment. No Vault/function/external-object-store state. Source address in tenant config must be rebound on isolated target.'}
        key=secrets.token_bytes(32);envelope=seal(payload,key)
        check('authenticated bundle round trip matches',open_bundle(envelope,key)==payload)
        check('shared platform credentials not added to configuration',all(target.values[k] not in json.dumps({name:value for name,value in payload.items() if name not in ('database','files')}) for k in ('encryption','admin','storage_admin','storage_control')))
        name='recovery-'+secrets.token_hex(8)
        archive=runtime.STATE/(name+'.json');key_path=runtime.PRIVATE/(name+'.key')
        check('archive and key paths ignored',all(subprocess.run(['git','check-ignore','-q',str(p)],cwd=lab.ROOT).returncode==0 for p in (archive,key_path)))
        runtime.atomic(archive,envelope);lab.secure_file(key_path,base64.b64encode(key).decode())
        runtime.atomic(runtime.STATE/'recovery-latest.json',{'archive':str(archive),'key':str(key_path)})
        check('private archive and key permissions',archive.stat().st_mode&0o777==0o600 and key_path.stat().st_mode&0o777==0o600)
        evidence={'scope':'Encrypted selected-environment export only, not a separate-cluster restore or off-host backup. Source services quiesced; whole owned source runtime stopped afterward. Shared encryption/admin keys not intentionally included in configuration; database contents are not scanned for embedded secrets.', 'checks':checks,'count':len(checks),'roles':len(roles),'objects':len(files),'signing_keys':len(jwks),'dump_bytes':len(dump),'encrypted_bytes':archive.stat().st_size}
    finally:
        runtime.stop()
    check('owned source runtime stopped after export',not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip())
    evidence['count']=len(checks)
    (lab.ROOT/'docs/evidence/recovery-export-checks.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(f'{len(checks)} recovery export checks passed; private artifact retained.')


if __name__=='__main__':
    try:
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            main()
    except Exception:
        raise SystemExit('Recovery export failed; private state retained, sensitive output withheld.') from None
