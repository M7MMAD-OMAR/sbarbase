"""Retain ownership and persist uncertainty before launching an external effect."""
import fcntl
import json
import os
import math
import signal
import time
from pathlib import Path
import subprocess
import sys
import uuid
from effect_receipt import publish,complete
from dev import child_status,terminate_group


def main():
    if len(sys.argv)<5:
        raise SystemExit('Expected worker lock, identity and effect command')
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    # Keep this guardian outside the worker group so worker escalation cannot
    # interrupt cleanup. Parent-death binding remains active after setsid.
    os.setsid()
    timeout=float(sys.argv[3])
    if not math.isfinite(timeout) or not 0<timeout<=180:raise SystemExit('Invalid effect deadline')
    lock_path=Path(sys.argv[1])
    held,expected=os.fstat(3),lock_path.stat()
    if (held.st_dev,held.st_ino)!=(expected.st_dev,expected.st_ino):
        raise SystemExit('Invalid effect ownership descriptor')
    fcntl.flock(3,fcntl.LOCK_EX|fcntl.LOCK_NB)
    held_lease,expected_lease=os.fstat(4),lock_path.with_name('effect.lock').stat()
    if (held_lease.st_dev,held_lease.st_ino)!=(expected_lease.st_dev,expected_lease.st_ino):
        raise SystemExit('Invalid effect lease descriptor')
    fcntl.flock(4,fcntl.LOCK_EX|fcntl.LOCK_NB)
    identity=json.loads(sys.argv[2])
    if (not isinstance(identity,dict)
            or any(not isinstance(identity.get(k),str) or not identity[k] for k in ('environment','runtime','claim'))
            or type(identity.get('attempt')) is not int or identity['attempt']<1):
        raise SystemExit('Invalid effect identity')
    receipt=lock_path.with_name('worker-effect.json')
    command=sys.argv[4:]
    native=None
    if command==['/usr/bin/python3','lab/durable_runtime.py','provision',identity['runtime']]:native='durable-provision-v1'
    if command==['/usr/bin/python3','lab/provision.py',identity['runtime']]:native='component-provision-v1'
    record={'version':1,'phase':'pending','token':str(uuid.uuid4()),'job':identity,'native':native}
    publish(receipt,record)
    child=None;interrupted=False;code=None
    deadline=time.monotonic()+timeout
    try:
        if stopping:return 1
        child=subprocess.Popen(command,pass_fds=(3,4),start_new_session=True,env=dict(os.environ,SBARBASE_EFFECT_TOKEN=record['token']))
        while (code:=child_status(child)) is None:
            if stopping or time.monotonic()>=deadline:
                interrupted=True;break
            time.sleep(.02)
    finally:
        if child is not None:terminate_group(child,grace=.5)
    if interrupted or stopping or time.monotonic()>=deadline:return 1
    # Admission refusal is proven before mutations only for this exact entry point.
    refused=code==75 and command==['/usr/bin/python3','lab/durable_runtime.py','provision',identity['runtime']]
    if code==0 or refused:complete(receipt,record,code)
    # Nonzero or signal termination leaves uncertainty, never automatic replay.
    return code if code>=0 else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:raise SystemExit('Effect ownership or outcome requires reconciliation') from None
