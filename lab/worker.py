"""Keep an OS lock alive in the worker process, including after wrapper exit."""
import fcntl
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
if sys.argv[1:] not in ([], ['--upstream']):
    raise SystemExit('Usage: worker.py [--upstream]')
profile = 'upstream' if sys.argv[1:] else 'component'
state = root / '.lab' / 'upstream' if profile == 'upstream' else root / '.lab'
state.mkdir(parents=True, exist_ok=True)
lock = os.open(state / 'worker.lock', os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Another provisioning worker is active')
os.set_inheritable(lock, True)
os.environ['SBARBASE_WORKER_LOCKED'] = '1'
os.environ['SBARBASE_RUNTIME_PROFILE'] = profile
os.execvp('bun', ['bun', 'lab/worker.ts'])
