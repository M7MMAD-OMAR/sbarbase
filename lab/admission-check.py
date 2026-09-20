"""Verify the full local lab refuses more runtime allocation without disruption."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT/'.lab/upstream'
checks = []

def check(name, condition):
    if not condition:
        raise RuntimeError(name)
    checks.append(name)

def wait_for(fn, timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=fn()
        if result: return result
        if runner.poll() is not None: raise RuntimeError('Runner exited unexpectedly')
        time.sleep(.05)
    raise RuntimeError('Timed out waiting for admission check')

def descriptor():
    path=STATE/'supervisor.json'
    if path.exists():
        value=json.loads(path.read_text())
        if value['pid']==runner.pid: return value

def docker(*args, data=None):
    return subprocess.run(['docker',*args], input=data, text=True, capture_output=True, check=True, timeout=15).stdout.strip()

def databases():
    return docker('exec','-i','sbarbase-durable-db','psql','-X','-U','supabase_admin','-d','postgres','-At',data="SELECT oid,datname FROM pg_database ORDER BY oid;")

def job():
    with sqlite3.connect(STATE/'control.sqlite') as db:
        db.row_factory=sqlite3.Row
        return dict(db.execute('SELECT * FROM provision_jobs WHERE environment=?',(environment,)).fetchone())

def health(endpoints):
    for value in endpoints.values():
        with urllib.request.urlopen(value['auth']+'/health',timeout=5) as response:
            if response.status!=200: return False
        with urllib.request.urlopen(value['rest']+'/',timeout=5) as response:
            if response.status!=200: return False
    return True

with (STATE/'admission-check.log').open('w') as log:
    runner=subprocess.Popen(['/usr/bin/python3','lab/dev.py'],cwd=ROOT,stdout=log,stderr=log)
    try:
        first=wait_for(descriptor,180)
        endpoints=json.loads((STATE/'endpoints.json').read_text())
        check('four environment fixture present',len(endpoints)==4)
        check('all existing Auth and REST endpoints respond before refusal',health(endpoints))
        before_databases=databases()
        before_containers=docker('ps','-aq','--filter','label=io.sbarbase.owner=durable-upstream')
        before_private=(ROOT/'.secrets/upstream/runtime.json').stat().st_mtime_ns
        setup='''import {Catalog} from './src/control/catalog';
const c=new Catalog('.lab/upstream/control.sqlite');
try {
 const path='.lab/upstream/admission-probe.json';
 if(await Bun.file(path).exists()) {
  const p=await Bun.file(path).json();
  const job=c.getProvision('durable-probe-owner',p.environment);
  if(job.state!=='failed') throw new Error('Retained admission fixture needs inspection');
  c.retryProvision('durable-probe-owner',p.environment);
 }else{
  const p=await Bun.file('.lab/upstream/probe.json').json();
  const environment=c.createEnvironment('durable-probe-owner',p.project,'capacity-refusal');
  await Bun.write(path,JSON.stringify({environment}));
 }
}finally{c.close();}'''
        subprocess.run(['bun','-e',setup],cwd=ROOT,stdout=log,stderr=log,check=True,timeout=10)
        environment=json.loads((STATE/'admission-probe.json').read_text())['environment']
        result=wait_for(lambda: (j if (j:=job())['state'] in ('succeeded','failed','cancelled') else None))
        check('excess environment job fails',result['state']=='failed')
        check('safe capacity reason retained',result['failure']=='capacity_exceeded')
        check('private runtime allocation file unchanged',(ROOT/'.secrets/upstream/runtime.json').stat().st_mtime_ns==before_private)
        check('database identities unchanged',databases()==before_databases)
        check('no extra owned container created',docker('ps','-aq','--filter','label=io.sbarbase.owner=durable-upstream')==before_containers)
        check('trusted endpoint registry unchanged',json.loads((STATE/'endpoints.json').read_text())==endpoints)
        current=descriptor()
        check('same worker alive after failed job',current['workerPid']==first['workerPid'] and f'PPid:\t{runner.pid}\n' in Path(f"/proc/{current['workerPid']}/status").read_text())
        check('all existing Auth and REST endpoints respond after refusal',health(endpoints))
        server=json.loads((STATE/'server.json').read_text())
        with urllib.request.urlopen(server['url'],timeout=5) as response:
            check('console responds after refused job',response.status==200)
        runner.terminate()
        check('runner shuts down cleanly',runner.wait(timeout=60)==0)
    finally:
        if runner.poll() is None:
            runner.terminate()
            runner.wait(timeout=60)
result={'scope':'Four-environment local guard, trusted catalog fixture actor; refused fifth runtime leaves failed metadata for explicit retry. Endpoint liveness and resource identities checked, not workload latency or full data-content equivalence.','count':len(checks),'checks':checks}
(ROOT/'docs/evidence/admission-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
