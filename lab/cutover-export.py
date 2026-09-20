"""Persist local maintenance then export a fenced source. No routing publication."""
import fcntl
import importlib.util
import json
import sqlite3
import subprocess
import time
import durable_runtime as runtime
import run as lab


def main():
    record=runtime.STATE/'cutover-operation.json'
    if record.exists():raise RuntimeError('Cutover operation exists; explicit continuation required')
    if lab.docker('ps','-q','--filter','label=io.sbarbase.owner=recovery-target').stdout.strip():raise RuntimeError('Stop recovery target first')
    target=runtime.Runtime()
    with sqlite3.connect('file:'+str(runtime.STATE/'control.sqlite')+'?mode=ro',uri=True) as catalog:
        runtimes=[row[0] for row in catalog.execute("SELECT runtime FROM provision_jobs WHERE state='succeeded' ORDER BY runtime") if row[0] in target.values['environments']]
    if not runtimes:raise RuntimeError('No ready source environments')
    def operator(value):
        result=subprocess.run(['bun','lab/routing-operator.ts'],input=json.dumps(value),text=True,capture_output=True,timeout=30)
        if result.returncode:raise RuntimeError('Routing operator failed')
        return json.loads(result.stdout)
    previous=operator({'action':'read','runtimes':runtimes})
    if any(row['maintenance'] or row['placement'] is not None for row in previous.values()):raise RuntimeError('Existing routing operation requires reconciliation')
    state={'phase':'pausing','started_at':time.time(),'previous_routing':previous,'paused_revisions':{},'old_export':json.loads((runtime.STATE/'recovery-latest.json').read_text())}
    runtime.atomic(record,state)
    try:
        # Shared Storage will stop, so every ready source environment is paused.
        for e in runtimes:
            value=operator({'action':'pause','runtimes':[e],'revision':previous[e]['revision']})
            state['paused_revisions'][e]=value['revision'];runtime.atomic(record,state)
        state['phase']='source-starting';runtime.atomic(record,state)
        target.start()
        state['phase']='exporting';runtime.atomic(record,state)
        spec=importlib.util.spec_from_file_location('cutover_exporter',lab.ROOT/'lab/recovery-export.py')
        exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
        exporter.main(cutover=True)
        state['new_export']=json.loads((runtime.STATE/'recovery-latest.json').read_text())
        state['phase']='source-exported-and-stopped';runtime.atomic(record,state)
    except BaseException:
        state['failed_phase']=state['phase'];state['phase']='needs-reconciliation';runtime.atomic(record,state)
        raise
    finally:
        runtime.stop()
    (lab.ROOT/'docs/evidence/cutover-export-checks.json').write_text(json.dumps({'scope':'Durable maintenance for all ready source runtimes, original source service startup, pre-export signed URL inside encrypted bundle, service-login fence and full database fence after export. Source stopped, target not published.','paused_environments':len(runtimes),'phase':state['phase'],'previous_target_retained':True},indent=2)+'\n')
    print('Cutover export complete; source stopped and routing remains paused')


if __name__=='__main__':
    try:
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);main()
    except Exception:raise SystemExit('Cutover export incomplete; retained operation requires reconciliation') from None
