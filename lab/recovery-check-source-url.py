"""Sequential source-issued URL verification against the independent target.
The URL is issued after export, so this is not a pre-export URL fixture.
"""
import base64
import json
import time
from pathlib import Path
from urllib.parse import quote
import durable_runtime as runtime
import run as lab
from recovery_bundle import open_bundle


def main():
    d=json.loads((runtime.STATE/'recovery-target.json').read_text());e=d['environment']
    if d.get('storage_stage')!='verified':raise RuntimeError('Verified target required')
    for owner in (runtime.OWNER,'recovery-target'):
        if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+owner).stdout.strip():raise RuntimeError('All experiment services must be stopped')
    if int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))<6*1024*1024:raise RuntimeError('Insufficient memory')
    payload=open_bundle(json.loads(Path(d['archive']).read_text()),base64.b64decode(Path(d['key']).read_text(),validate=True))
    source=runtime.Runtime();source_storage=runtime.PREFIX+'-storage';target_storage=d['prefix']+'-storage'
    checks=[]
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    def state(name,owner):
        value=json.loads(lab.docker('container','inspect',name).stdout)[0]
        if value['Config']['Labels'].get('io.sbarbase.owner')!=owner:raise RuntimeError('Ownership mismatch')
        return value
    for name,owner in [(runtime.DB,runtime.OWNER),(source_storage,runtime.OWNER),(d['database'],'recovery-target'),(target_storage,'recovery-target')]:state(name,owner)
    def endpoint(name,network):return 'http://'+state(name,runtime.OWNER if network==runtime.NETWORK else 'recovery-target')['NetworkSettings']['Networks'][network]['IPAddress']+':5000'
    headers={'authorization':'Bearer '+runtime.token(payload['credentials']['jwt'],'service_role'),'x-forwarded-host':e+'.storage.internal','content-type':'application/json'}
    def wait(url):
        for _ in range(60):
            try:
                if runtime.http(url+'/bucket',headers=headers)[0]==200:return
            except OSError:pass
            time.sleep(.5)
        raise RuntimeError('Storage readiness failed')
    def stop(names,owner):
        for name in names:
            state(name,owner);lab.docker('stop',name)
            if state(name,owner)['State']['Running']:raise RuntimeError('Stop failed')
    try:
        lab.docker('start',runtime.DB);lab.docker('start',source_storage)
        public=endpoint(source_storage,runtime.NETWORK);wait(public)
        raw=json.loads(source.sql("SELECT jsonb_agg(to_jsonb(t) ORDER BY id) FROM (SELECT id,tenant_id,kind,content,active,created_at FROM tenants_jwks WHERE tenant_id='"+e+"') t;",'storage_metadata').stdout)
        code="const fs=require('fs'),{decrypt}=require('/app/dist/internal/auth/crypto');process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(r=>({...r,content:JSON.parse(decrypt(r.content))}))));"
        decoded=json.loads(lab.docker('exec','-i',source_storage,'node','-e',code,data=json.dumps(raw)).stdout)
        check('source signing material still matches exported snapshot',decoded==payload['storage_jwks'])
        obj=json.loads(source.sql("SELECT row_to_json(t) FROM (SELECT bucket_id,name,version FROM storage.objects ORDER BY name LIMIT 1) t;",e).stdout)
        suffix=quote(obj['bucket_id'],safe='')+'/'+quote(obj['name'],safe='/')
        expected=base64.b64decode(next(f['data'] for f in payload['files'] if f['path']==obj['bucket_id']+'/'+obj['name']+'/'+obj['version']))
        status,body=runtime.http(public+'/object/sign/'+suffix,'POST',b'{"expiresIn":600}',headers)
        signed=json.loads(body);check('source issues valid URL',status==200 and isinstance(signed.get('signedURL'),str))
        url=signed['signedURL']
        if not url.startswith('/object/sign/'):raise RuntimeError('Unexpected URL')
        plain={'x-forwarded-host':e+'.storage.internal'}
        status,body=runtime.http(public+url,headers=plain);check('source URL returns expected original bytes',status==200 and body==expected)
        runtime.atomic(runtime.STATE/'recovery-source-url.json',{'path':url,'environment':e,'issued_after_export':True})
    finally:stop([source_storage,runtime.DB],runtime.OWNER)
    try:
        check('source fully stopped before target startup',not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip())
        lab.docker('start',d['database']);lab.docker('start',target_storage)
        public=endpoint(target_storage,d['network']);wait(public)
        status,body=runtime.http(public+url,headers=plain)
        check('unchanged source-issued path and signature work on independent target',status==200 and body==expected)
        base,token=url.split('token=',1)
        # Change a significant signature character, not unused base64 padding bits.
        parts=token.split('.')
        if len(parts)!=3:raise RuntimeError('Unexpected signing token')
        parts[2]=('A' if parts[2][0]!='A' else 'B')+parts[2][1:]
        status,_=runtime.http(public+base+'token='+'.'.join(parts),headers=plain)
        check('target rejects modified source signature',status in (400,401,403))
    finally:stop([target_storage,d['database']],'recovery-target')
    checks.append('source and target stopped after sequential verification')
    (lab.ROOT/'docs/evidence/independent-source-url-checks.json').write_text(json.dumps({'scope':'URL issued by source after export with signing material equal to exported snapshot, then consumed unchanged on independent target. Only origin/IP changed, tenant host preserved. Does not prove pre-export issuance, expiration handling or stable public route cutover.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' source-issued URL checks passed')


if __name__=='__main__':
    runtime.run_locked(main,'Source URL verification failed; sensitive output withheld')
