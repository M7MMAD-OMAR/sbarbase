"""Explicit read-only continuation after Storage restoration, before verification."""
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
    if d['status']!='database-restored' or d.get('storage_stage')!='signing-and-files':raise RuntimeError('Explicit recovery reconciliation required')
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip() or lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+OWNER).stdout.strip():raise RuntimeError('Source and target must be stopped')
    if int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))<6*1024*1024:raise RuntimeError('Memory headroom unavailable')
    payload=open_bundle(json.loads(Path(d['archive']).read_text()),base64.b64decode(Path(d['key']).read_text(),validate=True))
    image=json.loads((lab.ROOT/'lab/storage-image.lock.json').read_text())['id']
    if image!=payload['images']['storage']['id']:raise RuntimeError('Storage image mismatch')
    name=d['prefix']+'-storage';volume=d['prefix']+'-objects';auth=d['prefix']+'-auth'
    for item in (db,name):
        if not inspect('container',item):raise RuntimeError('Target unavailable')
    values=json.loads((runtime.PRIVATE/(d['prefix']+'-storage.json')).read_text())
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
        lab.docker('start',db);lab.docker('start',name)
        public=endpoint(name,5000)
        tenant_headers={'authorization':'Bearer '+runtime.token(payload['credentials']['jwt'],'service_role'),'x-forwarded-host':e+'.storage.internal'}
        wait(public+'/bucket',tenant_headers)
        current=json.loads(lab.docker('exec','-i',name,'node','/tmp/sbarbase-storage-files.cjs',data=json.dumps({'operation':'snapshot','tenant':e})).stdout)
        check('restored object bytes and xattrs still match export',current==payload['files'])
        raw=json.loads(sql("SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM (SELECT id,tenant_id,kind,content,active,created_at FROM tenants_jwks) t;",'storage_metadata'))
        code="const fs=require('fs'),{decrypt}=require('/app/dist/internal/auth/crypto');process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(r=>({...r,content:JSON.parse(decrypt(r.content))}))));"
        decoded=json.loads(lab.docker('exec','-i',name,'node','-e',code,data=json.dumps(raw)).stdout)
        check('restored signing material matches export',decoded==payload['storage_jwks'])
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
