"""Exercise receipt settlement with the existing refused-capacity fixture."""
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request
import run as lab

STATE=lab.ROOT/'.lab/upstream'


def main():
    fixture=STATE/'admission-probe.json'
    if not fixture.exists():raise RuntimeError('Existing admission fixture required; no allocation permitted')
    environment=json.loads(fixture.read_text())['environment']
    checks=[];runner=None
    def check(name,ok):
        if not ok:raise RuntimeError(name)
        checks.append(name)
    def job():
        with sqlite3.connect(STATE/'control.sqlite') as db:
            db.row_factory=sqlite3.Row
            return dict(db.execute('SELECT * FROM provision_jobs WHERE environment=?',(environment,)).fetchone())
    def wait(fn,seconds=70):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if runner.poll() is not None:raise RuntimeError('Supervisor exited')
            value=fn()
            if value:return value
            time.sleep(.1)
        raise RuntimeError('Receipt check deadline')
    def ready():
        try:
            owner=json.loads((STATE/'supervisor.json').read_text());server=json.loads((STATE/'server.json').read_text())
            if owner['pid']!=runner.pid or owner['serverPid']!=server['pid']:return None
            with urllib.request.urlopen(server['url'],timeout=1) as response:
                return owner if response.status==200 else None
        except (OSError,ValueError,KeyError):return None
    def inventory():
        return {owner:lab.docker('ps','-aq','--filter','label=io.sbarbase.owner='+owner).stdout.strip() for owner in ('durable-upstream','recovery-target')}
    before=job()
    check('retained failed capacity fixture reused',before['state']=='failed' and before['failure']=='capacity_exceeded')
    try:
        runner=subprocess.Popen(['/usr/bin/python3','lab/dev.py'],cwd=lab.ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        first=wait(ready)
        containers=inventory();allocation=(lab.ROOT/'.secrets/upstream/runtime.json').stat().st_mtime_ns
        setup="import {Catalog} from './src/control/catalog';const c=new Catalog('.lab/upstream/control.sqlite');try{const p=await Bun.file('.lab/upstream/admission-probe.json').json();c.retryProvision('durable-probe-owner',p.environment);}finally{c.close();}"
        result=subprocess.run(['bun','-e',setup],cwd=lab.ROOT,capture_output=True,timeout=10)
        check('existing failed operation explicitly retried',result.returncode==0)
        final=wait(lambda:(j if (j:=job())['state']=='failed' and j['attempt']>before['attempt'] else None),20)
        check('classified admission refusal preserved',final['failure']=='capacity_exceeded')
        check('same runtime identity and one new attempt',final['runtime']==before['runtime'] and final['attempt']==before['attempt']+1)
        with sqlite3.connect(STATE/'control.sqlite') as db:
            row=db.execute('SELECT runtime,exit_code FROM provision_effect_results WHERE environment=? AND attempt=?',(environment,final['attempt'])).fetchone()
        check('exact attempt outcome committed in catalog',row==(final['runtime'],75))
        wait(lambda:not (STATE/'worker-effect.json').exists(),5)
        check('receipt consumed only after committed outcome',not (STATE/'worker-effect.json').exists())
        check('no runtime container allocated',inventory()==containers)
        check('private allocation file unchanged',(lab.ROOT/'.secrets/upstream/runtime.json').stat().st_mtime_ns==allocation)
        check('worker survived classified refusal',ready()['workerPid']==first['workerPid'])
        result=subprocess.run(['bun','lab/combined-gateway-check.ts'],cwd=lab.ROOT,capture_output=True,timeout=45)
        check('combined console and all four environments respond',result.returncode==0)
    finally:
        if runner is not None and runner.poll() is None:
            runner.terminate()
            try:runner.wait(timeout=100)
            except subprocess.TimeoutExpired:runner.kill();runner.wait(timeout=10)
        result=subprocess.run(['/usr/bin/python3','lab/installation_runtime.py','stop'],cwd=lab.ROOT,capture_output=True,timeout=90)
        if result.returncode:raise RuntimeError('Owned runtime shutdown failed')
    check('all owned runtimes stopped',all(not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+owner).stdout.strip() for owner in ('durable-upstream','recovery-target')))
    (lab.ROOT/'docs/evidence/worker-receipt-checks.json').write_text(json.dumps({'scope':'Existing failed capacity fixture retried through real combined supervisor and receipt settlement; no new environment allocated. Does not test uncertain Docker daemon effects.','count':len(checks),'checks':checks},indent=2)+'\n')
    print(str(len(checks))+' worker receipt integration checks passed')


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Worker receipt check failed; inspect owned runtime state') from None
