"""Interrupt one owned provisioning worker after its private state is persisted.

Consumes the fourth retained lab environment. Uses the existing trusted local
fixture actor, not public authentication. Refuses to repeat a completed fixture.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT/'.lab/upstream'
checks = []

def check(name, condition):
    if not condition:
        raise RuntimeError(name)
    checks.append(name)

def wait_for(fn, timeout):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(.02)
    raise RuntimeError('Timed out waiting for recovery condition')

def descriptor():
    path = STATE/'supervisor.json'
    if path.exists():
        value = json.loads(path.read_text())
        if value['pid'] == runner.pid:
            return value
    if runner.poll() is not None:
        raise RuntimeError('Runner exited unexpectedly')
    return None

def job():
    with sqlite3.connect(STATE/'control.sqlite') as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM provision_jobs WHERE environment=?', (environment,)).fetchone()
        return dict(row)

fixture = STATE/'supervisor-recovery.json'
if fixture.exists():
    raise SystemExit('Recovery fixture already exists; inspect and resume it explicitly, do not allocate another environment.')
with (STATE/'supervisor-recovery.log').open('w') as log:
    runner = subprocess.Popen(['/usr/bin/python3', 'lab/dev.py'], cwd=ROOT, stdout=log, stderr=log)
    try:
        first = wait_for(descriptor, 180)
        private = ROOT/'.secrets/upstream/runtime.json'
        previous = private.stat().st_mtime_ns
        setup = '''import {Catalog} from './src/control/catalog';
const c=new Catalog('.lab/upstream/control.sqlite');
try {
 const p=await Bun.file('.lab/upstream/probe.json').json();
 const project=c.createProject('durable-probe-owner',p.organization,'Supervisor recovery');
 const environment=c.createEnvironment('durable-probe-owner',project,'interrupted');
 await Bun.write('.lab/upstream/supervisor-recovery.json',JSON.stringify({project,environment}));
} finally {c.close();}'''
        subprocess.run(['bun', '-e', setup], cwd=ROOT, check=True, stdout=log, stderr=log, timeout=10)
        environment = json.loads(fixture.read_text())['environment']
        active = wait_for(lambda: (j if (j:=job())['state']=='running' and private.stat().st_mtime_ns!=previous else None), 15)
        pid = first['workerPid']
        check('interrupted running claim after persistent runtime state changed', active['attempt']==1)
        check('target worker is direct owned child', f'PPid:\t{runner.pid}\n' in Path(f'/proc/{pid}/status').read_text())
        os.kill(pid, signal.SIGKILL)
        second = wait_for(lambda: (d if (d:=descriptor()) and d['workerPid']!=pid else None), 15)
        check('worker restarted while API process retained', second['serverPid']==first['serverPid'])
        final = wait_for(lambda: (j if (j:=job())['state'] in ('succeeded','failed','cancelled') else None), 120)
        check('interrupted operation succeeded', final['state']=='succeeded')
        check('operation claimed again with same runtime identity', final['attempt']>=2 and final['runtime']==active['runtime'])
        with sqlite3.connect(STATE/'control.sqlite') as db:
            check('one operation retained for runtime', db.execute('SELECT count(*) FROM provision_jobs WHERE runtime=?',(final['runtime'],)).fetchone()[0]==1)
        endpoints=json.loads((STATE/'endpoints.json').read_text())
        check('recovered environment has published trusted endpoints', final['runtime'] in endpoints)
        runner.terminate()
        check('runner stops after recovery', runner.wait(timeout=60)==0)
    finally:
        if runner.poll() is None:
            runner.terminate()
            runner.wait(timeout=60)
result={'scope':'One real provisioning interruption after private state persistence; trusted fixture actor, four retained environments; not exhaustive crash-point or disaster recovery coverage','count':len(checks),'checks':checks}
(ROOT/'docs/evidence/supervisor-recovery-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
