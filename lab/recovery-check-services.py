"""Run original Auth/REST against retained independent recovery database."""
import base64
import json
import re
import secrets
import time
from pathlib import Path
import durable_runtime as runtime
import resource_policy
import run as lab
from recovery_bundle import open_bundle

OWNER='recovery-target'


def inspect(name):
    result=lab.docker('container','inspect',name,check=False)
    if result.returncode:return None
    state=json.loads(result.stdout)[0]
    if state['Config']['Labels'].get('io.sbarbase.owner')!=OWNER:raise RuntimeError('Ownership collision')
    return state


def main():
    d=json.loads((runtime.STATE/'recovery-target.json').read_text());db=d['database'];e=d['environment']
    if d['status']!='database-restored':raise RuntimeError('Database verification required')
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip():raise RuntimeError('Source must remain stopped')
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+OWNER).stdout.strip():raise RuntimeError('Target already running')
    if int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))<6*1024*1024:raise RuntimeError('Insufficient memory')
    payload=open_bundle(json.loads(Path(d['archive']).read_text()),base64.b64decode(Path(d['key']).read_text(),validate=True))
    pins=json.loads((lab.ROOT/'lab/images.lock.json').read_text())
    names=[d['prefix']+'-'+kind for kind in ('auth','rest')]
    for name in names:
        if inspect(name):raise RuntimeError('Service already exists; explicit reconciliation required')
    state=inspect(db)
    if not state or d['network'] not in state['NetworkSettings']['Networks']:raise RuntimeError('Target placement mismatch')
    checks=[]
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    def query(sql):
        return lab.docker('exec','-i',db,'psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',e,data=sql).stdout.strip()
    def request(url,method='GET',body=None,token=None):
        headers={'content-type':'application/json'}
        if token:headers['authorization']='Bearer '+token
        return runtime.http(url,method,json.dumps(body).encode() if body is not None else None,headers)
    endpoints={}
    try:
        lab.docker('start',db)
        for _ in range(60):
            if lab.docker('exec',db,'pg_isready',check=False).returncode==0:break
            time.sleep(.5)
        users=json.loads(query("SELECT coalesce(jsonb_agg(jsonb_build_object('id',id,'email',email)),'[]') FROM auth.users WHERE email LIKE 'durable-%@example.com';"))
        check('restored fixture identity exists',bool(users))
        for kind,builder,port,suffix in [('auth',lab.auth_configuration,9999,'/health'),('rest',lab.rest_configuration,3000,'/')]:
            check(kind+' pinned image matches export',pins[kind]['id']==payload['images'][kind]['id'])
            name=d['prefix']+'-'+kind;env=runtime.PRIVATE/(name+'.env')
            lab.secure_file(env,''.join(k+'='+v+'\n' for k,v in builder(e,payload['credentials'],db).items()))
            lab.docker('run','-d','--pull','never','--name',name,'--label','io.sbarbase.owner='+OWNER,*resource_policy.labels('maintenance'),'--network',d['network'],'--memory','256m','--memory-swap','256m','--cpus','0.25','--pids-limit','128','--log-opt','max-size=5m','--log-opt','max-file=2','--env-file',str(env),pins[kind]['id'])
            address=inspect(name)['NetworkSettings']['Networks'][d['network']]['IPAddress'];url=f'http://{address}:{port}'
            ready=False
            for _ in range(60):
                try:
                    if request(url+suffix)[0]==200:ready=True;break
                except OSError:pass
                time.sleep(.5)
            check(kind+' original service starts on independent cluster',ready);endpoints[kind]=url
        for user in users:
            match=re.fullmatch(r'durable-([a-f0-9-]{36})@example.com',user['email'])
            if not match:raise RuntimeError('Unknown fixture identity')
            status,body=request(endpoints['auth']+'/token?grant_type=password','POST',{'email':user['email'],'password':'Local-'+match[1]})
            login=json.loads(body)
            check('original password retains restored user identity',status==200 and login['user']['id']==user['id'])
            status,body=request(endpoints['auth']+'/user',token=login['access_token'])
            check('restored Auth validates issued session',status==200 and json.loads(body)['id']==user['id'])
            status,body=request(endpoints['rest']+'/durable_items?select=*',token=login['access_token'])
            rows=json.loads(body)
            check('restored REST returns only original user rows',status==200 and bool(rows) and all(r['owner_id']==user['id'] and r['value']==e for r in rows))
        status,_=request(endpoints['rest']+'/durable_items',token=runtime.token(secrets.token_hex(32),'service_role'))
        check('REST rejects token signed by unrelated environment secret',status==401)
        check('source remains stopped after service verification',not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip())
    finally:
        for name in list(reversed(names))+[db]:
            if inspect(name):lab.docker('stop',name)
        if any(inspect(name) and inspect(name)['State']['Running'] for name in names+[db]):raise RuntimeError('Target cleanup incomplete')
    checks.append('all target services stopped with data retained')
    (lab.ROOT/'docs/evidence/independent-service-checks.json').write_text(json.dumps({'scope':'Original Auth and REST on independent restored database. Existing fixture passwords, user IDs, sessions, RLS rows and foreign signing-secret rejection. Storage not yet restored.','checks':checks,'count':len(checks)},indent=2)+'\n')
    print(str(len(checks))+' independent Auth/REST checks passed')


if __name__=='__main__':
    runtime.run_locked(main,'Independent service verification failed; sensitive output withheld')
