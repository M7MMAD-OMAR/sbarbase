"""Retain ownership and persist uncertainty before launching an external effect."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from effect_receipt import publish,complete


def main():
    if len(sys.argv)<4:
        raise SystemExit('Expected worker lock, identity and effect command')
    lock_path=Path(sys.argv[1])
    held,expected=os.fstat(3),lock_path.stat()
    if (held.st_dev,held.st_ino)!=(expected.st_dev,expected.st_ino):
        raise SystemExit('Invalid effect ownership descriptor')
    fcntl.flock(3,fcntl.LOCK_EX|fcntl.LOCK_NB)
    identity=json.loads(sys.argv[2])
    if (not isinstance(identity,dict)
            or any(not isinstance(identity.get(k),str) or not identity[k] for k in ('environment','runtime','claim'))
            or type(identity.get('attempt')) is not int or identity['attempt']<1):
        raise SystemExit('Invalid effect identity')
    receipt=lock_path.with_name('worker-effect.json')
    record={'version':1,'phase':'pending','token':str(uuid.uuid4()),'job':identity}
    publish(receipt,record)
    command=sys.argv[3:]
    child=subprocess.Popen(command,pass_fds=(3,),env=dict(os.environ,SBARBASE_EFFECT_TOKEN=record['token']))
    code=child.wait()
    # Admission refusal is proven before mutations only for this exact entry point.
    refused=code==75 and command==['/usr/bin/python3','lab/durable_runtime.py','provision',identity['runtime']]
    if code==0 or refused:complete(receipt,record,code)
    # Nonzero or signal termination leaves uncertainty, never automatic replay.
    return code if code>=0 else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:raise SystemExit('Effect ownership or outcome requires reconciliation') from None
