"""Retire an interrupted or failed recovery target so a fresh restore can start.

Usage: /usr/bin/python3 lab/retire_recovery_target.py [--reason TEXT]

Guards, all of which must hold:
- the active descriptor exists and its status is 'interrupted', 'failed' or
  'cleanup-failed'; a verified, restored or running target is never retired;
- every container of that prefix is stopped (run lab/recovery_reconcile.py first
  if a run was interrupted while containers were up);
- no per-target HBA operation is pending;
- the prefix and placement are well formed and owned by 'recovery-target'.

The descriptor is copied durably into recovery-target-history/ and the history
path is recorded in the cutover journal, then the active descriptor is removed.
Containers and volumes are never deleted: a retired target keeps its data until
an operator removes it by exact identity.
"""
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import durable_runtime as runtime
import hba_runtime

LAB=Path(__file__).resolve().parent
ROOT=LAB.parent
STATE=ROOT/'.lab'/'upstream'
PREFIX=re.compile(r'sbarbase-restore-[a-f0-9]{12}')
RETIRABLE=('interrupted','failed','cleanup-failed')


def docker(*args,check=True):
    result=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=60)
    if check and result.returncode:raise RuntimeError('Docker command failed during retirement')
    return result


def running_containers(prefix,command=docker):
    names=command('ps','--format','{{.Names}}',check=False).stdout.split()
    return sorted(name for name in names if name.startswith(prefix+'-'))


def retire(record=STATE/'recovery-target.json',history=STATE/'recovery-target-history',
           journal=STATE/'cutover-operation.json',*,reason='',command=docker,write=runtime.atomic,now=None):
    if not record.exists():raise RuntimeError('No active recovery target descriptor to retire')
    descriptor=json.loads(record.read_text())
    prefix=descriptor.get('prefix','')
    if not PREFIX.fullmatch(prefix):raise RuntimeError('Invalid recovery identity')
    if descriptor.get('database')!=prefix+'-db' or descriptor.get('network')!=prefix+'-net' or descriptor.get('volume')!=prefix+'-pgdata':
        raise RuntimeError('Recovery placement mismatch')
    status=descriptor.get('status')
    if status not in RETIRABLE:
        raise RuntimeError('Refusing to retire a target whose status is '+repr(status)+'; only '+', '.join(RETIRABLE)+' may be retired')
    active=running_containers(prefix,command)
    if active:raise RuntimeError('Refusing to retire while containers are running: '+' '.join(active))
    pending=STATE/'targets'/prefix/'hba-operation.json'
    if pending.exists():raise RuntimeError('Refusing to retire with a pending per-target HBA operation')
    stamp=(now or datetime.datetime.now().astimezone()).strftime('%Y%m%dT%H%M%S%z')
    history.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(history,0o700)
    target=history/(prefix+'-'+stamp+'.json')
    retired={**descriptor,'retired_at':stamp,'retired_reason':reason or 'unspecified','retired_previous_status':status}
    write(target,retired)
    # The history copy is durable before the active descriptor disappears.
    if json.loads(target.read_text())!=retired:raise RuntimeError('Retirement copy failed verification')
    if journal.exists():
        operation=json.loads(journal.read_text())
    else:
        operation={}
    operation.setdefault('retired_target_descriptors',[]).append({'prefix':prefix,'path':str(target),'status':status,'reason':reason or 'unspecified','at':stamp})
    write(journal,operation)
    record.unlink()
    handle=os.open(STATE,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(handle)
    finally:os.close(handle)
    return {'retired':str(target),'prefix':prefix,'previous_status':status,'data_retained':True}


def main():
    reason=''
    if len(sys.argv)==3 and sys.argv[1]=='--reason':reason=sys.argv[2]
    elif len(sys.argv)!=1:raise SystemExit('usage: retire_recovery_target.py [--reason TEXT]')
    with (STATE/'operation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print(json.dumps(retire(reason=reason)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        raise SystemExit('Recovery target retirement refused: '+str(error)) from None