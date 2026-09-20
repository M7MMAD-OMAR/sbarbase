"""Restore file-backed Storage on the independent recovery target."""
import base64
import fcntl
import json
import secrets
import time
from pathlib import Path
from urllib.parse import quote
import durable_runtime as runtime
import run as lab
from recovery_bundle import open_bundle

OWNER='recovery-target'


def inspect(kind,name):
    result=lab.docker(kind,'inspect',name,check=False)
    if result.returncode:return None
    state=json.loads(result.stdout)[0]
    labels=state.get('Config',{}).get('Labels',{}) if kind=='container' else state.get('Labels',{})
    if (labels or {}).get('io.sbarbase.owner')!=OWNER:raise RuntimeError('Ownership collision')
    return state


def main():
    descriptor=runtime.STATE/'recovery-target.json';d=json.loads(descriptor.read_text());db=d['database'];e=d['environment']
    if d['status']!='database-restored' or d.get('storage_stage'):raise RuntimeError('Explicit recovery reconciliation required')
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip() or lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+OWNER).stdout.strip():raise RuntimeError('Source and target must be stopped')
    if int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))<6*1024*1024:raise RuntimeError('Memory headroom unavailable')
    payload=open_bundle(json.loads(Path(d['archive']).read_text()),base64.b64decode(Path(d['key']).read_text(),validate=True))
    image=json.loads((lab.ROOT/'lab/storage-image.lock.json').read_text())['id']
    if image!=payload['images']['storage']['id']:raise RuntimeError('Storage image mismatch')
    name=d['prefix']+'-storage';volume=d['prefix']+'-objects';auth=d['prefix']+'-auth'
    for kind,item in [('container',name),('volume',volume)]:
        if inspect(kind,item):raise RuntimeError('Storage destination exists')
    for item in (db,auth):
        if not inspect('container',item):raise RuntimeError('Verified target unavailable')
    values={k:secrets.token_hex(32) for k in ('control','admin','encryption')}
    runtime.atomic(runtime.PRIVATE/(d['prefix']+'-storage.json'),values)
    checks=[]
    def check(label,ok):
        if not ok:raise RuntimeError(label)
        checks.append(label)
    def stage(value):d['storage_stage']=value;runtime.atomic(descriptor,d)
    def sql(query,database='postgres'):
        return lab.docker('exec','-i',db,'psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',database,data=query).stdout.strip()
    def endpoint(container,port):
        address=inspect('container',container)['NetworkSettings']['Networks'][d['network']]['IPAddress']
        return f'http://{address}:{port}'
    def wait(url,headers=None):
        for _ in range(60):
            try:
                if runtime.http(url,headers=headers)[0]==200:return
            except OSError:pass
            time.sleep(.5)
        raise RuntimeError('Service readiness timed out')
    def literal(value):return "'"+str(value).replace("'","''")+"'"
    try:
        stage('initialize')
        lab.docker('start',db)
        for _ in range(60):
            if lab.docker('exec',db,'pg_isready',check=False).returncode==0:break
            time.sleep(.5)
        sql("CREATE ROLE storage_control LOGIN NOINHERIT CONNECTION LIMIT 6 PASSWORD "+literal(values['control'])+';')
        sql('CREATE DATABASE storage_metadata OWNER storage_control;')
        sql('REVOKE ALL ON DATABASE storage_metadata FROM PUBLIC; ALTER DATABASE storage_metadata CONNECTION LIMIT 6;')
        hba=['local all supabase_admin trust','host storage_metadata storage_control 0.0.0.0/0 scram-sha-256']+[f'host {e} {e}_{kind} 0.0.0.0/0 scram-sha-256' for kind in ('auth','rest','storage')]+['host all all 0.0.0.0/0 reject','host all all ::/0 reject']
        lab.docker('exec','-i',db,'sh','-c','cat > /etc/postgresql/pg_hba.conf',data='\n'.join(hba)+'\n');sql('SELECT pg_reload_conf();')
        env={'MULTI_TENANT':'true','MULTITENANT_DATABASE_URL':f"postgres://storage_control:{values['control']}@{db}:5432/storage_metadata",'ENCRYPTION_KEY':values['encryption'],'ADMIN_API_KEYS':values['admin'],'DB_INSTALL_ROLES':'false','STORAGE_BACKEND':'file','GLOBAL_S3_BUCKET':'sbarbase-lab','FILE_STORAGE_BACKEND_PATH':'/tmp/storage-data','REGION':'local','FILE_SIZE_LIMIT':'1048576','DATABASE_MAX_CONNECTIONS':'3','MULTITENANT_DATABASE_MAX_CONNECTIONS':'3','PG_QUEUE_ENABLE':'false','ENABLE_IMAGE_TRANSFORMATION':'false','S3_PROTOCOL_ENABLED':'false','X_FORWARDED_HOST_REGEXP':r'^(e_[a-f0-9]{24})\.storage\.internal$','LOG_LEVEL':'error'}
        envfile=runtime.PRIVATE/(name+'.env');lab.secure_file(envfile,''.join(k+'='+v+'\n' for k,v in env.items()))
        lab.docker('volume','create','--label','io.sbarbase.owner='+OWNER,volume)
        lab.docker('run','-d','--pull','never','--name',name,'--label','io.sbarbase.owner='+OWNER,'--network',d['network'],'--memory','512m','--memory-swap','512m','--cpus','0.5','--pids-limit','128','--log-opt','max-size=5m','--log-opt','max-file=2','--env-file',str(envfile),'-v',volume+':/tmp/storage-data',image)
        admin=endpoint(name,5001);headers={'apikey':values['admin'],'content-type':'application/json'};wait(admin+'/tenants',headers)
        stage('tenant')
        config=dict(payload['storage_tenant']);config['databaseUrl']=f"postgres://{e}_storage:{payload['credentials']['storage']}@{db}:5432/{e}"
        # Only fields supported by the existing tenant registration contract.
        config={k:config[k] for k in ('anonKey','serviceKey','jwtSecret','databaseUrl','maxConnections','features') if k in config}
        status,_=runtime.http(admin+'/tenants/'+e,'POST',json.dumps(config).encode(),headers)
        check('tenant registered against independent database',status==201)
        stage('signing-and-files')
        code="const fs=require('fs'),{encrypt}=require('/app/dist/internal/auth/crypto');const rows=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(rows.map(r=>({...r,content:encrypt(JSON.stringify(r.content))}))));"
        encrypted=json.loads(lab.docker('exec','-i',name,'node','-e',code,data=json.dumps(payload['storage_jwks'])).stdout)
        sql('DELETE FROM tenants_jwks WHERE tenant_id='+literal(e)+';','storage_metadata')
        for row in encrypted:
            columns=['id','tenant_id','kind','content','active','created_at']
            sql('INSERT INTO tenants_jwks ('+','.join(columns)+') VALUES ('+','.join(('true' if row[k] else 'false') if k=='active' else literal(row[k]) for k in columns)+');','storage_metadata')
        decode="const fs=require('fs'),{decrypt}=require('/app/dist/internal/auth/crypto');const rows=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(rows.map(r=>({...r,content:JSON.parse(decrypt(r.content))}))));"
        stored=json.loads(sql("SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM (SELECT id,tenant_id,kind,content,active,created_at FROM tenants_jwks WHERE tenant_id="+literal(e)+') t;','storage_metadata'))
        decoded=json.loads(lab.docker('exec','-i',name,'node','-e',decode,data=json.dumps(stored)).stdout)
        check('original tenant signing material preserved under fresh encryption',decoded==payload['storage_jwks'])
        lab.docker('cp',str(lab.ROOT/'lab/storage-files.cjs'),name+':/tmp/sbarbase-storage-files.cjs')
        def files(operation):return json.loads(lab.docker('exec','-i',name,'node','/tmp/sbarbase-storage-files.cjs',data=json.dumps({'operation':operation,'tenant':e,'files':payload['files']})).stdout)
        files('restore');check('object bytes and extended attributes match export',files('snapshot')==payload['files'])
        lab.docker('restart',name);wait(endpoint(name,5001)+'/tenants',headers)
        public=endpoint(name,5000);tenant_headers={'authorization':'Bearer '+runtime.token(payload['credentials']['jwt'],'service_role'),'x-forwarded-host':e+'.storage.internal'}
        wait(public+'/bucket',tenant_headers)
        objects=json.loads(sql("SELECT jsonb_agg(jsonb_build_object('bucket',bucket_id,'name',name,'version',version)) FROM storage.objects;",e))
        for obj in objects:
            path='/object/'+quote(obj['bucket'],safe='')+'/'+quote(obj['name'],safe='/')
            status,body=runtime.http(public+path,headers=tenant_headers)
            check('restored private object downloads with original environment key',status==200 and body==base64.b64decode(next(f['data'] for f in payload['files'] if f['path']==obj['bucket']+'/'+obj['name']+'/'+obj['version'])))
            bad={**tenant_headers,'authorization':'Bearer '+runtime.token(secrets.token_hex(32),'service_role')}
            check('unrelated signing secret denied object access',runtime.http(public+path,headers=bad)[0] in (400,401,403))
        check('source remains stopped',not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip())
        stage('verified')
    finally:
        try:
            for item in (name,db):
                if inspect('container',item):lab.docker('stop',item)
            if any(inspect('container',item) and inspect('container',item)['State']['Running'] for item in (name,db)):raise RuntimeError('Cleanup incomplete')
        except BaseException:
            stage('cleanup-failed');raise
    checks.append('target stopped with restored objects retained')
    (lab.ROOT/'docs/evidence/independent-storage-checks.json').write_text(json.dumps({'scope':'Independent file Storage, fresh platform credentials, preserved tenant signing material and exact object bytes/xattrs. Service-key downloads and unrelated-secret rejection. No pre-export signed URL or end-user Storage RLS proof yet.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' independent Storage checks passed')


if __name__=='__main__':
    try:
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);main()
    except Exception:
        raise SystemExit('Storage recovery failed; private stage retained, sensitive output withheld') from None
