"""Keep an OS lock alive in the worker process, including after wrapper exit."""
import fcntl
import effect_lease
import os
import sys
import argparse
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--upstream', action='store_true')
parser.add_argument('--watch', action='store_true', help='Keep processing newly queued operations until stopped')
parser.add_argument('--settle-only', action='store_true', help='Settle a known outcome before runtime startup; refuse uncertainty')
args = parser.parse_args()
profile = 'upstream' if args.upstream else 'component'
state = root / '.lab' / 'upstream' if profile == 'upstream' else root / '.lab'
state.mkdir(parents=True, exist_ok=True)
inherited = os.environ.get('SBARBASE_WORKER_FD')
if inherited:
    lock = int(inherited)
    held, expected = os.fstat(lock), os.stat(state / 'worker.lock')
    if (held.st_dev, held.st_ino) != (expected.st_dev, expected.st_ino):
        raise SystemExit('Invalid inherited worker lock')
else:
    lock = os.open(state / 'worker.lock', os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Another provisioning worker is active')
# Never reuse an inherited effect lease: old guardians may still own it.
lease=effect_lease.acquire(state)
exported_lock,exported_lease=effect_lease.export_descriptors(lock,lease)
os.close(lock);os.close(lease)
lock,lease=exported_lock,exported_lease
operation=os.open(state/'operation.lock',os.O_CREAT|os.O_RDWR,0o600)
fcntl.flock(operation,fcntl.LOCK_EX|fcntl.LOCK_NB)
exported_operation=fcntl.fcntl(operation,fcntl.F_DUPFD_CLOEXEC,10)
os.close(operation);os.set_inheritable(exported_operation,True)
os.environ['SBARBASE_OPERATION_FD']=str(exported_operation)
os.environ['SBARBASE_EFFECT_FD']=str(lease)
os.set_inheritable(lock, True)
os.environ['SBARBASE_WORKER_FD'] = str(lock)
os.environ['SBARBASE_WORKER_LOCKED'] = '1'
os.environ['SBARBASE_RUNTIME_PROFILE'] = profile
os.environ['SBARBASE_RECEIPT_ONLY'] = '1' if args.settle_only else '0'
os.environ['SBARBASE_WORKER_WATCH'] = '1' if args.watch else '0'
os.execvp('bun', ['bun', 'lab/worker.ts'])
