"""Durable uncertainty marker for worker-driven external effects."""
import json
import os
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
    record={**record,'phase':'completed','exitCode':exit_code}
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


def require_settled(state):
    if (state/'worker-effect.json').exists():
        raise RuntimeError('Settle or reconcile provisioning receipt before startup')
