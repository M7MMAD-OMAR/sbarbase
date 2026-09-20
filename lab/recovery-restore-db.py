"""Restore the private fixture onto a fresh isolated cluster; leave it stopped.
Database stage only. Application/object/signed-URL verification follows separately.
"""
import base64
import fcntl
import hashlib
import json
import re
import secrets
import socket
import subprocess
import time
from pathlib import Path
import durable_runtime as runtime
import resource_admission
import run as lab
from recovery_bundle import open_bundle

OWNER='recovery-target'


def quote(value):
    return "'"+str(value).replace("'","''")+"'"


def identifier(value):
    return '"'+str(value).replace('"','""')+'"'


def main():
    source=json.loads((runtime.STATE/'recovery-latest.json').read_text())
    payload=open_bundle(json.loads(Path(source['archive']).read_text()),base64.b64decode(Path(source['key']).read_text(),validate=True))
    e=payload['environment'];roles=payload['roles'];database=payload['database_metadata'][0]
    scoped={e+'_'+kind for kind in ('auth','rest','storage')}
    if not re.fullmatch(r'e_[a-f0-9]{24}',e) or payload['format']!=2:raise RuntimeError('Unsupported identity')
    if {r['rolname'] for r in roles}!=scoped or len(roles)!=3:raise RuntimeError('Unsupported role inventory')
    for r in roles:
        if not r['rolcanlogin'] or any(r[k] for k in ('rolsuper','rolcreaterole','rolcreatedb','rolreplication','rolbypassrls')):raise RuntimeError('Privileged role rejected')
        if r['rolconfig'] or r['rolvaliduntil'] is not None or not isinstance(r['rolconnlimit'],int):raise RuntimeError('Unsupported role settings')
    for kind in ('auth','rest','storage','jwt'):
        if not re.fullmatch('[a-f0-9]{64}',payload['credentials'][kind]):raise RuntimeError('Invalid generated credential')
    for row in payload['memberships']:
        if row['member'] not in scoped or row['parent'] not in ('anon','authenticated','service_role') or row['admin_option']:raise RuntimeError('Unsupported membership')
    for row in payload['database_acl']:
        if row['grantee'] not in scoped|{'supabase_admin','PUBLIC'} or row['grantor']!='supabase_admin' or row['privilege_type'] not in ('CONNECT','CREATE','TEMPORARY'):raise RuntimeError('Unsupported database ACL')
    for row in payload['settings']:
        if row['role'] not in scoped or row['database'] not in (e,'ALL'):raise RuntimeError('Unsupported scoped defaults')
        if any(setting.split('=',1)[0] not in ('search_path','statement_timeout','transaction_timeout') for setting in row['setconfig']):raise RuntimeError('Unsupported setting')
    if database['datname']!=e or database['owner']!='supabase_admin' or database['datlocprovider'] not in ('c','i'):raise RuntimeError('Unsupported database properties')
    if 'datlocale' not in database or 'table_snapshots' not in payload:raise RuntimeError('Export requires locale and row snapshots')
    dump=base64.b64decode(payload['database'],validate=True)
    if hashlib.sha256(dump).hexdigest()!=payload['database_sha256']:raise RuntimeError('Dump checksum mismatch')
    pin=json.loads((lab.ROOT/'lab/distro-image.lock.json').read_text())
    if payload['images']['db']['id']!=pin['id']:raise RuntimeError('Pinned image mismatch')
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip():raise RuntimeError('Stop source before target startup')
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+OWNER).stdout.strip():raise RuntimeError('A restore target is already running')
    info=json.loads(resource_admission.docker('info','--format','{{json .}}'))
    if info.get('Name')!=socket.gethostname() or info.get('OSType')!='linux':raise RuntimeError('Native local Linux daemon required')
    available=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
    if available<6*1024*1024:raise RuntimeError('Insufficient host memory headroom')
    prefix='sbarbase-restore-'+secrets.token_hex(6);db=prefix+'-db';network=prefix+'-net';volume=prefix+'-pgdata'
    descriptor={**source,'environment':e,'prefix':prefix,'database':db,'network':network,'volume':volume,'status':'initializing','stage':'allocate'}
    record=runtime.STATE/'recovery-target.json'
    runtime.atomic(record,descriptor)
    checks=[]
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    def sql(query,database='postgres',check=True):
        return lab.docker('exec','-i',db,'psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database,data=query,check=check)
    def rows(query,database='postgres'):
        return json.loads(sql(f"SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]'::jsonb) FROM ({query}) t;",database).stdout)
    def stage(name):
        descriptor['stage']=name;runtime.atomic(record,descriptor)
    for kind,name in (('container',db),('network',network),('volume',volume)):
        if lab.docker(kind,'inspect',name,check=False).returncode==0:raise RuntimeError('Target resource already exists')
    try:
        lab.docker('network','create','--internal','--label','io.sbarbase.owner='+OWNER,network)
        lab.docker('volume','create','--label','io.sbarbase.owner='+OWNER,volume)
        stage('destination-headroom')
        measured=lab.docker('run','--rm','--pull','never','--network','none','--memory','64m','--memory-swap','64m','--cpus','0.25','--pids-limit','32','--label','io.sbarbase.owner='+OWNER,'--entrypoint','sh','-v',volume+':/target:ro',pin['id'],'-c','df -Pk /target && df -Pi /target && stat -f -c %t /target').stdout.splitlines()
        blocks=measured[1].split();inodes=measured[3].split()
        free_inodes=None if int(inodes[1])==0 and int(inodes[3])==0 and measured[4].strip().lower()=='9123683e' else int(inodes[3])
        free_bytes=int(blocks[3])*1024
        if resource_admission.refusal(resource_admission.Snapshot(available*1024,free_bytes,free_bytes,free_inodes,free_inodes)):raise RuntimeError('Destination headroom unavailable')
        check('fresh destination volume has disk and inode headroom',True)
        env=runtime.PRIVATE/(prefix+'.env')
        lab.secure_file(env,'POSTGRES_PASSWORD='+secrets.token_hex(32)+'\nPOSTGRES_HOST=/var/run/postgresql\nPOSTGRES_DB=postgres\n')
        lab.docker('run','-d','--pull','never','--name',db,'--label','io.sbarbase.owner='+OWNER,'--network',network,'--memory','1024m','--memory-swap','1024m','--cpus','1','--pids-limit','128','--log-opt','max-size=5m','--log-opt','max-file=2','--env-file',str(env),'-v',volume+':/var/lib/postgresql/data',pin['id'],'postgres','-c','config_file=/etc/postgresql/postgresql.conf','-c','log_statement=none')
        stage('bootstrap')
        ready=False
        for _ in range(120):
            result=sql("SELECT to_regrole('supabase_privileged_role') IS NOT NULL;",check=False)
            if result.returncode==0 and result.stdout.strip()=='t' and lab.docker('exec',db,'pg_isready','-h','127.0.0.1',check=False).returncode==0:ready=True;break
            time.sleep(.5)
        check('fresh pinned Supabase cluster ready',ready)
        check('source remains stopped during target bootstrap',not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip())
        canonical=rows("SELECT rolname,rolconfig FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role') ORDER BY rolname")
        check('canonical API defaults match source',canonical==payload['canonical_defaults'])
        stage('roles')
        for role in roles:
            name=role['rolname'];kind=name.rsplit('_',1)[1]
            sql(f"CREATE ROLE {identifier(name)} LOGIN {'INHERIT' if role['rolinherit'] else 'NOINHERIT'} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {role['rolconnlimit']} PASSWORD {quote(payload['credentials'][kind])};")
        for row in payload['memberships']:
            sql(f"GRANT {identifier(row['parent'])} TO {identifier(row['member'])} WITH INHERIT {'TRUE' if row['inherit_option'] else 'FALSE'}, SET {'TRUE' if row['set_option'] else 'FALSE'};")
        stage('create-empty-database')
        locale='icu' if database['datlocprovider']=='i' else 'libc'
        options=f"TEMPLATE template0 OWNER supabase_admin ENCODING {quote(database['encoding'])} LC_COLLATE {quote(database['datcollate'])} LC_CTYPE {quote(database['datctype'])} LOCALE_PROVIDER {locale}"
        if locale=='icu':
            options+=' ICU_LOCALE '+quote(database['datlocale'])
            if database['daticurules'] is not None:options+=' ICU_RULES '+quote(database['daticurules'])
        sql(f'CREATE DATABASE {identifier(e)} WITH {options};')
        stage('restore-dump')
        restored=subprocess.run(['docker','exec','-i',db,'pg_restore','-U','supabase_admin','--exit-on-error','--single-transaction','-d',e],input=dump,capture_output=True)
        if restored.returncode:raise RuntimeError('Database restore failed')
        check('logical dump restored transactionally on separate cluster',True)
        stage('reconcile-boundaries')
        sql(f'REVOKE ALL ON DATABASE {identifier(e)} FROM PUBLIC; ALTER DATABASE {identifier(e)} CONNECTION LIMIT {int(database["datconnlimit"])};')
        for row in payload['database_acl']:
            grantee='PUBLIC' if row['grantee']=='PUBLIC' else identifier(row['grantee'])
            sql(f"GRANT {row['privilege_type']} ON DATABASE {identifier(e)} TO {grantee}"+(' WITH GRANT OPTION' if row['is_grantable'] else '')+';')
        for row in payload['settings']:
            scope='' if row['database']=='ALL' else ' IN DATABASE '+identifier(e)
            for setting in row['setconfig']:
                key,value=setting.split('=',1);sql(f'ALTER ROLE {identifier(row["role"])}{scope} SET {identifier(key)} TO {quote(value)};')
        hba=['local all supabase_admin trust']+[f'host {e} {name} 0.0.0.0/0 scram-sha-256' for name in sorted(scoped)]+['host all all 0.0.0.0/0 reject','host all all ::/0 reject']
        lab.docker('exec','-i',db,'sh','-c','cat > /etc/postgresql/pg_hba.conf',data='\n'.join(hba)+'\n');sql('SELECT pg_reload_conf();')
        stage('verify-data')
        tables=rows("SELECT n.nspname AS schema,c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('auth','storage','public') AND c.relkind='r' ORDER BY 1,2",e)
        check('application Auth and Storage table inventory matches',tables==[{k:t[k] for k in ('schema','name')} for t in payload['table_snapshots']])
        for table in payload['table_snapshots']:
            relation=identifier(table['schema'])+'.'+identifier(table['name'])
            digest=sql(f"SELECT count(*)||'|'||encode(extensions.digest(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]'),'sha256'),'hex') FROM {relation} t;",e).stdout.strip().split('|')
            check('restored table row count and digest match '+table['schema']+'.'+table['name'],int(digest[0])==table['rows'] and digest[1]==table['sha256'])
        current=rows(f"SELECT datname,pg_get_userbyid(datdba) AS owner,pg_encoding_to_char(encoding) AS encoding,datcollate,datctype,datlocprovider,datlocale,daticurules,datcollversion,datconnlimit FROM pg_database WHERE datname={quote(e)}")
        check('database locale ownership and limits match',current==payload['database_metadata'])
        check('actual collation version matches recorded version',sql(f"SELECT datcollversion IS NOT DISTINCT FROM pg_database_collation_actual_version(oid) FROM pg_database WHERE datname={quote(e)};").stdout.strip()=='t')
        for kind in ('auth','rest','storage'):
            def connect(database,query):
                return lab.docker('exec','-i',db,'sh','-c','read -r PGPASSWORD; export PGPASSWORD; exec psql -X -At -v ON_ERROR_STOP=1 -h "$1" -U "$2" -d "$3"','restore',db,e+'_'+kind,database,data=payload['credentials'][kind]+'\n'+query,check=False)
            check('restored '+kind+' credential connects only to target',connect(e,'SELECT 1;').stdout.strip()=='1' and connect('postgres','SELECT 1;').returncode!=0)
            if kind=='rest':check('REST SQL deadlines apply after fresh login',connect(e,"SELECT current_setting('statement_timeout')||'|'||current_setting('transaction_timeout');").stdout.strip()=='8s|12s')
            if kind=='auth':check('Auth search path restored',connect(e,"SHOW search_path;").stdout.strip()=='auth')
        descriptor['status']='database-restored';stage('verified')
        evidence={'scope':'Fresh separate PostgreSQL cluster, database stage only. No target Auth/REST/Storage processes, object restore or signed-URL verification yet. Source stayed stopped; target stopped with isolated volume retained.','checks':checks,'count':len(checks),'tables':len(tables),'target_memory_mib':1024,'target_cpus':1}
    finally:
        inspected=lab.docker('container','inspect',db,check=False)
        if inspected.returncode==0:
            state=json.loads(inspected.stdout)[0]
            if state['Config']['Labels'].get('io.sbarbase.owner')!=OWNER:raise RuntimeError('Target ownership changed')
            lab.docker('stop',db)
        if descriptor['status']!='database-restored':descriptor['status']='failed'
        runtime.atomic(record,descriptor)
    (lab.ROOT/'docs/evidence/independent-database-restore.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print(f'{len(checks)} independent database restore checks passed; target retained stopped.')


if __name__=='__main__':
    try:
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);main()
    except Exception:
        raise SystemExit('Independent database restore failed; inspect private stage descriptor, sensitive output withheld.') from None
