"""Keep an OS lock alive in the worker process, including after wrapper exit."""
import fcntl
import os
import sys
import argparse
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--upstream', action='store_true')
parser.add_argument('--watch', action='store_true', help='Keep processing newly queued operations until stopped')
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
os.set_inheritable(lock, True)
os.environ['SBARBASE_WORKER_LOCKED'] = '1'
os.environ['SBARBASE_RUNTIME_PROFILE'] = profile
os.environ['SBARBASE_WORKER_WATCH'] = '1' if args.watch else '0'
os.execvp('bun', ['bun', 'lab/worker.ts'])
