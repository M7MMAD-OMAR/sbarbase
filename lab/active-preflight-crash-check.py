"""Crash the real supervisor while its native provisioner is paused at preflight."""
import fcntl
from contextlib import ExitStack
import json
import os
import signal
import tempfile
import provisioning_inspection as inspection
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
    checks=[];runner=None;native_fd=None;owned=False
    temporary=tempfile.TemporaryDirectory(prefix="sbarbase-preflight-crash-")
    hook=Path(temporary.name);marker=hook/"ready.json"
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
    def capacity_refuses():
        values=json.loads((lab.ROOT/'.secrets/upstream/runtime.json').read_text())
        return len(values['environments'])==4 and before['runtime'] not in values['environments']
    with (STATE/'supervisor.lock').open('r') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with inspection.existing_locks(STATE):
            check('installation idle before rehearsal',all(not item['running'] for item in inspection.owned_inventory(inspection.docker)))
    before=job()
    check('existing capacity failure cannot allocate at current ceiling',before['failure']=='capacity_exceeded' and capacity_refuses())
    with sqlite3.connect(STATE/'control.sqlite') as db:
        used=db.execute("SELECT count(*) FROM provision_recovery_decisions WHERE environment=? AND decision='retry'",(environment,)).fetchone()[0]
    check('existing fixture has bounded recovery budget',used<2)
    # Test-only fault injection, absent from normal startup and production code.
    # CPython profiling pauses after the real fsynced stage writer returns.
    source="""import json,os,signal,sys
from pathlib import Path
def pause(frame,event,arg):
    if (event=='return' and frame.f_code.co_name=='native_stage'
            and frame.f_code.co_filename.endswith('/lab/effect_receipt.py')
            and frame.f_locals.get('stage')=='preflight'
            and frame.f_locals.get('runtime')==RUNTIME):
        sys.setprofile(None)
        path=Path(MARKER)
        pending=path.with_suffix('.pending')
        pending.write_text(json.dumps({'pid':os.getpid()}))
        pending.replace(path)
        os.kill(os.getpid(),signal.SIGSTOP)
sys.setprofile(pause)
""".replace('RUNTIME',repr(before['runtime'])).replace('MARKER',repr(str(marker)))
    (hook/'sitecustomize.py').write_text(source)
    check('retained failed capacity fixture reused',before['state']=='failed' and not (STATE/'worker-effect.json').exists())
    try:
        runner=subprocess.Popen(['/usr/bin/python3','lab/dev.py'],cwd=lab.ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,env={**os.environ,'PYTHONPATH':str(hook)})
        first=wait(ready);owned=True
        containers=inventory();allocation=(lab.ROOT/'.secrets/upstream/runtime.json').stat().st_mtime_ns
        setup="import {Catalog} from './src/control/catalog';const c=new Catalog('.lab/upstream/control.sqlite');try{const p=await Bun.file('.lab/upstream/admission-probe.json').json();c.retryProvision('durable-probe-owner',p.environment);}finally{c.close();}"
        check('capacity still refuses before explicit retry',capacity_refuses())
        result=subprocess.run(['bun','-e',setup],cwd=lab.ROOT,capture_output=True,timeout=10)
        check('existing failed operation explicitly retried',result.returncode==0)
        wait(lambda:marker.exists(),20)
        native_pid=json.loads(marker.read_text())['pid']
        native_fd=os.pidfd_open(native_pid)
        wait(lambda:'State:\tT (stopped)' in Path('/proc',str(native_pid),'status').read_text(),5)
        pending=json.loads((STATE/'worker-effect.json').read_text())
        check('real native provisioner paused at exact claimed preflight',pending['job']['environment']==environment and pending['job']['attempt']==before['attempt']+1)
        runner.kill();check('active supervisor terminated by SIGKILL',runner.wait(timeout=10)==-9)
        deadline=time.monotonic()+15
        report=None
        while time.monotonic()<deadline:
            report=inspection.inspect_state(STATE)
            if report.get('local_ownership')=='exclusive_snapshot':break
            time.sleep(.1)
        check('guardian cleanup releases fresh worker effect and operation ownership',report.get('local_ownership')=='exclusive_snapshot')
        check('pending preflight remains and inspector requires fresh recovery',report.get('next_action')=='evaluate_bounded_preflight_recovery_under_fresh_lease' and not report['safe_to_replay'])
        check('crashed attempt has no completion witness',report.get('native_witness')=='missing')
        check('crash does not allocate containers or persist credentials',inventory()==containers and (lab.ROOT/'.secrets/upstream/runtime.json').stat().st_mtime_ns==allocation)
        check('capacity still refuses before recovery restart',capacity_refuses())
        runner=subprocess.Popen(['/usr/bin/python3','lab/dev.py'],cwd=lab.ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        first=wait(ready)
        final=wait(lambda:(j if (j:=job())['state']=='failed' and j['attempt']==before['attempt']+2 else None),20)
        check('classified admission refusal preserved',final['failure']=='capacity_exceeded')
        check('same runtime identity and exactly one automatic retry',final['runtime']==before['runtime'] and final['attempt']==before['attempt']+2)
        with sqlite3.connect(STATE/'control.sqlite') as db:
            row=db.execute('SELECT runtime,exit_code,claim FROM provision_effect_results WHERE environment=? AND attempt=?',(environment,final['attempt'])).fetchone()
        with sqlite3.connect(STATE/'control.sqlite') as db:
            decision=db.execute('SELECT runtime,claim,receipt_token,decision FROM provision_recovery_decisions WHERE environment=? AND attempt=?',(environment,before['attempt']+1)).fetchone()
        check('crashed claim has exact durable requeue decision',decision==(before['runtime'],pending['job']['claim'],pending['token'],'retry'))
        check('exact attempt outcome committed in catalog' ,row is not None and row[:2]==(final['runtime'],75))
        witnesses=[json.loads(path.read_text()) for path in (STATE/'effect-outcomes').glob('*.json')]
        matched=[w for w in witnesses if w.get('job',{}).get('environment')==environment and w.get('job',{}).get('attempt')==final['attempt']]
        check('native refusal witness binds the exact committed claim',len(matched)==1 and matched[0].get('phase')=='native-completed' and matched[0].get('native')=='durable-provision-v1' and matched[0].get('exitCode')==75 and matched[0]['job']['claim']==row[2])
        stage=json.loads((STATE/'effect-stages'/(matched[0]['token']+'.json')).read_text())
        check('native refusal retains exact preflight boundary',stage.get('stageProtocol')==1 and stage.get('stage')=='preflight' and stage.get('stageIndex')==0 and stage.get('job')==matched[0]['job'])
        wait(lambda:not (STATE/'worker-effect.json').exists(),5)
        check('receipt consumed only after committed outcome',not (STATE/'worker-effect.json').exists())
        check('no runtime container allocated',inventory()==containers)
        check('private allocation file unchanged',(lab.ROOT/'.secrets/upstream/runtime.json').stat().st_mtime_ns==allocation)
        check('worker survived classified refusal',ready()['workerPid']==first['workerPid'])
        result=subprocess.run(['bun','lab/combined-gateway-check.ts'],cwd=lab.ROOT,capture_output=True,timeout=45)
        check('combined console and all four environments respond',result.returncode==0)
    finally:
        if native_fd is not None:
            try:signal.pidfd_send_signal(native_fd,signal.SIGKILL)
            except ProcessLookupError:pass
            os.close(native_fd)
        if runner is not None and runner.poll() is None:
            runner.terminate()
            try:runner.wait(timeout=100)
            except subprocess.TimeoutExpired:runner.kill();runner.wait(timeout=10)
        if owned:
            # Never stop a successor supervisor or independently owned worker.
            with ExitStack() as stack:
                for name in ('supervisor.lock','worker.lock','effect.lock'):
                    lock=stack.enter_context((STATE/name).open('r'))
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                result=subprocess.run(['/usr/bin/python3','lab/installation_runtime.py','stop'],cwd=lab.ROOT,capture_output=True,timeout=90)
                if result.returncode:raise RuntimeError('Owned runtime shutdown failed')
    temporary.cleanup()
    check('all owned runtimes stopped',all(not lab.docker('ps','-q','--filter','label=io.sbarbase.owner='+owner).stdout.strip() for owner in ('durable-upstream','recovery-target')))
    (lab.ROOT/'docs/evidence/active-preflight-crash-checks.json').write_text(json.dumps({'scope':'Real supervisor SIGKILL with native provisioner paused by a temporary CPython profile hook after preflight persistence. Guardian cleanup, fresh startup requeue and subsequent capacity refusal verified. No external provisioning effects, active writes, later stages or power loss tested. No new environment allocated.','count':len(checks),'checks':checks},indent=2)+'\n')
    print(str(len(checks))+' active preflight crash checks passed')


if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Active preflight crash check failed; inspect owned runtime state') from None
