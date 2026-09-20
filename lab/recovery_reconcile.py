"""Reconcile retained recovery runtime after interruption, never replay data writes."""
import fcntl
import json
import re
from pathlib import Path
import subprocess
import durable_runtime as runtime

OWNER='recovery-target'


def docker(*args,check=True):
    try:
        result=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Recovery reconciliation command timed out') from None
    if check and result.returncode:raise RuntimeError('Recovery reconciliation command failed')
    return result


def reconcile(record,write=runtime.atomic,command=docker):
    d=json.loads(Path(record).read_text());prefix=d.get('prefix','')
    if not re.fullmatch(r'sbarbase-restore-[a-f0-9]{12}',prefix):raise RuntimeError('Invalid recovery identity')
    if d.get('database')!=prefix+'-db' or d.get('network')!=prefix+'-net' or d.get('volume')!=prefix+'-pgdata':raise RuntimeError('Recovery placement mismatch')
    allowed={prefix+'-'+suffix for suffix in ('db','auth','rest','storage','headroom')}
    # List then inspect exact IDs. Failed daemon inventory is never absence.
    inventory=command('ps','-a','--format','{{.ID}} {{.Names}}').stdout.splitlines()
    resources=[]
    for line in inventory:
        identity,name=line.split(maxsplit=1)
        if name.startswith(prefix+'-'):
            if name not in allowed:raise RuntimeError('Unexpected recovery container')
            state=json.loads(command('container','inspect',identity).stdout)[0]
            if state['Config']['Labels'].get('io.sbarbase.owner')!=OWNER:raise RuntimeError('Recovery ownership mismatch')
            if state.get('Name','').lstrip('/')!=name:raise RuntimeError('Recovery identity mismatch')
            resources.append((identity,name))
    if not any(name==d['database'] for _,name in resources):raise RuntimeError('Recovery database container unavailable')
    original=d.get('status')
    d['reconcile_previous_status']=original;d['reconcile_state']='stopping';write(record,d)
    failures=[]
    # Service shutdown precedes database shutdown; every owned resource is tried.
    for identity,name in sorted(resources,key=lambda item:item[1]==d['database']):
        try:
            command('stop',identity)
            if json.loads(command('container','inspect',identity).stdout)[0]['State']['Running']:raise RuntimeError('Recovery resource still running')
        except Exception:failures.append(name.rsplit('-',1)[1])
    d['reconcile_state']='stop-failed' if failures else 'stopped'
    d['reconcile_failures']=failures
    # Stopping a partially restored database cannot certify its contents.
    if original!='database-restored':d['status']='interrupted'
    write(record,d)
    if failures:raise RuntimeError('Recovery shutdown incomplete')
    return {'state':'stopped','containers':len(resources),'database_verified':original=='database-restored','data_retained':True}


if __name__=='__main__':
    try:
        with (runtime.STATE/'operation.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            print(json.dumps(reconcile(runtime.STATE/'recovery-target.json')))
    except Exception:
        raise SystemExit('Recovery reconciliation refused or incomplete; inspect retained state') from None
