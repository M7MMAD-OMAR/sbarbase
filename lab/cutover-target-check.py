"""Start verified target, exercise managed placement, retain paused target afterward."""
import base64
import json
import re
import subprocess
import time
from pathlib import Path
import durable_runtime as runtime
import run as lab
from recovery_bundle import open_bundle


def ensure_maintenance(environment,routing):
    current=routing({'action':'read','runtimes':[environment]})[environment]
    if not current['maintenance']:
        return routing({'action':'pause','runtimes':[environment],'revision':current['revision']})['revision']
    return current['revision']


def main():
    record=runtime.STATE/'cutover-operation.json';op=json.loads(record.read_text())
    if op['phase']!='target-services-verified-routing-paused':raise RuntimeError('Unexpected cutover phase')
    d=json.loads((runtime.STATE/'recovery-target.json').read_text());e=d['environment'];db=d['database']
    if d['archive']!=op['new_export']['archive'] or d.get('storage_stage')!='verified':raise RuntimeError('Target does not match cutover export')
    fence=json.loads((runtime.STATE/('export-fence-'+e+'.json')).read_text())
    if fence['phase']!='exported-and-fenced' or fence['archive']!=d['archive']:raise RuntimeError('Source fence record mismatch')
    for owner in (runtime.OWNER,'recovery-target'):
        if lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+owner).stdout.strip():raise RuntimeError('Owned runtimes must be stopped')
    if int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))<6*1024*1024:raise RuntimeError('Insufficient memory')
    payload=open_bundle(json.loads(Path(d['archive']).read_text()),base64.b64decode(Path(d['key']).read_text(),validate=True))
    names=[db]+[d['prefix']+'-'+kind for kind in ('auth','rest','storage')]
    def inspect(name):
        state=json.loads(lab.docker('container','inspect',name).stdout)[0]
        if state['Config']['Labels'].get('io.sbarbase.owner')!='recovery-target' or d['network'] not in state['NetworkSettings']['Networks']:raise RuntimeError('Target ownership/placement mismatch')
        return state
    for name in names:inspect(name)
    def sql(query):return lab.docker('exec','-i',db,'psql','-X','-At','-v','ON_ERROR_STOP=1','-U','supabase_admin','-d',e,data=query).stdout
    def endpoint(kind,port):return 'http://'+inspect(d['prefix']+'-'+kind)['NetworkSettings']['Networks'][d['network']]['IPAddress']+':'+str(port)
    def wait(url,headers=None):runtime.wait_ready(url,headers,'Target readiness failed')
    try:
        op['phase']='target-starting';runtime.atomic(record,op)
        lab.docker('start',db)
        for _ in range(60):
            if lab.docker('exec',db,'pg_isready',check=False).returncode==0:break
            time.sleep(.5)
        for name in names[1:]:lab.docker('start',name)
        placement={'auth':endpoint('auth',9999),'rest':endpoint('rest',3000),'storage':{'url':endpoint('storage',5000),'tenantHost':e+'.storage.internal'}}
        wait(placement['auth']+'/health');wait(placement['rest']+'/');wait(placement['storage']['url']+'/bucket',{'authorization':'Bearer '+runtime.token(payload['credentials']['jwt'],'service_role'),'x-forwarded-host':e+'.storage.internal'})
        user=json.loads(sql("SELECT row_to_json(t) FROM (SELECT id,email FROM auth.users WHERE email LIKE 'durable-%@example.com' ORDER BY id LIMIT 1) t;"))
        match=re.fullmatch(r'durable-([a-f0-9-]{36})@example.com',user['email'])
        if not match:raise RuntimeError('Original fixture unavailable')
        op['phase']='target-publication-probe';op['target_writes_may_exist']=True;runtime.atomic(record,op)
        value={'environment':e,'database':db,'placement':placement,'expectedRevision':op['paused_revisions'][e],'email':user['email'],'password':'Local-'+match[1],'user':user['id'],'signed':payload['signed_url_fixture']}
        result=subprocess.run(['bun','lab/cutover-sdk-check.ts'],input=json.dumps(value),text=True,capture_output=True,timeout=120)
        if result.returncode:raise RuntimeError('Managed target verification failed')
        op['phase']='managed-target-verified-routing-paused';op['target_routing_revision']=json.loads((lab.ROOT/'docs/evidence/cutover-sdk-checks.json').read_text())['finalRevision'];runtime.atomic(record,op)
        print(result.stdout.strip())
    except BaseException:
        op['failed_phase']=op['phase'];op['phase']='needs-reconciliation';runtime.atomic(record,op);raise
    finally:
        failures=[]
        # Parent restores maintenance even if the SDK child timed out or died.
        try:
            def routing(value):
                result=subprocess.run(['bun','lab/routing-operator.ts'],input=json.dumps(value),text=True,capture_output=True,timeout=30)
                if result.returncode:raise RuntimeError('Routing reconciliation failed')
                return json.loads(result.stdout)
            op['target_routing_revision']=ensure_maintenance(e,routing);runtime.atomic(record,op)
        except Exception:failures.append('routing-maintenance')
        for name in list(reversed(names[1:]))+[db]:
            try:
                lab.docker('stop',name)
                if inspect(name)['State']['Running']:raise RuntimeError('Still running')
            except Exception:failures.append(name)
        if failures:
            op['phase']='cleanup-failed';runtime.atomic(record,op);raise RuntimeError('Target cleanup incomplete')


if __name__=='__main__':
    runtime.run_locked(main,'Managed cutover check failed; retained operation requires reconciliation')
