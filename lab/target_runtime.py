"""Repeatable retained target lifecycle. Staged local mode, source stays stopped."""
import argparse
import base64
import fcntl
import json
import re
import subprocess
import time
from pathlib import Path
import durable_runtime as runtime
import run as lab
from recovery_bundle import open_bundle


class TargetRuntime:
    def __init__(self,stop_only=False):
        self.record=runtime.STATE/'cutover-operation.json'
        self.operation=json.loads(self.record.read_text())
        self.target=json.loads((runtime.STATE/'recovery-target.json').read_text())
        d=self.target;self.environment=d['environment'];self.prefix=d['prefix']
        if not re.fullmatch(r'e_[a-f0-9]{24}',self.environment) or not re.fullmatch(r'sbarbase-restore-[a-f0-9]{12}',self.prefix):raise RuntimeError('Invalid target identity')
        if d['database']!=self.prefix+'-db' or d['network']!=self.prefix+'-net' or d['volume']!=self.prefix+'-pgdata':raise RuntimeError('Target placement mismatch')
        self.payload=None
        if not stop_only:
            if d['archive']!=self.operation['new_export']['archive'] or d['status']!='database-restored' or d.get('storage_stage')!='verified':raise RuntimeError('Unverified target')
            fence=json.loads((runtime.STATE/('export-fence-'+self.environment+'.json')).read_text())
            if fence['phase']!='exported-and-fenced' or fence['archive']!=d['archive']:raise RuntimeError('Source fence record mismatch')
            self.payload=open_bundle(json.loads(Path(d['archive']).read_text()),base64.b64decode(Path(d['key']).read_text(),validate=True))
        self.identities={}
        for kind in ('db','auth','rest','storage'):
            value=json.loads(lab.docker('container','inspect',self.prefix+'-'+kind).stdout)[0]
            if value['Config']['Labels'].get('io.sbarbase.owner')!='recovery-target' or d['network'] not in value['NetworkSettings']['Networks'] or (self.payload is not None and value['Image']!=self.payload['images'][kind]['id']):raise RuntimeError('Target container drift')
            if kind in ('db','storage'):
                volume=d['volume'] if kind=='db' else self.prefix+'-objects'
                path='/var/lib/postgresql/data' if kind=='db' else '/tmp/storage-data'
                if not any(m.get('Name')==volume and m['Destination']==path for m in value['Mounts']):raise RuntimeError('Target volume drift')
            self.identities[kind]=value['Id']

    def routing(self,action='read',revision=None,placement=None):
        value={'action':action,'runtimes':[self.environment]}
        if revision is not None:value['revision']=revision
        if placement is not None:value['placement']=placement
        result=subprocess.run(['bun','lab/routing-operator.ts'],input=json.dumps(value),text=True,capture_output=True,timeout=30)
        if result.returncode:raise RuntimeError('Target routing operation failed')
        response=json.loads(result.stdout)
        return response[self.environment] if action=='read' else response

    def phase(self,phase):
        self.operation['phase']=phase;runtime.atomic(self.record,self.operation)

    def pause(self):
        state=self.routing()
        return state['revision'] if state['maintenance'] else self.routing('pause',state['revision'])['revision']

    def stop(self):
        errors=[]
        try:self.operation['target_routing_revision']=self.pause()
        except Exception:errors.append('maintenance')
        for kind in ('storage','rest','auth','db'):
            try:
                identity=self.identities[kind];lab.docker('stop',identity)
                if json.loads(lab.docker('inspect',identity).stdout)[0]['State']['Running']:raise RuntimeError('Still running')
            except Exception:errors.append(kind)
        self.phase('target-stop-failed' if errors else 'target-stopped-routing-paused')
        if errors:raise RuntimeError('Target shutdown incomplete')

    def start(self,combined_admission=None):
        # Preserve the current bounded staged-test budget until combined source
        # and target resource admission is implemented.
        if combined_admission is not None:combined_admission.check_current()
        elif lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+runtime.OWNER).stdout.strip():raise RuntimeError('Staged target mode requires stopped source')
        available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))
        if available<6*1024*1024:raise RuntimeError('Insufficient memory headroom')
        revision=self.pause();self.phase('target-starting')
        try:
            lab.docker('start',self.identities['db'])
            ready=False
            for _ in range(60):
                if lab.docker('exec',self.identities['db'],'pg_isready',check=False).returncode==0:ready=True;break
                time.sleep(.5)
            if not ready:raise RuntimeError('Target database not ready')
            for kind in ('auth','rest','storage'):lab.docker('start',self.identities[kind])
            def endpoint(kind,port):
                value=json.loads(lab.docker('inspect',self.identities[kind]).stdout)[0]
                address=value['NetworkSettings']['Networks'][self.target['network']]['IPAddress']
                if not address:raise RuntimeError('Target endpoint unavailable')
                return f'http://{address}:{port}'
            placement={'auth':endpoint('auth',9999),'rest':endpoint('rest',3000),'storage':{'url':endpoint('storage',5000),'tenantHost':self.environment+'.storage.internal'}}
            probes=[(placement['auth']+'/health',None),(placement['rest']+'/',None),(placement['storage']['url']+'/bucket',{'authorization':'Bearer '+runtime.token(self.payload['credentials']['jwt'],'service_role'),'x-forwarded-host':self.environment+'.storage.internal'})]
            for url,headers in probes:
                ready=False
                for _ in range(60):
                    try:
                        if runtime.http(url,headers=headers)[0]==200:ready=True;break
                    except OSError:pass
                    time.sleep(.5)
                if not ready:raise RuntimeError('Target service not ready')
            revision=self.routing('stage',revision,placement)['revision']
            self.operation['target_writes_may_exist']=True;self.phase('target-ready-before-resume')
            revision=self.routing('resume',revision)['revision']
            self.operation['target_routing_revision']=revision;self.phase('target-running')
            return {'running':True,'revision':revision}
        except BaseException:
            self.stop();raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=('up','stop'));args=parser.parse_args()
    try:
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            target=TargetRuntime(stop_only=args.command=='stop')
            if args.command=='up':print(json.dumps(target.start()))
            else:target.stop();print('Target stopped; routing paused')
    except Exception:raise SystemExit('Target lifecycle refused or incomplete; retained state requires reconciliation') from None
