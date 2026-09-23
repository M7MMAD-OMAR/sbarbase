"""Verify original end-user Storage access after independent recovery."""
import base64
import hashlib
import json
import re
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
    if d['status']!='database-restored' or d.get('storage_stage')!='verified':raise RuntimeError('Explicit recovery reconciliation required')
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip() or lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+OWNER).stdout.strip():raise RuntimeError('Source and target must be stopped')
    if int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))<6*1024*1024:raise RuntimeError('Memory headroom unavailable')
    payload=open_bundle(json.loads(Path(d['archive']).read_text()),base64.b64decode(Path(d['key']).read_text(),validate=True))
    image=json.loads((lab.ROOT/'lab/storage-image.lock.json').read_text())['id']
    if image!=payload['images']['storage']['id']:raise RuntimeError('Storage image mismatch')
    name=d['prefix']+'-storage';auth=d['prefix']+'-auth'
    for item in (db,name,auth):
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
    def wait(url,headers=None):runtime.wait_ready(url,headers,'Service readiness timed out')
    try:
        lab.docker('start',db);lab.docker('start',name);lab.docker('start',auth)
        public=endpoint(name,5000)
        tenant_headers={'authorization':'Bearer '+runtime.token(payload['credentials']['jwt'],'service_role'),'x-forwarded-host':e+'.storage.internal'}
        wait(public+'/bucket',tenant_headers)
        fixture=payload.get('signed_url_fixture')
        check('encrypted archive contains a URL issued before export',bool(fixture) and fixture.get('issued_before_export') is True and fixture['issued_at']<=Path(d['archive']).stat().st_mtime)
        if not fixture['path'].startswith('/object/sign/'):raise RuntimeError('Unexpected pre-export URL')
        status,body=runtime.http(public+fixture['path'],headers={'x-forwarded-host':e+'.storage.internal'})
        check('unchanged pre-export signed URL downloads exact bytes on fresh target',status==200 and hashlib.sha256(body).hexdigest()==fixture['sha256'])
        auth_url=endpoint(auth,9999);wait(auth_url+'/health')
        users=json.loads(sql("SELECT jsonb_agg(jsonb_build_object('id',id,'email',email)) FROM auth.users WHERE email LIKE 'durable-%@example.com';",e))
        check('original fixture identity available',bool(users))
        user=users[0];match=re.fullmatch(r'durable-([a-f0-9-]{36})@example.com',user['email'])
        if not match:raise RuntimeError('Unsupported fixture')
        status,body=runtime.http(auth_url+'/token?grant_type=password','POST',json.dumps({'email':user['email'],'password':'Local-'+match[1]}).encode(),{'content-type':'application/json'})
        login=json.loads(body);check('original user logs in after independent recovery',status==200 and login['user']['id']==user['id'])
        user_headers={'authorization':'Bearer '+login['access_token'],'x-forwarded-host':e+'.storage.internal','content-type':'application/json'}
        objects=json.loads(sql("SELECT jsonb_agg(jsonb_build_object('bucket',bucket_id,'name',name,'version',version,'owner',owner_id)) FROM storage.objects;",e))
        own=[o for o in objects if o['owner']==user['id']];other=[o for o in objects if o['owner']!=user['id']]
        check('fixture contains own and different-owner objects',bool(own) and bool(other))
        for obj in own+other:
            suffix=quote(obj['bucket'],safe='')+'/'+quote(obj['name'],safe='/')
            expected=base64.b64decode(next(f['data'] for f in payload['files'] if f['path']==obj['bucket']+'/'+obj['name']+'/'+obj['version']))
            status,body=runtime.http(public+'/object/'+suffix,headers=user_headers)
            if obj in own:
                check('original user downloads own exact restored bytes',status==200 and body==expected)
                status,body=runtime.http(public+'/object/sign/'+suffix,'POST',json.dumps({'expiresIn':120}).encode(),user_headers)
                signed=json.loads(body);check('restored owner can issue signed URL',status==200 and isinstance(signed.get('signedURL'),str))
                url=signed['signedURL']
                if not url.startswith('/object/sign/'):raise RuntimeError('Unexpected signed URL format')
                status,body=runtime.http(public+url,headers={'x-forwarded-host':e+'.storage.internal'})
                check('new signed URL downloads exact bytes without user token',status==200 and body==expected)
            else:
                check('original user cannot download different-owner object',status in (400,401,403,404))
                status,_=runtime.http(public+'/object/sign/'+suffix,'POST',json.dumps({'expiresIn':120}).encode(),user_headers)
                check('original user cannot sign different-owner object',status in (400,401,403,404))
            anonymous={'authorization':'Bearer '+runtime.token(payload['credentials']['jwt'],'anon'),'x-forwarded-host':e+'.storage.internal'}
            check('anonymous token cannot download private object',runtime.http(public+'/object/'+suffix,headers=anonymous)[0] in (400,401,403,404))
        check('source remains stopped',not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip())
        stage('verified')
    finally:
        try:
            for item in (auth,name,db):
                if inspect('container',item):lab.docker('stop',item)
            if any(inspect('container',item) and inspect('container',item)['State']['Running'] for item in (auth,name,db)):raise RuntimeError('Cleanup incomplete')
        except BaseException:
            stage('cleanup-failed');raise
    checks.append('target stopped with restored objects retained')
    (lab.ROOT/'docs/evidence/independent-storage-rls-checks.json').write_text(json.dumps({'scope':'Original end-user login on independent target; own-object bytes, other-owner download/sign denial, anonymous denial and newly issued signed URL. Also verifies the unchanged URL issued before this encrypted export. Internal origin changes; public gateway cutover remains unverified.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' independent Storage RLS checks passed')


if __name__=='__main__':
    runtime.run_locked(main,'Storage recovery failed; private stage retained, sensitive output withheld')
