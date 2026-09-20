"""Durable uncertainty marker for worker-driven external effects."""
import json
import os
import re
from pathlib import Path
import tempfile


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def publish(path,record):
    # A partially written initial record also blocks replay. Never overwrite it.
    with path.open('x',encoding='utf-8') as output:
        os.chmod(path,0o600)
        json.dump(record,output)
        output.flush();os.fsync(output.fileno())
    sync_directory(path.parent)


def complete(path,record,exit_code):
    atomic_record(path,{**record,'phase':'completed','exitCode':exit_code})


def atomic_record(path,record):
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as output:
            temporary=Path(output.name)
            json.dump(record,output);output.flush();os.fsync(output.fileno())
        os.replace(temporary,path);sync_directory(path.parent)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def require_permission(state,runtime):
    """Direct provisioning CLI must not bypass a pending worker receipt."""
    path=state/'worker-effect.json'
    if not path.exists():return
    value=json.loads(path.read_text())
    if (value.get('version')!=1 or value.get('phase')!='pending'
            or value.get('token')!=os.environ.get('SBARBASE_EFFECT_TOKEN')
            or value.get('job',{}).get('runtime')!=runtime):
        raise RuntimeError('Provisioning effect requires reconciliation')
    held,expected=os.fstat(3),os.stat(state/'worker.lock')
    if (held.st_dev,held.st_ino)!=(expected.st_dev,expected.st_ino):
        raise RuntimeError('Provisioning ownership unavailable')
    held,expected=os.fstat(4),os.stat(state/'effect.lock')
    if (held.st_dev,held.st_ino)!=(expected.st_dev,expected.st_ino):
        raise RuntimeError('Effect lease unavailable')


def require_settled(state):
    if (state/'worker-effect.json').exists():
        raise RuntimeError('Settle or reconcile provisioning receipt before startup')


def native_outcome(state,runtime,exit_code,native):
    """Called by the fixed native entry point only after synchronous effects end."""
    if not os.environ.get('SBARBASE_EFFECT_TOKEN'):return
    require_permission(state,runtime)
    record=json.loads((state/'worker-effect.json').read_text())
    token=record['token']
    if (native not in ('durable-provision-v1','component-provision-v1')
            or record.get('native')!=native or not re.fullmatch(r'[a-f0-9-]{36}',token)
            or exit_code not in (0,75) or (exit_code==75 and native!='durable-provision-v1')):
        raise RuntimeError('Native outcome identity mismatch')
    if record.get('stageProtocol')==1 and native=='durable-provision-v1':
        stage=json.loads((state/'effect-stages'/(token+'.json')).read_text())
        expected='preflight' if exit_code==75 else 'publication'
        if stage!={**record,'stage':expected,'stageIndex':STAGES.index(expected)}:
            raise RuntimeError('Native outcome stage mismatch')
    directory=state/'effect-outcomes'
    directory.mkdir(mode=0o700,exist_ok=True)
    sync_directory(state)
    publish(directory/(token+'.json'),{**record,'phase':'native-completed','exitCode':exit_code})


STAGES=('preflight','database','services','storage','publication')


def native_stage(state,runtime,stage):
    """Write-ahead marker: the next phase cannot begin before this is durable."""
    if not os.environ.get('SBARBASE_EFFECT_TOKEN'):return
    require_permission(state,runtime)
    receipt=json.loads((state/'worker-effect.json').read_text())
    if (receipt.get('native')!='durable-provision-v1' or type(receipt.get('stageProtocol')) is not int
            or receipt['stageProtocol']!=1 or stage not in STAGES
            or not re.fullmatch(r'[a-f0-9-]{36}',receipt['token'])):
        raise RuntimeError('Stage protocol mismatch')
    directory=state/'effect-stages';directory.mkdir(mode=0o700,exist_ok=True);sync_directory(state)
    path=directory/(receipt['token']+'.json')
    index=STAGES.index(stage)
    value={**receipt,'stage':stage,'stageIndex':index}
    if index==0:
        publish(path,value)
        return
    previous=json.loads(path.read_text())
    expected={**receipt,'stage':STAGES[index-1],'stageIndex':index-1}
    if previous!=expected:raise RuntimeError('Stage sequence mismatch')
    atomic_record(path,value)


def _native_identity(state,runtime,stage,*,hba=False):
    """Bind native effects to the exact pending worker claim and durable stage."""
    import sqlite3
    from contextlib import closing
    import sql_operation_fence
    require_permission(state,runtime)
    path=state/'worker-effect.json'
    if not path.exists():raise RuntimeError('Native provisioning requires a worker receipt')
    receipt=json.loads(path.read_text())
    job=receipt.get('job',{})
    if (type(receipt.get('version')) is not int or receipt['version']!=1
            or type(receipt.get('stageProtocol')) is not int or receipt['stageProtocol']!=1
            or receipt.get('phase')!='pending' or receipt.get('native')!='durable-provision-v1'
            or receipt.get('token')!=os.environ.get('SBARBASE_EFFECT_TOKEN')
            or not isinstance(job,dict) or job.get('runtime')!=runtime):
        raise RuntimeError('Native SQL receipt identity mismatch')
    if hba and (type(receipt.get('hbaProtocol')) is not int or receipt['hbaProtocol']!=1):
        raise RuntimeError('HBA journal protocol receipt required')
    args=(runtime,receipt.get('token'),job.get('claim'),job.get('attempt'))
    sql_operation_fence.identity(*args)
    record=json.loads((state/'effect-stages'/(receipt['token']+'.json')).read_text())
    if record!={**receipt,'stage':stage,'stageIndex':STAGES.index(stage)}:
        raise RuntimeError('Native SQL stage identity mismatch')
    with closing(sqlite3.connect((state/'control.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as database:
        row=database.execute('SELECT runtime,claim,attempt,state FROM provision_jobs WHERE environment=?',(job.get('environment'),)).fetchone()
    if row!=(runtime,job['claim'],job['attempt'],'running'):
        raise RuntimeError('Native SQL catalog claim mismatch')
    return args


def sql_identity(state,runtime,stage):
    if stage not in ('preflight','database'):
        raise ValueError('Unsupported SQL authorization stage')
    return _native_identity(state,runtime,stage)


def hba_identity(state,runtime):
    """Services-stage identity only; caller must separately own host locks."""
    return _native_identity(state,runtime,'services',hba=True)
